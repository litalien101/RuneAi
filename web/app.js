import * as THREE from 'three';
import { FixedStepRunner, NetworkSimulator, SnapshotBuffer, acknowledgeInputs, integrateMovement, walkablePosition } from './network.js';
import { InputManager } from './input/input_manager.js';
import { ThirdPersonCamera } from './camera/third_person_camera.js';

const canvas = document.querySelector('#world');
const $ = (selector) => document.querySelector(selector);
const scene = new THREE.Scene();
scene.background = new THREE.Color('#b7a98c');
scene.fog = new THREE.Fog('#b7a98c', 17, 40);

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.08;

const camera = new THREE.PerspectiveCamera(56, 1, 0.1, 140);
camera.zoom = 1;
camera.updateProjectionMatrix();

scene.add(new THREE.HemisphereLight('#f0dfbd', '#314c43', 1.75));
const sun = new THREE.DirectionalLight('#ffd9a1', 3.35);
sun.position.set(-10, 12, 8);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.left = -22; sun.shadow.camera.right = 22;
sun.shadow.camera.top = 22; sun.shadow.camera.bottom = -22;
sun.shadow.normalBias = 0.035;
scene.add(sun);

const skyDome = new THREE.Mesh(new THREE.SphereGeometry(85, 32, 20), new THREE.ShaderMaterial({
  side: THREE.BackSide,
  depthWrite: false,
  uniforms: {
    zenith: { value: new THREE.Color('#557c88') },
    upperHorizon: { value: new THREE.Color('#b9a88b') },
    sunset: { value: new THREE.Color('#d8a879') },
    lowHorizon: { value: new THREE.Color('#9eaa8b') },
  },
  vertexShader: `varying vec3 skyDirection; void main(){skyDirection=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`,
  fragmentShader: `
    uniform vec3 zenith; uniform vec3 upperHorizon; uniform vec3 sunset; uniform vec3 lowHorizon;
    varying vec3 skyDirection;
    void main(){
      float h=normalize(skyDirection).y;
      vec3 color=mix(upperHorizon,zenith,smoothstep(-0.02,0.72,h));
      float glow=exp(-pow((h-0.025)/0.16,2.0));
      color=mix(color,sunset,glow*0.76);
      color=mix(color,lowHorizon,1.0-smoothstep(-0.18,-0.035,h));
      gl_FragColor=vec4(color,1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`,
}));
skyDome.renderOrder = -10;
scene.add(skyDome);

const world = new THREE.Group();
scene.add(world);
const player = makeCharacter('player');
const otherTravelers = new Map();
const REMOTE_INTERPOLATION_DELAY_MS = 110;
const mara = makeCharacter('npc');
const mossling = makeMossling();
const mosslingRest = makeMosslingRest();
const inputManager = new InputManager(canvas);
const cameraController = new ThirdPersonCamera(camera);
inputManager.setZoomHandler(delta => cameraController.zoom(delta));
const reedModels = new Map();
const foliageModels = [];
const beacon = makeBeacon();
world.add(player, mara, mossling.group, mosslingRest, beacon.group);

let game = null;
let busy = false;
let toastTimer = 0;
let clickPointer = null;
let positionCorrection = { x: 0, z: 0 };
let predictedVelocity = { x: 0, z: 0 };
let sessionToken = sessionStorage.getItem('atlas.local.session') || '';
let sessionRecovery = null;
const network = new NetworkSimulator(undefined, undefined, authenticatedFetch);
const movementClock = new FixedStepRunner(1 / 60, 8);
const predictedPosition = { x: 0, z: 0 };
const previousPredictedPosition = { x: 0, z: 0 };
let movementSequence = 0;
let acknowledgedSequence = 0;
let pendingInputs = [];
let outgoingInputs = [];
let correctionDistance = 0;
let maxCorrectionDistance = 0;
let largeCorrections = 0;
let lastNetworkHudAt = 0;
let rewindVisualization = false;
let rewindOverlay = null;
let initialStateLoaded = false;
let clickDestination = null;
let lastSentInput = { x: 0, z: 0, run: false };
let nextInputAt = 0;
let nextSnapshotPollAt = 0;
let snapshotPollPending = false;
let walkWeight = 0;
let runWeight = 0;
let lastFrameTime = 0;
let targetPlayerYaw = 0;
let gaitPhase = 0;
let audioContext = null;
let ambientBus = null;
let ambientEnabled = true;
let birdCallTimer = null;
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);

function material(color, roughness = 0.86, extra = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness, ...extra });
}

function addMesh(parent, geometry, mat, position, options = {}) {
  const mesh = new THREE.Mesh(geometry, mat);
  mesh.position.set(...position);
  mesh.castShadow = options.castShadow ?? true;
  mesh.receiveShadow = options.receiveShadow ?? true;
  if (options.rotation) mesh.rotation.set(...options.rotation);
  parent.add(mesh);
  return mesh;
}

function seeded(x, y, salt = 0) {
  const value = Math.sin(x * 127.1 + y * 311.7 + salt * 74.7) * 43758.5453;
  return value - Math.floor(value);
}

function buildLandscape(state) {
  const treeObstacles=new Map((state.obstacles||[]).filter(obstacle=>obstacle.kind==='tree').map(obstacle=>[`${obstacle.x},${obstacle.z}`,obstacle]));
  const sea = addMesh(world, new THREE.PlaneGeometry(100, 100), material('#416a6b', 0.3, { metalness: 0.12 }), [9.5, -0.48, 6.5], { castShadow: false });
  sea.rotation.x = -Math.PI / 2;
  buildGround(state);
  for (let y = 0; y < state.height; y++) {
    for (let x = 0; x < state.width; x++) {
      const kind = state.terrain[y][x];
      if (kind === '~') continue;
      const n = seeded(x, y);
      const tree=treeObstacles.get(`${x},${y}`);
      if (tree) addTree(x, y, tree.seed, tree.scale);
      else if ((kind === 'g' && !isPathCell(x, y) && n > 0.49) || (kind !== 'g' && n > 0.72)) addGrassTuft(x, y, n);
    }
  }
  const route = [[2,10],[4,9],[6,9],[7,8],[8,7],[10,7],[11,6],[12,6],[13,5],[15,4]];
  const curve = new THREE.CatmullRomCurve3(route.map(([x,z]) => new THREE.Vector3(x, 0.045, z)));
  const path = addMesh(world, new THREE.TubeGeometry(curve, 120, 0.23, 9, false), material('#988768',.94), [0,0,0], { castShadow: false });
  path.receiveShadow = true;
  addFlowerPatch(3, 3, '#dcae78');
  addFlowerPatch(17, 7, '#d3c07a');
  addReedPatches(state);
}

function buildGround(state) {
  const vertices=[],uvs=[],indices=[];
  for(let z=0;z<state.height;z++)for(let x=0;x<state.width;x++){
    if(state.terrain[z][x]==='~')continue;
    const base=vertices.length/3;
    vertices.push(x-.5,0,z-.5,x+.5,0,z-.5,x+.5,0,z+.5,x-.5,0,z+.5);
    uvs.push(x/state.width,1-z/state.height,(x+1)/state.width,1-z/state.height,
      (x+1)/state.width,1-(z+1)/state.height,x/state.width,1-(z+1)/state.height);
    indices.push(base,base+2,base+1,base,base+3,base+2);
  }
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));
  geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uvs,2));
  geometry.setIndex(indices);geometry.computeVertexNormals();
  const canvas=document.createElement('canvas');canvas.width=512;canvas.height=358;
  const context=canvas.getContext('2d'),pixels=context.createImageData(canvas.width,canvas.height);
  for(let py=0;py<canvas.height;py++)for(let px=0;px<canvas.width;px++){
    const cellX=Math.min(state.width-1,Math.floor(px/canvas.width*state.width));
    const cellZ=Math.min(state.height-1,Math.floor(py/canvas.height*state.height));
    const path=isPathCell(cellX,cellZ),grove=state.terrain[cellZ][cellX]==='g';
    const base=path?[123,109,79]:grove?[78,102,69]:[99,119,78];
    const broad=(seeded(Math.floor(px/18),Math.floor(py/18),3)-.5)*13;
    const grain=(seeded(px,py,7)-.5)*9;
    const shade=broad+grain;
    const i=(py*canvas.width+px)*4;
    pixels.data[i]=THREE.MathUtils.clamp(base[0]+shade,0,255);
    pixels.data[i+1]=THREE.MathUtils.clamp(base[1]+shade,0,255);
    pixels.data[i+2]=THREE.MathUtils.clamp(base[2]+shade,0,255);
    pixels.data[i+3]=255;
  }
  context.putImageData(pixels,0,0);
  for(let i=0;i<2400;i++){
    const x=seeded(i,5,31)*canvas.width,y=seeded(8,i,32)*canvas.height;
    const cellX=Math.floor(x/canvas.width*state.width),cellZ=Math.floor(y/canvas.height*state.height);
    if(state.terrain[cellZ]?.[cellX]==='~')continue;
    const path=isPathCell(cellX,cellZ),green=state.terrain[cellZ][cellX]==='g';
    context.fillStyle=path?'rgba(224,202,151,.12)':green?'rgba(181,196,126,.12)':'rgba(199,204,140,.1)';
    context.beginPath();context.ellipse(x,y,1+seeded(i,4,9)*4,.7+seeded(3,i,6)*2,seeded(i,9)*Math.PI,0,Math.PI*2);context.fill();
  }
  const colorTexture=new THREE.CanvasTexture(canvas);colorTexture.colorSpace=THREE.SRGBColorSpace;colorTexture.anisotropy=renderer.capabilities.getMaxAnisotropy();
  const bumpTexture=new THREE.CanvasTexture(canvas);bumpTexture.anisotropy=renderer.capabilities.getMaxAnisotropy();
  const ground=addMesh(world,geometry,new THREE.MeshStandardMaterial({map:colorTexture,bumpMap:bumpTexture,bumpScale:.018,roughness:.96}),[0,.012,0],{castShadow:false,receiveShadow:true});
  ground.name='procedural valley ground';
}

function isPathCell(x, y) {
  const path = [[2,10],[4,9],[6,9],[7,8],[8,7],[10,7],[11,6],[12,6],[13,5],[15,4]];
  return path.some(([px,py]) => Math.abs(px-x)+Math.abs(py-y) <= 1);
}
function addTree(x, z, seed, scale = 0.75 + seed * 0.52) {
  const group = new THREE.Group();
  group.position.set(x, 0, z);
  addMesh(group, new THREE.CylinderGeometry(0.09, 0.16, 0.78, 12, 2), material('#72583e'), [0, 0.38, 0]);
  const green = ['#315849','#3b624d','#446c4f'][Math.floor(seed * 3)];
  if (seed > 0.69) {
    addMesh(group, new THREE.SphereGeometry(0.55 * scale, 18, 13), material(green, .94), [0, 1.0 * scale, 0]);
    addMesh(group, new THREE.SphereGeometry(0.39 * scale, 16, 11), material('#557754', .94), [0.22, 1.18 * scale, -0.08]);
  } else {
    addMesh(group, new THREE.ConeGeometry(0.58 * scale, 1.05 * scale, 14, 3), material(green, .95), [0, 0.91 * scale, 0]);
    addMesh(group, new THREE.ConeGeometry(0.42 * scale, 0.85 * scale, 12, 3), material('#527657', .95), [0, 1.42 * scale, 0]);
  }
  group.userData.windPhase = seeded(z, x, 12) * Math.PI * 2;
  group.userData.windStrength = 0.006 + seeded(x, z, 13) * 0.009;
  group.userData.kind = 'tree';
  foliageModels.push(group);
  world.add(group);
}

function addGrassTuft(x, z, seed) {
  const group = new THREE.Group();
  group.position.set(x, 0.04, z);
  const green = material(seed > .5 ? '#71875c' : '#809167');
  for (let i=0;i<3;i++) {
    const blade = addMesh(group, new THREE.ConeGeometry(.045, .29 + seeded(x,z,i)*.12, 4), green,
      [(i-1)*.09,.14,seeded(z,x,i)*.12-.06], {castShadow:false});
    blade.rotation.z = (i-1)*.23;
    blade.userData.baseRotation = blade.rotation.z;
    blade.userData.windPhase = seeded(x,z,i+18)*Math.PI*2;
    blade.userData.windStrength = .025 + seeded(z,x,i+22)*.035;
    foliageModels.push(blade);
  }
  world.add(group);
}

function addFlowerPatch(x, z, color) {
  for (let i=0;i<5;i++) {
    const dx = (seeded(x,z,i)-.5)*.65, dz = (seeded(z,x,i+8)-.5)*.65;
    const petal = addMesh(world, new THREE.IcosahedronGeometry(.055,0), material(color, .62, {emissive:color,emissiveIntensity:.08}), [x+dx,.14,z+dz], {castShadow:false});
    petal.scale.y=.62;
  }
}

function makeCharacter(kind) {
  const group = new THREE.Group();
  const isPlayer = kind === 'player' || kind === 'player2';
  const isSecondPlayer = kind === 'player2';
  group.userData.kind = kind;
  const robe = material(isPlayer ? (isSecondPlayer ? '#725d86' : '#426d71') : '#a56e43');
  const robeLight = material(isPlayer ? (isSecondPlayer ? '#a18ab2' : '#628f8d') : '#c5945d');
  const dark = material('#3b4037');
  const skin = material(isPlayer ? (isSecondPlayer ? '#cda88c' : '#d8b58e') : '#dfbd8a');
  const mantle = addMesh(group, new THREE.CylinderGeometry(.21,.31,.57,16,3), robe, [0,.61,0]);
  mantle.scale.z=.82;
  addMesh(group, new THREE.SphereGeometry(.205,18,14), skin, [0,1.03,0]);
  addMesh(group, new THREE.ConeGeometry(.245,.32,12,2), robeLight, [0,1.25,0]);
  const legL=new THREE.Group();legL.position.set(-.095,.39,0);group.add(legL);
  const legR=new THREE.Group();legR.position.set(.095,.39,0);group.add(legR);
  addMesh(legL,new THREE.CylinderGeometry(.055,.065,.39,10,2),dark,[0,-.195,0]);
  addMesh(legR,new THREE.CylinderGeometry(.055,.065,.39,10,2),dark,[0,-.195,0]);
  const armL=new THREE.Group();armL.position.set(isPlayer?-.25:-.22,.79,0);armL.rotation.z=-.27;group.add(armL);
  const armR=new THREE.Group();armR.position.set(isPlayer?.25:.22,.79,0);armR.rotation.z=.27;group.add(armR);
  addMesh(armL,new THREE.CylinderGeometry(.055,.075,.39,10,2),robe,[0,-.195,0]);
  addMesh(armR,new THREE.CylinderGeometry(.055,.075,.39,10,2),robe,[0,-.195,0]);
  if (isPlayer) {
    const scarf=addMesh(group,new THREE.TorusGeometry(.19,.045,6,9),material('#d1ad6c'),[0,.93,0]);
    scarf.rotation.x=Math.PI/2;
    const pack=addMesh(group,new THREE.BoxGeometry(.22,.25,.13),material('#755c43'),[0,.65,-.22]);
    const sword=new THREE.Group();sword.position.set(.32,.49,.1);sword.rotation.z=-.18;group.add(sword);
    addMesh(sword,new THREE.CylinderGeometry(.035,.035,.24,6),material('#6e4930'),[0,-.18,0]);
    addMesh(sword,new THREE.BoxGeometry(.26,.045,.07),material('#d1b36b'),[0,-.04,0]);
    addMesh(sword,new THREE.BoxGeometry(.075,.5,.035),material('#b9c8c1',.3,{metalness:.65}),[0,.22,0]);
    addMesh(sword,new THREE.ConeGeometry(.055,.14,4),material('#dce2d7',.28,{metalness:.7}),[0,.54,0]);
  } else {
    addMesh(group,new THREE.SphereGeometry(.055,8,6),material('#e5c97f',.38,{emissive:'#c89443',emissiveIntensity:.25}),[0,1.1,.18]);
  }
  group.userData.nameplate=makeNameplate(isPlayer?(isSecondPlayer?'PATHFINDER':'WAYFARER'):'MARA',isPlayer?(isSecondPlayer?'#e3c7f2':'#d3e1cb'):'#e7c78b',isPlayer?1.67:1.74);
  group.add(group.userData.nameplate);
  group.userData.parts={mantle,armL,armR,legL,legR};
  return group;
}

function makeMossling(){
  const group=new THREE.Group();group.position.set(12,0,9);
  const moss=material('#526c52'),mossLight=material('#789064'),stone=material('#59645a');
  const body=addMesh(group,new THREE.DodecahedronGeometry(.43,2),moss,[0,.72,0]);body.scale.set(1,.9,.8);
  addMesh(group,new THREE.IcosahedronGeometry(.31,2),mossLight,[0,1.18,.02]);
  addMesh(group,new THREE.DodecahedronGeometry(.2,1),stone,[-.31,.52,.04]);
  addMesh(group,new THREE.DodecahedronGeometry(.18,1),stone,[.3,.55,-.02]);
  for(const side of [-1,1]){
    const horn=addMesh(group,new THREE.ConeGeometry(.095,.34,5),material('#b2a47b'),[side*.17,1.48,-.015]);horn.rotation.z=side*.28;
    const eye=addMesh(group,new THREE.SphereGeometry(.055,12,9),material('#e0d27d',.25,{emissive:'#dfb848',emissiveIntensity:.9}),[side*.115,1.18,.275]);
  }
  const jaw=addMesh(group,new THREE.BoxGeometry(.2,.08,.08),stone,[0,1.02,.25]);
  group.add(makeNameplate('MOSSLING','#b9c98f',1.92));
  const healthBack=addMesh(group,new THREE.PlaneGeometry(.9,.075),material('#26322a'),[0,1.72,.02],{castShadow:false});
  const healthFill=addMesh(group,new THREE.PlaneGeometry(.86,.045),material('#9dbb72'),[0,1.72,.035],{castShadow:false});
  return {group,healthFill,body,mossLight};
}

function makeMosslingRest(){
  const group=new THREE.Group();group.position.set(12,0,9);group.visible=false;
  addMesh(group,new THREE.CylinderGeometry(.43,.52,.15,8),material('#514f43'),[0,.075,0],{castShadow:false});
  const seed=addMesh(group,new THREE.DodecahedronGeometry(.2,1),material('#87906b',.48,{emissive:'#677747',emissiveIntensity:.45}),[0,.29,0],{castShadow:false});
  const glow=new THREE.PointLight('#a6bc71',.45,2.6,2);glow.position.set(0,.34,0);group.add(glow);
  group.userData.seed=seed;
  return group;
}

function makeNameplate(label,color,y){
  const element=document.createElement('canvas');element.width=320;element.height=72;
  const g=element.getContext('2d');g.fillStyle='rgba(15,26,24,.78)';g.beginPath();g.roundRect(5,8,310,56,12);g.fill();
  g.strokeStyle='rgba(204,190,141,.65)';g.lineWidth=2;g.stroke();
  g.fillStyle=color;g.font='600 24px system-ui';g.textAlign='center';g.textBaseline='middle';g.letterSpacing='3px';g.fillText(label,160,37);
  const texture=new THREE.CanvasTexture(element);texture.colorSpace=THREE.SRGBColorSpace;
  const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,transparent:true,depthTest:false}));
  sprite.position.set(0,y,0);sprite.scale.set(1.9,.43,1);sprite.renderOrder=5;
  return sprite;
}

function makeBeacon() {
  const group=new THREE.Group();
  group.position.set(15,0,4);
  const stone=material('#777d70'),stoneLight=material('#a29d7a');
  addMesh(group,new THREE.CylinderGeometry(.57,.68,.24,20,2),stone,[0,.12,0]);
  addMesh(group,new THREE.CylinderGeometry(.43,.53,1.6,20,3),stone,[0,1.0,0]);
  addMesh(group,new THREE.CylinderGeometry(.56,.52,.22,20,2),stoneLight,[0,1.83,0]);
  addMesh(group,new THREE.ConeGeometry(.44,.48,16,2),material('#665f4a'),[0,2.16,0]);
  const crystalMat=material('#b4ad78',.3,{emissive:'#c9a958',emissiveIntensity:.15,metalness:.16});
  const crystal=addMesh(group,new THREE.OctahedronGeometry(.22,0),crystalMat,[0,2.49,0]);
  const ring=addMesh(group,new THREE.TorusGeometry(.37,.025,8,32),material('#c3b27a',.45,{emissive:'#c3a95e',emissiveIntensity:.1}),[0,2.49,0]);
  ring.rotation.x=Math.PI/2.5;
  const light=new THREE.PointLight('#f4d884',0,7,2);light.position.set(0,2.45,0);group.add(light);
  group.add(makeNameplate('THE LISTENING BEACON','#e3d39e',3.05));
  return {group,crystal,ring,light,crystalMat};
}

function makeReed(reed) {
  const group=new THREE.Group();group.position.set(reed.x,0,reed.y);
  const leaves=[];
  const stemMat=material('#7fb88d',.44,{emissive:'#4ca778',emissiveIntensity:.18});
  for(let i=0;i<4;i++){
    const angle=i*Math.PI/2;
    const leaf=addMesh(group,new THREE.ConeGeometry(.09,.68,9,2),stemMat,[Math.cos(angle)*.13,.48,Math.sin(angle)*.13]);
    leaf.rotation.z=-.28+seeded(reed.x,reed.y,i)*.18;leaf.rotation.y=angle;
    leaf.userData.baseRotation=leaf.rotation.z;leaf.userData.windPhase=seeded(reed.y,reed.x,i+32)*Math.PI*2;leaves.push(leaf);
  }
  const bloom=addMesh(group,new THREE.OctahedronGeometry(.1,0),material('#d2e7a3',.3,{emissive:'#9feaac',emissiveIntensity:.55}),[0,.83,0],{castShadow:false});
  const glow=new THREE.PointLight('#a7e3a7',.48,2.4,2);glow.position.set(0,.8,0);group.add(glow);
  world.add(group);reedModels.set(reed.id,{group,bloom,glow,leaves});
}

function addReedPatches(state){state.reed_patches.forEach(makeReed);}

function applyState(state) {
  const oldPlayer=game?.player;
  game=state;
  if(Number.isInteger(state.last_processed_input)){
    acknowledgedSequence=Math.max(acknowledgedSequence,state.last_processed_input);
    movementSequence=Math.max(movementSequence,state.last_processed_input);
    pendingInputs=acknowledgeInputs(pendingInputs,acknowledgedSequence);
    outgoingInputs=outgoingInputs.filter(frame=>frame.sequence>acknowledgedSequence);
  }
  const localName=state.active_player?.name||'Wayfarer';
  if(player.userData.displayName!==localName){
    if(player.userData.nameplate){player.remove(player.userData.nameplate);player.userData.nameplate.material.map?.dispose();player.userData.nameplate.material.dispose();}
    player.userData.nameplate=makeNameplate(localName.toUpperCase(),localName==='Pathfinder'?'#e3c7f2':'#d3e1cb',1.67);
    player.add(player.userData.nameplate);player.userData.displayName=localName;
  }
  const remoteIds=new Set();
  for(const traveler of state.players||[]){
    if(traveler.is_self)continue;
    remoteIds.add(traveler.id);
    let remote=otherTravelers.get(traveler.id);
    const receivedAt=performance.now();
    if(!remote){
      const avatar=makeCharacter(traveler.name==='Pathfinder'?'player2':'player');
      avatar.position.set(traveler.x,0,traveler.y);world.add(avatar);
      remote={avatar,snapshots:new SnapshotBuffer(24),latest:{x:traveler.x,z:traveler.y}};
      otherTravelers.set(traveler.id,remote);
    }
    remote.latest={x:traveler.x,z:traveler.y};
    remote.snapshots.add({time:receivedAt,x:traveler.x,z:traveler.y});
  }
  for(const [id,remote] of otherTravelers)if(!remoteIds.has(id)){world.remove(remote.avatar);otherTravelers.delete(id);}
  if(state.active_player)$('#region-label').textContent=`${state.active_player.name.toUpperCase()} · LOCAL WORLD`;
  const authoritativeVelocity=state.velocity||{x:0,z:0};
  player.userData.targetX=state.player.x;player.userData.targetZ=state.player.y;
  if(!initialStateLoaded){
    predictedVelocity.x=authoritativeVelocity.x;predictedVelocity.z=authoritativeVelocity.z;
    movementSequence=state.last_processed_input||0;
    acknowledgedSequence=movementSequence;
    player.position.set(state.player.x,0,state.player.y);
    predictedPosition.x=previousPredictedPosition.x=state.player.x;
    predictedPosition.z=previousPredictedPosition.z=state.player.y;
    player.rotation.y=Math.PI;targetPlayerYaw=Math.PI;
    initialStateLoaded=true;
  }else{
    positionCorrection.x=state.player.x-predictedPosition.x;
    positionCorrection.z=state.player.y-predictedPosition.z;
    predictedVelocity.x+=(authoritativeVelocity.x-predictedVelocity.x)*.18;
    predictedVelocity.z+=(authoritativeVelocity.z-predictedVelocity.z)*.18;
    const vx=authoritativeVelocity.x,vz=authoritativeVelocity.z;
    if(Math.hypot(vx,vz)>.08)targetPlayerYaw=Math.atan2(vx,vz);
    else if(oldPlayer&&(oldPlayer.x!==state.player.x||oldPlayer.y!==state.player.y))targetPlayerYaw=Math.atan2(state.player.x-oldPlayer.x,state.player.y-oldPlayer.y);
  }
  mara.position.set(state.mara.x,0,state.mara.y);
  mossling.group.visible=!state.mossling.defeated;
  mosslingRest.visible=state.mossling.defeated;
  mossling.healthFill.scale.x=Math.max(.001,state.mossling.health/state.mossling.max_health);
  mossling.healthFill.position.x=-(1-state.mossling.health/state.mossling.max_health)*.43;
  mossling.healthFill.material.color.set(state.mossling.health===1?'#d58c66':'#9dbb72');
  state.reed_patches.forEach(r=>{if(!reedModels.has(r.id))makeReed(r);});
  for(const [id,model] of reedModels)model.group.visible=state.reed_patches.some(r=>r.id===id);
  beacon.light.intensity=state.beacon_awake?2.3:0;
  beacon.crystalMat.emissiveIntensity=state.beacon_awake?1.6:.15;
  beacon.crystalMat.color.set(state.beacon_awake?'#f8df8a':'#b4ad78');
  renderHud();updateInteract();
}

function renderHud(){
  $('#inventory').textContent=game.inventory.lumen_reed;
  $('#inventory-badge').textContent=game.inventory.lumen_reed;
  $('#reed-count').textContent=String(game.inventory.lumen_reed).padStart(2,'0');
  $('#coords').innerHTML=`X ${String(Math.round(game.player.x)).padStart(2,'0')} <span>·</span> Y ${String(Math.round(game.player.y)).padStart(2,'0')}`;
  $('#vitality').innerHTML=`${game.player_health} <em>/ 100</em>`;
  $('#vitality-meter').style.width=`${game.player_health}%`;
  $('#attunement').innerHTML=`${game.beacon_awake?1:0} <em>/ 1</em>`;
  $('#attune-meter').style.width=game.beacon_awake?'100%':'0%';
  $('#objective').textContent=game.beacon_awake?(game.mossling.defeated?'Return to the mossling’s resting place to call it back.':'Follow the new signal beyond the valley.'):game.mara_met?`Gather three lumen reeds · ${game.inventory.lumen_reed}/3`:'Find Mara near the western path.';
  $('#encounter').textContent=game.mossling.defeated?'Mossling driven off':`Mossling · ${game.mossling.health}/${game.mossling.max_health}`;
  $('#combat-health').style.width=`${Math.max(0,game.mossling.health/game.mossling.max_health*100)}%`;
  $('#combat-block').classList.toggle('is-ready',game.player_defense==='guard');
  $('#combat-dodge').classList.toggle('is-ready',game.player_defense==='dodge');
  $('#journal').innerHTML=game.journal.map((entry,i)=>`<div class="journal-entry"><i>${String(i+1).padStart(2,'0')}</i><span>${escapeHtml(entry)}</span></div>`).join('');
  $('#note-count').textContent=String(game.journal.length).padStart(2,'0');
  $('#beacon-state').textContent=game.beacon_awake?'SIGNAL RETURNED':'DORMANT';
  $('#beacon-copy').textContent=game.beacon_awake?'A second light answers from beyond the valley. The world has changed.':'A weathered signal tower waits beneath the eastern trees.';
  $('#beacon-card').classList.toggle('awake',game.beacon_awake);
  $('#beacon-action').disabled=game.beacon_awake;
  $('#beacon-action').innerHTML=game.beacon_awake?'The beacon is awake <span>✦</span>':'Wake the beacon <span>→</span>';
  $('#events').innerHTML=game.events.length?game.events.map(e=>`<div class="world-event"><time>#${String(e.sequence).padStart(3,'0')}</time><b>${eventName(e.type)}</b><br>${escapeHtml(e.rationale)} · ${escapeHtml(e.source.kind)}</div>`).join(''):'<div class="empty-event">Your actions will leave a trace here.</div>';
}

function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function eventName(type){return ({PlayerMoved:'Trail walked',ResourceGathered:'Lumen reed gathered',NPCSpokenTo:'Mara was heard',BeaconAwakened:'Beacon awakened',CreatureDamaged:'Mossling encounter',PlayerGuarded:'Guard raised',PlayerDodged:'Attack evaded',CreatureReawakened:'Mossling returned'})[type]||type;}

function clearRewindOverlay(){
  if(!rewindOverlay)return;
  scene.remove(rewindOverlay);
  rewindOverlay.traverse(object=>{object.geometry?.dispose();if(object.material){if(Array.isArray(object.material))object.material.forEach(m=>m.dispose());else object.material.dispose();}});
  rewindOverlay=null;
}
function showRewindOverlay(detail){
  if(!rewindVisualization||!detail.attack_origin||!detail.rewound_target)return;
  clearRewindOverlay();rewindOverlay=new THREE.Group();
  const currentHitbox=new THREE.Mesh(new THREE.CapsuleGeometry(.35,.55,4,8),new THREE.MeshBasicMaterial({color:0x36e487,wireframe:true,transparent:true,opacity:.8,depthTest:false}));
  currentHitbox.position.set(mossling.group.position.x,.63,mossling.group.position.z);
  const rewoundHitbox=new THREE.Mesh(new THREE.CapsuleGeometry(.35,.55,4,8),new THREE.MeshBasicMaterial({color:0x2788ff,wireframe:true,transparent:true,opacity:.95,depthTest:false}));
  rewoundHitbox.position.set(detail.rewound_target.x,.63,detail.rewound_target.z);
  const origin=new THREE.Vector3(detail.attack_origin.x,.16,detail.attack_origin.z);
  const predicted=new THREE.Mesh(new THREE.SphereGeometry(.15,8,6),new THREE.MeshBasicMaterial({color:0xffdd45,depthTest:false}));
  predicted.position.set(player.position.x,.16,player.position.z);
  const authoritative=new THREE.Mesh(new THREE.SphereGeometry(.15,8,6),new THREE.MeshBasicMaterial({color:0xb565ff,depthTest:false}));
  authoritative.position.copy(origin);
  const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints([origin,new THREE.Vector3(detail.rewound_target.x,.63,detail.rewound_target.z)]),new THREE.LineBasicMaterial({color:0xff3c35,depthTest:false}));
  rewindOverlay.add(currentHitbox,rewoundHitbox,predicted,authoritative,line);scene.add(rewindOverlay);
  setTimeout(()=>{if(rewindOverlay)clearRewindOverlay();},3000);
}

async function refresh(){
  const response=await authenticatedFetch('/api/state',{headers:{'Accept':'application/json'}});
  if(!response.ok){
    const payload=await response.json().catch(()=>({}));
    const error=new Error(payload.error||`The valley could not be reached (${response.status}).`);
    error.status=response.status;throw error;
  }
  applyState(await response.json());
}

function showConnectionError(message){
  $('#connection-message').textContent=message;
  $('#connection-overlay').hidden=false;
  $('#connection-retry').focus();
}
async function connectToWorld(){
  const retry=$('#connection-retry');retry.disabled=true;retry.textContent='Connecting…';
  try{
    const response=await authenticatedFetch('/api/state',{headers:{'Accept':'application/json'}});
    if(!response.ok){const payload=await response.json().catch(()=>({}));throw new Error(payload.error||`World state request failed (${response.status}).`);}
    const state=await response.json();
    if(!game)buildLandscape(state);
    applyState(state);cameraController.snap(player.position);
    $('#connection-overlay').hidden=true;
  }catch(error){
    console.error('Atlas world connection failed:',error);
    $('#connection-message').textContent=error.message||'The valley could not load. Try again in a moment.';
  }finally{retry.disabled=false;retry.textContent='Try again';}
}
$('#connection-retry').addEventListener('click',connectToWorld);

async function act(action){
  if(busy)return;busy=true;
  try{
    const options={method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify(action)};
    const delivered=action.type==='move'?await network.request('/api/action',options,{retry:true}):null;
    const response=delivered?.response||await authenticatedFetch('/api/action',options);
    const result=delivered?.payload||await response.json();
    if(!response.ok){
      if(action.type==='move'&&Array.isArray(action.frames)){
        const existing=new Set(outgoingInputs.map(frame=>frame.sequence));
        outgoingInputs=[...action.frames.filter(frame=>!existing.has(frame.sequence)),...outgoingInputs].sort((a,b)=>a.sequence-b.sequence);
      }
      showToast(result.error||'That action did not work.');return;
    }
    if(result.detail.dialogue)showToast(result.detail.dialogue);
    else if(result.event_type==='BeaconAwakened')showToast('The beacon sings. Something answers from beyond the valley.');
    else if(result.event_type==='CreatureDamaged'){
      showToast(result.detail.defeated?'The Mossling retreats into the undergrowth.':result.detail.defense_used==='guard'?'Your guard turns aside the Mossling’s swipe.':result.detail.defense_used==='dodge'?'You evade its swipe and find an opening.':'The Mossling swipes back at you.');
      showRewindOverlay(result.detail);
    }
    else if(result.event_type==='PlayerGuarded')showToast('Guard ready · your next strike will turn aside its swipe.');
    else if(result.event_type==='PlayerDodged')showToast('You sidestep the attack line · its next swipe will miss.');
    else if(result.event_type==='CreatureReawakened')showToast('The Mossling stirs. Attack, Block, or Dodge while it is near.');
    else if(result.event_type==='ResourceGathered')showToast('+1 Lumen reed');
    if(action.type==='move'&&result.detail){
      const detail=result.detail;
      if(Number.isInteger(result.acknowledged_sequence)){
        acknowledgedSequence=Math.max(acknowledgedSequence,result.acknowledged_sequence);
        pendingInputs=acknowledgeInputs(pendingInputs,acknowledgedSequence);
      }
      const renderedBeforeCorrection={x:predictedPosition.x,z:predictedPosition.z};
      predictedPosition.x=detail.x;predictedPosition.z=detail.z;
      predictedVelocity.x=detail.vx;predictedVelocity.z=detail.vz;
      for(const frame of pendingInputs)simulateMovementFrame(frame.input,frame.run,1/60);
      previousPredictedPosition.x=predictedPosition.x;previousPredictedPosition.z=predictedPosition.z;
      positionCorrection.x=renderedBeforeCorrection.x-predictedPosition.x;
      positionCorrection.z=renderedBeforeCorrection.z-predictedPosition.z;
      correctionDistance=Math.hypot(positionCorrection.x,positionCorrection.z);
      maxCorrectionDistance=Math.max(maxCorrectionDistance,correctionDistance);
      if(correctionDistance>1)largeCorrections++;
      game.player={x:detail.x,y:detail.z};game.velocity={x:detail.vx,z:detail.vz};
      if(Math.hypot(detail.vx,detail.vz)>.08)targetPlayerYaw=Math.atan2(detail.vx,detail.vz);
      renderHud();updateInteract();
    }else await refresh();
  }catch{
    if(action.type==='move'&&Array.isArray(action.frames)){
      const existing=new Set(outgoingInputs.map(frame=>frame.sequence));
      outgoingInputs=[...action.frames.filter(frame=>!existing.has(frame.sequence)),...outgoingInputs].sort((a,b)=>a.sequence-b.sequence);
    }
    showToast('The local world server is unavailable. Restart it and try again.');
  }
  finally{busy=false;}
}

function targetNear(){
  if(!game)return null;const p=game.player;
  const targets=[...game.reed_patches.map(r=>({...r,type:'reed'})),{...game.mara,type:'mara'},{...game.beacon,type:'beacon'},[{...game.mossling,type:game.mossling.defeated?'dormant':'creature'}]];
  return targets.flat().filter(t=>Math.hypot(p.x-t.x,p.y-t.y)<=(t.type==='dormant'?4:1.25)).sort((a,b)=>Math.hypot(p.x-a.x,p.y-a.y)-Math.hypot(p.x-b.x,p.y-b.y))[0]||null;
}
function actionForTarget(target){
  return target.type==='creature'
    ?{type:'attack',target:target.id,rewind_sequence:acknowledgedSequence}
    :target.type==='dormant'?{type:'reawaken',target:target.id}
    :{type:'interact',target:target.id};
}
function updateInteract(){
  const target=targetNear(),button=$('#interact'),combat=$('#combat-actions');
  const inCombat=target?.type==='creature';combat.hidden=!inCombat;button.style.display=target&&!inCombat?'block':'none';
  $('#combat-attack').disabled=!inCombat||Math.hypot(game.player.x-target.x,game.player.y-target.y)>1.0;
  button.textContent=target?`E  ${target.type==='reed'?'GATHER LUMEN REED':target.type==='mara'?'SPEAK WITH MARA':target.type==='creature'?'FIGHT MOSSLING':target.type==='dormant'?'REAWAKEN MOSSLING':'ATTUNE BEACON'}`:'E  INTERACT';
  button.dataset.target=target?.id||'';
}
function showToast(message){const el=$('#toast');el.textContent=message;el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),2800);}

function setAmbientEnabled(enabled){ambientEnabled=enabled;$('#audio-toggle').setAttribute('aria-pressed',String(enabled));if(audioContext&&ambientBus)ambientBus.gain.setTargetAtTime(enabled ? .22 : 0,audioContext.currentTime,.35);}
function scheduleBirdCall(){
  clearTimeout(birdCallTimer);
  if(!audioContext)return;
  birdCallTimer=setTimeout(()=>{
    if(!audioContext)return;
    const now=audioContext.currentTime,base=850+Math.random()*500;
    for(let note=0;note<2;note++){
      const start=now+note*.19,oscillator=audioContext.createOscillator(),envelope=audioContext.createGain();
      oscillator.type='sine';oscillator.frequency.setValueAtTime(base*(note?1.32:1),start);
      oscillator.frequency.exponentialRampToValueAtTime(base*(note?1.18:.88),start+.24);
      envelope.gain.setValueAtTime(.0001,start);envelope.gain.exponentialRampToValueAtTime(.012,start+.035);envelope.gain.exponentialRampToValueAtTime(.0001,start+.31);
      oscillator.connect(envelope).connect(ambientBus);oscillator.start(start);oscillator.stop(start+.32);
    }
    scheduleBirdCall();
  },9000+Math.random()*12000);
}
function startAmbient(){
  if(!audioContext){
    const AudioContextClass=window.AudioContext||window.webkitAudioContext;if(!AudioContextClass)return;
    audioContext=new AudioContextClass();ambientBus=audioContext.createGain();ambientBus.gain.value=0;ambientBus.connect(audioContext.destination);
    const buffer=audioContext.createBuffer(1,audioContext.sampleRate*3,audioContext.sampleRate),samples=buffer.getChannelData(0);
    for(let i=0;i<samples.length;i++)samples[i]=(Math.random()*2-1)*.24;
    const wind=audioContext.createBufferSource();wind.buffer=buffer;wind.loop=true;
    const filter=audioContext.createBiquadFilter();filter.type='lowpass';filter.frequency.value=520;filter.Q.value=.35;
    const windGain=audioContext.createGain();windGain.gain.value=.12;wind.connect(filter).connect(windGain).connect(ambientBus);wind.start();
    const windLfo=audioContext.createOscillator(),windDepth=audioContext.createGain();windLfo.type='sine';windLfo.frequency.value=.055;windDepth.gain.value=.045;windLfo.connect(windDepth).connect(windGain.gain);windLfo.start();
    for(const [frequency,gainValue] of [[110,.035],[164.8,.022]]){const tone=audioContext.createOscillator(),gain=audioContext.createGain();tone.type='sine';tone.frequency.value=frequency;gain.gain.value=gainValue;tone.connect(gain).connect(ambientBus);tone.start();}
  }
  if(audioContext.state==='suspended')audioContext.resume();
  if(!birdCallTimer)scheduleBirdCall();
  setAmbientEnabled(ambientEnabled);
}

function movementInput(){
  const movement=inputManager.movement();
  const forward=movement.forward,strafe=movement.strafe;
  let x=0,z=0;
  if(forward||strafe){
    clickDestination=null;
    const cameraAngle=cameraController.yaw,cameraForwardX=-Math.sin(cameraAngle),cameraForwardZ=-Math.cos(cameraAngle);
    const cameraRightX=Math.cos(cameraAngle),cameraRightZ=-Math.sin(cameraAngle);
    x=forward*cameraForwardX+strafe*cameraRightX;z=forward*cameraForwardZ+strafe*cameraRightZ;
  }else if(clickDestination&&game){x=clickDestination.x-game.player.x;z=clickDestination.z-game.player.y;if(Math.hypot(x,z)<.2){clickDestination=null;x=0;z=0;}}
  const length=Math.hypot(x,z);if(length>1){x/=length;z/=length;}
  return {x,z,run:movement.run&&length>0};
}
function walkableAt(x,z){
  const playerObstacles=[...(game?.players||[]).filter(p=>!p.is_self).map(p=>({x:p.x,z:p.y,radius:.2}))];
  return !game?.terrain||walkablePosition(x,z,game.terrain,game.width,game.height,.2,[...(game.obstacles||[]),...playerObstacles]);
}

async function createLocalSession(){
  const response=await fetch('/api/session',{method:'POST',headers:{'Accept':'application/json'}});
  const payload=await response.json();
  if(!response.ok)throw new Error(payload.error||'No local traveler seat is available.');
  sessionToken=payload.session_token;sessionStorage.setItem('atlas.local.session',sessionToken);
}

async function authenticatedFetch(url,options={}){
  const send=()=>fetch(url,{...options,headers:{...(options.headers||{}),'X-Atlas-Session':sessionToken}});
  let response=await send();
  if(response.status===401&&url!=='/api/session'){
    if(!sessionRecovery)sessionRecovery=createLocalSession().finally(()=>{sessionRecovery=null;});
    await sessionRecovery;response=await send();
  }
  return response;
}
function tickMovement(time){
  const lastFrame=outgoingInputs.at(-1);
  const changed=lastFrame&&(lastFrame.input.x!==lastSentInput.x||lastFrame.input.z!==lastSentInput.z||lastFrame.run!==lastSentInput.run);
    if(!busy&&outgoingInputs.length&&(changed||time>=nextInputAt)){
    const frames=outgoingInputs.splice(0,32);
    lastSentInput=frames.at(-1);nextInputAt=time+95;
    act({type:'move',sequence:frames.at(-1).sequence,frames});
  }
}

function simulateMovementFrame(input,run,dt){
  const speed=run?4.2:2.5;
  const step=integrateMovement(predictedPosition,predictedVelocity,input,dt,speed);
  if(walkableAt(step.position.x,predictedPosition.z)){
    predictedPosition.x=step.position.x;predictedVelocity.x=step.velocity.x;
  }else predictedVelocity.x=0;
  if(walkableAt(predictedPosition.x,step.position.z)){
    predictedPosition.z=step.position.z;predictedVelocity.z=step.velocity.z;
  }else predictedVelocity.z=0;
}
function updateNetworkHud(time){
  if(time-lastNetworkHudAt<150)return;lastNetworkHudAt=time;
  const hud=$('#network-debug');if(!hud||hud.hidden)return;
  hud.textContent=`NET ${network.profileName.toUpperCase()}  ·  RTT ${network.stats.ping.toFixed(0)} ms  ·  JIT ${network.stats.jitter.toFixed(0)} ms  ·  LOSS ${(network.lossRate*100).toFixed(1)}%  ·  RETRY ${network.stats.retries}  ·  PENDING ${pendingInputs.length}  ·  ACK ${acknowledgedSequence}  ·  CORR ${correctionDistance.toFixed(2)} m  ·  MAX ${maxCorrectionDistance.toFixed(2)} m  ·  >1m ${largeCorrections}`;
}
function selectNetworkProfile(profile){network.setProfile(profile);$('#network-debug').hidden=false;showToast(`Network simulation: ${profile.toUpperCase()}`);}
window.addEventListener('keydown',event=>{
  if(!game||['INPUT','TEXTAREA'].includes(document.activeElement.tagName))return;
  const key=event.key.toLowerCase();
  if(key==='6'&&!event.repeat){rewindVisualization=!rewindVisualization;if(!rewindVisualization)clearRewindOverlay();showToast(`Rewind overlay ${rewindVisualization?'on':'off'}`);return;}
  if(['1','2','3','4','5'].includes(key)){
    event.preventDefault();selectNetworkProfile(({1:'local',2:'good',3:'average',4:'bad',5:'loss'})[key]);return;
  }
  if(['w','a','s','d','shift'].includes(key)){clickDestination=null;startAmbient();return;}
  if(event.repeat)return;
  if(event.key==='e'||event.key==='E'){startAmbient();const target=targetNear();if(target)act(actionForTarget(target));}
});
$('#interact').addEventListener('click',()=>{const target=targetNear();if(target)act(actionForTarget(target));});
$('#combat-attack').addEventListener('click',()=>{if(!game?.mossling.defeated)act({type:'attack',target:'mossling',rewind_sequence:acknowledgedSequence});});
$('#combat-block').addEventListener('click',()=>act({type:'block',target:'mossling'}));
$('#combat-dodge').addEventListener('click',()=>act({type:'dodge',target:'mossling'}));
$('#beacon-action').addEventListener('click',()=>act({type:'interact',target:'beacon'}));
function openDrawer(section){const drawer=$('#memory-drawer');const opening=drawer.hidden||drawer.dataset.section!==section;drawer.dataset.section=section;drawer.hidden=!opening;$('#satchel-content').hidden=!opening||section!=='satchel';$('#notes-content').hidden=!opening||section!=='notes';$('#drawer-title').textContent=section==='satchel'?'Satchel':'Field notes';$('#satchel-toggle').setAttribute('aria-expanded',String(opening&&section==='satchel'));$('#memory-toggle').setAttribute('aria-expanded',String(opening&&section==='notes'));}
$('#satchel-toggle').addEventListener('click',()=>openDrawer('satchel'));
$('#memory-toggle').addEventListener('click',()=>openDrawer('notes'));
$('#close-memory').addEventListener('click',()=>{const drawer=$('#memory-drawer');drawer.hidden=true;$('#satchel-toggle').setAttribute('aria-expanded','false');$('#memory-toggle').setAttribute('aria-expanded','false');});
$('#audio-toggle').addEventListener('click',()=>{startAmbient();setAmbientEnabled(!ambientEnabled);});
$('#interact').addEventListener('click',startAmbient);

const viewSettings=$('#view-settings');
const viewToggle=$('#view-toggle');
const cameraControls={sensitivity:$('#camera-sensitivity'),zoomSpeed:$('#camera-zoom-speed'),smoothing:$('#camera-smoothing'),invertY:$('#camera-invert-y')};
for(const [name,control] of Object.entries(cameraControls)){
  const value=cameraController.settings[name];
  if(name==='invertY')control.checked=value;else control.value=String(value);
  control.addEventListener('input',()=>cameraController.setOption(name,name==='invertY'?control.checked:control.value));
}
function setViewSettings(open){viewSettings.hidden=!open;viewToggle.setAttribute('aria-expanded',String(open));}
viewToggle.addEventListener('click',()=>setViewSettings(viewSettings.hidden));
$('#close-view-settings').addEventListener('click',()=>setViewSettings(false));

function resize(){
  const width=canvas.clientWidth,height=canvas.clientHeight;if(!width||!height)return;
  camera.aspect=width/height;camera.updateProjectionMatrix();renderer.setSize(width,height,false);
}
const resizeObserver=new ResizeObserver(resize);resizeObserver.observe(canvas.parentElement);window.addEventListener('resize',resize);

canvas.addEventListener('pointerdown',event=>{if(event.button!==0)return;startAmbient();clickPointer={id:event.pointerId,x:event.clientX,y:event.clientY};canvas.setPointerCapture(event.pointerId);});
canvas.addEventListener('pointerup',event=>{
  if(event.button!==0)return;
  const click=clickPointer;clickPointer=null;if(!click||click.id!==event.pointerId||Math.hypot(event.clientX-click.x,event.clientY-click.y)>5||!game)return;
  pointer.x=(event.clientX/canvas.clientWidth)*2-1;pointer.y=-(event.clientY/canvas.clientHeight)*2+1;
  raycaster.setFromCamera(pointer,camera);const hit=new THREE.Vector3();
  if(raycaster.ray.intersectPlane(groundPlane,hit)){
    clickDestination={x:THREE.MathUtils.clamp(hit.x,.3,18.7),z:THREE.MathUtils.clamp(hit.z,.3,12.7)};
  }
});

function animate(time){
  requestAnimationFrame(animate);
  const frameDelta=lastFrameTime?Math.min((time-lastFrameTime)/1000,.05):1/60;lastFrameTime=time;
  if(game){
    updateNetworkHud(time);
    const delta=frameDelta;
    const input=movementInput();
    movementClock.update(delta,stepDelta=>{
      previousPredictedPosition.x=predictedPosition.x;previousPredictedPosition.z=predictedPosition.z;
      simulateMovementFrame(input,input.run,stepDelta);
      if(Math.hypot(input.x,input.z)>.001||Math.hypot(predictedVelocity.x,predictedVelocity.z)>.03){
        const frame={sequence:++movementSequence,timestamp:performance.now(),input:{x:input.x,z:input.z},run:input.run};
        pendingInputs.push(frame);outgoingInputs.push(frame);
      }
    });
    const renderAlpha=movementClock.alpha();
    positionCorrection.x*=Math.exp(-7*delta);positionCorrection.z*=Math.exp(-7*delta);
    player.position.x=previousPredictedPosition.x+(predictedPosition.x-previousPredictedPosition.x)*renderAlpha+positionCorrection.x;
    player.position.z=previousPredictedPosition.z+(predictedPosition.z-previousPredictedPosition.z)*renderAlpha+positionCorrection.z;
    tickMovement(time);
    if(!busy&&!snapshotPollPending&&time>=nextSnapshotPollAt){
      nextSnapshotPollAt=time+100;snapshotPollPending=true;
      refresh().catch(error=>{if(error.status===409)showConnectionError(error.message);}).finally(()=>{snapshotPollPending=false;});
    }
    const speed=Math.hypot(predictedVelocity.x,predictedVelocity.z);
    const yawDifference=Math.atan2(Math.sin(targetPlayerYaw-player.rotation.y),Math.cos(targetPlayerYaw-player.rotation.y));
    player.rotation.y+=yawDifference*(1-Math.exp(-12*delta));
    const targetWalk=speed>.08?1:0,targetRun=speed>2.85?1:0;
    walkWeight+=(targetWalk-walkWeight)*(1-Math.exp(-10*delta));
    runWeight+=(targetRun-runWeight)*(1-Math.exp(-8*delta));
    const moving=walkWeight>.025;
    gaitPhase+=speed/(1.28+.38*runWeight)*Math.PI*2*delta;
    const gait=moving?Math.sin(gaitPhase):0;
    const playerParts=player.userData.parts;
    playerParts.legL.rotation.x=gait*.62*walkWeight;playerParts.legR.rotation.x=-gait*.62*walkWeight;
    playerParts.armL.rotation.x=-gait*.42*walkWeight;playerParts.armR.rotation.x=gait*.42*walkWeight;
    player.position.y=moving?Math.abs(gait)*(.025+.025*runWeight)*walkWeight:Math.sin(time*.0022)*.012;
    cameraController.update(delta,player.position,inputManager.cameraIntent());
    camera.position.y+=Math.sin(gaitPhase*2)*.018*walkWeight;
    skyDome.position.copy(camera.position);
    for(const foliage of foliageModels){
      const sway=Math.sin(time*.00048+(foliage.userData.windPhase||0))*(foliage.userData.windStrength||.025);
      if(foliage.userData.kind==='tree'){foliage.rotation.z=sway;foliage.rotation.x=sway*.55;}
      else foliage.rotation.z=(foliage.userData.baseRotation||0)+sway;
    }
    const maraParts=mara.userData.parts;maraParts.mantle.position.y=.61+Math.sin(time*.0018+1)*.018;
    const pulse=1+Math.sin(time*.003)*.055;
    beacon.ring.scale.setScalar(pulse);beacon.ring.rotation.y=time*.00035;
    for(const model of reedModels.values())if(model.group.visible){
      model.bloom.scale.setScalar(1+Math.sin(time*.004+model.group.position.x)*.12);
      for(const leaf of model.leaves)leaf.rotation.z=leaf.userData.baseRotation+Math.sin(time*.00075+leaf.userData.windPhase)*.035;
    }
    if(mossling.group.visible){mossling.group.position.y=Math.sin(time*.0025)*.035;mossling.body.rotation.z=Math.sin(time*.002)*.035;}
    if(mosslingRest.visible)mosslingRest.userData.seed.rotation.y=time*.0007;
  }
  if(!game)skyDome.position.copy(camera.position);
  const remoteRenderTime=performance.now()-REMOTE_INTERPOLATION_DELAY_MS;
  for(const remote of otherTravelers.values()){
    const sample=remote.snapshots.at(remoteRenderTime)||remote.latest;
    remote.avatar.position.x=sample.x;remote.avatar.position.z=sample.z;
    remote.avatar.position.y=.008*Math.sin(time*.002+sample.x);
  }
  renderer.render(scene,camera);
}

scene.add(new THREE.AmbientLight('#d8d4bd',.24));
cameraController.snap(player.position);resize();
connectToWorld();
requestAnimationFrame(animate);
