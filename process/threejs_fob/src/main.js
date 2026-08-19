import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js';

import { makeRng, D2R } from './rng.js';
import { makeAssets } from './textures.js';
import { buildSite } from './layout.js';
import { makeGround } from './ground.js';
import { Builder } from './builder.js';
import { buildBase } from './base.js';

/* ============================ 基础设施 ============================ */

const app = document.getElementById('app');
const $ = (id) => document.getElementById(id);
// 让出一帧给主线程刷新进度条。刻意不用 rAF —— 标签页不在前台时 rAF 不触发，
// 构建流程会整个卡住。
const tick = () => new Promise((r) => setTimeout(r, 0));

const renderer = new THREE.WebGLRenderer({
  antialias: true, powerPreference: 'high-performance', preserveDrawingBuffer: true,
});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.88;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.info.autoReset = false;
app.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const HAZE = new THREE.Color(0xc6b492);
scene.background = HAZE.clone();
scene.fog = new THREE.FogExp2(HAZE.clone(), 0.0013);

// 简易环境光照：天顶冷、地平线暖、下半球是沙地反射。
// 没有它金属件会黑得发假，有了之后 IBL 顺便把环境光也接管了。
function makeEnv() {
  const c = document.createElement('canvas');
  c.width = 128; c.height = 64;
  const g = c.getContext('2d');
  const grd = g.createLinearGradient(0, 0, 0, 64);
  grd.addColorStop(0.00, '#93aed4');
  grd.addColorStop(0.38, '#cbd0cd');
  grd.addColorStop(0.50, '#d6c39a');
  grd.addColorStop(0.62, '#a8916a');
  grd.addColorStop(1.00, '#7d6c4e');
  g.fillStyle = grd; g.fillRect(0, 0, 128, 64);
  // 太阳一侧稍亮
  const sunG = g.createRadialGradient(96, 20, 0, 96, 20, 46);
  sunG.addColorStop(0, 'rgba(255,244,214,0.85)');
  sunG.addColorStop(1, 'rgba(255,244,214,0)');
  g.fillStyle = sunG; g.fillRect(0, 0, 128, 64);
  const t = new THREE.CanvasTexture(c);
  t.mapping = THREE.EquirectangularReflectionMapping;
  t.colorSpace = THREE.SRGBColorSpace;
  const pmrem = new THREE.PMREMGenerator(renderer);
  const env = pmrem.fromEquirectangular(t).texture;
  pmrem.dispose(); t.dispose();
  return env;
}
scene.environment = makeEnv();
scene.environmentIntensity = 0.62;

const camera = new THREE.PerspectiveCamera(30, innerWidth / innerHeight, 2, 4200);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.06;
controls.maxPolarAngle = Math.PI * 0.497;
controls.minDistance = 12;
controls.maxDistance = 1800;
controls.screenSpacePanning = false;

const VIEWS = {
  cam0: { p: [2, 100, 272], t: [-8, 5, 88], fov: 34 },
  cam1: { p: [10, 470, 190], t: [0, 0, -10], fov: 42 },
  cam2: { p: [24, 3.2, 172], t: [-4, 9, 60], fov: 48 },
};
function applyView(v) {
  camera.position.set(...v.p);
  controls.target.set(...v.t);
  camera.fov = v.fov;
  camera.updateProjectionMatrix();
  $('fov').value = v.fov;
  $('fovv').textContent = v.fov + '°';
  controls.update();
}
applyView(VIEWS.cam0);

/* ============================ 光照 ============================ */

const sun = new THREE.DirectionalLight(0xfff1d5, 3.1);
sun.castShadow = true;
sun.shadow.mapSize.set(4096, 4096);
sun.shadow.camera.near = 180;
sun.shadow.camera.far = 1180;
sun.shadow.bias = -0.0006;
sun.shadow.normalBias = 0.16;
const D = 330;
Object.assign(sun.shadow.camera, { left: -D, right: D, top: D, bottom: -D });
sun.target.position.set(0, 0, 20);
scene.add(sun, sun.target);

// 环境贴图已经提供大部分间接光，这里只补一点方向性
const hemi = new THREE.HemisphereLight(0xa8c2e4, 0xc0a478, 0.22);
scene.add(hemi);
// 逆光方向的补光，模拟大气散射
const fill = new THREE.DirectionalLight(0xcdd6e8, 0.30);
fill.position.set(-260, 140, 320);
scene.add(fill);

function setSun(azDeg, elDeg) {
  const a = azDeg * D2R, e = elDeg * D2R;
  const dir = new THREE.Vector3(
    Math.cos(e) * Math.sin(a), Math.sin(e), Math.cos(e) * Math.cos(a));
  sun.position.copy(sun.target.position).addScaledVector(dir, 640);
  const warm = Math.max(0, 1 - elDeg / 60);
  sun.color.setHSL(0.10, 0.15 + warm * 0.35, 0.62 + (1 - warm) * 0.1);
  sun.intensity = 1.6 + Math.sin(Math.max(0.08, e)) * 3.0;
  fill.position.set(-dir.x * 400, 180, -dir.z * 400);
}

/* ============================ 后期 ============================ */

const GradeShader = {
  uniforms: {
    tDiffuse: { value: null },
    uVig: { value: 0.42 }, uGrain: { value: 0.02 },
    uWarm: { value: 0.35 }, uSeed: { value: 0 },
  },
  vertexShader: `varying vec2 vUv;
    void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
  fragmentShader: `
    uniform sampler2D tDiffuse; uniform float uVig, uGrain, uWarm, uSeed;
    varying vec2 vUv;
    float h21(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
    void main(){
      vec3 c = texture2D(tDiffuse, vUv).rgb;
      c *= mix(vec3(1.0), vec3(1.055, 1.005, 0.935), uWarm);   // 暖调
      c += vec3(0.011, 0.009, 0.007) * uWarm;                   // 沙尘抬黑
      vec2 d = vUv - 0.5;
      c *= 1.0 - uVig * dot(d, d) * 0.9;                        // 暗角
      c += (h21(vUv * 1024.0 + uSeed) - 0.5) * uGrain;          // 颗粒
      gl_FragColor = vec4(c, 1.0);
    }`,
};

let composer, gtaoPass, gradePass, useAO = true, aoOK = true;
function buildComposer() {
  const w = innerWidth, h = innerHeight;
  const rt = new THREE.WebGLRenderTarget(w, h, {
    type: THREE.HalfFloatType, samples: 4,
    colorSpace: THREE.LinearSRGBColorSpace,
  });
  composer = new EffectComposer(renderer, rt);
  composer.setPixelRatio(renderer.getPixelRatio());
  composer.setSize(w, h);
  composer.addPass(new RenderPass(scene, camera));
  try {
    gtaoPass = new GTAOPass(scene, camera, w, h);
    gtaoPass.output = GTAOPass.OUTPUT.Default;
    gtaoPass.blendIntensity = 0.95;
    gtaoPass.updateGtaoMaterial({
      radius: 1.6, distanceExponent: 1.4, thickness: 1.2,
      distanceFallOff: 1.0, scale: 1.1, samples: 16, screenSpaceRadius: false,
    });
    gtaoPass.updatePdMaterial({ lumaPhi: 8, depthPhi: 2.5, normalPhi: 3.5, radius: 3, rings: 2, samples: 12 });
    composer.addPass(gtaoPass);
  } catch (e) {
    console.warn('GTAO 不可用，已关闭：', e);
    aoOK = false; gtaoPass = null;
    $('ao').disabled = true;
  }
  gradePass = new ShaderPass(GradeShader);
  composer.addPass(gradePass);
  composer.addPass(new OutputPass());
}
buildComposer();

/* ============================ 生成 ============================ */

let assets = null;
let siteGroup = null, terrain = null, siteData = null;

function disposeGroup(g) {
  if (!g) return;
  scene.remove(g);
  g.traverse((o) => {
    if (o.isMesh) {
      o.geometry.dispose();
      if (o.material.map && o.material.userData.own) o.material.map.dispose();
    }
  });
}
function disposeTerrain(g) {
  if (!g) return;
  scene.remove(g);
  g.traverse((o) => {
    if (!o.isMesh) return;
    o.geometry.dispose();
    for (const k of ['map', 'normalMap', 'roughnessMap']) if (o.material[k]) o.material[k].dispose();
    o.material.dispose();
  });
}

const loadEl = $('load'), barEl = $('bar').firstElementChild, msgEl = $('loadmsg');
function progress(p, msg) {
  barEl.style.width = (p * 100).toFixed(0) + '%';
  if (msg) msgEl.textContent = msg;
}

async function generate(seed) {
  loadEl.classList.remove('done');
  progress(0.02, '准备…');
  await tick();

  if (!assets) {
    let i = 0;
    assets = makeAssets((name) => { progress(0.04 + (i++) * 0.035, '烘焙贴图 · ' + name); });
    await tick();
  }

  disposeGroup(siteGroup); siteGroup = null;
  disposeTerrain(terrain); terrain = null;

  progress(0.36, '排布总平面…');
  await tick();
  siteData = buildSite(makeRng(seed));

  progress(0.44, '烘焙地表 4096²…');
  await tick();
  terrain = makeGround(siteData, makeRng(seed ^ 0x51ed), assets.sand);
  scene.add(terrain);

  progress(0.60, '生成构筑物与载具…');
  await tick();
  const b = new Builder({ aoHeight: 1.6, aoStrength: 0.24 });
  buildBase(b, siteData, seed);
  const parts = b.parts, verts = b.verts;

  progress(0.86, `合并 ${parts.toLocaleString()} 个构件…`);
  await tick();
  siteGroup = b.build(assets.materials);
  scene.add(siteGroup);

  progress(0.97, '编译着色器…');
  await tick();
  renderer.compile(scene, camera);

  progress(1, '完成');
  await tick();
  loadEl.classList.add('done');
  statInfo = { parts, verts, cells: siteData.cells.length };
}

/* ============================ UI ============================ */

let statInfo = { parts: 0, verts: 0, cells: 0 };

$('regen').onclick = () => generate(parseInt($('seed').value, 10) || 1);
$('rand').onclick = () => {
  $('seed').value = Math.floor(Math.random() * 1e8);
  generate(parseInt($('seed').value, 10));
};
for (const k of ['cam0', 'cam1', 'cam2']) $(k).onclick = () => applyView(VIEWS[k]);

$('fov').oninput = (e) => {
  camera.fov = +e.target.value;
  camera.updateProjectionMatrix();
  $('fovv').textContent = e.target.value + '°';
};
const syncSun = () => {
  const az = +$('az').value, el = +$('el').value;
  $('azv').textContent = az + '°';
  $('elv').textContent = el + '°';
  setSun(az, el);
};
$('az').oninput = syncSun;
$('el').oninput = syncSun;
syncSun();

$('exp').oninput = (e) => {
  renderer.toneMappingExposure = +e.target.value;
  $('expv').textContent = (+e.target.value).toFixed(2);
};
$('haze').oninput = (e) => {
  scene.fog.density = +e.target.value / 10000;
  $('hazev').textContent = e.target.value;
};
$('ao').onchange = (e) => { useAO = e.target.checked && aoOK; if (gtaoPass) gtaoPass.enabled = useAO; };
$('shadow').onchange = (e) => {
  renderer.shadowMap.enabled = e.target.checked;
  scene.traverse((o) => { if (o.isMesh) o.material.needsUpdate = true; });
};
$('grade').onchange = (e) => { gradePass.enabled = e.target.checked; };

let wire = false;
$('wire').onclick = () => {
  wire = !wire;
  if (siteGroup) siteGroup.traverse((o) => { if (o.isMesh) o.material.wireframe = wire; });
};
$('shot').onclick = () => {
  const a = document.createElement('a');
  a.download = `fob_${$('seed').value}.png`;
  a.href = renderer.domElement.toDataURL('image/png');
  a.click();
};
$('toggle').onclick = () => {
  $('hud').classList.toggle('hide');
  $('toggle').classList.toggle('hide');
  $('toggle').textContent = $('hud').classList.contains('hide') ? '›' : '‹';
};

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
  composer.setSize(innerWidth, innerHeight);
  if (gtaoPass) gtaoPass.setSize(innerWidth, innerHeight);
});

/* ============================ 主循环 ============================ */

const statEl = $('stat');
let frames = 0, fps = 0, last = performance.now(), acc = 0;

function loop(now) {
  requestAnimationFrame(loop);
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;

  if ($('spin').checked) {
    const r = camera.position.clone().sub(controls.target);
    const a = dt * 0.045;
    const ca = Math.cos(a), sa = Math.sin(a);
    camera.position.set(
      controls.target.x + r.x * ca - r.z * sa,
      camera.position.y,
      controls.target.z + r.x * sa + r.z * ca);
  }
  controls.update();
  gradePass.uniforms.uSeed.value = (now * 0.017) % 1000;
  // 一帧里 composer 会多次调 render，autoReset 会把统计冲掉
  renderer.info.reset();
  composer.render();

  frames++; acc += dt;
  if (acc > 0.5) { fps = frames / acc; frames = 0; acc = 0; }
  if (frames === 0) {
    const info = renderer.info.render;
    statEl.innerHTML =
      `帧率     <b>${fps.toFixed(0)}</b> fps\n` +
      `绘制批次 <b>${info.calls}</b>\n` +
      `三角面   <b>${(info.triangles / 1e6).toFixed(2)}</b> M\n` +
      `构件数   <b>${statInfo.parts.toLocaleString()}</b>\n` +
      `院落     <b>${statInfo.cells}</b>`;
  }
}

// 调试钩子（也方便在控制台里改参数）
window.__fob = {
  THREE, scene, camera, renderer, controls, composer, generate, applyView, VIEWS,
  get info() {
    const meshes = [];
    if (siteGroup) siteGroup.traverse((o) => o.isMesh && meshes.push(
      [o.name, o.geometry.attributes.position.count]));
    return { meshes, parts: statInfo.parts, verts: statInfo.verts, cells: statInfo.cells };
  },
  render() { composer.render(); },
  // 无头/未合成的环境下把当前画面存到 webui/base3d/_shots/
  async shoot(name = 'shot', w = 1600, h = 900) {
    const old = [renderer.domElement.width, renderer.domElement.height];
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    if (gtaoPass) gtaoPass.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    composer.render();
    const url = renderer.domElement.toDataURL('image/png');
    const r = await fetch('/save?name=' + encodeURIComponent(name), { method: 'POST', body: url });
    renderer.setSize(old[0], old[1], false);
    composer.setSize(old[0], old[1]);
    if (gtaoPass) gtaoPass.setSize(old[0], old[1]);
    camera.aspect = old[0] / old[1];
    camera.updateProjectionMatrix();
    return r.text();
  },
};

generate(parseInt($('seed').value, 10) || 20260813).then(() => requestAnimationFrame(loop));
