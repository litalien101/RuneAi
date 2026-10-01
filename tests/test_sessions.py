import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from atlas_server.contracts import AtlasContracts
from atlas_server.server import Handler
from atlas_server.store import WorldStore
from atlas_server.world import PLAYER_ENTITY_IDS, initial_state

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "specs" / "atlas" / "specs"


class SessionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = WorldStore(Path(self.temp.name) / "world.sqlite3", AtlasContracts(SPECS))
        handler = type("BoundHandler", (Handler,), {"store": self.store})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, token=None):
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["X-Atlas-Session"] = token
        req = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_api_requires_session_and_binds_events_to_server_resolved_player(self):
        status, body = self.request("/api/state")
        self.assertEqual(status, 401)
        status, session = self.request("/api/session", method="POST")
        self.assertEqual(status, 201)
        self.assertIn(session["player_id"], PLAYER_ENTITY_IDS)
        status, state = self.request("/api/state", token=session["session_token"])
        self.assertEqual(status, 200)
        self.assertEqual(state["active_player"]["id"], session["player_id"])
        status, _ = self.request("/api/action", method="POST", body={
            "type": "move", "sequence": 1,
            "frames": [{"sequence": 1, "input": {"x": 1, "z": 0}, "run": False}],
            "player_id": PLAYER_ENTITY_IDS[1] if session["player_id"] == PLAYER_ENTITY_IDS[0] else PLAYER_ENTITY_IDS[0],
        }, token=session["session_token"])
        self.assertEqual(status, 200)
        _, recent = self.store.read(session["player_id"])
        self.assertEqual(recent[-1]["actor_id"], session["player_id"])
        self.assertNotEqual(recent[-1]["actor_id"], body.get("player_id"))

    def test_old_single_player_projection_migrates_without_losing_personal_or_shared_progress(self):
        path = Path(self.temp.name) / "legacy.sqlite3"
        legacy = initial_state()
        legacy["player"].update(x=6.25, y=8.5)
        legacy["inventory"]["lumen_reed"] = 2
        legacy["gathered"] = ["reed-west", "reed-north"]
        legacy["mara_met"] = True
        import sqlite3
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE world_state(singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL, state_json TEXT NOT NULL)")
            db.execute("INSERT INTO world_state VALUES (1, 4, ?)", (json.dumps(legacy),))
        migrated = WorldStore(path, AtlasContracts(SPECS))
        first, _ = migrated.read(PLAYER_ENTITY_IDS[0])
        second, _ = migrated.read(PLAYER_ENTITY_IDS[1])
        self.assertEqual((first["player"]["x"], first["player"]["y"]), (6.25, 8.5))
        self.assertEqual(first["inventory"]["lumen_reed"], 2)
        self.assertTrue(first["mara_met"])
        self.assertEqual(first["gathered"], ["reed-west", "reed-north"])
        self.assertEqual(second["inventory"]["lumen_reed"], 0)
        self.assertEqual(second["player"]["x"], 4.2)


if __name__ == "__main__":
    unittest.main()
