# Atlas humanoid rig template v1

This rigging and equipment-fitting reference is built from the current female
base model. It contains the `ATLAS_HUMANOID_V1` armature, the base and optional
underwear meshes, and bone-parented equipment socket markers.

Preserve the armature's bone names, hierarchy, rest pose, orientation, and
source scale. Duplicate the bare body or a compatible equipment mesh before
editing. Skinned additions must use this armature; rigid props can attach to the
matching `SOCKET_*` empty. Socket positions are starting markers and may be
adjusted for each item.

The browser normalizes the female base to 1.68 m. New equipment should retain
the source scale and inherit the character's runtime scale. The reference body
has no underwear baked into it; underwear remains separate optional equipment.

The template does not replace the browser animation controller or include new
animations. Use the existing Mixamo-compatible clips and preview each addition
through idle, walk, run, jump, fall, and land.

`atlas_humanoid_v1.json` records the bone hierarchy, rest matrices, socket map,
source checksum, and coordinate conventions. Rebuild from the project root:

```sh
blender --background --python tools/characters/build_atlas_humanoid_v1.py
```
