"""SQLite persistence: append-only event history plus a current-state projection."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from threading import RLock
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import AtlasContracts
from .history import EntityHistory, HitboxSnapshot
from .world import PLAYER_ENTITY_ID, WORLD_ENTITY_IDS, apply_action, initial_state, normalize_movement, replay_event

class WorldStore:
    def __init__(self, path: Path, contracts: AtlasContracts):
        self.path = path
        self.contracts = contracts
        self._last_sim_tick = time.monotonic()
        self._sim_accumulator = 0.0
        self._active_input = {"x": 0.0, "z": 0.0}
        self._active_run = False
        self._command_lock = RLock()
        self._last_input_sequence = 0
        self._movement_ack_cache: dict[int, dict[str, Any]] = {}
        self._player_history = EntityHistory(max_seconds=2.0, max_samples=120)
        self._target_history = EntityHistory(max_seconds=2.0, max_samples=120)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        initial, _ = self.read()
        self._record_history(0, time.monotonic(), initial["player"])

    def _record_history(self, sequence: int, at: float, player: dict[str, Any]) -> None:
        self._player_history.record(HitboxSnapshot(at, player["x"], 0.0, player["y"], .2, 1.8, 0.0, sequence))
        self._target_history.record(HitboxSnapshot(at, 12.0, 0.0, 9.0, .35, 1.25, 0.0, sequence))

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    def _initialize(self) -> None:
        with closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS world_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    version INTEGER NOT NULL,
                    state_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS world_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_identifier TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    object_id TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS world_events_no_update
                    BEFORE UPDATE ON world_events BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS world_events_no_delete
                    BEFORE DELETE ON world_events BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
            """)
            db.execute(
                "INSERT OR IGNORE INTO world_state(singleton, version, state_json) VALUES (1, 0, ?)",
                (json.dumps(initial_state(), separators=(",", ":")),),
            )

    def read(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with closing(self.connect()) as db:
            row = db.execute("SELECT state_json FROM world_state WHERE singleton = 1").fetchone()
            events = db.execute(
                "SELECT sequence, event_id, schema_version, event_type, occurred_at, actor_id, source_kind, source_identifier, rationale, subject_id, object_id, payload_json FROM world_events ORDER BY sequence DESC LIMIT 8"
            ).fetchall()
        recent = [{"sequence": e["sequence"], "event_id": e["event_id"], "schema_version": e["schema_version"],
                   "type": e["event_type"], "at": e["occurred_at"], "actor_id": e["actor_id"],
                   "source": {"kind": e["source_kind"], "identifier": e["source_identifier"]},
                   "rationale": e["rationale"], "subject_id": e["subject_id"], "object_id": e["object_id"],
                   "detail": json.loads(e["payload_json"])} for e in reversed(events)]
        return json.loads(row["state_json"]), recent

    @property
    def last_input_sequence(self) -> int:
        with self._command_lock:
            return self._last_input_sequence

    def command(self, action: dict[str, Any]) -> dict[str, Any]:
        # Movement commands update in-memory simulation input as well as SQLite;
        # serialize the whole command so concurrent HTTP requests cannot race.
        with self._command_lock:
            return self._command_locked(action)

    def _command_locked(self, action: dict[str, Any]) -> dict[str, Any]:
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version, state_json FROM world_state WHERE singleton = 1").fetchone()
            state = json.loads(row["state_json"])
            input_sequence = None
            movement_frames = None
            if action.get("type") == "move":
                input_sequence = action.get("sequence")
                if isinstance(input_sequence, bool) or not isinstance(input_sequence, int) or input_sequence < 1:
                    raise ValueError("Movement input sequence is invalid.")
                cached = self._movement_ack_cache.get(input_sequence)
                if cached is not None:
                    db.execute("ROLLBACK")
                    return cached
                if input_sequence <= self._last_input_sequence:
                    raise ValueError("Movement input sequence is stale.")
                movement_frames = action.get("frames")
                if movement_frames is not None:
                    if not isinstance(movement_frames, list) or not 1 <= len(movement_frames) <= 32:
                        raise ValueError("Movement frame batch must contain between 1 and 32 frames.")
                    expected_sequence = self._last_input_sequence + 1
                    normalized_frames = []
                    for frame in movement_frames:
                        if not isinstance(frame, dict) or frame.get("sequence") != expected_sequence:
                            raise ValueError("Movement frame sequence is invalid or contains a gap.")
                        frame_input = normalize_movement(frame.get("input"), frame.get("run"))
                        normalized_frames.append({"sequence": expected_sequence, "input": frame_input, "run": frame["run"]})
                        expected_sequence += 1
                    if normalized_frames[-1]["sequence"] != input_sequence:
                        raise ValueError("Movement acknowledgement must match the final frame.")
                    movement_frames = normalized_frames
                    final_frame = movement_frames[-1]
                    requested_input, requested_run = final_frame["input"], final_frame["run"]
                    action = {**action, "input": requested_input, "run": requested_run}
                else:
                    now = time.monotonic()
                    requested_input = normalize_movement(action.get("input"), action.get("run", False))
                    requested_run = action.get("run", False)
                    elapsed = min(max(now - self._last_sim_tick, 0.0), 0.33)
                    self._last_sim_tick = now
                    accumulated = self._sim_accumulator + elapsed
                    fixed_step = 1.0 / 60.0
                    steps = int((accumulated + 1e-12) / fixed_step)
                    sim_dt = steps * fixed_step
                    self._sim_accumulator = accumulated - sim_dt
                    action = {**action, "input": requested_input, "_sim_input": self._active_input,
                              "_sim_run": self._active_run, "dt": sim_dt}
                state["player"].setdefault("vx", 0.0)
                state["player"].setdefault("vz", 0.0)
            elif action.get("type") == "attack" and "rewind_sequence" in action:
                rewind_sequence = action["rewind_sequence"]
                if isinstance(rewind_sequence, bool) or not isinstance(rewind_sequence, int):
                    raise ValueError("Attack rewind sequence is invalid.")
                if rewind_sequence <= self._last_input_sequence:
                    now = time.monotonic()
                    attacker_history = self._player_history.rewind_sequence(rewind_sequence, now)
                    target_history = (self._target_history.rewind(attacker_history.time)
                                      if attacker_history is not None else None)
                    if attacker_history is not None and target_history is not None:
                        action = {**action,
                            "_attack_origin": {"x": attacker_history.x, "y": attacker_history.z},
                            "_rewound_target": {"x": target_history.x, "y": target_history.z,
                                                "radius": target_history.radius, "height": target_history.height},
                            "_rewind_applied": True}
            if movement_frames is not None:
                state, event_type, payload = state, "PlayerMoved", {}
                for frame in movement_frames:
                    frame_action = {"type": "move", "input": frame["input"], "run": frame["run"],
                                    "_sim_input": frame["input"], "_sim_run": frame["run"], "dt": 1.0 / 60.0}
                    state, event_type, payload = apply_action(state, frame_action)
            else:
                state, event_type, payload = apply_action(state, action)
            now = time.monotonic()
            occurred_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            event_id = str(uuid.uuid4())
            source_identifier = str(uuid.uuid4())
            target = action.get("target")
            object_id = WORLD_ENTITY_IDS.get(target)
            relation_for_event = {
                "ResourceGathered": "gathers",
                "NPCSpokenTo": "speaks_with",
                "BeaconAwakened": "awakens",
                "CreatureDamaged": "attacks",
                "PlayerGuarded": "guards",
                "PlayerDodged": "evades",
                "CreatureReawakened": "reawakens",
            }.get(event_type)
            if event_type == "PlayerMoved":
                rationale = f"The local player moved through the valley to {payload['x']},{payload['z']}."
            elif event_type == "ResourceGathered":
                rationale = "The player gathered a nearby lumen reed patch."
            elif event_type == "NPCSpokenTo":
                rationale = "The player chose to speak with Mara."
            elif event_type == "CreatureDamaged":
                rationale = "The player fought the nearby Mossling; the creature retaliated."
            elif event_type == "PlayerGuarded":
                rationale = "The player raised a guard against the nearby Mossling."
            elif event_type == "PlayerDodged":
                rationale = "The player sidestepped the nearby Mossling's attack line."
            elif event_type == "CreatureReawakened":
                rationale = "The player called the Mossling back from its resting place."
            else:
                rationale = "The player offered three lumen reeds to awaken the listening beacon."
            event_contract = {
                "event_id": event_id,
                "event_type": event_type,
                "schema_version": 1,
                "occurred_at": occurred_at,
                "actor_id": PLAYER_ENTITY_ID,
                "source_kind": "player_action",
                "source_identifier": source_identifier,
                "rationale": rationale,
                "subject_id": PLAYER_ENTITY_ID,
                "object_id": object_id,
            }
            self.contracts.validate_event(event_contract, relation_for_event)
            db.execute(
                """INSERT INTO world_events(
                       event_id, schema_version, occurred_at, event_type, actor_id,
                       source_kind, source_identifier, rationale, subject_id, object_id, payload_json
                   ) VALUES (?, 1, ?, ?, ?, 'player_action', ?, ?, ?, ?, ?)""",
                (event_id, occurred_at, event_type, PLAYER_ENTITY_ID, source_identifier, rationale, PLAYER_ENTITY_ID, object_id,
                 json.dumps(payload, separators=(",", ":"), sort_keys=True)),
            )
            updated = db.execute(
                "UPDATE world_state SET version = ?, state_json = ? WHERE singleton = 1 AND version = ?",
                (row["version"] + 1, json.dumps(state, separators=(",", ":")), row["version"]),
            ).rowcount
            if updated != 1:
                raise RuntimeError("world projection version changed during command")
            db.execute("COMMIT")
            if event_type == "PlayerMoved":
                self._active_input = requested_input
                self._active_run = requested_run
                self._record_history(input_sequence, now, state["player"])
            elif event_type == "PlayerDodged":
                self._active_input = {"x": 0.0, "z": 0.0}
                self._active_run = False
            result = {"event_id": event_id, "event_type": event_type, "at": occurred_at, "detail": payload}
            if event_type == "PlayerMoved" and input_sequence is not None:
                result["acknowledged_sequence"] = input_sequence
                self._last_input_sequence = input_sequence
                self._movement_ack_cache[input_sequence] = result
                if len(self._movement_ack_cache) > 64:
                    del self._movement_ack_cache[min(self._movement_ack_cache)]
            return result
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def rebuild_projection(self) -> dict[str, Any]:
        """Reconstruct current world state from the immutable event stream."""
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            state = initial_state()
            events = db.execute("SELECT event_type, payload_json FROM world_events ORDER BY sequence").fetchall()
            for event in events:
                replay_event(state, event["event_type"], json.loads(event["payload_json"]))
            db.execute(
                "UPDATE world_state SET version = ?, state_json = ? WHERE singleton = 1",
                (len(events), json.dumps(state, separators=(",", ":"))),
            )
            db.execute("COMMIT")
            return state
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
