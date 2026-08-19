// 地面。把整张总平面（场坪、道路、轮胎印、油渍、停机坪标线）
// 烘到一张 4096 的贴图上 —— 一个 draw call 就把地表所有信息画完，
// 再用一张小尺寸高频法线补细节。
import * as THREE from 'three';
import { fbmField, makeCanvas, tex } from './textures.js';
import { SITE } from './layout.js';

const PLANE = 900;          // 烘焙地面的边长（米）
const N = 4096;             // 贴图分辨率
const K = N / PLANE;        // 像素/米
const HALF = PLANE / 2;

const px = (x) => (x + HALF) * K;
const py = (z) => (z + HALF) * K;

function grayTile(size, base, oct, seed, gain) {
  const f = fbmField(size, base, oct, seed, gain);
  const c = makeCanvas(size);
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(size, size);
  for (let i = 0; i < f.length; i++) {
    const v = f[i] * 255;
    img.data[i * 4] = img.data[i * 4 + 1] = img.data[i * 4 + 2] = v;
    img.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

export function makeGround(site, rng, sandAsset) {
  const c = makeCanvas(N);
  const g = c.getContext('2d');

  /* ---------- 1. 沙土底色 ---------- */
  g.fillStyle = '#bcae90';
  g.fillRect(0, 0, N, N);

  // 大尺度色斑
  const coarse = grayTile(256, 5, 4, 991, 0.6);
  g.globalCompositeOperation = 'overlay';
  g.globalAlpha = 0.40;
  g.drawImage(coarse, 0, 0, N, N);
  g.globalAlpha = 1;
  g.globalCompositeOperation = 'source-over';

  /* ---------- 2. 道路 ---------- */
  const rect = (x0, z0, x1, z1, fill) => {
    g.fillStyle = fill;
    g.fillRect(px(x0), py(z0), (x1 - x0) * K, (z1 - z0) * K);
  };

  // 压实的车行道比周围的浮沙更暗更灰
  for (const r of site.roads) rect(r.x0, r.z0, r.x1, r.z1, '#9d937c');
  // 周界外的土路更黄更脏
  rect(site.outer.x0, site.outer.z0, site.outer.x1, site.outer.z1, '#a8916a');

  /* ---------- 3. 院内混凝土场坪 ---------- */
  const flat = new Uint8Array(512 * 512);
  const markFlat = (x0, z0, x1, z1) => {
    const a = Math.max(0, Math.floor((x0 + HALF) / PLANE * 512));
    const b = Math.min(511, Math.ceil((x1 + HALF) / PLANE * 512));
    const cc = Math.max(0, Math.floor((z0 + HALF) / PLANE * 512));
    const d = Math.min(511, Math.ceil((z1 + HALF) / PLANE * 512));
    for (let j = cc; j <= d; j++) for (let i = a; i <= b; i++) flat[j * 512 + i] = 1;
  };
  for (const r of site.roads) markFlat(r.x0, r.z0, r.x1, r.z1);

  for (const cell of site.cells) {
    if (!cell.pad) continue;
    const m = 1.2;
    const x0 = cell.x0 + m, x1 = cell.x1 - m, z0 = cell.z0 + m, z1 = cell.z1 - m;
    const tone = 138 + rng.int(-14, 18);
    g.fillStyle = `rgb(${tone},${tone - 2},${tone - 12})`;
    g.fillRect(px(x0), py(z0), (x1 - x0) * K, (z1 - z0) * K);
    markFlat(x0, z0, x1, z1);

    // 场坪上飘的沙 —— 不然一块干净水泥板太假
    for (let i = 0, n = rng.int(8, 16); i < n; i++) {
      const bx = rng.range(x0, x1), bz = rng.range(z0, z1);
      const br = rng.range(2.5, 9);
      const gd = g.createRadialGradient(px(bx), py(bz), 0, px(bx), py(bz), br * K);
      gd.addColorStop(0, `rgba(184,168,132,${rng.range(0.14, 0.36).toFixed(2)})`);
      gd.addColorStop(1, 'rgba(184,168,132,0)');
      g.fillStyle = gd;
      g.beginPath(); g.arc(px(bx), py(bz), br * K, 0, Math.PI * 2); g.fill();
    }

    // 分仓缝
    g.strokeStyle = 'rgba(90,80,64,0.35)';
    g.lineWidth = 1.6;
    const nx = Math.round((x1 - x0) / 6), nz = Math.round((z1 - z0) / 6);
    for (let i = 1; i < nx; i++) {
      const x = x0 + (x1 - x0) * i / nx;
      g.beginPath(); g.moveTo(px(x), py(z0)); g.lineTo(px(x), py(z1)); g.stroke();
    }
    for (let i = 1; i < nz; i++) {
      const z = z0 + (z1 - z0) * i / nz;
      g.beginPath(); g.moveTo(px(x0), py(z)); g.lineTo(px(x1), py(z)); g.stroke();
    }
    // 破损裂缝
    g.strokeStyle = 'rgba(70,60,48,0.45)';
    g.lineWidth = 1.4;
    for (let i = 0, n = rng.int(1, 4); i < n; i++) {
      let x = rng.range(x0, x1), z = rng.range(z0, z1);
      g.beginPath(); g.moveTo(px(x), py(z));
      for (let k = 0; k < 6; k++) {
        x += rng.j(4); z += rng.j(4);
        g.lineTo(px(x), py(z));
      }
      g.stroke();
    }
  }

  /* ---------- 4. 停机坪 ---------- */
  if (site.helipad) {
    const h = site.helipad;
    const s = 17;
    g.fillStyle = '#7d7668';
    g.fillRect(px(h.cx - s), py(h.cz - s), s * 2 * K, s * 2 * K);
    g.strokeStyle = 'rgba(226,222,208,0.62)';
    g.lineWidth = 0.55 * K;
    g.beginPath();
    g.arc(px(h.cx), py(h.cz), 11 * K, 0, Math.PI * 2);
    g.stroke();
    // H
    g.lineWidth = 1.3 * K;
    g.beginPath();
    g.moveTo(px(h.cx - 3.4), py(h.cz - 5)); g.lineTo(px(h.cx - 3.4), py(h.cz + 5));
    g.moveTo(px(h.cx + 3.4), py(h.cz - 5)); g.lineTo(px(h.cx + 3.4), py(h.cz + 5));
    g.moveTo(px(h.cx - 3.4), py(h.cz)); g.lineTo(px(h.cx + 3.4), py(h.cz));
    g.stroke();
    markFlat(h.cx - s, h.cz - s, h.cx + s, h.cz + s);
  }

  /* ---------- 5. 轮胎印 ---------- */
  g.lineCap = 'round';
  const track = (x0, z0, x1, z1, wob, dark) => {
    for (const off of [-1.0, 1.0]) {
      g.strokeStyle = dark ? 'rgba(96,82,62,0.30)' : 'rgba(150,132,102,0.34)';
      g.lineWidth = 0.55 * K;
      g.beginPath();
      const steps = 16;
      const nx = -(z1 - z0), nz = x1 - x0;
      const l = Math.hypot(nx, nz) || 1;
      for (let i = 0; i <= steps; i++) {
        const t = i / steps;
        const w = Math.sin(t * 7 + wob) * 1.6;
        const x = x0 + (x1 - x0) * t + (nx / l) * (off + w);
        const z = z0 + (z1 - z0) * t + (nz / l) * (off + w);
        i ? g.lineTo(px(x), py(z)) : g.moveTo(px(x), py(z));
      }
      g.stroke();
    }
  };
  for (const r of site.roads) {
    const n = r.v ? 2 : 3;
    for (let i = 0; i < n; i++) {
      const t = (i + 0.5) / n;
      if (r.v) {
        const x = r.x0 + (r.x1 - r.x0) * t;
        track(x, r.z0, x, r.z1, rng.range(0, 6), rng.chance(0.5));
      } else {
        const z = r.z0 + (r.z1 - r.z0) * t;
        track(r.x0, z, r.x1, z, rng.range(0, 6), rng.chance(0.5));
      }
    }
  }
  for (let i = 0; i < 2; i++) {
    const z = rng.range(site.outer.z0 + 3, site.outer.z1 - 3);
    track(site.outer.x0, z, site.outer.x1, z + rng.j(6), rng.range(0, 6), true);
  }
  // 院子里的零散车辙
  for (let i = 0; i < 130; i++) {
    const cell = rng.pick(site.cells);
    const x0 = rng.range(cell.x0, cell.x1), z0 = rng.range(cell.z0, cell.z1);
    const a = rng.range(0, Math.PI * 2), len = rng.range(8, 26);
    track(x0, z0, x0 + Math.cos(a) * len, z0 + Math.sin(a) * len, rng.range(0, 6), rng.chance(0.4));
  }

  /* ---------- 6. 油渍 / 碎屑 ---------- */
  for (let i = 0; i < 260; i++) {
    const cell = rng.pick(site.cells);
    const x = rng.range(cell.x0, cell.x1), z = rng.range(cell.z0, cell.z1);
    const r = rng.range(0.6, 3.2);
    const grd = g.createRadialGradient(px(x), py(z), 0, px(x), py(z), r * K);
    grd.addColorStop(0, 'rgba(38,30,22,0.42)');
    grd.addColorStop(1, 'rgba(38,30,22,0)');
    g.fillStyle = grd;
    g.beginPath(); g.arc(px(x), py(z), r * K, 0, Math.PI * 2); g.fill();
  }
  g.fillStyle = 'rgba(90,78,58,0.5)';
  for (let i = 0; i < 5000; i++) {
    const x = rng.range(-HALF, HALF), z = rng.range(-HALF, HALF);
    g.fillRect(px(x), py(z), rng.range(1, 3.4), rng.range(1, 3.4));
  }

  /* ---------- 7. 高频颗粒 ---------- */
  const fine = grayTile(512, 40, 5, 1231, 0.52);
  g.globalCompositeOperation = 'overlay';
  g.globalAlpha = 0.42;
  for (let i = 0; i < 8; i++) for (let j = 0; j < 8; j++) g.drawImage(fine, i * 512, j * 512, 512, 512);
  g.globalAlpha = 0.28;
  g.drawImage(coarse, 0, 0, N, N);
  g.globalAlpha = 1;
  g.globalCompositeOperation = 'source-over';

  /* ---------- 8. 边缘向远景沙色过渡 ---------- */
  const EDGE = '188,174,144';
  const edge = g.createLinearGradient(0, 0, 0, N);
  edge.addColorStop(0, `rgba(${EDGE},0.95)`);
  edge.addColorStop(0.09, `rgba(${EDGE},0)`);
  edge.addColorStop(0.93, `rgba(${EDGE},0)`);
  edge.addColorStop(1, `rgba(${EDGE},0.9)`);
  g.fillStyle = edge; g.fillRect(0, 0, N, N);
  const edge2 = g.createLinearGradient(0, 0, N, 0);
  edge2.addColorStop(0, `rgba(${EDGE},0.95)`);
  edge2.addColorStop(0.09, `rgba(${EDGE},0)`);
  edge2.addColorStop(0.93, `rgba(${EDGE},0)`);
  edge2.addColorStop(1, `rgba(${EDGE},0.9)`);
  g.fillStyle = edge2; g.fillRect(0, 0, N, N);

  /* ---------- 9. 粗糙度 ---------- */
  const rc = makeCanvas(512);
  const rg = rc.getContext('2d');
  rg.fillStyle = '#f2f2f2'; rg.fillRect(0, 0, 512, 512);   // 沙土：非常粗糙
  rg.fillStyle = '#c4c4c4';
  for (const r of site.roads) {
    rg.fillRect((r.x0 + HALF) / PLANE * 512, (r.z0 + HALF) / PLANE * 512,
      (r.x1 - r.x0) / PLANE * 512, (r.z1 - r.z0) / PLANE * 512);
  }
  rg.fillStyle = '#b0b0b0';
  for (const cell of site.cells) {
    if (!cell.pad) continue;
    rg.fillRect((cell.x0 + HALF) / PLANE * 512, (cell.z0 + HALF) / PLANE * 512,
      SITE.CELL_W / PLANE * 512, SITE.CELL_D / PLANE * 512);
  }

  /* ---------- 10. 网格 + 起伏 ---------- */
  const SEG = 320;
  const geo = new THREE.PlaneGeometry(PLANE, PLANE, SEG, SEG);
  const hf = fbmField(256, 7, 5, 555, 0.55);
  const hf2 = fbmField(256, 30, 3, 557, 0.5);
  const pos = geo.attributes.position;
  const sampleFlat = (x, z) => {
    const i = Math.min(511, Math.max(0, ((x + HALF) / PLANE * 512) | 0));
    const j = Math.min(511, Math.max(0, ((z + HALF) / PLANE * 512) | 0));
    // 3x3 平滑，避免场坪边缘出现台阶
    let s = 0;
    for (let a = -2; a <= 2; a++) for (let b = -2; b <= 2; b++) {
      const ii = Math.min(511, Math.max(0, i + a)), jj = Math.min(511, Math.max(0, j + b));
      s += flat[jj * 512 + ii];
    }
    return s / 25;
  };
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = -pos.getY(i);
    const u = Math.min(255, Math.max(0, ((x + HALF) / PLANE * 256) | 0));
    const v = Math.min(255, Math.max(0, ((z + HALF) / PLANE * 256) | 0));
    const dune = (hf[v * 256 + u] - 0.5) * 2.6 + (hf2[v * 256 + u] - 0.5) * 0.5;
    const paved = sampleFlat(x, z);
    let h = dune * (1 - paved) * 0.85;
    // 周界外堆土
    const dz = Math.abs(z - SITE.OUTER_ROAD + 16);
    if (z > SITE.FENCE + 2 && dz < 8) h += (1 - dz / 8) * 1.1;
    // 场区之外抬起一点，形成远景地平
    const rad = Math.max(Math.abs(x), Math.abs(z));
    if (rad > 300) h += (rad - 300) * 0.012 + dune * (rad - 300) * 0.01;
    pos.setZ(i, h);
  }
  geo.computeVertexNormals();
  geo.rotateX(-Math.PI / 2);

  const map = tex(c, { srgb: true, aniso: 16 });
  map.wrapS = map.wrapT = THREE.ClampToEdgeWrapping;
  const roughMap = tex(rc);
  roughMap.wrapS = roughMap.wrapT = THREE.ClampToEdgeWrapping;
  const nrm = sandAsset.normalMap.clone();
  nrm.needsUpdate = true;
  nrm.wrapS = nrm.wrapT = THREE.RepeatWrapping;
  nrm.repeat.set(240, 240);

  const mat = new THREE.MeshStandardMaterial({
    map, roughnessMap: roughMap, normalMap: nrm,
    normalScale: new THREE.Vector2(0.55, 0.55),
    roughness: 1.0, metalness: 0.0,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.receiveShadow = true;
  mesh.name = 'ground';

  // 远景地面（只为把地平线填满）
  const farMat = new THREE.MeshStandardMaterial({
    color: 0xb3a586, roughness: 1, metalness: 0,
    map: sandAsset.map.clone(),
  });
  farMat.map.wrapS = farMat.map.wrapT = THREE.RepeatWrapping;
  farMat.map.repeat.set(300, 300);
  farMat.map.needsUpdate = true;
  const far = new THREE.Mesh(new THREE.PlaneGeometry(6000, 6000), farMat);
  far.rotation.x = -Math.PI / 2;
  far.position.y = -1.2;
  far.name = 'far-ground';

  const group = new THREE.Group();
  group.add(far, mesh);
  group.name = 'terrain';
  return group;
}
