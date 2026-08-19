// 载具：悍马、6x6 卡车、油罐拖车、防空导弹发射车、高炮、黑鹰直升机。
// 都是盒子和圆柱堆出来的，重点是剪影比例站得住。
import { TAU } from './rng.js';
import { jit, PAL } from './props.js';

function wheel(b, rng, x, y, z, r, w) {
  b.cyl('dark', r, r, w, 14, x, y, z, { rz: Math.PI / 2, color: jit(rng, PAL.rubber, 0.25) });
  b.cyl('metal', r * 0.5, r * 0.5, w * 1.06, 10, x, y, z,
    { rz: Math.PI / 2, color: jit(rng, 0x5f5b50, 0.12) });
  b.cyl('paint', r * 0.24, r * 0.24, w * 1.12, 8, x, y, z,
    { rz: Math.PI / 2, color: 0x39392f });
}

function headlight(b, x, y, z, r = 0.13) {
  b.cyl('glass', r, r, 0.07, 10, x, y, z, { rz: Math.PI / 2, color: 0xcfc6a8 });
}

/* ============================ 悍马 ============================ */
export function humvee(b, rng, x, z, ry, o = {}) {
  const c = o.color ? jit(rng, o.color, 0.08) : jit(rng, rng.pick(PAL.tanVeh), 0.1);
  const dark = jit(rng, 0x1d2124, 0.2);
  b.save().at(x, 0, z).ry(ry);

  // 底盘 + 车体
  b.box('paint', 4.55, 0.62, 2.12, 0, 0.92, 0, { color: c, tile: 1.6 });
  b.box('paint', 4.3, 0.42, 1.86, 0, 0.6, 0, { color: jit(rng, 0x4b4a40, 0.12) });
  // 机盖
  b.box('paint', 1.5, 0.44, 2.0, 1.62, 1.42, 0, { rz: -0.045, color: c, tile: 1.4 });
  b.box('dark', 0.14, 0.5, 1.5, 2.36, 1.32, 0, { color: 0x2c2f2b });     // 进气格栅
  headlight(b, 2.4, 1.36, 0.72); headlight(b, 2.4, 1.36, -0.72);
  b.box('metal', 0.24, 0.28, 2.3, 2.42, 0.98, 0, { color: jit(rng, 0x55564a, 0.1) });
  // 防撞架
  for (const s of [-1, 1]) {
    b.cyl('metal', 0.05, 0.05, 1.5, 6, 2.5, 1.35, s * 0.95, { color: 0x53544a });
  }
  b.cyl('metal', 0.05, 0.05, 1.9, 6, 2.5, 2.05, 0, { rz: Math.PI / 2, color: 0x53544a });

  // 驾驶室
  b.box('paint', 1.75, 1.0, 2.06, 0.55, 2.02, 0, { color: c, tile: 1.4 });
  b.box('dark', 0.12, 0.86, 1.82, 1.44, 2.06, 0, { rz: -0.42, color: dark });   // 风挡
  for (const s of [-1, 1]) {
    b.box('dark', 0.9, 0.6, 0.08, 0.5, 2.2, s * 1.04, { color: dark });          // 侧窗
    b.box('metal', 0.28, 0.18, 0.05, 1.35, 2.25, s * 1.18, { color: 0x4e4f45 }); // 后视镜
    b.cyl('metal', 0.03, 0.03, 0.3, 5, 1.42, 2.1, s * 1.12, { base: true, color: 0x4e4f45 });
  }
  // 车顶 + 顶圈
  b.box('paint', 1.85, 0.12, 2.1, 0.5, 2.58, 0, { color: c, tile: 1.4 });
  b.cyl('paint', 0.56, 0.6, 0.3, 14, 0.45, 2.62, 0, { base: true, color: c });
  if (o.gun !== false && rng.chance(0.55)) {
    b.cyl('metal', 0.05, 0.06, 1.15, 8, 0.85, 3.0, 0, { rz: -1.35, color: 0x35362e });
    b.box('paint', 0.4, 0.3, 0.24, 0.5, 3.02, 0, { color: 0x3b3c33 });
  }
  // 后货厢
  b.box('paint', 1.85, 1.15, 2.08, -1.42, 2.05, 0, { color: c, tile: 1.5 });
  b.box('paint', 0.1, 1.0, 1.95, -2.34, 2.02, 0, { color: jit(rng, c, 0.06) });
  b.box('metal', 0.22, 0.26, 2.2, -2.42, 1.0, 0, { color: 0x55564a });
  if (rng.chance(0.4)) {
    b.cyl('dark', 0.42, 0.42, 0.24, 14, -2.5, 1.75, 0.4, { rz: Math.PI / 2, color: 0x232221 });
  }
  // 挡泥板 + 车轮
  for (const sx of [1.5, -1.5]) for (const sz of [1, -1]) {
    b.box('paint', 1.5, 0.16, 0.46, sx, 1.3, sz * 0.95, { color: c });
    wheel(b, rng, sx, 0.55, sz * 0.94, 0.55, 0.38);
  }
  // 天线
  b.cyl('metal', 0.012, 0.02, 2.4, 4, -2.2, 1.6, 0.9, { base: true, rz: 0.09, color: 0x2f302a });
  b.restore();
}

/* ============================ 6x6 篷布卡车 ============================ */
export function cargoTruck(b, rng, x, z, ry, o = {}) {
  const c = o.color ? jit(rng, o.color, 0.08) : jit(rng, rng.pick([...PAL.olive, ...PAL.tanVeh]), 0.1);
  const tarp = jit(rng, rng.pick(PAL.canvasTan), 0.09);
  const dark = jit(rng, 0x1d2124, 0.2);
  b.save().at(x, 0, z).ry(ry);

  // 大梁
  for (const s of [-1, 1]) {
    b.box('paint', 7.6, 0.26, 0.16, -0.3, 0.98, s * 0.48, { color: 0x3e3d35 });
  }
  b.box('paint', 7.0, 0.2, 1.0, -0.3, 0.86, 0, { color: 0x39382f });

  // 机头
  b.box('paint', 1.7, 0.9, 2.16, 3.35, 1.66, 0, { color: c, tile: 1.6 });
  b.box('dark', 0.16, 0.72, 1.5, 4.22, 1.6, 0, { color: 0x2a2d29 });
  headlight(b, 4.28, 1.72, 0.86, 0.17); headlight(b, 4.28, 1.72, -0.86, 0.17);
  b.box('metal', 0.28, 0.34, 2.4, 4.32, 1.0, 0, { color: jit(rng, 0x4b4c42, 0.1) });
  for (const s of [-1, 1]) {
    b.box('paint', 1.9, 0.2, 0.5, 3.3, 1.16, s * 1.08, { color: c });   // 前翼子板
  }

  // 驾驶室
  b.box('paint', 2.0, 1.55, 2.3, 1.85, 2.28, 0, { color: c, tile: 1.6 });
  b.box('dark', 0.14, 0.92, 2.0, 2.82, 2.58, 0, { rz: -0.2, color: dark });
  for (const s of [-1, 1]) {
    b.box('dark', 1.0, 0.72, 0.1, 1.75, 2.62, s * 1.14, { color: dark });
    b.box('paint', 0.06, 1.4, 1.1, 1.55, 2.2, s * 1.16, { color: jit(rng, c, 0.05) });
    b.box('metal', 0.3, 0.5, 0.06, 2.7, 2.7, s * 1.34, { color: 0x45463d });
    b.cyl('metal', 0.035, 0.035, 0.6, 5, 2.72, 2.5, s * 1.28, { base: true, color: 0x45463d });
  }
  b.box('paint', 2.15, 0.14, 2.35, 1.85, 3.1, 0, { color: c, tile: 1.4 });
  // 排气立管
  b.cyl('metal', 0.09, 0.09, 2.6, 8, 0.95, 1.1, 1.12, { base: true, color: jit(rng, 0x5a564c, 0.14) });

  // 货箱 + 篷布
  const bx = -1.75, bl = 4.7;
  b.box('paint', bl, 0.18, 2.42, bx, 1.24, 0, { color: 0x44443a });
  for (const s of [-1, 1]) {
    b.box('paint', bl, 0.62, 0.1, bx, 1.62, s * 1.2, { color: jit(rng, c, 0.06), tile: 1.2 });
  }
  b.box('canvas', bl, 1.15, 2.44, bx, 2.35, 0, { color: tarp, tile: 1.4 });
  b.cyl('canvas', 1.22, 1.22, bl, 16, bx, 2.92, 0, { rz: Math.PI / 2, color: tarp, tile: 1.6 });
  b.box('canvas', 0.08, 1.3, 2.3, bx + bl / 2 + 0.02, 2.6, 0, { color: jit(rng, tarp, 0.05) });
  b.box('canvas', 0.08, 1.3, 2.3, bx - bl / 2 - 0.02, 2.6, 0, { color: jit(rng, tarp, 0.07) });
  for (let i = 0; i <= 4; i++) {   // 篷杆
    b.torus('metal', 1.235, 0.035, bx - bl / 2 + (i / 4) * bl, 2.92, 0,
      { ry: Math.PI / 2, seg: 16, seg2: 5, color: 0x50514a });
  }
  b.box('paint', 0.12, 0.9, 2.4, bx - bl / 2 - 0.06, 1.7, 0, { color: jit(rng, c, 0.05) });

  // 车轮：前 1 后 2（双胎）
  wheel(b, rng, 3.3, 0.66, 1.08, 0.66, 0.42);
  wheel(b, rng, 3.3, 0.66, -1.08, 0.66, 0.42);
  for (const ax of [-1.35, -2.85]) {
    for (const s of [-1, 1]) {
      wheel(b, rng, ax, 0.66, s * 1.02, 0.66, 0.4);
      wheel(b, rng, ax, 0.66, s * 1.42, 0.66, 0.4);
    }
  }
  for (const s of [-1, 1]) {
    b.box('paint', 3.0, 0.2, 1.1, -2.1, 1.42, s * 1.22, { color: c });
  }
  // 备胎 / 油箱
  b.cyl('paint', 0.42, 0.42, 1.3, 12, 0.35, 1.05, -1.05, { rz: Math.PI / 2, color: 0x4a4a3f });
  b.cyl('metal', 0.012, 0.02, 2.6, 4, 0.9, 3.15, 1.05, { base: true, rz: 0.07, color: 0x2f302a });
  b.restore();
}

/* ============================ 油罐 ============================ */
function tankBody(b, rng, len, r, y, col) {
  b.cyl('metal', r, r, len, 18, 0, y, 0, { rz: Math.PI / 2, color: col, tile: 2.2 });
  for (const s of [-1, 1]) {
    b.sphere('metal', r, s * len / 2, y, 0, { seg: 18, seg2: 8, color: col });
  }
  for (let i = 1; i < 4; i++) {
    b.torus('metal', r * 1.01, 0.045, -len / 2 + (i / 4) * len, y, 0,
      { ry: Math.PI / 2, seg: 18, seg2: 5, color: jit(rng, 0x8b8474, 0.1) });
  }
  // 人孔 + 顶部走道
  for (const p of [-0.22, 0.22]) {
    b.cyl('metal', 0.34, 0.34, 0.22, 12, p * len, y + r, 0, { color: 0x86807a });
  }
  b.box('metal', len * 0.8, 0.05, 0.5, 0, y + r + 0.14, 0, { color: 0x7d786e });
}

export function tankerTrailer(b, rng, x, z, ry) {
  const col = jit(rng, rng.pick([0x9a9384, 0x8e8a76, 0x8a7f66]), 0.08);
  b.save().at(x, 0, z).ry(ry);
  b.box('paint', 7.4, 0.22, 1.0, -0.2, 0.95, 0, { color: 0x3f3e35 });
  tankBody(b, rng, 6.5, 1.05, 2.05, col);
  // 后双桥
  for (const ax of [-2.0, -3.3]) {
    for (const s of [-1, 1]) wheel(b, rng, ax, 0.62, s * 1.05, 0.62, 0.4);
  }
  b.box('paint', 3.0, 0.16, 2.4, -2.6, 1.4, 0, { color: 0x4b4a40 });
  // 牵引杆 + 支腿
  b.box('metal', 2.2, 0.18, 0.34, 4.3, 0.85, 0, { color: 0x53534a });
  b.cyl('metal', 0.13, 0.13, 0.5, 8, 5.35, 0.85, 0, { rz: Math.PI / 2, color: 0x53534a });
  for (const s of [-1, 1]) {
    b.box('metal', 0.16, 0.85, 0.16, 2.4, 0.42, s * 0.7, { color: 0x53534a });
    b.box('metal', 0.4, 0.1, 0.4, 2.4, 0.05, s * 0.7, { color: 0x4a4a42 });
  }
  // 尾梯 + 泵箱
  for (const s of [-1, 1]) {
    b.cyl('metal', 0.025, 0.025, 2.0, 5, -3.7, 1.0, s * 0.35, { base: true, color: 0x7d786e });
  }
  for (let i = 0; i < 5; i++) {
    b.box('metal', 0.06, 0.04, 0.7, -3.7, 1.1 + i * 0.35, 0, { color: 0x7d786e });
  }
  b.box('paint', 0.9, 0.7, 1.4, 1.4, 1.35, 0, { color: jit(rng, 0x6b6656, 0.1) });
  b.restore();
}

export function tankerTruck(b, rng, x, z, ry) {
  const c = jit(rng, rng.pick(PAL.olive), 0.09);
  b.save().at(x, 0, z).ry(ry);
  // 前部沿用卡车驾驶室
  b.box('paint', 1.7, 0.9, 2.16, 3.35, 1.66, 0, { color: c, tile: 1.6 });
  b.box('dark', 0.16, 0.72, 1.5, 4.22, 1.6, 0, { color: 0x2a2d29 });
  headlight(b, 4.28, 1.72, 0.86, 0.17); headlight(b, 4.28, 1.72, -0.86, 0.17);
  b.box('metal', 0.28, 0.34, 2.4, 4.32, 1.0, 0, { color: 0x4b4c42 });
  b.box('paint', 2.0, 1.55, 2.3, 1.85, 2.28, 0, { color: c, tile: 1.6 });
  b.box('dark', 0.14, 0.92, 2.0, 2.82, 2.58, 0, { rz: -0.2, color: 0x1e2225 });
  b.box('paint', 2.15, 0.14, 2.35, 1.85, 3.1, 0, { color: c });
  for (const s of [-1, 1]) b.box('dark', 1.0, 0.72, 0.1, 1.75, 2.62, s * 1.14, { color: 0x1e2225 });
  for (const s of [-1, 1]) {
    b.box('paint', 7.4, 0.26, 0.16, -0.3, 0.98, s * 0.48, { color: 0x3e3d35 });
  }
  b.save().at(-1.5, 0, 0);
  tankBody(b, rng, 4.9, 1.0, 2.05, jit(rng, 0x8d8874, 0.08));
  b.restore();
  wheel(b, rng, 3.3, 0.66, 1.08, 0.66, 0.42);
  wheel(b, rng, 3.3, 0.66, -1.08, 0.66, 0.42);
  for (const ax of [-1.9, -3.3]) for (const s of [-1, 1]) {
    wheel(b, rng, ax, 0.66, s * 1.02, 0.66, 0.4);
    wheel(b, rng, ax, 0.66, s * 1.42, 0.66, 0.4);
  }
  b.cyl('metal', 0.09, 0.09, 2.6, 8, 0.95, 1.1, 1.12, { base: true, color: 0x5a564c });
  b.restore();
}

/* ============================ 防空导弹发射车 ============================ */
export function missileLauncher(b, rng, x, z, ry, elev = 0.72) {
  const c = jit(rng, rng.pick(PAL.olive), 0.08);
  b.save().at(x, 0, z).ry(ry);
  // 拖车
  b.box('paint', 7.6, 0.55, 2.5, -0.3, 1.15, 0, { color: c, tile: 1.8 });
  b.box('paint', 7.0, 0.3, 1.2, -0.3, 0.82, 0, { color: 0x3d3c33 });
  for (const ax of [-2.1, -3.5]) for (const s of [-1, 1]) wheel(b, rng, ax, 0.62, s * 1.15, 0.62, 0.4);
  b.box('metal', 2.4, 0.2, 0.4, 4.3, 0.9, 0, { color: 0x4e4e45 });
  for (const s of [-1, 1]) {
    b.box('metal', 0.18, 1.0, 0.18, 2.9, 0.5, s * 1.15, { color: 0x505046 });
    b.box('metal', 0.5, 0.12, 0.5, 2.9, 0.05, s * 1.15, { color: 0x4a4a42 });
    b.box('metal', 0.18, 1.0, 0.18, -3.9, 0.5, s * 1.15, { color: 0x505046 });
    b.box('metal', 0.5, 0.12, 0.5, -3.9, 0.05, s * 1.15, { color: 0x4a4a42 });
  }
  // 起竖架
  b.save().at(-2.4, 1.45, 0).rz(elev);
  const L = 5.4, cw = 0.78, gap = 0.16;
  for (const sy of [0, 1]) for (const sz of [-1, 1]) {
    const cy = 0.55 + sy * (cw + gap), cz = sz * (cw + gap) / 2;
    b.box('paint', L, cw, cw, L / 2 - 0.4, cy, cz, { color: jit(rng, c, 0.09), tile: 1.2 });
    // 端盖 + 筒身加强箍
    b.box('dark', 0.1, cw * 0.92, cw * 0.92, L - 0.44, cy, cz, { color: 0x24281f });
    for (let k = 1; k < 4; k++) {
      b.box('metal', 0.07, cw * 1.03, cw * 1.03, k * L / 4 - 0.4, cy, cz, { color: 0x4a4c42 });
    }
  }
  // 侧框
  for (const sz of [-1, 1]) {
    b.box('metal', L + 0.5, 0.14, 0.1, L / 2 - 0.5, 0.55 + (cw + gap) / 2, sz * (cw + gap),
      { color: 0x53544a });
  }
  b.box('metal', 0.5, 2.3, 2.2, -0.35, 1.1, 0, { color: jit(rng, 0x4e4f45, 0.1) });
  b.restore();
  // 液压撑杆
  b.strut('metal', [-0.3, 1.4, 0.9], [-2.0, 2.9, 0.9], 0.11, { color: 0x8b8f8c });
  b.strut('metal', [-0.3, 1.4, -0.9], [-2.0, 2.9, -0.9], 0.11, { color: 0x8b8f8c });
  // 电源 / 控制箱
  b.box('paint', 1.6, 1.0, 2.2, 2.5, 1.95, 0, { color: jit(rng, c, 0.07), tile: 1.4 });
  b.restore();
}

/* ============================ 双管高炮 ============================ */
export function aaGun(b, rng, x, z, ry, turret = 0.4) {
  const c = jit(rng, rng.pick(PAL.olive), 0.08);
  b.save().at(x, 0, z).ry(ry);
  // 炮车 + 大架
  b.box('paint', 2.8, 0.35, 1.5, 0, 0.55, 0, { color: c, tile: 1.2 });
  for (const s of [-1, 1]) wheel(b, rng, 0.2, 0.45, s * 1.05, 0.45, 0.28);
  b.box('metal', 2.0, 0.18, 0.24, -1.9, 0.4, 0, { color: 0x4e4f45 });
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * TAU + 0.5;
    b.box('metal', 1.5, 0.14, 0.3, Math.cos(a) * 1.3, 0.16, Math.sin(a) * 1.3,
      { ry: -a, color: 0x4e4f45 });
  }
  // 回转塔
  b.save().at(0, 0.75, 0).ry(turret);
  b.cyl('paint', 0.75, 0.85, 0.45, 14, 0, 0, 0, { base: true, color: c });
  b.box('paint', 1.1, 0.7, 1.5, -0.2, 0.8, 0, { color: c, tile: 1 });
  b.box('paint', 0.1, 0.9, 1.7, 0.5, 1.1, 0, { rz: -0.18, color: jit(rng, c, 0.06) }); // 防盾
  for (const s of [-1, 1]) {
    b.box('paint', 0.5, 0.6, 0.6, -0.3, 0.95, s * 0.85, { color: jit(rng, c, 0.08) }); // 弹箱
  }
  // 双管
  b.save().at(0.35, 1.05, 0).rz(0.42);
  for (const s of [-1, 1]) {
    b.cyl('metal', 0.055, 0.075, 2.5, 8, 1.15, 0, s * 0.22, { rz: Math.PI / 2, color: 0x3b3d38 });
    b.cyl('metal', 0.1, 0.1, 0.5, 8, 0.25, 0, s * 0.22, { rz: Math.PI / 2, color: 0x44463f });
    b.cyl('metal', 0.09, 0.09, 0.3, 8, 2.4, 0, s * 0.22, { rz: Math.PI / 2, color: 0x35362f });
  }
  b.box('paint', 0.7, 0.35, 0.7, -0.1, 0, 0, { color: c });
  b.restore();
  b.restore();
  b.restore();
}

/* ============================ 黑鹰直升机 ============================ */
export function blackhawk(b, rng, x, z, ry, o = {}) {
  const c = jit(rng, 0x35392f, 0.12, 0.2);
  const dk = jit(rng, 0x24271f, 0.12);
  const glass = 0x181c1e;
  const spin = o.spin ?? 0;
  b.save().at(x, o.y ?? 0, z).ry(ry);

  // ---- 机身 ----
  b.box('paint', 5.6, 1.5, 2.28, 0.2, 2.05, 0, { color: c, tile: 2 });
  b.box('paint', 5.4, 0.6, 2.1, 0.2, 1.32, 0, { rx: 0, color: dk, tile: 2 });
  // 座舱（前部下倾）
  b.box('paint', 1.9, 1.25, 2.1, 3.5, 2.05, 0, { rz: 0.1, color: c, tile: 1.6 });
  b.box('glass', 1.3, 0.95, 1.92, 4.05, 2.2, 0, { rz: 0.22, color: glass });
  b.box('glass', 0.12, 0.8, 1.7, 4.62, 1.85, 0, { rz: 0.55, color: glass });
  b.cone('paint', 0.85, 1.1, 10, 4.95, 1.55, 0, { rz: -Math.PI / 2, color: c });
  // 侧舱门 + 舷窗
  for (const s of [-1, 1]) {
    b.box('dark', 1.5, 1.1, 0.06, 0.5, 2.15, s * 1.15, { color: dk });
    b.box('glass', 0.5, 0.42, 0.05, 0.95, 2.42, s * 1.18, { color: glass });
    b.box('glass', 0.45, 0.4, 0.05, 2.6, 2.42, s * 1.12, { color: glass });
    b.box('glass', 0.45, 0.4, 0.05, -0.9, 2.42, s * 1.1, { color: glass });
  }
  // 发动机舱 + 进气
  for (const s of [-1, 1]) {
    b.box('paint', 3.0, 0.72, 0.8, 0.5, 3.05, s * 0.72, { color: dk, tile: 1.4 });
    b.cyl('metal', 0.34, 0.34, 0.5, 12, 1.95, 3.15, s * 0.72, { rz: Math.PI / 2, color: 0x2b2e28 });
    b.cyl('dark', 0.24, 0.3, 0.9, 10, -1.3, 3.15, s * 0.72, { rz: Math.PI / 2 - 0.12, color: 0x1d1f1b });
  }
  b.box('paint', 2.4, 0.4, 1.5, 0.4, 3.4, 0, { color: dk });

  // ---- 尾梁 ----
  b.box('paint', 3.2, 0.85, 0.85, -4.2, 2.55, 0, { color: c, tile: 1.6 });
  b.box('paint', 1.6, 0.6, 0.6, -5.9, 2.62, 0, { color: c, tile: 1.4 });
  // 尾斜梁
  b.save().at(-6.6, 2.75, 0).rz(-0.32);
  b.box('paint', 0.55, 2.3, 0.42, 0, 1.0, 0, { color: c, tile: 1.2 });
  b.restore();
  b.box('paint', 0.8, 0.5, 0.55, -7.35, 4.55, 0, { color: c });
  // 平尾
  b.box('paint', 1.0, 0.1, 3.4, -6.0, 2.35, 0, { color: c, tile: 1.4 });
  for (const s of [-1, 1]) b.box('paint', 0.5, 0.5, 0.08, -6.0, 2.55, s * 1.6, { color: c });
  // 尾桨
  b.save().at(-7.35, 4.55, 0.36).rz(spin * 3.1);
  b.cyl('metal', 0.16, 0.16, 0.28, 10, 0, 0, 0, { rx: Math.PI / 2, color: 0x3a3c34 });
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * TAU;
    b.box('paintDS', 1.55, 0.05, 0.26,
      Math.cos(a) * 0.85, Math.sin(a) * 0.85, 0.08,
      { rz: a, color: dk });
  }
  b.restore();

  // ---- 起落架 ----
  for (const s of [-1, 1]) {
    b.strut('metal', [1.3, 1.3, s * 0.9], [1.55, 0.42, s * 1.5], 0.075, { color: 0x3f423a });
    b.strut('metal', [1.9, 1.35, s * 0.9], [1.6, 0.45, s * 1.5], 0.06, { color: 0x3f423a });
    b.cyl('dark', 0.42, 0.42, 0.28, 12, 1.58, 0.42, s * 1.55, { rz: Math.PI / 2, color: 0x1d1c1b });
  }
  b.strut('metal', [-6.0, 2.1, 0], [-6.1, 0.5, 0], 0.06, { color: 0x3f423a });
  b.cyl('dark', 0.26, 0.26, 0.2, 10, -6.1, 0.32, 0, { rz: Math.PI / 2, color: 0x1d1c1b });

  // ---- 主旋翼 ----
  b.cyl('metal', 0.42, 0.5, 0.55, 14, 0.4, 3.75, 0, { base: true, color: 0x3a3c34 });
  b.cyl('metal', 0.28, 0.28, 0.4, 12, 0.4, 4.3, 0, { color: 0x44463d });
  b.save().at(0.4, 4.22, 0).ry(spin);
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * TAU;
    b.save().ry(a);
    b.box('metal', 0.5, 0.14, 0.3, 0.5, 0, 0, { color: 0x4a4c43 });          // 桨毂夹
    b.strut('metal', [0.45, 0.12, 0.2], [1.0, -0.05, 0.1], 0.035, { color: 0x6d7069 });
    // 桨叶（带下垂）
    b.save().at(0.9, 0, 0).rz(-0.075);
    b.box('paintDS', 7.3, 0.075, 0.62, 3.65, 0, 0, { color: dk, tile: 2 });
    b.box('paintDS', 0.5, 0.08, 0.62, 7.1, 0.02, 0, { color: 0xa8a396 });     // 桨尖标记
    b.restore();
    b.restore();
  }
  b.restore();
  b.restore();
}

/* ============================ 叉车（点缀） ============================ */
export function forklift(b, rng, x, z, ry) {
  const c = jit(rng, 0x8a7d3a, 0.12, 0.3);
  b.save().at(x, 0, z).ry(ry);
  b.box('paint', 2.1, 0.8, 1.2, -0.2, 0.75, 0, { color: c, tile: 1 });
  b.box('paint', 0.9, 0.9, 1.0, -0.7, 1.6, 0, { color: c });
  b.box('dark', 0.7, 0.15, 0.9, -0.55, 2.15, 0, { color: 0x2c2f2b });
  for (const s of [-1, 1]) {
    b.cyl('metal', 0.05, 0.05, 1.1, 5, -0.2, 2.1, s * 0.45, { base: true, color: 0x4a4c45 });
    b.box('metal', 0.12, 2.6, 0.12, 0.85, 1.5, s * 0.42, { color: 0x55574f });
  }
  b.box('metal', 0.1, 0.12, 1.0, 0.95, 0.35, 0, { color: 0x5c5e56 });
  for (const s of [-1, 1]) b.box('metal', 0.9, 0.06, 0.16, 1.35, 0.16, s * 0.3, { color: 0x63655d });
  wheel(b, rng, 0.55, 0.4, 0.62, 0.4, 0.26);
  wheel(b, rng, 0.55, 0.4, -0.62, 0.4, 0.26);
  wheel(b, rng, -1.0, 0.32, 0.5, 0.32, 0.22);
  wheel(b, rng, -1.0, 0.32, -0.5, 0.32, 0.22);
  b.restore();
}
