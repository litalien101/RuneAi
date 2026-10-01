import math
import json
import tempfile
import unittest
from pathlib import Path

from atlas_server.contracts import AtlasContracts
from atlas_server.history import EntityHistory, HitboxSnapshot, validate_melee_hit
from atlas_server.store import WorldStore
from atlas_server.world import WORLD_OBSTACLES, TERRAIN, apply_action, initial_state, walkable_position


ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "specs" / "atlas" / "specs"


class MovementSequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = WorldStore(Path(self.temp.name) / "world.sqlite3", AtlasContracts(SPECS))

    def tearDown(self):
        self.temp.cleanup()

    def move(self, sequence, direction):
        return self.store.command({"type": "move", "sequence": sequence,
                                   "input": {"x": direction, "z": 0}, "run": False})

    def test_retry_of_committed_input_returns_same_ack_without_duplicate_event(self):
        first = self.move(1, 1)
        duplicate = self.move(1, 1)
        self.assertEqual(duplicate, first)
        self.assertEqual(self.store.last_input_sequence, 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM world_events").fetchone()[0], 1)

    def test_skipped_sequence_is_allowed_and_old_uncached_sequence_is_rejected(self):
        self.move(1, 1)
        latest = self.move(3, 0)
        self.assertEqual(latest["acknowledged_sequence"], 3)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.move(2, -1)

    def test_fixed_tick_batch_applies_each_frame_and_retries_idempotently(self):
        frames = [
            {"sequence": 1, "input": {"x": 1, "z": 0}, "run": False},
            {"sequence": 2, "input": {"x": 1, "z": 0}, "run": False},
            {"sequence": 3, "input": {"x": 0, "z": 0}, "run": False},
        ]
        command = {"type": "move", "sequence": 3, "frames": frames}
        first = self.store.command(command)
        self.assertEqual(first["acknowledged_sequence"], 3)
        state, events = self.store.read()
        self.assertAlmostEqual(state["player"]["x"], first["detail"]["x"], places=4)
        self.assertEqual(len(events), 1)
        self.assertEqual(self.store.command(command), first)
        self.assertEqual(len(self.store.read()[1]), 1)

    def test_fixed_tick_batch_rejects_sequence_gaps(self):
        command = {"type": "move", "sequence": 2, "frames": [
            {"sequence": 1, "input": {"x": 1, "z": 0}, "run": False},
            {"sequence": 2, "input": {"x": 1, "z": 0}, "run": False},
        ]}
        self.store.command(command)
        invalid = {"type": "move", "sequence": 4, "frames": [
            {"sequence": 4, "input": {"x": 0, "z": 1}, "run": False},
        ]}
        with self.assertRaisesRegex(ValueError, "gap"):
            self.store.command(invalid)

    def test_high_ping_tick_is_bounded_and_integrated_without_tunneling_steps(self):
        state = initial_state()
        state, _, _ = apply_action(state, {"type": "move", "input": {"x": 1, "z": 0},
                                           "_sim_input": {"x": 1, "z": 0}, "_sim_run": False, "dt": .25})
        self.assertTrue(math.isfinite(state["player"]["x"]))
        self.assertLess(state["player"]["x"] - 3, 2.5 * .25)

    def test_server_movement_matches_shared_browser_fixed_tick_contract(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "movement-contract.json").read_text())
        state = initial_state()
        for segment in fixture["segments"]:
            for _ in range(segment["ticks"]):
                state, _, _ = apply_action(state, {
                    "type": "move", "input": segment["input"], "run": segment["run"],
                    "_sim_input": segment["input"], "_sim_run": segment["run"],
                    "dt": 1 / fixture["fixed_hz"],
                })
        player = state["player"]
        self.assertLess(math.hypot(player["x"] - fixture["expected"]["position"]["x"],
                                   player["y"] - fixture["expected"]["position"]["z"]), 1e-9)
        self.assertLess(math.hypot(player["vx"] - fixture["expected"]["velocity"]["x"],
                                   player["vz"] - fixture["expected"]["velocity"]["z"]), 1e-9)

    def test_server_terrain_footprint_matches_shared_collision_contract(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "movement-contract.json").read_text())
        for sample in fixture["walkability"]:
            self.assertEqual(walkable_position(sample["x"], sample["z"]), sample["walkable"])

    def test_server_circular_colliders_match_shared_collision_contract(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "movement-contract.json").read_text())
        for sample in fixture["obstacleCollisions"]:
            self.assertEqual(walkable_position(sample["x"], sample["z"], obstacles=[sample["obstacle"]]),
                             sample["walkable"])
        self.assertEqual(sum(obstacle["kind"] == "tree" for obstacle in WORLD_OBSTACLES), 16)
        self.assertEqual({obstacle["kind"] for obstacle in WORLD_OBSTACLES if obstacle["kind"] != "tree"},
                         {"npc", "beacon", "creature"})

    def test_recent_server_recorded_position_can_rewind_a_late_melee_attack(self):
        import time
        self.store._last_input_sequence = 7
        self.store._record_history(7, time.monotonic(), {"x": 11.5, "y": 9.0})
        result = self.store.command({"type": "attack", "target": "mossling", "rewind_sequence": 7})
        self.assertEqual(result["event_type"], "CreatureDamaged")
        self.assertTrue(result["detail"]["rewind_applied"])

    def test_stale_attack_history_falls_back_to_current_authoritative_position(self):
        import time
        self.store._player_history = EntityHistory(max_seconds=2.0, max_samples=120)
        self.store._target_history = EntityHistory(max_seconds=2.0, max_samples=120)
        self.store._last_input_sequence = 7
        self.store._record_history(7, time.monotonic() - .75, {"x": 11.5, "y": 9.0})
        with self.assertRaisesRegex(ValueError, "closer"):
            self.store.command({"type": "attack", "target": "mossling", "rewind_sequence": 7})


class HitboxRewindTests(unittest.TestCase):
    def setUp(self):
        self.history = EntityHistory(max_seconds=2.0, max_samples=120)
        self.history.record(HitboxSnapshot(10.0, 0, 0, 0, .35, 1.8, 0))
        self.history.record(HitboxSnapshot(10.1, 1, 0, 0, .45, 1.8, .2))
        self.history.record(HitboxSnapshot(10.2, 2, 0, 0, .35, 1.8, .4))

    def test_rewind_interpolates_position_dimensions_and_rotation(self):
        sample = self.history.rewind(10.05)
        self.assertIsNotNone(sample)
        self.assertAlmostEqual(sample.x, .5)
        self.assertAlmostEqual(sample.radius, .4)
        self.assertAlmostEqual(sample.yaw, .1)

    def test_delayed_attack_uses_target_hitbox_at_attack_time(self):
        rewound = self.history.rewind(10.05)
        self.assertTrue(validate_melee_hit((0, 0, 0), rewound, reach=.3))
        current = self.history.rewind(10.2)
        self.assertFalse(validate_melee_hit((-.7, 0, 0), current, reach=.3))

    def test_rejects_timestamps_outside_recorded_window_and_invalid_snapshots(self):
        self.assertIsNone(self.history.rewind(9.0))
        self.assertIsNone(self.history.rewind(10.5))
        with self.assertRaises(ValueError):
            self.history.record(HitboxSnapshot(10.15, 0, 0, 0, .3, 1.8))


if __name__ == "__main__":
    unittest.main()
