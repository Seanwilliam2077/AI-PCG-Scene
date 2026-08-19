// 按总平面把整个基地装配出来。
import { makeRng, TAU } from './rng.js';
import { SITE } from './layout.js';
import {
  tWallRun, chainFence, guardTower, satDish, smallDish, latticeMast,
  container, crateStack, drumCluster, hescoRun, supplyStack, lightPole,
  canopy, netScreen, jersey, debris, tyreStack, pallet, jit, PAL, razorCoil,
} from './props.js';
import { flatBuilding, hangar, chuBlock, hut } from './buildings.js';
import {
  humvee, cargoTruck, tankerTrailer, tankerTruck, missileLauncher,
  aaGun, blackhawk, forklift,
} from './vehicles.js';

/* ---------------- 院墙 ---------------- */
function cellWalls(b, rng, c) {
  const m = 0.6;
  const sides = {
    n: [c.x0 + m, c.z0, c.x1 - m, c.z0],
    s: [c.x0 + m, c.z1, c.x1 - m, c.z1],
    w: [c.x0, c.z0 + m, c.x0, c.z1 - m],
    e: [c.x1, c.z0 + m, c.x1, c.z1 - m],
  };
  for (const k of Object.keys(sides)) {
    if (!c.walls[k]) continue;
    const g = c.gate === k ? [0.42, 0.60] : null;
    tWallRun(b, rng, ...sides[k], { h: rng.range(3.3, 4.0), gap: g, lod: c.lod });
    if (g && c.lod < 2) {
      // 大门柱 + 横杆
      const [x0, z0, x1, z1] = sides[k];
      for (const t of g) {
        const x = x0 + (x1 - x0) * t, z = z0 + (z1 - z0) * t;
        b.box('concrete', 0.55, 4.4, 0.55, x, 2.2, z, { color: 0xa8a191, tile: 2 });
        b.box('concrete', 0.7, 0.25, 0.7, x, 4.5, z, { color: 0xb2ab9b });
      }
    }
  }
}

/* ---------------- 通用杂物 ---------------- */

// 沿着院墙内侧堆东西 —— 真实基地就是这么用地的，
// 顺便避开院子中央（那儿一般站着建筑或车）。
function edgeClutter(b, rng, c, n) {
  const inset = () => rng.range(3.4, 8.5);
  for (let i = 0; i < n; i++) {
    const side = rng.int(0, 3);
    const t = rng.range(0.1, 0.9);
    let x, z, ry;
    if (side === 0) { x = c.x0 + (c.x1 - c.x0) * t; z = c.z0 + inset(); ry = 0; }
    else if (side === 1) { x = c.x0 + (c.x1 - c.x0) * t; z = c.z1 - inset(); ry = Math.PI; }
    else if (side === 2) { x = c.x0 + inset(); z = c.z0 + (c.z1 - c.z0) * t; ry = Math.PI / 2; }
    else { x = c.x1 - inset(); z = c.z0 + (c.z1 - c.z0) * t; ry = -Math.PI / 2; }
    ry += rng.j(0.12);
    switch (rng.weighted([['crate', 4], ['drums', 3], ['supply', 2],
                          ['pallet', 2], ['tyre', 1], ['jersey', 1], ['box', 2]])) {
      case 'crate': crateStack(b, rng, x, z, ry); break;
      case 'drums': drumCluster(b, rng, x, z, rng.int(3, 9)); break;
      case 'supply': supplyStack(b, rng, x, z, ry, rng.int(2, 4), rng.int(1, 2), rng.int(1, 2)); break;
      case 'pallet': pallet(b, rng, x, z, ry, 1.4, 1.1); break;
      case 'tyre': tyreStack(b, rng, x, z); break;
      case 'jersey': jersey(b, rng, x, z, ry, false); break;
      case 'box':
        b.box('paint', rng.range(1.6, 3.0), rng.range(1.2, 2.2), rng.range(1.4, 2.4),
          x, 0, z, { base: true, ry, color: jit(rng, rng.pick(PAL.olive), 0.12), tile: 1.4 });
        break;
    }
  }
}

/* ---------------- 各类院子的内容 ---------------- */

function fillSatcom(b, rng, c) {
  satDish(b, rng, c.cx - 11, c.cz + 4, 9.2, rng.range(-0.55, -0.3), 0.78);
  satDish(b, rng, c.cx + 3, c.cz + 1, 8.0, rng.range(0.15, 0.45), 0.86);
  smallDish(b, rng, c.cx - 6.5, 0, c.cz + 14, 3.0, -0.9, 0.55);
  smallDish(b, rng, c.cx - 17, 0, c.cz - 2, 2.2, 1.4, 0.7);
  latticeMast(b, rng, c.x0 + 5, c.cz - 8, 25);
  netScreen(b, rng, c.cx + 16, c.cz + 8, 12, 5.4, 0.06);
  tankerTrailer(b, rng, c.cx - 6, c.cz + 17, 0.03);

  // 设备方舱 + 电源
  container(b, rng, c.cx + 13, c.cz - 6, Math.PI / 2, 4.0);
  container(b, rng, c.cx + 19, c.cz - 2, Math.PI / 2, 6.06);
  b.box('paint', 2.4, 2.2, 2.2, c.cx + 8, 1.1, c.cz - 12, { color: jit(rng, 0x8d8874, 0.08), tile: 1.6 });
  b.box('paint', 3.4, 2.6, 2.6, c.cx - 18, 1.3, c.cz + 12, { color: jit(rng, 0x6f6a56, 0.1), tile: 1.6 });
  drumCluster(b, rng, c.cx + 18, c.cz - 13, 6);
  drumCluster(b, rng, c.cx - 20, c.cz + 5, 8);
  hescoRun(b, rng, c.x0 + 3, c.z0 + 3, c.x0 + 3, c.z0 + 16, { levels: 1, depth: 1 });
  lightPole(b, rng, c.x1 - 5, c.z0 + 6, 0.4);
  lightPole(b, rng, c.x0 + 6, c.z1 - 5, 2.1);
  crateStack(b, rng, c.cx + 19, c.cz + 15, 0.5);
  crateStack(b, rng, c.cx + 8, c.cz + 16, 1.1);
  humvee(b, rng, c.cx + 15, c.cz + 11, -1.4);
  debris(b, rng, c.cx, c.cz, 20, 26);
}

function fillHescoYard(b, rng, c) {
  supplyStack(b, rng, c.cx - 12, c.cz - 8, 0.02, 4, 3, 2);
  supplyStack(b, rng, c.cx - 12, c.cz + 4, 0.03, 4, 2, 2);
  supplyStack(b, rng, c.cx + 4, c.cz - 9, -0.02, 3, 3, 3);
  supplyStack(b, rng, c.cx + 6, c.cz + 6, 0.05, 3, 2, 1);
  hescoRun(b, rng, c.x0 + 4, c.z1 - 5, c.x1 - 4, c.z1 - 5, { levels: 2, depth: 1 });
  for (let i = 0; i < 4; i++) {
    crateStack(b, rng, rng.range(c.x0 + 5, c.x1 - 5), rng.range(c.z0 + 5, c.z1 - 5), rng.range(0, TAU));
  }
  drumCluster(b, rng, c.cx + 17, c.cz + 12, 9);
  container(b, rng, c.cx + 16, c.cz - 15, 0.1, 6.06);
  forklift(b, rng, c.cx + 1, c.cz + 14, 1.2);
  debris(b, rng, c.cx, c.cz, 22, 30);
}

function fillMotorpool(b, rng, c) {
  const n = rng.int(3, 5);
  for (let i = 0; i < n; i++) {
    cargoTruck(b, rng, c.x0 + 9 + i * 0.6 + rng.j(2), c.z0 + 7 + i * 7.5 + rng.j(1.6),
      rng.chance(0.5) ? rng.j(0.12) : Math.PI + rng.j(0.12));
  }
  for (let i = 0, m = rng.int(1, 3); i < m; i++) {
    humvee(b, rng, c.cx + rng.j(14), c.cz + rng.j(14), rng.range(0, TAU));
  }
  if (rng.chance(0.75)) {
    missileLauncher(b, rng, c.cx + 12 + rng.j(3), c.cz - 6 + rng.j(4),
      Math.PI * 0.5 + rng.j(0.4), rng.range(0.6, 0.85));
  }
  if (rng.chance(0.5)) {
    missileLauncher(b, rng, c.cx + 10 + rng.j(3), c.cz + 12 + rng.j(3),
      Math.PI * 0.5 + rng.j(0.4), rng.range(0.55, 0.8));
  }
  for (let i = 0, m = rng.int(4, 7); i < m; i++) {
    container(b, rng, rng.range(c.x0 + 6, c.x1 - 6), rng.range(c.z0 + 5, c.z1 - 5),
      rng.chance(0.5) ? rng.j(0.1) : Math.PI / 2 + rng.j(0.1), rng.pick([6.06, 6.06, 12.2]));
  }
  for (let i = 0, m = rng.int(5, 9); i < m; i++) {
    crateStack(b, rng, rng.range(c.x0 + 4, c.x1 - 4), rng.range(c.z0 + 4, c.z1 - 4), rng.range(0, TAU));
  }
  drumCluster(b, rng, rng.range(c.x0 + 5, c.x1 - 5), rng.range(c.z0 + 5, c.z1 - 5), rng.int(4, 10));
  drumCluster(b, rng, rng.range(c.x0 + 5, c.x1 - 5), rng.range(c.z0 + 5, c.z1 - 5), rng.int(4, 12));
  lightPole(b, rng, c.x0 + 4, c.cz, 1.6);
  lightPole(b, rng, c.x1 - 4, c.cz + 8, -1.6);
  if (rng.chance(0.5)) canopy(b, rng, c.cx + rng.j(6), c.z0 + 8, 12, 7, rng.j(0.1), { side: rng.chance(0.5) });
  debris(b, rng, c.cx, c.cz, 22, 24);
}

function fillVehiclePark(b, rng, c) {
  for (let i = 0, m = rng.int(4, 6); i < m; i++) {
    humvee(b, rng, c.x0 + 8 + i * 4.4 + rng.j(0.8), c.z0 + 9 + rng.j(3), rng.j(0.15) + Math.PI / 2);
  }
  for (let i = 0, m = rng.int(2, 4); i < m; i++) {
    humvee(b, rng, c.x0 + 9 + i * 4.4 + rng.j(0.8), c.cz + 7 + rng.j(3), rng.j(0.15) - Math.PI / 2);
  }
  aaGun(b, rng, c.cx + 12 + rng.j(3), c.cz + 8 + rng.j(3), rng.range(0, TAU), rng.range(-1, 1));
  if (rng.chance(0.6)) aaGun(b, rng, c.x1 - 7, c.z0 + 7, rng.range(0, TAU), rng.range(-1, 1));
  cargoTruck(b, rng, c.cx - 4, c.z1 - 9, Math.PI + rng.j(0.1));
  cargoTruck(b, rng, c.x1 - 8, c.z1 - 13, Math.PI / 2 + rng.j(0.1));
  for (let i = 0, m = rng.int(4, 7); i < m; i++) {
    crateStack(b, rng, rng.range(c.x0 + 4, c.x1 - 4), rng.range(c.z0 + 4, c.z1 - 4), rng.range(0, TAU));
  }
  container(b, rng, c.x1 - 8, c.cz - 10, Math.PI / 2, 6.06);
  drumCluster(b, rng, c.x0 + 6, c.z1 - 7, 5);
  canopy(b, rng, c.cx + 2, c.z1 - 6, 11, 7, 0.02, { side: true });
  lightPole(b, rng, c.x0 + 3, c.z0 + 5, 0.8);
  debris(b, rng, c.cx, c.cz, 22, 22);
}

function fillHQ(b, rng, c) {
  flatBuilding(b, rng, c.cx - 2, c.cz - 2, c.rot + rng.j(0.02), {
    w: 25, d: 16, floors: 2, fh: 3.6, parapet: 0.9, lod: c.lod,
  });
  humvee(b, rng, c.cx + 12, c.cz + 10, -1.5);
  humvee(b, rng, c.cx + 12, c.cz + 5, -1.5);
  smallDish(b, rng, c.cx + 15, 0, c.cz - 12, 2.6, 0.7, 0.6);
  b.box('paint', 3.2, 2.4, 2.4, c.cx - 16, 1.2, c.cz + 13, { color: jit(rng, 0x8a8571, 0.08), tile: 1.6 });
  drumCluster(b, rng, c.x0 + 6, c.z0 + 6, 4);
  crateStack(b, rng, c.x1 - 7, c.z1 - 7, 0.3);
  lightPole(b, rng, c.x1 - 4, c.z1 - 4, -0.7);
  debris(b, rng, c.cx, c.cz, 22, 16);
}

function fillBarracks(b, rng, c) {
  flatBuilding(b, rng, c.cx, c.cz - 1, c.rot + rng.j(0.02), {
    w: 30, d: 14, floors: 3, fh: 3.15, parapet: 0.5, lod: c.lod,
  });
  for (let i = 0; i < 3; i++) {
    b.box('concrete', 2.6, 0.9, 1.2, c.cx - 10 + i * 9, 0.45, c.cz + 9,
      { color: 0xa9a292, tile: 1.4 });
  }
  crateStack(b, rng, c.x0 + 6, c.z1 - 6, 0.2);
  drumCluster(b, rng, c.x1 - 7, c.z1 - 8, 6);
  lightPole(b, rng, c.x0 + 4, c.cz, 1.4);
  debris(b, rng, c.cx, c.cz, 22, 16);
}

function fillWarehouse(b, rng, c) {
  hangar(b, rng, c.cx, c.cz - 2, c.rot + rng.j(0.02), {
    w: rng.range(26, 32), d: rng.range(14, 18), h: rng.range(5.5, 6.8), lod: c.lod,
  });
  for (let i = 0, m = rng.int(2, 4); i < m; i++) {
    crateStack(b, rng, rng.range(c.x0 + 5, c.x1 - 5), c.z1 - rng.range(5, 11), rng.range(0, TAU));
  }
  if (rng.chance(0.6)) forklift(b, rng, c.cx + rng.j(8), c.z1 - 8, rng.range(0, TAU));
  if (rng.chance(0.6)) cargoTruck(b, rng, c.cx + rng.j(6), c.z1 - 7, rng.j(0.2));
  container(b, rng, c.x0 + 7, c.z0 + 6, 0.05, 6.06);
  drumCluster(b, rng, c.x1 - 6, c.z0 + 6, 5);
  lightPole(b, rng, c.x1 - 4, c.z1 - 5, -1.2);
  debris(b, rng, c.cx, c.cz, 22, 18);
}

function fillHangarCell(b, rng, c) {
  hangar(b, rng, c.cx, c.cz, c.rot + rng.j(0.02), {
    w: rng.range(28, 36), d: rng.range(16, 20), h: rng.range(6.5, 8), lod: c.lod,
  });
  if (rng.chance(0.5)) cargoTruck(b, rng, c.cx + rng.j(10), c.z1 - 7, rng.j(0.2));
  crateStack(b, rng, c.x0 + 6, c.z1 - 6, 0.4);
  container(b, rng, c.x1 - 8, c.z1 - 7, Math.PI / 2, 6.06);
  drumCluster(b, rng, c.x0 + 5, c.z0 + 5, 4);
  debris(b, rng, c.cx, c.cz, 22, 16);
}

function fillDepot(b, rng, c) {
  const rows = rng.int(3, 4);
  for (let r = 0; r < rows; r++) {
    const z = c.z0 + 6 + r * 8.5;
    for (let i = 0, m = rng.int(3, 5); i < m; i++) {
      const x = c.x0 + 8 + i * 7.5 + rng.j(1);
      container(b, rng, x, z, rng.j(0.05), 6.06);
      if (rng.chance(0.4)) container(b, rng, x + rng.j(0.2), z + rng.j(0.2), rng.j(0.06), 6.06, 2.62);
    }
  }
  for (let i = 0, m = rng.int(4, 8); i < m; i++) {
    crateStack(b, rng, rng.range(c.x0 + 4, c.x1 - 4), rng.range(c.z0 + 4, c.z1 - 4), rng.range(0, TAU));
  }
  drumCluster(b, rng, rng.range(c.x0 + 5, c.x1 - 5), rng.range(c.z0 + 5, c.z1 - 5), rng.int(4, 9));
  drumCluster(b, rng, rng.range(c.x0 + 5, c.x1 - 5), rng.range(c.z0 + 5, c.z1 - 5), rng.int(3, 8));
  if (rng.chance(0.5)) hut(b, rng, c.x1 - 8, c.z0 + 6, rng.j(0.1), {});
  if (rng.chance(0.4)) forklift(b, rng, c.cx, c.cz, rng.range(0, TAU));
  lightPole(b, rng, c.x0 + 4, c.cz, 1.5);
  debris(b, rng, c.cx, c.cz, 22, 20);
}

function fillBillet(b, rng, c) {
  chuBlock(b, rng, c.cx, c.cz, c.rot + rng.j(0.02), rng.int(2, 3), rng.int(2, 3),
    { floors: rng.chance(0.45) ? 2 : 1 });
  hut(b, rng, c.x0 + 8, c.z1 - 6, rng.j(0.1), { w: 5, d: 4 });
  hescoRun(b, rng, c.x0 + 3, c.z0 + 4, c.x0 + 3, c.z1 - 4, { levels: 1, depth: 1 });
  drumCluster(b, rng, c.x1 - 6, c.z1 - 6, 4);
  lightPole(b, rng, c.x1 - 4, c.cz, -1.5);
  debris(b, rng, c.cx, c.cz, 22, 14);
}

function fillHelipad(b, rng, c) {
  blackhawk(b, rng, c.cx + rng.j(2), c.cz + rng.j(2), rng.range(-0.5, 0.5));
  // 消防 / 加油点
  drumCluster(b, rng, c.x0 + 7, c.z0 + 7, 6);
  b.box('paint', 2.2, 1.8, 1.8, c.x1 - 8, 0.9, c.z0 + 8, { color: 0x7c3a2c, tile: 1.4 });
  hut(b, rng, c.x0 + 8, c.z1 - 7, 0.05, { w: 5, d: 4, h: 2.8 });
  // 风向袋
  b.cyl('metal', 0.05, 0.07, 6.5, 6, c.x1 - 6, 0, c.z1 - 6, { base: true, color: 0x8b8f8c });
  b.cyl('canvas', 0.42, 0.22, 1.6, 10, c.x1 - 5.2, 6.2, c.z1 - 6,
    { rz: Math.PI / 2 - 0.35, color: 0xb2603a });
  lightPole(b, rng, c.x0 + 4, c.cz, 1.5);
  lightPole(b, rng, c.x1 - 4, c.cz, -1.5);
  debris(b, rng, c.cx, c.cz, 20, 10);
}

function fillYard(b, rng, c) {
  for (let i = 0, m = rng.int(1, 3); i < m; i++) {
    crateStack(b, rng, rng.range(c.x0 + 5, c.x1 - 5), rng.range(c.z0 + 5, c.z1 - 5), rng.range(0, TAU));
  }
  if (rng.chance(0.6)) drumCluster(b, rng, rng.range(c.x0 + 5, c.x1 - 5), rng.range(c.z0 + 5, c.z1 - 5), rng.int(3, 7));
  if (rng.chance(0.5)) tyreStack(b, rng, rng.range(c.x0 + 5, c.x1 - 5), rng.range(c.z0 + 5, c.z1 - 5));
  for (let i = 0, m = rng.int(0, 3); i < m; i++) {
    jersey(b, rng, c.x0 + 8 + i * 3.2, c.z1 - 6, 0, false);
  }
  debris(b, rng, c.cx, c.cz, 22, 34);
}

// 远景：只保留剪影 —— 一两栋楼、几只集装箱，其余交给沙尘
function fillBackground(b, rng, c) {
  const kind = c.kind;
  if (kind === 'hangar' || kind === 'warehouse') {
    hangar(b, rng, c.cx, c.cz, c.rot, {
      w: rng.range(24, 34), d: rng.range(14, 19), h: rng.range(5.5, 7.5), lod: 2,
    });
  } else if (kind === 'billet') {
    chuBlock(b, rng, c.cx, c.cz, c.rot, 2, 2, { floors: rng.chance(0.4) ? 2 : 1 });
  } else if (kind === 'yard' || kind === 'depot' || kind === 'motorpool') {
    for (let i = 0, m = rng.int(2, 6); i < m; i++) {
      container(b, rng, rng.range(c.x0 + 6, c.x1 - 6), rng.range(c.z0 + 5, c.z1 - 5),
        rng.chance(0.5) ? 0 : Math.PI / 2, 6.06);
    }
    if (rng.chance(0.6)) {
      flatBuilding(b, rng, c.cx + rng.j(6), c.cz + rng.j(6), c.rot,
        { w: rng.range(14, 22), d: rng.range(10, 15), floors: 1, lod: 2 });
    }
  } else {
    flatBuilding(b, rng, c.cx, c.cz, c.rot, {
      w: rng.range(16, 26), d: rng.range(11, 16), floors: rng.int(1, 2), lod: 2,
    });
  }
  if (rng.chance(0.55)) {
    flatBuilding(b, rng, c.x0 + rng.range(8, 16), c.z0 + rng.range(7, 12), c.rot + Math.PI / 2,
      { w: rng.range(10, 16), d: rng.range(8, 12), floors: 1, lod: 2 });
  }
  // 远处也要有深色的堆料，否则整片背景糊成一块浅色
  for (let i = 0, m = rng.int(2, 5); i < m; i++) {
    const x = rng.range(c.x0 + 5, c.x1 - 5), z = rng.range(c.z0 + 5, c.z1 - 5);
    if (rng.chance(0.45)) {
      container(b, rng, x, z, rng.chance(0.5) ? 0 : Math.PI / 2, 6.06);
    } else {
      b.box('paint', rng.range(2.2, 5.0), rng.range(1.2, 2.6), rng.range(2.0, 3.4),
        x, 0, z, { base: true, ry: rng.range(0, TAU), tile: 1.6,
                   color: jit(rng, rng.chance(0.5) ? rng.pick(PAL.olive) : rng.pick(PAL.canvasTan), 0.14) });
    }
  }
  if (rng.chance(0.6)) drumCluster(b, rng, rng.range(c.x0 + 5, c.x1 - 5), rng.range(c.z0 + 5, c.z1 - 5), rng.int(4, 9));
  if (rng.chance(0.5)) lightPole(b, rng, c.x0 + 5, c.cz, 1.4);
}

const FILLERS = {
  satcom: fillSatcom, hescoYard: fillHescoYard, motorpool: fillMotorpool,
  vehiclePark: fillVehiclePark, hq: fillHQ, barracks: fillBarracks,
  warehouse: fillWarehouse, hangar: fillHangarCell, depot: fillDepot,
  billet: fillBillet, helipad: fillHelipad, yard: fillYard,
};

/* ---------------- 周界与前区 ---------------- */
function perimeter(b, rng, site) {
  const F = SITE.FENCE, W = SITE.FRONT_WALL;
  const x0 = SITE.X_MIN, x1 = SITE.X_MAX;

  // 前排长 T 墙（中间留车辆通道）
  const gateX = 18;
  tWallRun(b, rng, x0, W, gateX - 7, W, { h: 3.9 });
  tWallRun(b, rng, gateX + 7, W, x1, W, { h: 3.9 });
  for (const s of [-1, 1]) {
    b.box('concrete', 0.7, 4.6, 0.7, gateX + s * 7, 2.3, W, { color: 0xa8a191, tile: 2 });
  }
  // 通道处的斜置路障
  jersey(b, rng, gateX - 3.5, W + 5, 0.5, true);
  jersey(b, rng, gateX + 4.0, W + 8, -0.4, true);
  jersey(b, rng, gateX - 1.0, W + 11, 0.2, true);

  // 铁丝网周界（分段，避免单条几何过长）
  const seg = 60;
  for (let x = x0; x < x1; x += seg) {
    const xe = Math.min(x1, x + seg);
    if (gateX - 8 < xe && gateX + 8 > x) {
      chainFence(b, rng, x, F, Math.max(x, gateX - 8), F, {});
      chainFence(b, rng, Math.min(xe, gateX + 8), F, xe, F, {});
    } else {
      chainFence(b, rng, x, F, xe, F, {});
    }
  }
  // 侧向周界（只做画面里看得到的一段）
  chainFence(b, rng, x0, F, x0, 20, {});
  chainFence(b, rng, x1, F, x1, 30, {});
  tWallRun(b, rng, x0 + 10, F - 14, x0 + 10, -20, { h: 3.7 });
  tWallRun(b, rng, x1 - 10, F - 14, x1 - 10, -10, { h: 3.7 });

  // 前区（T 墙与铁丝网之间）
  hut(b, rng, gateX + 16, W + 9, 0.03, { w: 6.5, d: 4.5, h: 3.0 });
  canopy(b, rng, gateX + 34, W + 12, 13, 8, 0.02, { h: 4.8 });
  tankerTruck(b, rng, gateX + 36, W + 12, 0.04);
  drumCluster(b, rng, gateX + 26, W + 6, 6);
  crateStack(b, rng, gateX + 47, W + 6, 0.3);
  supplyStack(b, rng, gateX + 52, W + 13, 0.02, 3, 2, 1);
  humvee(b, rng, gateX + 60, W + 10, 0.1);
  cargoTruck(b, rng, gateX + 74, W + 11, 0.06);
  hescoRun(b, rng, x0 + 30, W + 6, x0 + 46, W + 6, { levels: 1, depth: 1 });
  for (let i = 0; i < 5; i++) {
    lightPole(b, rng, x0 + 40 + i * 62, W + 4, i % 2 ? 1.2 : -1.2, 9.0);
  }
  debris(b, rng, 0, W + 10, 120, 60);
  debris(b, rng, 0, F + 12, 150, 90);
}

/* ---------------- 主入口 ---------------- */
export function buildBase(b, site, seed) {
  const rng = makeRng(seed ^ 0x9e37);

  for (const c of site.cells) {
    const r = makeRng(c.seed);
    cellWalls(b, r, c);
  }
  for (const c of site.cells) {
    const r = makeRng(c.seed ^ 0x5bf0);
    if (c.lod >= 2) { fillBackground(b, r, c); continue; }
    (FILLERS[c.kind] || fillYard)(b, r, c);
    edgeClutter(b, r, c, c.lod === 0 ? r.int(10, 18) : r.int(5, 10));
  }
  perimeter(b, rng, site);
  for (const t of site.towers) guardTower(b, rng, t.x, t.z, t.ry);

  // 路上的车流与路灯
  for (const road of site.roads) {
    if (road.v) continue;
    const z = (road.z0 + road.z1) / 2;
    for (let i = 0, n = rng.int(0, 2); i < n; i++) {
      const x = rng.range(SITE.X_MIN + 40, SITE.X_MAX - 40);
      const dir = rng.chance(0.5) ? 0 : Math.PI;
      if (rng.chance(0.5)) cargoTruck(b, rng, x, z + rng.j(1.5), dir + rng.j(0.05));
      else humvee(b, rng, x, z + rng.j(1.5), dir + rng.j(0.05));
    }
  }
  return b;
}
