"""Loopback-only HTTP server for the local Reach game."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import AtlasContracts
from .store import WorldStore
from .world import public_state

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DATA = Path(os.environ.get("ATLAS_DATA_DIR", ROOT / "data"))
SPECS = ROOT.parent / "specs" / "atlas" / "specs"
MAX_BODY = 4096

class Handler(BaseHTTPRequestHandler):
    server_version = "AtlasLocal/0.1"
    store: WorldStore

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/state":
            state, events = self.store.read()
            response = public_state(state, events)
            response["last_processed_input"] = self.store.last_input_sequence
            return self._json(200, response)
        asset = {"/": "index.html", "/game.js": "game.js", "/styles.css": "styles.css"}.get(path)
        if asset is None:
            return self._json(404, {"error": "Not found"})
        content_type = "text/html; charset=utf-8" if asset.endswith(".html") else "text/javascript; charset=utf-8" if asset.endswith(".js") else "text/css; charset=utf-8"
        body = (WEB / asset).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/action":
            return self._json(404, {"error": "Not found"})
        expected_host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host", "").lower() not in {expected_host, f"localhost:{self.server.server_port}"}:
            return self._json(403, {"error": "Invalid host"})
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
            return self._json(403, {"error": "Cross-origin actions are not allowed"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._json(400, {"error": "Invalid content length"})
        if length < 1 or length > MAX_BODY:
            return self._json(413, {"error": "Action request is too large or empty"})
        if self.headers.get_content_type() != "application/json":
            return self._json(415, {"error": "Expected application/json"})
        try:
            action = json.loads(self.rfile.read(length))
            if not isinstance(action, dict):
                raise ValueError("Action must be an object.")
            event = self.store.command(action)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"error": "Malformed JSON"})
        except ValueError as exc:
            return self._json(422, {"error": str(exc)})
        except Exception:
            self.log_error("request failed while applying an action")
            return self._json(500, {"error": "The action could not be saved."})
        return self._json(200, event)

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

def main() -> None:
    host = os.environ.get("ATLAS_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("This prototype is loopback-only. Do not expose it to a network.")
    port = int(os.environ.get("ATLAS_PORT", "8765"))
    contracts = AtlasContracts(SPECS)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    Handler.store = WorldStore(DATA / "world.sqlite3", contracts)
    print(f"The Reach is running at http://127.0.0.1:{server.server_port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down The Reach.")
    finally:
        server.server_close()
