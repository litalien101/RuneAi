"""SQLite persistence for shared world history and per-player projections."""

from __future__ import annotations

import json
import secrets
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
from .world import (
    PLAYER_ENTITY_ID, PLAYER_ENTITY_IDS, PLAYER_NAMES, PLAYER_STARTS,
    WORLD_ENTITY_IDS, apply_action, initial_state, normalize_movement, replay_event,
)

PERSONAL_FIELDS = ("player", "inventory", "player_health", "player_defense", "journal")
SESSION_IDLE_TTL_SECONDS = 30.0


def _split_state(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    personal = {key: state[key] for key in PERSONAL_FIELDS if key in state}
    shared = {key: value for key, value in state.items() if key not in PERSONAL_FIELDS and key != "players"}
    return shared, personal


class WorldStore:
    """One shared valley, two durable traveler profiles, and ephemeral tab sessions."""

    def __init__(self, path: Path, contracts: AtlasContracts):
        self.path = path
        self.contracts = contracts
        self._command_lock = RLock()
        self._sessions: dict[str, str] = {}
        self._seat_by_player: dict[str, str] = {}
        self._session_last_seen: dict[str, float] = {}
        self._runtime: dict[str, dict[str, Any]] = {}
        self._target_history = EntityHistory(max_seconds=2.0, max_samples=120)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        for player_id in PLAYER_ENTITY_IDS:
            self._runtime[player_id] = self._make_runtime(self._last_tick_from_db(player_id))
        # Compatibility aliases used by the single-player movement tests and tools.
        self._player_history = self._runtime[PLAYER_ENTITY_ID]["history"]
        for player_id in PLAYER_ENTITY_IDS:
            state, _ = self.read(player_id)
            own = next(player for player in state["players"] if player["is_self"])
            self._record_history(player_id, own["last_processed_input"], time.monotonic(), state["player"])

    @staticmethod
    def _make_runtime(last_tick: int = 0) -> dict[str, Any]:
        return {
            "last_tick": last_tick,
            "active_input": {"x": 0.0, "z": 0.0},
            "active_run": False,
            "last_sim_tick": time.monotonic(),
            "sim_accumulator": 0.0,
            "ack_cache": {},
            "history": EntityHistory(max_seconds=2.0, max_samples=120),
        }

    def _record_history(self, player_id: str | int, sequence: int | float,
                        at: float | dict[str, Any], player: dict[str, Any] | None = None) -> None:
        if player is None:  # Preserve the original single-player test/helper signature.
            player = at
            at = sequence
            sequence = int(player_id)
            player_id = PLAYER_ENTITY_ID
        assert isinstance(player, dict)
        self._runtime[player_id]["history"].record(
            HitboxSnapshot(float(at), player["x"], 0.0, player["y"], .2, 1.8, 0.0, int(sequence))
        )
        self._target_history.record(HitboxSnapshot(float(at), 12.0, 0.0, 9.0, .35, 1.25, 0.0, int(sequence)))

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
                CREATE TABLE IF NOT EXISTS player_states (
                    player_id TEXT PRIMARY KEY,
                    player_name TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    last_tick INTEGER NOT NULL DEFAULT 0
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
            row = db.execute("SELECT state_json FROM world_state WHERE singleton = 1").fetchone()
            legacy_state = json.loads(row["state_json"])
            _, default_personal = _split_state(initial_state())
            for player_id in PLAYER_ENTITY_IDS:
                exists = db.execute("SELECT 1 FROM player_states WHERE player_id = ?", (player_id,)).fetchone()
                if exists:
                    continue
                if player_id == PLAYER_ENTITY_ID:
                    personal = {key: legacy_state.get(key, value) for key, value in default_personal.items()}
                else:
                    personal = json.loads(json.dumps(default_personal))
                    x, y = PLAYER_STARTS[player_id]
                    personal["player"].update(x=float(x), y=float(y), vx=0.0, vz=0.0)
                    personal["journal"] = ["Explore the valley together."]
                db.execute(
                    "INSERT INTO player_states(player_id, player_name, state_json, last_tick) VALUES (?, ?, ?, 0)",
                    (player_id, PLAYER_NAMES[player_id], json.dumps(personal, separators=(",", ":"))),
                )
            # Existing saves keep shared progress in the world row and player-specific
            # fields in player_states. This is an in-place migration from the old shape.
            shared, _ = _split_state(legacy_state)
            db.execute("UPDATE world_state SET state_json = ? WHERE singleton = 1",
                       (json.dumps(shared, separators=(",", ":")),))

    def _last_tick_from_db(self, player_id: str) -> int:
        with closing(self.connect()) as db:
            row = db.execute("SELECT last_tick FROM player_states WHERE player_id = ?", (player_id,)).fetchone()
        if row is None:
            raise ValueError("Player profile is unavailable.")
        return int(row["last_tick"])

    def create_session(self) -> dict[str, str]:
        """Assign the first free local traveler seat to a new browser tab."""
        with self._command_lock:
            self._expire_sessions(time.monotonic())
            player_id = next((pid for pid in PLAYER_ENTITY_IDS if pid not in self._seat_by_player), None)
            if player_id is None:
                raise SessionCapacityError("Both local traveler seats are in use. Restart the server to release them.")
            token = secrets.token_urlsafe(32)
            self._sessions[token] = player_id
            self._seat_by_player[player_id] = token
            self._session_last_seen[token] = time.monotonic()
            return {"session_token": token, "player_id": player_id, "player_name": PLAYER_NAMES[player_id]}

    def player_for_session(self, token: str | None) -> str | None:
        if not isinstance(token, str):
            return None
        with self._command_lock:
            now = time.monotonic()
            self._expire_sessions(now)
            player_id = self._sessions.get(token)
            if player_id is not None:
                self._session_last_seen[token] = now
            return player_id

    def _expire_sessions(self, now: float) -> None:
        expired = [key for key, seen in self._session_last_seen.items()
                   if now - seen > SESSION_IDLE_TTL_SECONDS]
        for key in expired:
            player_id = self._sessions.pop(key, None)
            self._session_last_seen.pop(key, None)
            if player_id is not None and self._seat_by_player.get(player_id) == key:
                del self._seat_by_player[player_id]

    def session_count(self) -> int:
        with self._command_lock:
            return len(self._sessions)

    def read(self, player_id: str = PLAYER_ENTITY_ID) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if player_id not in PLAYER_ENTITY_IDS:
            raise ValueError("Player profile is unavailable.")
        with closing(self.connect()) as db:
            shared_row = db.execute("SELECT state_json FROM world_state WHERE singleton = 1").fetchone()
            player_rows = db.execute("SELECT player_id, player_name, state_json, last_tick FROM player_states ORDER BY player_id").fetchall()
            events = db.execute(
                "SELECT sequence, event_id, schema_version, event_type, occurred_at, actor_id, source_kind, source_identifier, rationale, subject_id, object_id, payload_json FROM world_events ORDER BY sequence DESC LIMIT 8"
            ).fetchall()
        shared = json.loads(shared_row["state_json"])
        players_by_id = {row["player_id"]: (row, json.loads(row["state_json"])) for row in player_rows}
        row, personal = players_by_id[player_id]
        state = {**shared, **personal}
        state["players"] = [
            {"id": pid, "name": player_row["player_name"], "x": saved["player"]["x"],
             "y": saved["player"]["y"], "vx": saved["player"].get("vx", 0.0),
             "vz": saved["player"].get("vz", 0.0), "last_processed_input": player_row["last_tick"],
             "height": saved["player"].get("height", 0.0),
             "vertical_velocity": saved["player"].get("vy", 0.0),
             "grounded": saved["player"].get("grounded", True),
             "is_self": pid == player_id}
            for pid, (player_row, saved) in players_by_id.items()
        ]
        recent = [{"sequence": e["sequence"], "event_id": e["event_id"], "schema_version": e["schema_version"],
                   "type": e["event_type"], "at": e["occurred_at"], "actor_id": e["actor_id"],
                   "source": {"kind": e["source_kind"], "identifier": e["source_identifier"]},
                   "rationale": e["rationale"], "subject_id": e["subject_id"], "object_id": e["object_id"],
                   "detail": json.loads(e["payload_json"])} for e in reversed(events)]
        return state, recent

    @property
    def last_input_sequence(self) -> int:
        return self.last_input_sequence_for(PLAYER_ENTITY_ID)

    def last_input_sequence_for(self, player_id: str) -> int:
        return self._last_tick_from_db(player_id)

    @property
    def _last_input_sequence(self) -> int:
        return self._runtime[PLAYER_ENTITY_ID]["last_tick"]

    @_last_input_sequence.setter
    def _last_input_sequence(self, value: int) -> None:
        self._runtime[PLAYER_ENTITY_ID]["last_tick"] = value
        with closing(self.connect()) as db:
            db.execute("UPDATE player_states SET last_tick = ? WHERE player_id = ?", (value, PLAYER_ENTITY_ID))

    @property
    def _player_history(self) -> EntityHistory:
        return self._runtime[PLAYER_ENTITY_ID]["history"]

    @_player_history.setter
    def _player_history(self, value: EntityHistory) -> None:
        if hasattr(self, "_runtime") and PLAYER_ENTITY_ID in self._runtime:
            self._runtime[PLAYER_ENTITY_ID]["history"] = value

    def command(self, action: dict[str, Any], player_id: str = PLAYER_ENTITY_ID) -> dict[str, Any]:
        if player_id not in PLAYER_ENTITY_IDS:
            raise ValueError("Player profile is unavailable.")
        with self._command_lock:
            return self._command_locked(action, player_id)

    def _command_locked(self, action: dict[str, Any], player_id: str) -> dict[str, Any]:
        runtime = self._runtime[player_id]
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version, state_json FROM world_state WHERE singleton = 1").fetchone()
            profile = db.execute("SELECT state_json, last_tick FROM player_states WHERE player_id = ?", (player_id,)).fetchone()
            shared = json.loads(row["state_json"])
            personal = json.loads(profile["state_json"])
            state = {**shared, **personal}
            input_sequence = None
            movement_frames = None
            other_players = db.execute("SELECT player_id, state_json FROM player_states WHERE player_id != ?", (player_id,)).fetchall()
            dynamic_obstacles = [
                {"x": saved["player"]["x"], "z": saved["player"]["y"], "radius": .2}
                for saved in (json.loads(item["state_json"]) for item in other_players)
            ]
            from .world import WORLD_OBSTACLES
            collision_obstacles = [*WORLD_OBSTACLES, *dynamic_obstacles]
            if action.get("type") == "move":
                input_sequence = action.get("sequence")
                if isinstance(input_sequence, bool) or not isinstance(input_sequence, int) or input_sequence < 1:
                    raise ValueError("Movement input sequence is invalid.")
                cached = runtime["ack_cache"].get(input_sequence)
                if cached is not None:
                    db.execute("ROLLBACK")
                    return cached
                last_tick = int(profile["last_tick"])
                if input_sequence <= last_tick:
                    raise ValueError("Movement input sequence is stale.")
                movement_frames = action.get("frames")
                if movement_frames is not None:
                    if not isinstance(movement_frames, list) or not 1 <= len(movement_frames) <= 32:
                        raise ValueError("Movement frame batch must contain between 1 and 32 frames.")
                    expected_tick = last_tick + 1
                    normalized_frames = []
                    for frame in movement_frames:
                        if not isinstance(frame, dict) or frame.get("sequence") != expected_tick:
                            raise ValueError("Movement frame sequence is invalid or contains a gap.")
                        frame_input = normalize_movement(frame.get("input"), frame.get("run"))
                        jump = frame.get("jump", False)
                        if not isinstance(jump, bool):
                            raise ValueError("Jump input must be boolean.")
                        normalized_frames.append({"sequence": expected_tick, "input": frame_input,
                                                  "run": frame["run"], "jump": jump})
                        expected_tick += 1
                    if normalized_frames[-1]["sequence"] != input_sequence:
                        raise ValueError("Movement acknowledgement must match the final frame.")
                    movement_frames = normalized_frames
                    final_frame = movement_frames[-1]
                    requested_input, requested_run = final_frame["input"], final_frame["run"]
                    requested_jump = final_frame["jump"]
                    action = {**action, "input": requested_input, "run": requested_run, "jump": requested_jump}
                else:
                    now = time.monotonic()
                    requested_input = normalize_movement(action.get("input"), action.get("run", False))
                    requested_run = action.get("run", False)
                    requested_jump = action.get("jump", False)
                    if not isinstance(requested_jump, bool):
                        raise ValueError("Jump input must be boolean.")
                    elapsed = min(max(now - runtime["last_sim_tick"], 0.0), 0.33)
                    runtime["last_sim_tick"] = now
                    accumulated = runtime["sim_accumulator"] + elapsed
                    fixed_step = 1.0 / 60.0
                    steps = int((accumulated + 1e-12) / fixed_step)
                    sim_dt = steps * fixed_step
                    runtime["sim_accumulator"] = accumulated - sim_dt
                    action = {**action, "input": requested_input, "_sim_input": runtime["active_input"],
                              "_sim_run": runtime["active_run"], "dt": sim_dt, "_obstacles": collision_obstacles}
                state["player"].setdefault("vx", 0.0)
                state["player"].setdefault("vz", 0.0)
            elif action.get("type") == "attack" and "rewind_sequence" in action:
                rewind_sequence = action["rewind_sequence"]
                if isinstance(rewind_sequence, bool) or not isinstance(rewind_sequence, int):
                    raise ValueError("Attack rewind sequence is invalid.")
                if rewind_sequence <= int(profile["last_tick"]):
                    now = time.monotonic()
                    attacker_history = runtime["history"].rewind_sequence(rewind_sequence, now)
                    target_history = self._target_history.rewind(attacker_history.time) if attacker_history else None
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
                                    "jump": frame["jump"],
                                    "_sim_input": frame["input"], "_sim_run": frame["run"], "dt": 1.0 / 60.0,
                                    "_obstacles": collision_obstacles}
                    state, event_type, payload = apply_action(state, frame_action)
            else:
                state, event_type, payload = apply_action(state, action)

            now = time.monotonic()
            occurred_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            event_id, source_identifier = str(uuid.uuid4()), str(uuid.uuid4())
            object_id = WORLD_ENTITY_IDS.get(action.get("target"))
            relation_for_event = {
                "ResourceGathered": "gathers", "NPCSpokenTo": "speaks_with", "BeaconAwakened": "awakens",
                "CreatureDamaged": "attacks", "PlayerGuarded": "guards", "PlayerDodged": "evades",
                "CreatureReawakened": "reawakens",
            }.get(event_type)
            rationale = {
                "ResourceGathered": f"{PLAYER_NAMES[player_id]} gathered a nearby lumen reed patch.",
                "NPCSpokenTo": f"{PLAYER_NAMES[player_id]} chose to speak with Mara.",
                "BeaconAwakened": f"{PLAYER_NAMES[player_id]} offered three lumen reeds to awaken the listening beacon.",
                "CreatureDamaged": f"{PLAYER_NAMES[player_id]} fought the nearby Mossling; the creature retaliated.",
                "PlayerGuarded": f"{PLAYER_NAMES[player_id]} raised a guard against the nearby Mossling.",
                "PlayerDodged": f"{PLAYER_NAMES[player_id]} sidestepped the nearby Mossling's attack line.",
                "CreatureReawakened": f"{PLAYER_NAMES[player_id]} called the Mossling back from its resting place.",
            }.get(event_type)
            if event_type == "PlayerMoved":
                rationale = f"{PLAYER_NAMES[player_id]} moved through the valley to {payload['x']},{payload['z']}."
            assert rationale is not None
            if event_type == "PlayerMoved" and input_sequence is not None:
                payload = {**payload, "tick": input_sequence}
            event_contract = {"event_id": event_id, "event_type": event_type, "schema_version": 1,
                              "occurred_at": occurred_at, "actor_id": player_id, "source_kind": "player_action",
                              "source_identifier": source_identifier, "rationale": rationale,
                              "subject_id": player_id, "object_id": object_id}
            self.contracts.validate_event(event_contract, relation_for_event)
            db.execute(
                """INSERT INTO world_events(
                       event_id, schema_version, occurred_at, event_type, actor_id,
                       source_kind, source_identifier, rationale, subject_id, object_id, payload_json
                   ) VALUES (?, 1, ?, ?, ?, 'player_action', ?, ?, ?, ?, ?)""",
                (event_id, occurred_at, event_type, player_id, source_identifier, rationale, player_id, object_id,
                 json.dumps(payload, separators=(",", ":"), sort_keys=True)),
            )
            new_shared, new_personal = _split_state(state)
            updated = db.execute(
                "UPDATE world_state SET version = ?, state_json = ? WHERE singleton = 1 AND version = ?",
                (row["version"] + 1, json.dumps(new_shared, separators=(",", ":")), row["version"]),
            ).rowcount
            if updated != 1:
                raise RuntimeError("world projection version changed during command")
            new_tick = input_sequence if event_type == "PlayerMoved" and input_sequence is not None else int(profile["last_tick"])
            db.execute("UPDATE player_states SET state_json = ?, last_tick = ? WHERE player_id = ?",
                       (json.dumps(new_personal, separators=(",", ":")), new_tick, player_id))
            db.execute("COMMIT")
            if event_type == "PlayerMoved":
                runtime["active_input"] = requested_input
                runtime["active_run"] = requested_run
                self._record_history(player_id, new_tick, now, state["player"])
            elif event_type == "PlayerDodged":
                runtime["active_input"] = {"x": 0.0, "z": 0.0}
                runtime["active_run"] = False
            result = {"event_id": event_id, "event_type": event_type, "at": occurred_at, "detail": payload}
            if event_type == "PlayerMoved" and input_sequence is not None:
                result["acknowledged_sequence"] = input_sequence
                runtime["last_tick"] = input_sequence
                runtime["ack_cache"][input_sequence] = result
                if len(runtime["ack_cache"]) > 64:
                    del runtime["ack_cache"][min(runtime["ack_cache"])]
            return result
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def rebuild_projection(self) -> dict[str, Any]:
        """Rebuild shared and per-player projections from immutable events."""
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            base = initial_state()
            shared, _ = _split_state(base)
            personal_by_player: dict[str, dict[str, Any]] = {}
            last_ticks: dict[str, int] = {}
            for player_id in PLAYER_ENTITY_IDS:
                row = db.execute("SELECT state_json, last_tick FROM player_states WHERE player_id = ?", (player_id,)).fetchone()
                personal_by_player[player_id] = json.loads(row["state_json"])
                last_ticks[player_id] = int(row["last_tick"])
            events = db.execute("SELECT actor_id, event_type, payload_json FROM world_events ORDER BY sequence").fetchall()
            for event in events:
                player_id = event["actor_id"] if event["actor_id"] in personal_by_player else PLAYER_ENTITY_ID
                payload = json.loads(event["payload_json"])
                state = {**shared, **personal_by_player[player_id]}
                replay_event(state, event["event_type"], payload)
                shared, personal_by_player[player_id] = _split_state(state)
                if event["event_type"] == "PlayerMoved" and isinstance(payload.get("tick"), int):
                    last_ticks[player_id] = max(last_ticks[player_id], payload["tick"])
            db.execute("UPDATE world_state SET version = ?, state_json = ? WHERE singleton = 1",
                       (len(events), json.dumps(shared, separators=(",", ":"))))
            for player_id, personal in personal_by_player.items():
                db.execute("UPDATE player_states SET state_json = ?, last_tick = ? WHERE player_id = ?",
                           (json.dumps(personal, separators=(",", ":")), last_ticks[player_id], player_id))
            db.execute("COMMIT")
            return {**shared, **personal_by_player[PLAYER_ENTITY_ID]}
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()


class SessionCapacityError(ValueError):
    """Raised when both local player seats are occupied."""
