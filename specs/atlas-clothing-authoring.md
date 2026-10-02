# Atlas clothing authoring workflow

Atlas uses Blender as the clothing authoring tool and a repeatable Python script
for the work that is easy to automate. Artists still model garment shape and
details in Blender. The script transfers rig weights and body morphs, checks the
Atlas rig contract, and exports a skinned GLB with metadata.

## Make a working file

Open `art/characters/atlas_female_base_v1/atlas_female_base_v1.blend` in Blender
and save a copy under `art/characters/workspaces/`. Keep the Atlas body and
`ATLAS_HUMANOID_V1_RIG` in the file. Hide the existing underwear meshes while
modeling if they get in the way.

For a fitted garment, duplicate the needed body faces, separate them into a new
mesh object, then shape the result into the garment. Add thickness, sleeves,
panels, seams, and other details in Blender. Name the final mesh object (for
example, `shirt_basic`). Keep it positioned around the body in its neutral pose.
This starting method works best for fitted garments; loose robes, skirts, and
hard armor need deliberate modeling and pose review.

## Prepare and export

From the project root, run Blender with the working copy and garment object:

```sh
blender --background --python tools/characters/prepare_clothing_asset.py -- \
  --blend art/characters/workspaces/shirt_basic.blend \
  --garment shirt_basic \
  --slot chest \
  --asset-id atlas.clothing.shirt_basic_v1 \
  --output-dir art/characters/equipment
```

The supplied `../../Reference_Assets/characters/male_warrior.glb` can use this
preparation workflow after importing it into a Blender working file. Its 67 bone names and
parent hierarchy match `ATLAS_HUMANOID_V1`; pass the body and rig object names
shown in Blender with `--body` and `--rig`. The male GLB currently has no body
shape keys, so the script can transfer its current proportions and bone weights
but has no male morphs to copy yet.

The script leaves the artist's working file untouched. It writes a prepared
`.blend`, a skinned `.glb`, and a JSON metadata file into the output directory.
It uses the body surface to transfer the closest skin weights, limits each
vertex to four normalized bone influences, copies all body shape keys to the
garment, and binds the garment to `ATLAS_HUMANOID_V1`.

Optional `--exclude-bone NAME` flags remove weights for bones that should not
drive that garment, such as `mixamorig:Head` on a chest piece. The tool stops if
any vertex would be left without a valid weight.

## Review before use

The export is a prepared asset, not an automatically approved outfit. Open the
prepared `.blend` and inspect every body morph at its low and high values. Pose
the arms, legs, and spine; inspect the garment from every side for clipping,
stretching, flipped surfaces, and bad deformation. Fix the source mesh and rerun
the script when necessary.

The script does not invent garment geometry, infer a desired clothing style, or
guarantee a clip-free fit. It also does not register or equip the exported GLB in
the browser yet; runtime equipment loading is a separate integration step.
