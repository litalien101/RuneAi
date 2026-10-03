# Atlas character generation workflow

## Pipeline stages

Character creation uses small, reviewable artifacts at each stage:

1. **Text brief to design profile.** An AI assistant converts the user's brief
   into `atlas-character-design-profile/v1` JSON. It records the source prompt,
   concise design summary, assumptions, silhouette controls, palette, feature
   modules, and intended actions. The AI does not generate a mesh or animation.
2. **Profile to base model.** Blender uses either profile-scaled procedural
   anatomy or, when a licensed reference is attached to the profile, bakes its
   evaluated visible mesh and materials into one Atlas mesh object. It places
   named landmark guides and writes a review-only `.blend` plus static GLB
   preview under `art/characters/pending_models/`.
3. **Human base review.** Inspect the silhouette and feature placement. Record
   accept or reject with a named reviewer and rationale. Only accepted bases
   unlock marker export.
4. **Rig marker placement.** Move the named empty guides in Blender to the
   character's joints and facial landmarks, then export their positions as
   `atlas-rig-landmarks/v1`. Marker data is a separate input artifact.
5. **Rig, weights, and motion review.** Use the accepted base and reviewed
   landmarks to build or fit the Atlas-compatible skeleton, generate skin
   weights, and inspect the requested actions. This remains a distinct gate;
   marker export does not approve a rig.
6. **Appearance and runtime export.** Add detailed materials, textures, eyes,
   hair, and other surface features after the base and deformation are accepted.
   A separate release process must review provenance, rig compatibility, and
   runtime behavior before registration.

The existing `atlas-character-profile/v1` is the in-game appearance/save
contract. The design profile defined here is a creator input and does not change
runtime appearance storage.

The body remains one Blender mesh object. This means it is editable and
exportable as one object; it does not promise that every vertex belongs to one
connected, watertight surface. `body_regions.json` assigns semantic face
regions such as head, neck, torso, arms, hands, legs, and feet; `atlas_body_region`
and matching vertex groups carry those labels in Blender. Point landmarks such
as eyes, brows, nose, shoulders, and collarbones are stored as named coordinates
and surface landmarks are projected onto the generated mesh. These labels let
later tools find a region or anchor without guessing from a broad bounding box;
they do not split the body into separate head or torso meshes.

Every generated base uses the canonical `t_pose_fingers_spread` neutral pose:
arms extended horizontally at shoulder height, fingers extended with visible
gaps, and thumbs angled away from the palms. This makes joint placement and
weight review more consistent. The seed-model path repositions lateral arm
mesh islands and fans small digit islands when available; because this is a
static mesh operation, inspect the shoulders, elbows, and fingers for seams or
intersections before accepting the base. A later pose option may use an A-pose
when a specific rig benefits from it, but T-pose remains the default.

## v1 limits

The procedural mode creates a new mesh from high-resolution parametric body forms.
Reference-seeded mode starts from the licensed model named by
`reference_calibration.source_path`, bakes its visible geometry/materials, and
removes its rig and animations. The profile exposes body
proportions plus muscle definition and jaw, nose, hand, foot, and ear controls. Troll, orc, elf, and goblin
also have modest deterministic silhouette priors; these are starting points,
not species-specific anatomy. Horns, tusks, eyes, brows, and pointed ears are
still simple feature blockouts. The reference-seeded troll currently contains
additional disconnected surface islands. The generator stitches the torso and
upper-arm boundary arcs on both sides with interpolated mesh rows and UVs. This
is a topology bridge between existing surfaces, rather than a separate filler
ball or panel. The generated record reports the number of stitched sides. It
does not merge every other source island or prove the complete body is
watertight. Review axilla curvature, shoulder seams, and the complete surface
in Blender before accepting the base.
The procedural result is not a finished or universally correct model: the profile
vocabulary and procedural shape library must grow with authored examples and
human review. Requested actions are recorded for
later rig and animation work; they are not generated at this stage. These
limits are stored with every generated artifact so the preview is not
mistaken for a finished character.

## Generate a base

The AI response should be one JSON document conforming to
[`atlas-character-design-profile-v1.schema.json`](atlas-character-design-profile-v1.schema.json).
Keep the original user brief in `concept.source_prompt`; put inferred design
choices in `concept.design_summary` and uncertainties in `concept.assumptions`.
The profile should use the supported template and enumerated action/feature
fields. Choose `realistic` unless the prompt explicitly requests a stylized or
low-poly look. Unsupported anatomy requests should remain visible in
assumptions for the human reviewer.

For each brief, ask the AI to return only the profile JSON, preserve the
requested traits and actions, choose explicit colors and supported proportions
(including muscle definition and jaw, nose, hand, foot, and ear sizes), and list every invented detail
under `concept.assumptions`. This keeps the AI step to a small structured
document instead of generated mesh data or long modeling instructions. The
current repository does not call an AI service itself; the assistant or a
future profile-authoring UI supplies this JSON. Geometry generation is local,
deterministic Blender code and does not consume model-generation tokens.

```sh
python3 tools/characters/validate_character_design_profile.py \
  art/characters/profiles/stone_troll.json

blender --background --python tools/characters/generate_character_base.py -- \
  --profile art/characters/profiles/stone_troll.json
```

The output contains `character_base.blend`, `character_base_preview.glb`, and
`character.json`. It is never added to `web/assets` or the runtime manifest by
the generator.

## Review and marker handoff

Open the `.blend`, inspect the preview, and shape the procedural blockout as
needed. Save edits, then refresh the static GLB so review always refers to the
saved Blender file:

```sh
blender --background --python tools/characters/refresh_character_base_preview.py -- \
  --blend art/characters/pending_models/stone_troll/character_base.blend \
  --character-record art/characters/pending_models/stone_troll/character.json
```

After reviewing the refreshed preview, record the explicit gate:

```sh
python3 tools/characters/review_character_base.py \
  --character-dir art/characters/pending_models/stone_troll \
  --reviewer "Reviewer name" --decision accept \
  --rationale "Base silhouette and proportions accepted for rig authoring."
```

## Licensed reference calibration

Use licensed exemplars to measure proportions and compare recipe outputs. Keep
the source file outside runtime assets; the calibration record stores its hash,
embedded source/license attribution, normalized cross-sections, and rest-pose
rig landmarks. It does not copy source geometry, materials, skinning, or motion.

```sh
blender --background --python tools/characters/calibrate_character_reference.py -- \
  --source /path/to/reference.glb \
  --output art/characters/reference_calibrations/reference_name.json

blender --background --python tools/characters/calibrate_character_reference.py -- \
  --source art/characters/pending_models/stone_troll/character_base_preview.glb \
  --output /tmp/stone_troll_generated_calibration.json

python3 tools/characters/compare_character_calibrations.py \
  --reference art/characters/reference_calibrations/troll_sketchfab.json \
  --generated-calibration /tmp/stone_troll_generated_calibration.json \
  --character-record art/characters/pending_models/stone_troll/character.json \
  --output art/characters/reference_calibrations/stone_troll_atlas_comparison.json
```

Reference-seeded generation uses the visible seed mesh directly so its base
silhouette and materials are retained; it bakes the source rig out, normalizes
the mesh to the profile height, and fits initial Atlas markers from the source
rest-pose skeleton. Measurements are review evidence, not automatic parameter
fitting. Whole-mesh sections can be skewed by a reference pose, and source bone
positions are not Atlas rig targets. The creator should inspect the generated
silhouette, including the arm angle and the bilateral torso-to-arm axilla
stitches, and manually review markers before accepting the base. The stitch
interpolates new rows from existing boundary arcs and carries UV values across
the bridge; it does not use a separate filler object. Keep the
reference attribution and license with any derived work.

Install `tools/characters/atlas_marker_placement_addon.py` through Blender's
Preferences > Add-ons > Install from Disk, enable **Atlas Landmark Placement**,
and reopen the accepted `.blend`. In the 3D View sidebar under **Atlas
Markers**, choose an eye, brow, nose, elbow, knee, or other named landmark and
move it with the gizmo or `G` then `X`, `Y`, or `Z`. Marker guides stay locked
until the base-review record says accepted. Save the `.blend`, then export:

```sh
blender --background --python tools/characters/export_character_markers.py -- \
  --blend art/characters/pending_models/stone_troll/character_base.blend \
  --character-record art/characters/pending_models/stone_troll/character.json \
  --output art/characters/pending_models/stone_troll/rig_landmarks.json
```

The exporter refuses to run before base acceptance and writes marker coordinates
in root-local meters, applying the generated root scale. Rig generation and weighting consume
this sidecar in a later stage. Marker exports and generated bases remain
authoring artifacts; they do not modify the runtime manifest.
