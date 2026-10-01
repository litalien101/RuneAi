# Game asset provenance policy

## Decision for The Reach

The Reach uses procedural geometry authored in `web/app.js` for its player, NPC, beacon, vegetation, and resources. The world is rendered with Three.js, whose code is under MIT; it supplies rendering functionality, not RuneScape game content.

Do not extract or package models, textures, animations, maps, sounds, or other game data from the RuneAi cache or Darkan reference client in this project. The local RuneAi repository contains a 601 MB `main_file_cache.dat2` plus cache indexes and a hash manifest, but the inspected cache files do not establish a reuse license. The vendored Darkan client and server source trees identify GPL-3.0; their source license does not establish rights to separately bundled game cache assets. Cleaning, retopologizing, recoloring, or converting an asset does not establish permission to reuse its underlying content.

Those repositories may inform broad genre conventions and technical lessons. Do not copy their distinctive character designs, world layouts, UI art, names, or extracted content into The Reach.

## Future imported asset gate

Before importing any non-original asset, record and review:

- Asset identifier and file checksum.
- Creator and original source URL.
- Exact license or written permission, including commercial use and modification rights.
- Required attribution and redistribution conditions.
- Modifications made and the person who made them.

Reject assets whose ownership or reuse terms cannot be verified. Prefer original commissioned or authored assets and permissively licensed assets with clear source files. Store this record beside the asset under `web/assets/manifest.yaml`.
