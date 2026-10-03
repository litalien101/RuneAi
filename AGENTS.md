# Contributor and AI orientation

Read `README.md` first. Use the focused references below before changing a
subsystem; do not infer production readiness from a generated preview.

## Project map

- `atlas_server/`: local Python HTTP server, world/action rules, storage, and
  appearance profile validation.
- `web/`: browser client, Three.js rendering, UI, runtime asset manifest, and
  bundled browser output (`web/game.js`). Edit `web/app.js` and source modules;
  rebuild the bundle with `npm run build`.
- `art/characters/profiles/`: input design profiles, validated against
  `specs/atlas-character-design-profile-v1.schema.json`.
- `tools/characters/`: deterministic Blender generation, asset preparation,
  profile validation, base review, landmark placement, and asset checks.
- `art/characters/pending_models/` and `pending_equipment/`: review artifacts;
  not served by the game unless separately approved and registered.
- `web/assets/manifest.yaml`: runtime asset inventory, provenance, approval
  state, and checksums. Do not mark an asset cleared without recorded rights
  evidence.
- `specs/`: versioned data contracts and subsystem workflow documentation.

## Character pipeline

The repository does not call an AI API. An AI or user supplies a compact
design-profile JSON; Blender scripts deterministically generate or process the
mesh. The stages are profile, base generation, human base acceptance, landmark
placement, rig/weight review, appearance, then separate runtime release review.
The runtime appearance save profile is distinct from the creator design
profile. Begin with `specs/atlas-character-generation.md` and follow its
commands and review gates.

The current `stone_troll` output is seeded from an external `/srv/projects/troll.glb`
reference. That source is not committed. Its calibration and attribution are in
`art/characters/reference_calibrations/`; collaborators need an authorized
source file at the recorded path to reproduce it. The generated model is a
pending review artifact, not an approved runtime character. Its Blender object
contains 20 disconnected surface components. The torso and both upper arms are
topologically stitched at the armpits, but the whole model is not one connected
or watertight surface; inspect it visually before base acceptance.

## Useful commands

From the repository root:

```sh
npm ci
npm run build
python3 -m atlas_server
```

Character profile and asset checks:

```sh
npm run characters:profile-check -- art/characters/profiles/stone_troll.json
npm run assets:check
```

Blender generation requires Blender 4.x or newer with glTF support:

```sh
blender --background --python tools/characters/generate_character_base.py -- \
  --profile art/characters/profiles/stone_troll.json
```

The project test suites are available through `npm run ci`; review the README
and package scripts before running checks that alter generated outputs.

## Change and release rules

- Update the relevant spec/schema and README when changing a contract or
  workflow. Keep examples synchronized with actual tool behavior.
- Regenerate derived artifacts through their source scripts; do not hand-edit
  generated bundles, checksums, or model records without regenerating them.
- Keep source references, licenses, and provenance with derived art. Do not
  commit private or unlicensed source assets.
- A pending artifact is not runtime approved. Registration and release checks
  are explicit gates, not automatic consequences of generation.
- Preserve the active feature branch unless the user explicitly requests a
  merge or main-branch push.
