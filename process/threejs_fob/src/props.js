// 场站构件库：防爆墙、铁丝网、岗楼、卫星天线、桅杆、集装箱、
// 货堆、油桶、灯杆、雨棚、HESCO 掩体……全部程序化生成。
import * as THREE from 'three';
import { TAU } from './rng.js';

const _c = new THREE.Color();

// 色调抖动：同一批构件不会看起来像复制粘贴
export function jit(rng, hex, amt = 0.07, sat = 0.35) {
  _c.set(hex);
  const hsl = { h: 0, s: 0, l: 0 };
  _c.getHSL(hsl);
  return new THREE.Color().setHSL(
    hsl.h + rng.j(0.012),
    Math.max(0, hsl.s * (1 + rng.j(sat))),
    Math.max(0.02, hsl.l * (1 + rng.j(amt)))
  );
}

export const PAL = {
  // 混凝土整体偏冷灰，才压得住满地的暖沙色
  concrete: [0x9d9c94, 0xa9a89e, 0x918f87, 0xa3a196, 0xb0aea2, 0x8b8a83],
  concreteWarm: [0xa39a86, 0x9b9280, 0xada48d, 0x948c7b],
  // 屋面：深灰铁皮 / 沥青，参考图里屋顶明显比墙暗一大截
  roof: [0x54524b, 0x5f5d55, 0x494740, 0x666359, 0x403f3a],
  wall: 0xa9a291,
  steel: 0x7f8589,
  darkSteel: 0x45484b,
  rust: 0x6d4227,
  olive: [0x3a3d2d, 0x333627, 0x434732, 0x2e3125, 0x4a4e37],
  tanVeh: [0x7d7150, 0x877a57, 0x726749, 0x8f8261],
  rubber: 0x141413,
  glass: 0x1b2124,
  wood: 0x8e6c40,
  canvasTan: [0x776c48, 0x827751, 0x6b6141, 0x8b8058],
  containerCols: [0x6b3e2b, 0x35505a, 0x7a7359, 0x3d4535, 0x5c5a4f, 0x77592f],
  drum: [0x3d4535, 0x2f3d4a, 0x6d4227, 0x5d5945],
};

// 方向角：让局部 +X 沿着 (dx,dz)
export const alignX = (dx, dz) => Math.atan2(-dz, dx);

/* ============================ 防爆墙 (T-wall) ============================ */

export function tWallRun(b, rng, x0, z0, x1, z1, o = {}) {
  const h = o.h ?? 3.7;
  const lod = o.lod ?? 0;
  const segLen = o.seg ?? (lod === 0 ? 1.55 : lod === 1 ? 3.1 : 12);
  const dx = x1 - x0, dz = z1 - z0;
  const len = Math.hypot(dx, dz);
  if (len < 0.5) return;
  const n = Math.max(1, Math.round(len / segLen));
  if (lod >= 2) {
    // 远景只留一道整墙的剪影
    const ry2 = alignX(dx, dz);
    b.box('concrete', len, h, 0.34, (x0 + x1) / 2, h / 2, (z0 + z1) / 2,
      { ry: ry2, color: jit(rng, rng.pick(PAL.concrete), 0.07), tile: 2.2 });
    b.box('concrete', len, 0.13, 0.44, (x0 + x1) / 2, h + 0.06, (z0 + z1) / 2,
      { ry: ry2, color: jit(rng, 0xb5aea0, 0.06), tile: 2.2 });
    return;
  }
  const sl = len / n;
  const ry = alignX(dx, dz);
  const gap = o.gap;             // [t0,t1] 归一化开口（大门）
  for (let i = 0; i < n; i++) {
    const t = (i + 0.5) / n;
    if (gap && t > gap[0] && t < gap[1]) continue;
    const x = x0 + dx * t, z = z0 + dz * t;
    const col = jit(rng, rng.pick(PAL.concrete), 0.09);
    const hh = h * (1 + rng.j(0.012));
    const yr = ry + rng.j(0.012);
    if (lod === 0) b.box('concrete', sl * 0.99, 0.22, 0.92, x, 0.11, z, { ry: yr, color: col, tile: 3.6 });
    b.box('concrete', sl * 0.965, hh, 0.30, x, 0.22 + hh / 2, z,
      { ry: yr, color: col, tile: 3.6 });
    b.box('concrete', sl * 0.99, 0.13, 0.42, x, 0.22 + hh + 0.06, z,
      { ry: yr, color: jit(rng, 0xb0aca2, 0.07), tile: 3.6 });
  }
}

// 小号混凝土块 / 路障
export function jersey(b, rng, x, z, ry, striped) {
  const col = jit(rng, 0xb2ab9a, 0.08);
  b.save().at(x, 0, z).ry(ry);
  b.prism('concrete', [[-0.42, 0], [0.42, 0], [0.22, 0.36], [0.14, 0.95], [-0.14, 0.95], [-0.22, 0.36]],
    3.0, 0, 0, 0, { ry: Math.PI / 2, color: col });
  if (striped) {
    for (let i = 0; i < 4; i++) {
      b.box('concrete', 0.36, 0.62, 0.02, -1.1 + i * 0.74, 0.55, 0.18,
        { color: i % 2 ? 0xa83c2c : 0xd8d2c4 });
      b.box('concrete', 0.36, 0.62, 0.02, -1.1 + i * 0.74, 0.55, -0.18,
        { color: i % 2 ? 0xa83c2c : 0xd8d2c4 });
    }
  }
  b.restore();
}

/* ============================ 铁丝网 + 刀片刺网 ============================ */

export function razorCoil(b, x0, z0, x1, z1, y, r, pitch = 0.62) {
  const dx = x1 - x0, dz = z1 - z0;
  const len = Math.hypot(dx, dz);
  if (len < 1) return;
  const ux = dx / len, uz = dz / len;
  const nx = -uz, nz = ux;
  const turns = len / pitch;
  const steps = Math.ceil(turns * 6);
  const pts = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const a = t * turns * TAU;
    const ca = Math.cos(a), sa = Math.sin(a);
    pts.push(new THREE.Vector3(
      x0 + dx * t + nx * ca * r,
      y + sa * r,
      z0 + dz * t + nz * ca * r));
  }
  const curve = new THREE.CatmullRomCurve3(pts);
  b.tube('metal', curve, 0.028, steps, 4, { color: 0x9fa3a0 });
}

export function chainFence(b, rng, x0, z0, x1, z1, o = {}) {
  const h = o.h ?? 2.6;
  const dx = x1 - x0, dz = z1 - z0;
  const len = Math.hypot(dx, dz);
  if (len < 1) return;
  const ry = alignX(dx, dz);
  const cxm = (x0 + x1) / 2, czm = (z0 + z1) / 2;

  // 网片
  b.plane('mesh', len, h, cxm, h / 2 + 0.05, czm,
    { ry, color: 0x8d9290, tile: 0.55 });
  // 立柱
  const n = Math.max(1, Math.round(len / 3.0));
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    const x = x0 + dx * t, z = z0 + dz * t;
    b.cyl('metal', 0.055, 0.062, h + 0.28, 6, x, 0, z,
      { base: true, color: jit(rng, 0x8b8f8c, 0.1) });
    // 顶部外挑臂
    if (o.razor !== false) {
      b.strut('metal', [x, h + 0.05, z], [x + Math.sin(ry) * 0 - 0, h + 0.55, z], 0.03);
    }
  }
  // 上下横杆
  b.cyl('metal', 0.04, 0.04, len, 5, cxm, h + 0.05, czm,
    { ry, rz: Math.PI / 2, color: 0x8b8f8c });
  b.cyl('metal', 0.035, 0.035, len, 5, cxm, 0.18, czm,
    { ry, rz: Math.PI / 2, color: 0x8b8f8c });
  if (o.razor !== false) {
    razorCoil(b, x0, z0, x1, z1, h + 0.42, 0.33, o.pitch ?? 0.62);
  }
}

/* ============================ 岗楼 ============================ */

export function guardTower(b, rng, x, z, ry) {
  const H = 5.4, S = 1.45;
  const col = jit(rng, 0x9c927d, 0.08);
  b.save().at(x, 0, z).ry(ry);

  // 四条带收分的立柱 + 剪刀撑
  const legs = [[-S, -S], [S, -S], [S, S], [-S, S]];
  const top = legs.map(([a, c]) => [a * 0.78, H, c * 0.78]);
  for (let i = 0; i < 4; i++) {
    b.strut('wood', [legs[i][0], 0, legs[i][1]], top[i], 0.115,
      { square: true, color: jit(rng, 0x8a7248, 0.1) });
  }
  for (let i = 0; i < 4; i++) {
    const j = (i + 1) % 4;
    for (let k = 0; k < 2; k++) {
      const y0 = 0.4 + k * 2.3, y1 = y0 + 2.3;
      const f0 = y0 / H, f1 = y1 / H;
      const p = (idx, f, y) => [legs[idx][0] * (1 - 0.22 * f), y, legs[idx][1] * (1 - 0.22 * f)];
      b.strut('wood', p(i, f0, y0), p(j, f1, y1), 0.055, { square: true, color: 0x7d6841 });
      b.strut('wood', p(j, f0, y0), p(i, f1, y1), 0.055, { square: true, color: 0x7d6841 });
    }
  }
  // 平台
  b.box('wood', 3.5, 0.18, 3.5, 0, H + 0.09, 0, { color: jit(rng, 0x8f7a4e, 0.08), tile: 1.2 });
  // 岗亭
  const cw = 2.5, cd = 2.2, ch = 2.15, cy = H + 0.18;
  const wallCol = jit(rng, 0xa39a83, 0.07);
  b.box('concrete', cw, ch, 0.14, 0, cy + ch / 2, -cd / 2, { color: wallCol, tile: 1.6 });
  b.box('concrete', cw, ch, 0.14, 0, cy + ch / 2, cd / 2, { color: wallCol, tile: 1.6 });
  b.box('concrete', 0.14, ch, cd, -cw / 2, cy + ch / 2, 0, { color: wallCol, tile: 1.6 });
  b.box('concrete', 0.14, ch, cd, cw / 2, cy + ch / 2, 0, { color: wallCol, tile: 1.6 });
  // 观察窗
  const wy = cy + ch * 0.62;
  b.box('dark', cw * 0.78, 0.72, 0.06, 0, wy, -cd / 2 - 0.03, { color: 0x1c2124 });
  b.box('dark', cw * 0.78, 0.72, 0.06, 0, wy, cd / 2 + 0.03, { color: 0x1c2124 });
  b.box('dark', 0.06, 0.72, cd * 0.72, -cw / 2 - 0.03, wy, 0, { color: 0x1c2124 });
  b.box('dark', 0.06, 0.72, cd * 0.72, cw / 2 + 0.03, wy, 0, { color: 0x1c2124 });
  // 铁皮顶
  b.box('corr', cw + 0.7, 0.1, cd + 0.7, 0, cy + ch + 0.1, 0,
    { rz: 0.05, color: jit(rng, rng.pick(PAL.roof), 0.12), tile: 1.6 });
  // 栏杆
  railing(b, -1.75, -1.75, 1.75, -1.75, H + 0.18, 0.95);
  railing(b, -1.75, 1.75, 1.75, 1.75, H + 0.18, 0.95);
  railing(b, 1.75, -1.75, 1.75, 1.75, H + 0.18, 0.95);
  // 沙袋围挡
  sandbags(b, rng, -1.6, -1.6, 1.6, -1.6, H + 0.18, 2);
  // 楼梯
  stairs(b, rng, 2.2, 0, 0, H + 0.18, 1.15, 6.2);
  b.restore();
}

export function railing(b, x0, z0, x1, z1, y, h) {
  const dx = x1 - x0, dz = z1 - z0;
  const len = Math.hypot(dx, dz);
  const ry = alignX(dx, dz);
  const n = Math.max(1, Math.round(len / 1.1));
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    b.cyl('metal', 0.028, 0.028, h, 5, x0 + dx * t, y, z0 + dz * t,
      { base: true, color: 0x8a8e8b });
  }
  b.cyl('metal', 0.032, 0.032, len, 5, (x0 + x1) / 2, y + h, (z0 + z1) / 2,
    { ry, rz: Math.PI / 2, color: 0x8a8e8b });
  b.cyl('metal', 0.026, 0.026, len, 5, (x0 + x1) / 2, y + h * 0.52, (z0 + z1) / 2,
    { ry, rz: Math.PI / 2, color: 0x8a8e8b });
}

// 直跑楼梯：从地面到 (0,topY,0) 处的平台，沿 +X 方向下行
export function stairs(b, rng, x, z, ry, topY, w, run) {
  b.save().at(x, 0, z).ry(ry);
  const steps = Math.max(4, Math.round(topY / 0.21));
  const dx = run / steps, dy = topY / steps;
  for (let i = 0; i < steps; i++) {
    b.box('wood', dx * 1.05, 0.055, w, dx * (i + 0.5), dy * (i + 1), 0,
      { color: jit(rng, 0x8a7248, 0.08), tile: 1 });
    if (i % 2 === 0) {
      b.box('wood', 0.05, dy, w * 0.94, dx * i, dy * (i + 0.5), 0, { color: 0x7a6540 });
    }
  }
  // 斜梁
  for (const s of [-1, 1]) {
    b.strut('wood', [0, 0.05, s * w / 2], [run, topY, s * w / 2], 0.075,
      { square: true, color: 0x7d6841 });
    b.strut('metal', [0.2, 1.0, s * w / 2], [run, topY + 1.0, s * w / 2], 0.028);
    for (let i = 0; i <= 4; i++) {
      const t = i / 4;
      b.cyl('metal', 0.024, 0.024, 1.0, 5, run * t, 0.05 + topY * t, s * w / 2,
        { base: true, color: 0x8a8e8b });
    }
  }
  b.restore();
}

export function sandbags(b, rng, x0, z0, x1, z1, y, rows) {
  const dx = x1 - x0, dz = z1 - z0;
  const len = Math.hypot(dx, dz);
  const ry = alignX(dx, dz);
  const n = Math.max(1, Math.round(len / 0.42));
  for (let r = 0; r < rows; r++) {
    for (let i = 0; i < n; i++) {
      const t = (i + (r % 2 ? 0.5 : 0)) / n;
      if (t > 1) continue;
      b.sphere('hesco', 0.24, x0 + dx * t, y + 0.15 + r * 0.22, z0 + dz * t, {
        ry: ry + rng.j(0.2), seg: 7, seg2: 5,
        color: jit(rng, 0x968a66, 0.12),
      });
      // 压扁
    }
  }
}

/* ============================ HESCO 掩体 ============================ */

export function hescoRun(b, rng, x0, z0, x1, z1, o = {}) {
  const cell = 1.06, levels = o.levels ?? 1, depth = o.depth ?? 1;
  const dx = x1 - x0, dz = z1 - z0;
  const len = Math.hypot(dx, dz);
  const ry = alignX(dx, dz);
  const nx = -dz / len, nz = dx / len;
  const n = Math.max(1, Math.round(len / cell));
  for (let lv = 0; lv < levels; lv++) {
    const cs = cell * (lv === 0 ? 1 : 0.82);
    const y0 = lv === 0 ? 0 : cell;
    for (let d = 0; d < (lv === 0 ? depth : Math.max(1, depth - 1)); d++) {
      for (let i = 0; i < n; i++) {
        const t = (i + 0.5) / n;
        const off = (d - (depth - 1) / 2) * cell;
        b.box('hesco', len / n * 0.99, cs, cell * 0.99,
          x0 + dx * t + nx * off, y0 + cs / 2, z0 + dz * t + nz * off,
          { ry: ry + rng.j(0.01), color: jit(rng, 0xa2946c, 0.11), tile: 1.06 });
      }
    }
  }
}

// 成堆的 HESCO / 补给方块（参考图左侧那片浅色方垛）
export function supplyStack(b, rng, x, z, ry, cols, rows, layers) {
  b.save().at(x, 0, z).ry(ry);
  const u = 1.15;
  pallet(b, rng, 0, 0, 0, cols * u, rows * u);
  for (let l = 0; l < layers; l++) {
    for (let i = 0; i < cols; i++) {
      for (let j = 0; j < rows; j++) {
        if (l === layers - 1 && rng.chance(0.22)) continue;
        b.box('hesco', u * 0.95, u * 0.86, u * 0.95,
          (i - (cols - 1) / 2) * u + rng.j(0.04),
          0.14 + l * 0.88 + 0.43,
          (j - (rows - 1) / 2) * u + rng.j(0.04),
          { ry: rng.j(0.03), color: jit(rng, 0xada07a, 0.1), tile: 1.15 });
      }
    }
  }
  b.restore();
}

export function pallet(b, rng, x, z, ry, w = 1.2, d = 1.0) {
  b.save().at(x, 0, z).ry(ry);
  for (let i = 0; i < 6; i++) {
    b.box('wood', w, 0.025, d / 8, 0, 0.125, (i / 5 - 0.5) * d,
      { color: jit(rng, 0x9a7a4c, 0.1), tile: 0.6 });
  }
  for (const s of [-0.4, 0, 0.4]) {
    b.box('wood', w * 0.1, 0.1, d, s * w, 0.06, 0, { color: 0x8a6c42 });
  }
  b.restore();
}

/* ============================ 货物 ============================ */

export function crateStack(b, rng, x, z, ry) {
  b.save().at(x, 0, z).ry(ry);
  pallet(b, rng, 0, 0, 0, 1.5, 1.2);
  const n = rng.int(2, 5);
  let y = 0.15;
  for (let i = 0; i < n; i++) {
    const w = rng.range(0.9, 1.45), h = rng.range(0.34, 0.6), d = rng.range(0.6, 1.05);
    const ammo = rng.chance(0.45);
    b.box(ammo ? 'paint' : 'wood', w, h, d, rng.j(0.12), y + h / 2, rng.j(0.1),
      { ry: rng.j(0.14), color: ammo ? jit(rng, rng.pick(PAL.olive), 0.1) : jit(rng, PAL.wood, 0.12), tile: 0.8 });
    // 加固条
    b.box('metal', w * 1.01, 0.035, 0.05, rng.j(0.12), y + h * 0.75, rng.j(0.1) + d / 2,
      { ry: rng.j(0.14), color: 0x6a6d68 });
    y += h + 0.01;
  }
  b.restore();
}

export function container(b, rng, x, z, ry, len = 6.06, stackOn = 0) {
  const H = 2.59, W = 2.44;
  const col = jit(rng, rng.pick(PAL.containerCols), 0.13, 0.25);
  b.save().at(x, stackOn, z).ry(ry);
  // tile 决定波纹间距：2.2 m/贴图 × 8 道 ≈ 27 cm 一道，接近真实集装箱
  b.box('corr', len, H, W, 0, H / 2, 0, { color: col, tile: 2.2 });
  // 顶板稍亮
  b.box('corr', len * 0.995, 0.06, W * 0.995, 0, H, 0, { color: jit(rng, 0x8d8677, 0.1), tile: 2.2 });
  // 角件
  for (const sx of [-1, 1]) for (const sy of [0, 1]) for (const sz of [-1, 1]) {
    b.box('metal', 0.28, 0.24, 0.24, sx * (len / 2 - 0.14), sy * (H - 0.12) + 0.12, sz * (W / 2 - 0.12),
      { color: 0x565a58 });
  }
  // 门端
  b.box('dark', 0.05, H * 0.9, W * 0.94, len / 2 + 0.03, H / 2, 0, { color: 0x2e2c28 });
  for (const o of [-0.62, -0.2, 0.2, 0.62]) {
    b.cyl('metal', 0.035, 0.035, H * 0.86, 6, len / 2 + 0.07, H / 2, o, { color: 0x6f7370 });
  }
  b.restore();
}

export function drum(b, rng, x, y, z, ry) {
  const col = jit(rng, rng.pick(PAL.drum), 0.14, 0.3);
  b.cyl('paint', 0.295, 0.295, 0.86, 12, x, y, z, { base: true, color: col, tile: 0.9 });
  b.torus('metal', 0.3, 0.028, x, y + 0.28, z, { rx: Math.PI / 2, seg: 12, color: col });
  b.torus('metal', 0.3, 0.028, x, y + 0.58, z, { rx: Math.PI / 2, seg: 12, color: col });
  b.cyl('metal', 0.3, 0.3, 0.045, 12, x, y + 0.86, z, { color: jit(rng, 0x8f9490, 0.1) });
}

export function drumCluster(b, rng, x, z, n) {
  const cols = Math.ceil(Math.sqrt(n));
  const ry = rng.range(0, TAU);
  b.save().at(x, 0, z).ry(ry);
  for (let i = 0; i < n; i++) {
    const cx = (i % cols - (cols - 1) / 2) * 0.68 + rng.j(0.06);
    const cz = (Math.floor(i / cols) - (cols - 1) / 2) * 0.68 + rng.j(0.06);
    const two = rng.chance(0.25);
    drum(b, rng, cx, 0, cz, 0);
    if (two) drum(b, rng, cx, 0.87, cz, 0);
  }
  b.restore();
}

/* ============================ 卫星天线 ============================ */

export function satDish(b, rng, x, z, D, az, el) {
  const R = D / 2;
  const f = 0.31 * D;      // f/D 小一点，碟面更深，从空中看得出弧度
  // 反射面比周围都亮 —— 参考图里两口大天线是画面最白的东西
  const col = jit(rng, 0xece9e0, 0.03, 0.2);
  const hubY = R * 0.72 + 1.6;

  b.save().at(x, 0, z);
  // 基础
  b.box('concrete', D * 0.9, 0.22, D * 0.9, 0, 0.11, 0,
    { color: jit(rng, 0xa8a191, 0.06), tile: 2 });
  b.ry(az);
  b.cyl('concrete', 0.62, 0.78, 1.55, 12, 0, 0.2, 0,
    { base: true, color: jit(rng, 0x9b9484, 0.06), tile: 1.6 });
  b.cyl('metal', 0.42, 0.42, 0.5, 12, 0, 1.75, 0, { base: true, color: 0x6d716e });

  // 俯仰叉架
  for (const s of [-1, 1]) {
    b.box('metal', 0.16, 1.5, 0.5, s * R * 0.52, 1.9 + 0.55, 0, { color: 0x74787a });
  }
  b.cyl('metal', 0.16, 0.16, R * 1.1, 10, 0, hubY, 0,
    { rz: Math.PI / 2, color: 0x64686a });

  // 反射面
  b.at(0, hubY, 0).rx(Math.PI / 2 - el);
  const pts = [];
  const NS = 14;
  const yy = (r) => (r * r) / (4 * f);
  for (let i = 0; i <= NS; i++) {
    const r = 0.28 + (R - 0.28) * (i / NS);
    pts.push(new THREE.Vector2(r, yy(r)));
  }
  pts.push(new THREE.Vector2(R + 0.045, yy(R) + 0.06));
  for (let i = NS; i >= 0; i--) {
    const r = 0.28 + (R - 0.28) * (i / NS);
    pts.push(new THREE.Vector2(r, yy(r) - 0.075));
  }
  b.lathe('paintDS', pts, 40, 0, 0, 0, { color: col });
  // 背筋
  for (let i = 0; i < 10; i++) {
    const a = (i / 10) * TAU;
    b.box('metal', R * 0.92, 0.16, 0.05,
      Math.cos(a) * R * 0.5, -0.22, Math.sin(a) * R * 0.5,
      { ry: -a, color: 0x7c807e });
  }
  b.cyl('metal', 0.42, 0.52, 0.55, 12, 0, -0.35, 0, { color: 0x6d716e });
  // 馈源三脚架
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * TAU + 0.4;
    b.strut('metal', [Math.cos(a) * R * 0.94, yy(R * 0.94) - 0.03, Math.sin(a) * R * 0.94],
      [0, f * 0.98, 0], 0.038, { color: 0x8b8f8c });
  }
  b.cyl('metal', 0.17, 0.24, 0.55, 10, 0, f * 0.92, 0, { color: 0x7e827f });
  b.cone('metal', 0.2, 0.3, 10, 0, f * 0.62, 0, { color: 0x6a6e6b });
  b.restore();
}

// 屋顶 / 地面小天线
export function smallDish(b, rng, x, y, z, D, az, el) {
  const R = D / 2, f = 0.4 * D;
  b.save().at(x, y, z).ry(az);
  b.cyl('metal', 0.09, 0.13, R * 0.9, 8, 0, 0, 0, { base: true, color: 0x7c807e });
  b.at(0, R * 0.9, 0).rx(Math.PI / 2 - el);
  const pts = [];
  const yy = (r) => (r * r) / (4 * f);
  for (let i = 0; i <= 8; i++) { const r = 0.1 + (R - 0.1) * i / 8; pts.push(new THREE.Vector2(r, yy(r))); }
  for (let i = 8; i >= 0; i--) { const r = 0.1 + (R - 0.1) * i / 8; pts.push(new THREE.Vector2(r, yy(r) - 0.05)); }
  b.lathe('paintDS', pts, 22, 0, 0, 0, { color: jit(rng, 0xdcd8cc, 0.06) });
  b.strut('metal', [R * 0.7, yy(R * 0.7), 0], [0, f, 0], 0.025);
  b.strut('metal', [-R * 0.5, yy(R * 0.5), R * 0.5], [0, f, 0], 0.025);
  b.strut('metal', [-R * 0.5, yy(R * 0.5), -R * 0.5], [0, f, 0], 0.025);
  b.cyl('metal', 0.08, 0.1, 0.26, 8, 0, f, 0, { color: 0x6a6e6b });
  b.restore();
}

/* ============================ 桁架桅杆 ============================ */

export function latticeMast(b, rng, x, z, H) {
  const bays = Math.round(H / 2.1);
  const w0 = 1.5, w1 = 0.62;
  const corner = (i, bay) => {
    const t = bay / bays;
    const w = w0 + (w1 - w0) * t;
    const a = (i / 4) * TAU + Math.PI / 4;
    return [x + Math.cos(a) * w, (H * t), z + Math.sin(a) * w];
  };
  b.box('concrete', 4.2, 0.25, 4.2, x, 0.12, z, { color: jit(rng, 0xa59e8e, 0.06), tile: 2 });
  for (let bay = 0; bay < bays; bay++) {
    for (let i = 0; i < 4; i++) {
      b.strut('metal', corner(i, bay), corner(i, bay + 1), 0.055, { color: 0x8d918e });
      const j = (i + 1) % 4;
      b.strut('metal', corner(i, bay + 1), corner(j, bay + 1), 0.035, { color: 0x828683 });
      b.strut('metal', corner(i, bay), corner(j, bay + 1), 0.028, { color: 0x828683 });
      if (bay % 2 === 0) b.strut('metal', corner(j, bay), corner(i, bay + 1), 0.028, { color: 0x828683 });
    }
  }
  // 微波天线鼓
  for (let i = 0; i < 3; i++) {
    const y = H * (0.55 + i * 0.14);
    const a = rng.range(0, TAU);
    const r = 0.5 - i * 0.08;
    b.cyl('paintDS', r, r, 0.42, 14,
      x + Math.cos(a) * (0.9 + r), y, z + Math.sin(a) * (0.9 + r),
      { rz: Math.PI / 2, ry: -a, color: jit(rng, 0xbdb8aa, 0.06) });
  }
  // 板状天线
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * TAU + 0.6;
    b.box('paint', 0.16, 1.1, 0.34, x + Math.cos(a) * 1.0, H * 0.86, z + Math.sin(a) * 1.0,
      { ry: -a, color: 0xa9a496 });
  }
  b.cyl('metal', 0.03, 0.03, 2.4, 5, x, H, z, { base: true, color: 0x8d918e });
  b.sphere('dark', 0.13, x, H + 2.5, z, { color: 0x6a1f1a, seg: 8, seg2: 6 });
  // 拉线
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * TAU + 0.9;
    b.strut('metal', [x + Math.cos(a) * 0.8, H * 0.78, z + Math.sin(a) * 0.8],
      [x + Math.cos(a) * H * 0.5, 0, z + Math.sin(a) * H * 0.5], 0.016, { color: 0x6f736f });
  }
}

/* ============================ 灯杆 / 雨棚 / 网屏 ============================ */

export function lightPole(b, rng, x, z, ry, h = 8.5) {
  b.save().at(x, 0, z).ry(ry);
  b.box('concrete', 0.7, 0.35, 0.7, 0, 0.17, 0, { color: 0xa39c8c, tile: 1 });
  b.cyl('metal', 0.075, 0.115, h, 8, 0, 0.3, 0, { base: true, color: jit(rng, 0x8e9390, 0.08) });
  b.box('metal', 1.7, 0.09, 0.09, 0, h + 0.28, 0, { color: 0x838784 });
  for (const s of [-1, 1]) {
    b.save().at(s * 0.72, h + 0.2, 0).rx(0.42);
    b.box('metal', 0.5, 0.2, 0.42, 0, 0, 0, { color: 0x878b88 });
    b.box('glass', 0.42, 0.05, 0.34, 0, -0.11, 0, { color: 0xb9b19a });
    b.restore();
  }
  b.restore();
}

export function canopy(b, rng, x, z, w, d, ry, o = {}) {
  const h = o.h ?? 4.4;
  b.save().at(x, 0, z).ry(ry);
  const nx = Math.max(2, Math.round(w / 5)), nz = 2;
  for (let i = 0; i <= nx; i++) {
    for (let j = 0; j < nz; j++) {
      const px = (i / nx - 0.5) * w, pz = (j - 0.5) * d;
      b.box('metal', 0.19, h, 0.19, px, h / 2, pz, { color: jit(rng, 0x7d8178, 0.1) });
      b.box('concrete', 0.5, 0.16, 0.5, px, 0.08, pz, { color: 0xa39c8c });
    }
  }
  for (let j = 0; j < nz; j++) {
    b.box('metal', w * 1.02, 0.22, 0.14, 0, h + 0.11, (j - 0.5) * d, { color: 0x7d8178 });
  }
  // 波纹顶（单坡）
  b.save().at(0, h + 0.3, 0).rx(-0.07);
  b.box('corr', w * 1.1, 0.11, d * 1.25, 0, 0, 0,
    { color: jit(rng, rng.pick(PAL.roof), 0.12), tile: 1.1 });
  b.restore();
  if (o.side) {
    b.box('corr', w * 1.05, h * 0.55, 0.07, 0, h * 0.6, -d / 2 - 0.1,
      { color: jit(rng, 0x7c7970, 0.12), tile: 1.1 });
  }
  b.restore();
}

// 迷彩网 / 挡视线网屏
export function netScreen(b, rng, x, z, w, h, ry) {
  b.save().at(x, 0, z).ry(ry);
  const n = Math.max(2, Math.round(w / 3.2));
  for (let i = 0; i <= n; i++) {
    b.cyl('metal', 0.055, 0.065, h + 0.25, 6, (i / n - 0.5) * w, 0, 0,
      { base: true, color: 0x74786f });
  }
  b.box('metal', w, 0.09, 0.09, 0, h + 0.2, 0, { color: 0x74786f });
  b.plane('net', w, h, 0, h / 2 + 0.1, 0, { color: jit(rng, 0x585c46, 0.14), tile: 1.6 });
  b.plane('net', w, h, 0, h / 2 + 0.1, 0.06, { color: jit(rng, 0x4a4e3a, 0.14), tile: 1.9 });
  b.restore();
}

/* ============================ 屋顶设备 ============================ */

export function acUnit(b, rng, x, y, z, ry) {
  b.save().at(x, y, z).ry(ry);
  b.box('paint', 1.15, 0.72, 0.9, 0, 0.36, 0, { color: jit(rng, 0x9d9c94, 0.08), tile: 1 });
  b.box('dark', 0.95, 0.5, 0.05, 0, 0.4, 0.47, { color: 0x40443f });
  b.cyl('metal', 0.34, 0.34, 0.07, 12, 0, 0.75, 0, { color: 0x84888a });
  b.restore();
}

export function waterTank(b, rng, x, y, z) {
  b.cyl('metal', 1.05, 1.05, 1.5, 14, x, y + 1.0, z, { color: jit(rng, 0x9a9e9b, 0.08), tile: 1.4 });
  b.cyl('metal', 0.7, 1.05, 0.35, 14, x, y + 1.92, z, { color: 0x9a9e9b });
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * TAU + 0.7;
    b.cyl('metal', 0.06, 0.06, 1.0, 5, x + Math.cos(a) * 0.85, y, z + Math.sin(a) * 0.85,
      { base: true, color: 0x7c807e });
  }
}

export function whip(b, rng, x, y, z, h) {
  b.cyl('metal', 0.018, 0.03, h, 5, x, y, z, { base: true, color: 0x8a8e8b, rz: rng.j(0.03) });
  if (rng.chance(0.5)) {
    for (const s of [-1, 1]) {
      b.strut('metal', [x, y + h * 0.35, z], [x + s * h * 0.2, y, z + s * h * 0.15], 0.01);
    }
  }
}

export function ventPipe(b, rng, x, y, z) {
  b.cyl('metal', 0.11, 0.11, 0.9, 8, x, y, z, { base: true, color: 0x83878a });
  b.cyl('metal', 0.17, 0.17, 0.14, 8, x, y + 0.95, z, { color: 0x83878a });
}

/* ============================ 碎石 / 杂物 ============================ */

export function debris(b, rng, x, z, r, n) {
  for (let i = 0; i < n; i++) {
    const a = rng.range(0, TAU), d = Math.sqrt(rng()) * r;
    const s = rng.range(0.14, 0.5);
    b.emit('concrete', new THREE.IcosahedronGeometry(s, 0),
      {
        x: x + Math.cos(a) * d, y: s * 0.35, z: z + Math.sin(a) * d,
        rx: rng.range(0, TAU), ry: rng.range(0, TAU),
        color: jit(rng, 0x9c937f, 0.14),
      });
  }
}

export function tyreStack(b, rng, x, z) {
  const n = rng.int(2, 5);
  for (let i = 0; i < n; i++) {
    b.torus('dark', 0.42, 0.17, x + rng.j(0.05), 0.17 + i * 0.3, z + rng.j(0.05),
      { rx: Math.PI / 2, seg: 14, seg2: 7, color: jit(rng, 0x232221, 0.2) });
  }
}
