# Atlas female base v1

This is a Mixamo-compatible female base for visual equipment work. The supplied
source is preserved as `female_base_source.blend`; `atlas_female_base_v1.blend`
adds Atlas materials and the missing eye joints. The browser uses
`web/assets/characters/female_base_atlas_v1.glb`.

The body keeps the source mesh and skin weights and has no underwear baked into
its surface. The supplied bra and briefs are separate, optional equipment meshes
with their own textures, stable equipment slots, and weights transferred to the
same skeleton. The base body and both garments include matching light/heavy body
shape targets. Runtime controls can change skin tone and height as well.

The source contains 65 Mixamo-named bones with the same parent hierarchy as the
Atlas rig. The two eye bones were added to reach the 67-bone
`ATLAS_HUMANOID_V1` contract. The export trims and renormalizes vertices to glTF's
four-influence limit. The supplied texture maps are packed into the GLB at a
maximum dimension of 1024 pixels. Atlas's current animation set is retargeted in the browser;
review all locomotion states for fit before treating this as production-ready.

Rebuild with Blender 5.2.2 LTS from the project root:

```sh
blender --background --python tools/characters/build_atlas_female_base_v1.py
```
