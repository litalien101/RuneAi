import * as THREE from 'three';

const LOOP = THREE.LoopRepeat;
const ONCE = THREE.LoopOnce;

function quaternionTrack(bone, frames, duration) {
  const times = [], values = [];
  for (const [time, rotation] of frames) {
    const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(...rotation));
    times.push(time * duration);
    values.push(q.x, q.y, q.z, q.w);
  }
  return new THREE.QuaternionKeyframeTrack(`${bone.name}.quaternion`, times, values);
}

function cycleTrack(bone, amplitude, phase, duration, restRoll = 0) {
  const frames = [];
  for (let index = 0; index <= 8; index++) {
    const time = index / 8;
    frames.push([time, [Math.sin(time * Math.PI * 2 + phase) * amplitude, 0, restRoll]]);
  }
  return quaternionTrack(bone, frames, duration);
}

export class CharacterAnimator {
  constructor(root, bones) {
    this.mixer = new THREE.AnimationMixer(root);
    const { pelvis, spine, armL, armR, legL, legR } = bones;
    const clips = [
      new THREE.AnimationClip('idle', 2.4, [
        quaternionTrack(spine, [[0,[0,0,0]],[.5,[.012,0,.015]],[1,[0,0,0]]], 2.4),
        quaternionTrack(armL,[[0,[0,0,-.27]],[1,[0,0,-.27]]],2.4),
        quaternionTrack(armR,[[0,[0,0,.27]],[1,[0,0,.27]]],2.4),
      ]),
      new THREE.AnimationClip('walk', 1, [
        cycleTrack(legL,.52,0,1),cycleTrack(legR,.52,Math.PI,1),
        cycleTrack(armL,.28,Math.PI,1,-.27),cycleTrack(armR,.28,0,1,.27),
      ]),
      new THREE.AnimationClip('run', .72, [
        cycleTrack(legL,.78,0,.72),cycleTrack(legR,.78,Math.PI,.72),
        cycleTrack(armL,.48,Math.PI,.72,-.27),cycleTrack(armR,.48,0,.72,.27),
        quaternionTrack(spine,[[0,[.08,0,0]],[.5,[.12,0,0]],[1,[.08,0,0]]],.72),
      ]),
      new THREE.AnimationClip('jump', .8, [
        quaternionTrack(legL,[[0,[-.42,0,0]],[1,[-.42,0,0]]],.8),
        quaternionTrack(legR,[[0,[.28,0,0]],[1,[.28,0,0]]],.8),
        quaternionTrack(armL,[[0,[-1.0,0,-.27]],[1,[-1.0,0,-.27]]],.8),
        quaternionTrack(armR,[[0,[-1.0,0,.27]],[1,[-1.0,0,.27]]],.8),
      ]),
      new THREE.AnimationClip('fall', .8, [
        quaternionTrack(legL,[[0,[.2,0,0]],[1,[.2,0,0]]],.8),
        quaternionTrack(legR,[[0,[.28,0,0]],[1,[.28,0,0]]],.8),
        quaternionTrack(armL,[[0,[.38,0,-.27]],[1,[.38,0,-.27]]],.8),
        quaternionTrack(armR,[[0,[.38,0,.27]],[1,[.38,0,.27]]],.8),
      ]),
      new THREE.AnimationClip('land', .14, [
        new THREE.VectorKeyframeTrack(`${pelvis.name}.position`,[0,.07,.14],[0,.32,0,0,.25,0,0,.32,0]),
        quaternionTrack(spine,[[0,[.04,0,0]],[.5,[.16,0,0]],[1,[0,0,0]]],.14),
      ]),
    ];
    this.actions = new Map(clips.map(clip => [clip.name, this.mixer.clipAction(clip)]));
    this.state = null;
    this.setState('idle', 0);
  }

  setState(state, speed = 0) {
    if (!this.actions.has(state)) state = 'idle';
    if (state === this.state) {
      if (state === 'run' || state === 'walk') this.actions.get(state).timeScale = Math.max(.65, speed / (state === 'run' ? 4.2 : 2.5));
      return;
    }
    const previous = this.state && this.actions.get(this.state);
    if (previous) previous.fadeOut(.12);
    const action = this.actions.get(state);
    action.reset();
    action.enabled = true;
    action.setLoop(state === 'jump' || state === 'fall' ? LOOP : ['idle','walk','run'].includes(state) ? LOOP : ONCE,
      state === 'jump' || state === 'fall' ? Infinity : 1);
    action.clampWhenFinished = true;
    if (state === 'run' || state === 'walk') action.timeScale = Math.max(.65, speed / (state === 'run' ? 4.2 : 2.5));
    action.fadeIn(.12).play();
    this.state = state;
  }

  update(delta) { this.mixer.update(Math.min(delta, .05)); }
}
