// 全部贴图都在浏览器里用 Canvas 现算 —— 零外部资源依赖。
import * as THREE from 'three';

/* ============================ 噪声 ============================ */

function hash(x, y, s) {
  let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ Math.imul(s | 0, 2147483647);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

// 可无缝平铺的值噪声：坐标按 period 取模
function vnoise(x, y, s, period) {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const p = period;
  const x0 = ((xi % p) + p) % p, x1 = (x0 + 1) % p;
  const y0 = ((yi % p) + p) % p, y1 = (y0 + 1) % p;
  const a = hash(x0, y0, s), b = hash(x1, y0, s);
  const c = hash(x0, y1, s), d = hash(x1, y1, s);
  return (a + (b - a) * u) + ((c + (d - c) * u) - (a + (b - a) * u)) * v;
}

// 分形噪声，返回 0..1，size 像素上按 base 个格子平铺
function fbmField(size, base, octaves, seed, gain = 0.5, warp = 0) {
  const out = new Float32Array(size * size);
  let min = 1e9, max = -1e9;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let f = base, amp = 1, sum = 0, norm = 0;
      let px = x / size, py = y / size;
      if (warp) {
        px += warp * (vnoise(px * base * 2, py * base * 2, seed + 91, base * 2) - 0.5);
        py += warp * (vnoise(px * base * 2 + 5, py * base * 2 + 5, seed + 47, base * 2) - 0.5);
      }
      for (let o = 0; o < octaves; o++) {
        sum += amp * vnoise(px * f, py * f, seed + o * 131, f);
        norm += amp;
        amp *= gain;
        f *= 2;
      }
      const v = sum / norm;
      out[y * size + x] = v;
      if (v < min) min = v;
      if (v > max) max = v;
    }
  }
  const inv = 1 / Math.max(1e-6, max - min);
  for (let i = 0; i < out.length; i++) out[i] = (out[i] - min) * inv;
  return out;
}

// 各向异性 fbm（拉长的条纹，用于流挂、刷痕、木纹）
function streakField(size, bx, by, octaves, seed) {
  const out = new Float32Array(size * size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let fx = bx, fy = by, amp = 1, sum = 0, norm = 0;
      for (let o = 0; o < octaves; o++) {
        const p = Math.max(fx, fy);
        sum += amp * vnoise((x / size) * fx, (y / size) * fy, seed + o * 77, p);
        norm += amp; amp *= 0.55; fx *= 2; fy *= 2;
      }
      out[y * size + x] = sum / norm;
    }
  }
  return out;
}

function makeCanvas(size) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  return c;
}

// 从高度场生成法线贴图
function normalCanvas(height, size, strength) {
  const c = makeCanvas(size);
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(size, size);
  const d = img.data;
  const at = (x, y) => height[(((y % size) + size) % size) * size + (((x % size) + size) % size)];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = (at(x + 1, y) - at(x - 1, y)) * strength;
      const dy = (at(x, y + 1) - at(x, y - 1)) * strength;
      let nx = -dx, ny = -dy, nz = 1;
      const l = Math.hypot(nx, ny, nz);
      const i = (y * size + x) * 4;
      d[i] = (nx / l * 0.5 + 0.5) * 255;
      d[i + 1] = (ny / l * 0.5 + 0.5) * 255;
      d[i + 2] = (nz / l * 0.5 + 0.5) * 255;
      d[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

function tex(canvas, { srgb = false, repeat = 1, aniso = 8 } = {}) {
  const t = new THREE.CanvasTexture(canvas);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(repeat, repeat);
  t.anisotropy = aniso;
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.needsUpdate = true;
  return t;
}

// 用回调逐像素写一张画布
function paint(size, fn) {
  const c = makeCanvas(size);
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(size, size);
  const d = img.data;
  const rgb = [0, 0, 0];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      fn(x, y, rgb);
      const i = (y * size + x) * 4;
      d[i] = Math.max(0, Math.min(255, rgb[0] * 255));
      d[i + 1] = Math.max(0, Math.min(255, rgb[1] * 255));
      d[i + 2] = Math.max(0, Math.min(255, rgb[2] * 255));
      d[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

/* ============================ 各类材质贴图 ============================ */

// 风化混凝土：底噪 + 竖向流挂 + 模板缝 + 污渍。平均亮度接近白，
// 真正的颜色交给顶点色，这样同一张图能当墙、路面、掩体。
function concreteMaps(S = 1024) {
  const grain = fbmField(S, 64, 5, 11);
  const blotch = fbmField(S, 8, 4, 23, 0.55, 0.35);
  const drip = streakField(S, 90, 5, 4, 37);
  const height = new Float32Array(S * S);

  const col = paint(S, (x, y, o) => {
    const i = y * S + x;
    let v = 0.80 + (grain[i] - 0.5) * 0.16 + (blotch[i] - 0.5) * 0.20;
    // 竖向雨痕：只在 drip 值高的列出现，越往下越淡
    const dr = drip[i];
    if (dr > 0.62) v -= (dr - 0.62) * 1.25 * (0.35 + 0.65 * (y / S));
    // 模板板缝（每半张一道，弱一点 —— 密了会在墙面上形成假条纹）
    const sy = y % (S / 2);
    if (sy < 2) v -= 0.05;
    // 零星孔洞 / 崩边
    if (grain[i] > 0.955) v -= 0.28;
    height[i] = v;
    o[0] = v * 1.0; o[1] = v * 0.985; o[2] = v * 0.955;
  });

  const rough = paint(S >> 1, (x, y, o) => {
    const i = (y * 2) * S + x * 2;
    const v = 0.86 + (grain[i] - 0.5) * 0.18 - (blotch[i] - 0.5) * 0.10;
    o[0] = o[1] = o[2] = v;
  });

  return {
    map: tex(col, { srgb: true }),
    roughnessMap: tex(rough),
    normalMap: tex(normalCanvas(height, S, 3.2)),
  };
}

// 波纹铁皮：横向正弦起伏 + 锈斑 + 竖向污流
function corrugatedMaps(S = 512, ribs = 8) {
  const rust = fbmField(S, 10, 4, 55, 0.55, 0.5);
  const fine = fbmField(S, 96, 3, 71);
  const dirt = streakField(S, 40, 4, 3, 83);
  const height = new Float32Array(S * S);
  const col = paint(S, (x, y, o) => {
    const i = y * S + x;
    const w = Math.sin((x / S) * Math.PI * 2 * ribs);
    const shade = 0.70 + 0.30 * (w * 0.5 + 0.5);
    height[i] = w * 0.5 + 0.5 + fine[i] * 0.05;
    let r = shade, g = shade, b = shade;
    const ru = rust[i];
    if (ru > 0.60) {                       // 锈蚀
      const t = Math.min(1, (ru - 0.60) * 3.2);
      r = r * (1 - t) + t * 0.46;
      g = g * (1 - t) + t * 0.25;
      b = b * (1 - t) + t * 0.15;
    }
    const dv = (dirt[i] - 0.5) * 0.22;
    r += dv; g += dv * 0.95; b += dv * 0.85;
    r *= 0.95 + fine[i] * 0.1; g *= 0.95 + fine[i] * 0.1; b *= 0.95 + fine[i] * 0.1;
    // 搭接缝
    if (x % Math.floor(S / 2) < 2) { r *= 0.8; g *= 0.8; b *= 0.8; }
    o[0] = r; o[1] = g; o[2] = b;
  });
  return {
    map: tex(col, { srgb: true }),
    normalMap: tex(normalCanvas(height, S, 5.0)),
  };
}

// 帆布篷：织纹 + 大褶皱 + 横向接缝
function canvasMaps(S = 512) {
  const folds = fbmField(S, 6, 3, 101, 0.6, 0.3);
  const weave = fbmField(S, 150, 2, 113);
  const height = new Float32Array(S * S);
  const col = paint(S, (x, y, o) => {
    const i = y * S + x;
    let v = 0.80 + (folds[i] - 0.5) * 0.30;
    const wx = Math.sin(x * 1.9) * 0.5 + 0.5, wy = Math.sin(y * 1.9) * 0.5 + 0.5;
    v *= 0.93 + 0.07 * (wx * 0.5 + wy * 0.5) + (weave[i] - 0.5) * 0.05;
    const sy = y % (S / 3);
    if (sy < 3) v *= 0.86;                  // 缝合线
    height[i] = v + (wx + wy) * 0.02;
    o[0] = v; o[1] = v * 0.985; o[2] = v * 0.95;
  });
  return {
    map: tex(col, { srgb: true }),
    normalMap: tex(normalCanvas(height, S, 2.0)),
  };
}

// 划痕金属
function metalMaps(S = 512) {
  const streak = streakField(S, 200, 8, 4, 131);
  const patch = fbmField(S, 12, 3, 149);
  const height = new Float32Array(S * S);
  const col = paint(S, (x, y, o) => {
    const i = y * S + x;
    let v = 0.78 + (streak[i] - 0.5) * 0.30 + (patch[i] - 0.5) * 0.12;
    height[i] = v;
    o[0] = v; o[1] = v * 0.99; o[2] = v * 0.98;
  });
  const rough = paint(S >> 1, (x, y, o) => {
    const i = (y * 2) * S + x * 2;
    const v = 0.42 + (streak[i] - 0.5) * 0.35 + (patch[i] - 0.5) * 0.2;
    o[0] = o[1] = o[2] = v;
  });
  return {
    map: tex(col, { srgb: true }),
    roughnessMap: tex(rough),
    normalMap: tex(normalCanvas(height, S, 1.4)),
  };
}

// 木板 / 弹药箱
function woodMaps(S = 512, planks = 5) {
  const grain = streakField(S, 220, 10, 4, 173);
  const tone = fbmField(S, 4, 2, 191);
  const height = new Float32Array(S * S);
  const col = paint(S, (x, y, o) => {
    const i = y * S + x;
    const pw = S / planks;
    const pi = Math.floor(y / pw);
    const local = y - pi * pw;
    let v = 0.72 + (grain[i] - 0.5) * 0.30 + (hash(pi, 3, 7) - 0.5) * 0.16 + (tone[i] - 0.5) * 0.1;
    if (local < 2 || local > pw - 2) v *= 0.62;   // 板缝
    height[i] = v - (local < 2 || local > pw - 2 ? 0.3 : 0);
    o[0] = v * 1.0; o[1] = v * 0.80; o[2] = v * 0.56;
  });
  return {
    map: tex(col, { srgb: true }),
    normalMap: tex(normalCanvas(height, S, 2.2)),
  };
}

// HESCO 挡墙：铁丝网格 + 土工布 + 碎石填料
function hescoMaps(S = 512) {
  const fill = fbmField(S, 40, 4, 211, 0.55, 0.3);
  const coarse = fbmField(S, 14, 3, 227);
  const height = new Float32Array(S * S);
  const cell = S / 4;
  const col = paint(S, (x, y, o) => {
    const i = y * S + x;
    let v = 0.74 + (fill[i] - 0.5) * 0.34 + (coarse[i] - 0.5) * 0.18;
    let wire = false;
    const mx = x % cell, my = y % cell;
    if (mx < 3 || my < 3) wire = true;                    // 主网格
    if ((x % (cell / 4) < 1.4) || (y % (cell / 4) < 1.4)) { v *= 0.9; }  // 细网
    height[i] = v + (wire ? 0.35 : 0);
    if (wire) { o[0] = 0.30; o[1] = 0.28; o[2] = 0.25; return; }
    o[0] = v * 1.0; o[1] = v * 0.88; o[2] = v * 0.68;
  });
  return {
    map: tex(col, { srgb: true }),
    normalMap: tex(normalCanvas(height, S, 3.0)),
  };
}

// 车辆漆面上的浮尘（近白，只做细微起伏）
function dustMaps(S = 512) {
  const n = fbmField(S, 24, 4, 251, 0.55, 0.25);
  const s = streakField(S, 12, 90, 3, 263);
  const col = paint(S, (x, y, o) => {
    const i = y * S + x;
    const v = 0.90 + (n[i] - 0.5) * 0.16 + (s[i] - 0.5) * 0.08;
    o[0] = v; o[1] = v * 0.99; o[2] = v * 0.97;
  });
  const rough = paint(S >> 1, (x, y, o) => {
    const i = (y * 2) * S + x * 2;
    const v = 0.62 + (n[i] - 0.5) * 0.3;
    o[0] = o[1] = o[2] = v;
  });
  return { map: tex(col, { srgb: true }), roughnessMap: tex(rough) };
}

// 沙地细节（给地面当法线 / 远景色）
function sandMaps(S = 512) {
  const n = fbmField(S, 48, 5, 307, 0.5);
  const grit = fbmField(S, 180, 2, 311);
  const height = new Float32Array(S * S);
  const col = paint(S, (x, y, o) => {
    const i = y * S + x;
    const v = 0.72 + (n[i] - 0.5) * 0.22 + (grit[i] - 0.5) * 0.10;
    height[i] = v;
    o[0] = v * 1.0; o[1] = v * 0.88; o[2] = v * 0.68;
  });
  return {
    map: tex(col, { srgb: true }),
    normalMap: tex(normalCanvas(height, S, 1.8)),
    heightCanvas: col,
  };
}

// 铁丝网（alphaMap，白 = 不透明）
function chainlinkAlpha(S = 256) {
  const c = makeCanvas(S);
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, S, S);
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 2.4;
  const step = S / 8;
  for (let i = -S; i < S * 2; i += step) {
    ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i + S, S); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(i, S); ctx.lineTo(i + S, 0); ctx.stroke();
  }
  return tex(c);
}

// 遮阳网 / 迷彩网屏
function netAlpha(S = 256) {
  const c = makeCanvas(S);
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, S, S);
  ctx.fillStyle = '#fff';
  const n = fbmField(64, 8, 3, 401);
  for (let y = 0; y < 64; y++) {
    for (let x = 0; x < 64; x++) {
      if (n[y * 64 + x] > 0.42) ctx.fillRect(x * S / 64, y * S / 64, S / 64 + 0.6, S / 64 + 0.6);
    }
  }
  ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5;
  for (let i = 0; i <= S; i += S / 16) {
    ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, S); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, i); ctx.lineTo(S, i); ctx.stroke();
  }
  return tex(c);
}

/* ============================ 装配 ============================ */

export function makeAssets(onStep = () => {}) {
  onStep('混凝土');   const concrete = concreteMaps(1024);
  onStep('波纹铁皮'); const corr = corrugatedMaps(512, 8);
  onStep('帆布');     const canvasT = canvasMaps(512);
  onStep('金属');     const metal = metalMaps(512);
  onStep('木材');     const wood = woodMaps(512, 5);
  onStep('HESCO');    const hesco = hescoMaps(512);
  onStep('浮尘');     const dust = dustMaps(512);
  onStep('沙地');     const sand = sandMaps(512);
  onStep('铁丝网');   const link = chainlinkAlpha(256);
  const net = netAlpha(256);

  const M = (o) => new THREE.MeshStandardMaterial({ vertexColors: true, ...o });

  const materials = {
    // 混凝土构筑物：墙、楼、掩体、路面台
    concrete: M({ ...concrete, roughness: 1.0, metalness: 0.0, normalScale: new THREE.Vector2(0.8, 0.8) }),
    // 波纹铁皮屋面 / 集装箱
    corr: M({ ...corr, roughness: 0.68, metalness: 0.45, normalScale: new THREE.Vector2(1.1, 1.1) }),
    // 帆布篷 / 迷彩布
    canvas: M({ ...canvasT, roughness: 1.0, metalness: 0.0, normalScale: new THREE.Vector2(0.7, 0.7) }),
    // 裸金属：钢架、管、桅杆
    metal: M({ ...metal, roughness: 0.55, metalness: 0.8 }),
    // 车辆漆面
    paint: M({ ...dust, roughness: 0.78, metalness: 0.16 }),
    // 木箱 / 托盘
    wood: M({ ...wood, roughness: 0.92, metalness: 0.0 }),
    // HESCO / 沙袋
    hesco: M({ ...hesco, roughness: 1.0, metalness: 0.0 }),
    // 橡胶 / 阴影里的开口 / 玻璃
    dark: M({ roughness: 0.9, metalness: 0.0 }),
    glass: M({ roughness: 0.15, metalness: 0.5 }),
    // 铁丝网 —— 透明贴片
    mesh: M({ alphaMap: link, transparent: true, alphaTest: 0.42, side: THREE.DoubleSide,
              roughness: 0.7, metalness: 0.6, depthWrite: true }),
    // 迷彩网屏
    net: M({ alphaMap: net, transparent: true, alphaTest: 0.4, side: THREE.DoubleSide,
             roughness: 0.95, metalness: 0.0 }),
  };
  // 薄壳构件（天线反射面、旋翼桨叶）双面渲染，免得车削/挤出的朝向出错
  materials.concreteDS = M({ ...concrete, roughness: 1.0, metalness: 0.0,
    side: THREE.DoubleSide, normalScale: new THREE.Vector2(0.5, 0.5) });
  materials.paintDS = M({ ...dust, roughness: 0.7, metalness: 0.2, side: THREE.DoubleSide });
  materials.mesh.alphaMap.repeat.set(1, 1);

  return { materials, sand, concrete };
}

export { fbmField, streakField, makeCanvas, tex, normalCanvas };
