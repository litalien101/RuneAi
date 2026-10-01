# Project Atlas: The Reach

The Reach is Atlas's first playable reference world: a small, persistent exploration game where player actions change a world that keeps its history.

## Run locally

Requires Python 3.11 or newer and Node.js 20+. The Python server uses PyYAML to validate Atlas's YAML contracts; the browser client uses Three.js for rendering. Both projects use the permissive MIT license. Third-party notices are in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

```bash
cd Project_Atlas
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
npm ci
npm run build
python3 -m atlas_server
```

Open <http://127.0.0.1:8765>. Each browser tab claims one of two local traveler sessions; open a second independent tab to play together. Both travelers appear in both views, while each tab controls its own character with **WASD**, **Shift** to run, **Space** to jump, and left-click travel. A third concurrent tab is refused; inactive sessions expire after 30 seconds, and restarting the server releases all seats. Use **right mouse drag** (or middle mouse drag) and the **arrow keys** to orbit the third-person view; the **scroll wheel** smoothly zooms in and out. Open **View** in the top-right corner to adjust mouse sensitivity, zoom speed, camera follow smoothing, and vertical-look inversion; these preferences are saved in this browser. Each traveler predicts movement on fixed ticks and submits independent ordered input-frame batches; the server keeps positions, vertical jump state, and tick acknowledgements separate while both travelers share gathered patches, Mara's introduction, the beacon, and the Mossling. Both client and server check terrain boundaries and shared circular colliders for tree trunks, Mara, the beacon, the Mossling, and the other traveler. Press **E** or click a nearby prompt to interact. When the Mossling is close, a contextual panel offers **Attack**, **Block**, and **Dodge**; a readied defense protects against its retaliation on your next strike, and Dodge sidesteps its attack line. After defeating it, approach its glowing resting seed and press **E** to reawaken it and replay the encounter without resetting your save. The satchel and Field notes belong to the active traveler. Ambient sound begins after your first input and can be muted there. Gather three reeds and activate the beacon. The local world is saved in `data/world.sqlite3`.

To reset the save, stop the server and remove `data/world.sqlite3`.

### Network development controls

Use **1–5** while playing to switch the movement request simulator: **1** local, **2** good (50 ms), **3** average (120 ms), **4** bad (250 ms), and **5** packet-loss mode (10%). The debug strip reports RTT, jitter, simulated loss, retries, queued acknowledgements, and reconciliation correction. The simulator retries sequenced fixed-tick movement batches; duplicate retries are idempotent on the server. This is a local HTTP stress tool, not a packet-driven multiplayer transport. Press **6** to show the combat rewind overlay after an attack.

Run `npm run ci` for deterministic JavaScript network/property-style checks, Python movement and rewind checks, and a client build. Both runtimes consume `tests/fixtures/movement-contract.json` to verify the same 60 Hz acceleration, diagonal-run, stop, and terrain-footprint behavior. The test setup uses Node's built-in runner instead of adding Jest or property-testing packages to this small JavaScript project. Attacks validate against a bounded history of server-recorded player positions, and the rewind overlay shows the historic attack origin and hitbox. The Mossling is stationary, so moving-target rewind remains outside this local two-player slice. Remote traveler snapshots are polled over local HTTP; this is not a persistent multiplayer transport.

## What this proves

- The server validates and applies actions; the browser only renders state and sends intent.
- Startup validates the seeded Player, NPC, Region, Building, ResourceNode entities and their relationships against the bundled Atlas ontology and schema registry in `specs/atlas/specs`.
- Every accepted game event is validated as a registered Atlas `Event` entity and its action relationship is checked against the ontology.
- World state persists in SQLite.
- Each accepted action appends an immutable event in the same transaction as the state update.
- The event history records schema version, actor, source, affected object IDs, and rationale; it can rebuild current state.
- The beacon activation is a creator-authored deterministic world change, not an AI-generated claim.
- The client is a working game loop, not an Atlas platform or production MMO service.

## Architecture

```text
Browser client  ->  local HTTP API  ->  action rules  ->  SQLite transaction
      ^                                                    ├── current projection
      └──────────────── state query  <-───────────────────└── append-only event source
```

The project uses permissively licensed libraries and original procedural game geometry. It does not package assets from the reference RSPS cache. It has no external service, account system, analytics, AI, or network exposure. It binds to loopback by default. Local tab sessions are ephemeral seat assignments, not real player accounts. This is a local foundation demo, not safe to expose to the public internet or use for real player accounts.

The two-player local session, state ownership, and per-player tick rules are in [`specs/local-multiplayer.md`](specs/local-multiplayer.md).
The game-to-ontology mapping and event relationships are documented in [`specs/reach-world-model.md`](specs/reach-world-model.md).
The art and asset reuse rules are in [`specs/asset-provenance.md`](specs/asset-provenance.md).
The world’s palette, lighting, composition, camera, and sound goals are in [`specs/visual-direction.md`](specs/visual-direction.md).

## Next foundation milestone

Before online multiplayer, replace local seat assignment with durable account/session credentials, add server-driven tick scheduling and a persistent transport, and define reconnection and presence behavior. Colyseus remains deferred until that remote-play milestone. Rapier was evaluated but is not part of the movement stack: the current scene has a small static grid, and introducing separate JS/Python physics bindings would add version and parity risk without solving a current obstacle-collision requirement. Revisit it when the world gains authored 3D colliders, slopes, or moving rigid bodies. The bundled ontology and schema registry are the contracts this game slice validates; they do not replace the broader Atlas evidence and policy contracts.

## License policy

The application code is original. Third-party libraries use permissive licenses whose notices are included in `THIRD_PARTY_NOTICES.md`. The project owner should choose and add a license for distributing Atlas source code; until then, assume its source is not granted for reuse.
