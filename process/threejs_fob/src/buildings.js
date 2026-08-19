// 建筑：平顶混凝土楼、坡顶铁皮库房、集装箱营房、小岗亭。
import { TAU } from './rng.js';
import { jit, PAL, acUnit, waterTank, whip, ventPipe, smallDish, railing, stairs } from './props.js';

/* ---------- 窗 ---------- */
function windowsOnFace(b, rng, { w, y, faceZ, dirZ, count, lit = 0.0, frame = true }) {
  if (count < 1) return;
  const gap = w / count;
  for (let i = 0; i < count; i++) {
    const x = (i + 0.5) * gap - w / 2;
    const ww = Math.min(1.45, gap * 0.58), wh = 1.55;
    // 洞口做得深一点，斜射光下自带一条阴影，远看才有“窗格”的密度
    b.box('dark', ww, wh, 0.34, x, y, faceZ - dirZ * 0.19,
      { color: rng.chance(lit) ? 0x4e4636 : 0x0e1113 });
    // 窗套必须是一圈边框，不能是整块板 —— 整块板会把窗洞盖住，
    // 远看就变成一片浅色方格而不是黑洞。
    if (frame) {
      const fw = 0.17, fz = faceZ + dirZ * 0.03;
      const fc = jit(rng, 0xb8b2a4, 0.05);
      b.box('concrete', ww + fw * 2, fw, 0.1, x, y + wh / 2 + fw / 2, fz, { color: fc });
      b.box('concrete', ww + fw * 2, fw, 0.1, x, y - wh / 2 - fw / 2, fz, { color: fc });
      b.box('concrete', fw, wh, 0.1, x - ww / 2 - fw / 2, y, fz, { color: fc });
      b.box('concrete', fw, wh, 0.1, x + ww / 2 + fw / 2, y, fz, { color: fc });
    }
    // 窗台
    b.box('concrete', ww + 0.42, 0.1, 0.2, x, y - wh / 2 - 0.26, faceZ + dirZ * 0.07,
      { color: jit(rng, 0xb2aa99, 0.05) });
    if (rng.chance(0.16)) {
      b.box('paint', 0.62, 0.45, 0.4, x + 0.3, y - wh / 2 - 0.5, faceZ + dirZ * 0.22,
        { color: jit(rng, 0x8e8c84, 0.08) });
    }
  }
}

/* ---------- 屋面设备 ---------- */
function roofClutter(b, rng, w, d, H, lod) {
  if (lod > 1) return;
  const n = rng.int(1, 3);
  for (let i = 0; i < n; i++) {
    acUnit(b, rng, rng.range(-w / 2 + 1.6, w / 2 - 1.6), H + 0.15,
      rng.range(-d / 2 + 1.6, d / 2 - 1.6), rng.range(0, TAU));
  }
  if (rng.chance(0.45)) {
    waterTank(b, rng, rng.range(-w / 2 + 2, w / 2 - 2), H + 0.15, rng.range(-d / 2 + 2, d / 2 - 2));
  }
  for (let i = 0, m = rng.int(1, 4); i < m; i++) {
    ventPipe(b, rng, rng.range(-w / 2 + 1, w / 2 - 1), H + 0.15, rng.range(-d / 2 + 1, d / 2 - 1));
  }
  for (let i = 0, m = rng.int(1, 3); i < m; i++) {
    whip(b, rng, rng.range(-w / 2 + 1, w / 2 - 1), H + 0.15,
      rng.range(-d / 2 + 1, d / 2 - 1), rng.range(2.5, 6.5));
  }
  if (rng.chance(0.4)) {
    smallDish(b, rng, rng.range(-w / 2 + 2, w / 2 - 2), H + 0.15,
      rng.range(-d / 2 + 2, d / 2 - 2), rng.range(1.1, 1.9), rng.range(0, TAU), rng.range(0.5, 1.0));
  }
  // 楼梯出口小间
  if (rng.chance(0.5)) {
    const sw = rng.range(2.2, 3.2), sd = rng.range(2.0, 2.8), sh = rng.range(2.2, 2.8);
    const sx = rng.range(-w / 2 + sw, w / 2 - sw), sz = rng.range(-d / 2 + sd, d / 2 - sd);
    b.box('concrete', sw, sh, sd, sx, H + 0.15 + sh / 2, sz,
      { color: jit(rng, 0xaca595, 0.06), tile: 2.4 });
    b.box('corr', sw + 0.3, 0.1, sd + 0.3, sx, H + 0.15 + sh + 0.05, sz,
      { color: jit(rng, rng.pick(PAL.roof), 0.12), tile: 1.2 });
  }
}

/* ---------- 平顶楼 ---------- */
export function flatBuilding(b, rng, x, z, ry, o = {}) {
  const w = o.w ?? rng.range(14, 26);
  const d = o.d ?? rng.range(10, 18);
  const floors = o.floors ?? rng.int(1, 2);
  const fh = o.fh ?? 3.3;
  const H = floors * fh;
  const lod = o.lod ?? 0;
  const body = jit(rng, rng.pick(PAL.concrete), 0.07);

  b.save().at(x, 0, z).ry(ry);
  // 勒脚
  b.box('concrete', w + 0.4, 0.55, d + 0.4, 0, 0.27, 0,
    { color: jit(rng, 0x87857c, 0.06), tile: 3 });
  // 主体
  b.box('concrete', w, H, d, 0, H / 2 + 0.3, 0, { color: body, tile: 5.5 });
  // 楼层分隔线
  for (let f = 1; f < floors; f++) {
    b.box('concrete', w + 0.14, 0.16, d + 0.14, 0, 0.3 + f * fh, 0,
      { color: jit(rng, 0x98968c, 0.05), tile: 3 });
  }
  // 屋面（深色，和墙拉开）
  b.box('corr', w - 0.2, 0.16, d - 0.2, 0, H + 0.46, 0,
    { color: jit(rng, rng.pick(PAL.roof), 0.1), tile: 1.6 });
  // 女儿墙
  const pt = 0.3, ph = o.parapet ?? 0.65;
  b.box('concrete', w + 0.1, 0.22, d + 0.1, 0, H + 0.4, 0, { color: jit(rng, 0x9d9b91, 0.05), tile: 3 });
  for (const s of [-1, 1]) {
    b.box('concrete', w + 0.1, ph, pt, 0, H + 0.4 + ph / 2, s * (d / 2 + 0.05 - pt / 2),
      { color: jit(rng, 0xa5a399, 0.06), tile: 3 });
    b.box('concrete', pt, ph, d + 0.1 - pt * 2, s * (w / 2 + 0.05 - pt / 2), H + 0.4 + ph / 2, 0,
      { color: jit(rng, 0xa5a399, 0.06), tile: 3 });
  }

  // 窗
  if (lod < 2) {
    const nx = Math.max(2, Math.floor((w - 1.6) / 2.7));
    const nz = Math.max(1, Math.floor((d - 1.6) / 2.7));
    const frame = lod === 0;
    for (let f = 0; f < floors; f++) {
      const y = 0.3 + f * fh + fh * 0.58;
      windowsOnFace(b, rng, { w, y, faceZ: d / 2, dirZ: 1, count: nx, lit: 0.05, frame });
      windowsOnFace(b, rng, { w, y, faceZ: -d / 2, dirZ: -1, count: nx, lit: 0.05, frame });
      b.save().ry(Math.PI / 2);
      windowsOnFace(b, rng, { w: d, y, faceZ: w / 2, dirZ: 1, count: nz, lit: 0.05, frame });
      windowsOnFace(b, rng, { w: d, y, faceZ: -w / 2, dirZ: -1, count: nz, lit: 0.05, frame });
      b.restore();
    }
  }

  // 门 + 雨篷 + 台阶
  const dx = rng.range(-w * 0.28, w * 0.28);
  b.box('dark', 1.5, 2.4, 0.12, dx, 1.5, d / 2 - 0.05, { color: 0x22262a });
  b.box('concrete', 2.0, 0.16, 1.3, dx, 2.85, d / 2 + 0.5, { color: 0xb0a999, tile: 1.5 });
  for (const s of [-1, 1]) {
    b.cyl('metal', 0.05, 0.05, 2.7, 6, dx + s * 0.9, 0.3, d / 2 + 1.0, { base: true, color: 0x83878a });
  }
  for (let i = 0; i < 2; i++) {
    b.box('concrete', 2.4 - i * 0.4, 0.16, 0.9 - i * 0.28, dx, 0.08 + i * 0.16, d / 2 + 0.6 + i * 0.15,
      { color: 0xb2ab9b, tile: 1 });
  }

  // 外挂楼梯
  if (floors > 1 && rng.chance(0.55)) {
    const side = rng.sign();
    stairs(b, rng, side * (w / 2 + 0.4), rng.range(-d * 0.25, d * 0.25),
      side > 0 ? Math.PI : 0, H * 0.5, 1.2, 4.2);
  }
  if (rng.chance(0.4)) railing(b, -w / 2 + 0.4, d / 2 - 0.4, w / 2 - 0.4, d / 2 - 0.4, H + 0.62, 0.9);

  roofClutter(b, rng, w - 1.4, d - 1.4, H + 0.5, lod);
  b.restore();
  return { w, d, H };
}

/* ---------- 坡顶库房 / 机库 ---------- */
export function hangar(b, rng, x, z, ry, o = {}) {
  const w = o.w ?? rng.range(20, 34);
  const d = o.d ?? rng.range(12, 20);
  const h = o.h ?? rng.range(5.0, 7.0);
  const rise = o.rise ?? d * rng.range(0.14, 0.22);
  const lod = o.lod ?? 0;
  const body = jit(rng, rng.pick(PAL.concreteWarm), 0.07);
  const roofCol = jit(rng, rng.pick(PAL.roof), 0.13);

  b.save().at(x, 0, z).ry(ry);
  b.box('concrete', w + 0.4, 0.4, d + 0.4, 0, 0.2, 0, { color: jit(rng, 0x86847b, 0.06), tile: 3 });
  b.box('concrete', w, h, d, 0, h / 2 + 0.2, 0, { color: body, tile: 5.0 });

  // 山墙
  const ang = Math.atan2(rise, d / 2);
  const slope = Math.hypot(d / 2, rise);
  for (const s of [-1, 1]) {
    b.prism('concrete', [[-d / 2, 0], [d / 2, 0], [0, rise]], 0.25,
      s * (w / 2 - 0.12), h + 0.2, 0, { ry: Math.PI / 2, color: body });
  }
  // 两坡屋面
  for (const s of [-1, 1]) {
    b.box('corr', w + 0.9, 0.13, slope + 0.35, 0, h + 0.2 + rise / 2 - 0.02, s * d / 4,
      { rx: s * ang, color: roofCol, tile: 1.15 });
  }
  b.box('corr', w + 1.0, 0.16, 0.7, 0, h + 0.2 + rise + 0.05, 0, { color: roofCol, tile: 1 });
  // 檐沟
  for (const s of [-1, 1]) {
    b.cyl('metal', 0.12, 0.12, w + 0.8, 8, 0, h + 0.16, s * (d / 2 + 0.28),
      { rz: Math.PI / 2, color: 0x8b8f8c });
    b.cyl('metal', 0.08, 0.08, h, 6, w / 2 - 0.6, 0.2, s * (d / 2 + 0.25),
      { base: true, color: 0x8b8f8c });
  }

  // 卷帘大门
  if (lod < 2) {
    const nDoor = w > 26 ? 2 : 1;
    for (let i = 0; i < nDoor; i++) {
      const dx = nDoor === 1 ? rng.range(-w * 0.2, w * 0.2)
        : (i - 0.5) * w * 0.44;
      const dw = Math.min(5.4, w / (nDoor + 1)), dh = Math.min(h - 0.9, 4.4);
      b.box('dark', dw, dh, 0.3, dx, dh / 2 + 0.2, d / 2 - 0.08, { color: 0x191c1e });
      // 门框
      b.box('concrete', dw + 0.5, 0.3, 0.34, dx, dh + 0.38, d / 2 + 0.04, { color: 0xb2ab9b, tile: 1.4 });
      for (const s of [-1, 1]) {
        b.box('concrete', 0.3, dh + 0.5, 0.34, dx + s * (dw / 2 + 0.22), (dh + 0.5) / 2 + 0.2, d / 2 + 0.04,
          { color: 0xb2ab9b, tile: 1.4 });
      }
      if (rng.chance(0.5)) {   // 半开的卷帘
        const open = rng.range(0.25, 0.8);
        for (let k = 0; k < 8; k++) {
          b.box('corr', dw - 0.1, dh * (1 - open) / 8, 0.09, dx,
            0.2 + dh - k * dh * (1 - open) / 8 - dh * (1 - open) / 16, d / 2 - 0.02,
            { color: jit(rng, 0x9a9484, 0.06), tile: 0.6 });
        }
      }
    }
    // 侧窗
    const nx = Math.max(2, Math.floor((w - 3) / 4.5));
    for (let i = 0; i < nx; i++) {
      const px = (i + 0.5) / nx * w - w / 2;
      b.box('dark', 1.5, 0.9, 0.1, px, h * 0.72, -d / 2 + 0.05, { color: 0x1b1f22 });
      b.box('concrete', 1.75, 1.12, 0.06, px, h * 0.72, -d / 2 - 0.02, { color: 0xb1aa9a });
    }
  }
  b.restore();
  return { w, d, H: h + rise };
}

/* ---------- 集装箱式营房 ---------- */
export function chuBlock(b, rng, x, z, ry, cols, rows, o = {}) {
  const L = 6.1, W = 2.5, H = 2.6;
  const gx = L + 1.4, gz = W + 1.3;
  b.save().at(x, 0, z).ry(ry);
  const two = o.floors === 2;
  for (let i = 0; i < cols; i++) {
    for (let j = 0; j < rows; j++) {
      const px = (i - (cols - 1) / 2) * gx, pz = (j - (rows - 1) / 2) * gz;
      for (let f = 0; f < (two ? 2 : 1); f++) {
        const y = 0.25 + f * (H + 0.12);
        const col = jit(rng, 0xb0aba0, 0.06, 0.2);
        b.box('corr', L, H, W, px, y + H / 2, pz, { color: col, tile: 2.2 });
        b.box('corr', L + 0.2, 0.09, W + 0.2, px, y + H + 0.05, pz,
          { color: jit(rng, 0x6c6961, 0.1), tile: 1.6 });
        // 门窗
        b.box('dark', 0.85, 1.95, 0.08, px - L * 0.3, y + 0.98, pz + W / 2 - 0.02, { color: 0x24282a });
        b.box('dark', 0.95, 0.75, 0.08, px + L * 0.22, y + 1.55, pz + W / 2 - 0.02, { color: 0x1a1e21 });
        b.box('paint', 0.62, 0.42, 0.42, px + L * 0.22, y + 0.95, pz + W / 2 + 0.16,
          { color: 0x9c9a92 });
      }
      // 基础墩
      for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
        b.box('concrete', 0.5, 0.3, 0.4, px + sx * (L / 2 - 0.3), 0.12, pz + sz * (W / 2 - 0.2),
          { color: 0x9c9585 });
      }
      if (two) {
        stairs(b, rng, px + L / 2 + 0.3, pz, Math.PI, H + 0.37, 0.9, 2.6);
      }
    }
  }
  // 遮阳顶棚
  if (rng.chance(0.5)) {
    const tw = cols * gx, td = rows * gz;
    b.save().at(0, (two ? 2 : 1) * (H + 0.12) + 1.5, 0);
    b.box('corr', tw, 0.09, td, 0, 0, 0, { color: jit(rng, rng.pick(PAL.roof), 0.12), tile: 1.2 });
    b.restore();
    for (let i = 0; i <= cols; i++) for (let j = 0; j <= rows; j++) {
      const px = (i - cols / 2) * gx, pz = (j - rows / 2) * gz;
      b.cyl('metal', 0.07, 0.07, (two ? 2 : 1) * (H + 0.12) + 1.5, 6, px, 0, pz,
        { base: true, color: 0x7d8178 });
    }
  }
  b.restore();
}

/* ---------- 小岗亭 / 检查站 ---------- */
export function hut(b, rng, x, z, ry, o = {}) {
  const w = o.w ?? rng.range(4.5, 8), d = o.d ?? rng.range(3.5, 5.5), h = o.h ?? rng.range(2.7, 3.3);
  b.save().at(x, 0, z).ry(ry);
  b.box('concrete', w + 0.3, 0.3, d + 0.3, 0, 0.15, 0, { color: 0x9a9383, tile: 2 });
  b.box('concrete', w, h, d, 0, h / 2 + 0.15, 0, { color: jit(rng, rng.pick(PAL.concrete), 0.07), tile: 2.6 });
  b.box('corr', w + 0.7, 0.11, d + 0.7, 0, h + 0.25, 0,
    { rz: 0.04, color: jit(rng, rng.pick(PAL.roof), 0.12), tile: 1.1 });
  b.box('dark', 0.95, 2.1, 0.1, -w * 0.25, 1.2, d / 2 - 0.03, { color: 0x23272a });
  b.box('dark', w * 0.34, 0.95, 0.1, w * 0.22, h * 0.62, d / 2 - 0.03, { color: 0x1a1e21 });
  b.box('concrete', w * 0.4, 1.15, 0.07, w * 0.22, h * 0.62, d / 2 + 0.03, { color: 0xb2ab9b });
  b.restore();
}
