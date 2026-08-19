// 场站总平面。地面贴图的烘焙和三维物件的摆放共用这一份数据，
// 所以混凝土场坪、道路、轮胎印永远和墙、车、楼对得上。

export const SITE = {
  CELL_W: 48, CELL_D: 40, ROAD: 11,
  PITCH_X: 59, PITCH_Z: 51,
  COL0: -5, COL1: 5, ROW0: -8, ROW1: 2,
  OX: 12, OZ: 10,
  FRONT_WALL: 138,     // 最前排院墙外的长 T 墙
  FENCE: 151,          // 铁丝网周界
  OUTER_ROAD: 162,     // 周界外的土路中心线
  X_MIN: -320, X_MAX: 348,
};

export function cellRect(col, row) {
  const cx = col * SITE.PITCH_X + SITE.OX;
  const cz = row * SITE.PITCH_Z + SITE.OZ;
  return {
    cx, cz,
    x0: cx - SITE.CELL_W / 2, x1: cx + SITE.CELL_W / 2,
    z0: cz - SITE.CELL_D / 2, z1: cz + SITE.CELL_D / 2,
  };
}

// 前景三排是照着参考图排的（天线场→左中，指挥楼+营房→正中，
// 直升机坪→中右后），后面几排随机填充，透过沙尘只看得见轮廓。
// 索引 = col - COL0，共 11 列。
const PLAN = {
  2: ['depot', 'yard', 'depot', 'hescoYard', 'satcom', 'motorpool',
      'motorpool', 'vehiclePark', 'depot', 'yard', 'depot'],
  1: ['depot', 'warehouse', 'hangar', 'hangar', 'hq', 'barracks',
      'warehouse', 'hangar', 'depot', 'billet', 'warehouse'],
  0: ['yard', 'depot', 'billet', 'warehouse', 'billet', 'billet',
      'helipad', 'warehouse', 'hangar', 'depot', 'billet'],
};
const FILL = ['depot', 'billet', 'warehouse', 'hangar', 'motorpool', 'yard', 'billet', 'depot'];

export function buildSite(rng) {
  const cells = [];
  for (let row = SITE.ROW1; row >= SITE.ROW0; row--) {
    for (let col = SITE.COL0; col <= SITE.COL1; col++) {
      const r = cellRect(col, row);
      const kind = PLAN[row] ? PLAN[row][col - SITE.COL0] : rng.pick(FILL);
      const dist = SITE.ROW1 - row;                 // 离相机的排数
      const cell = {
        col, row, kind, ...r,
        seed: (rng() * 1e9) | 0,
        lod: dist >= 4 ? 2 : dist >= 2 ? 1 : 0,     // 远处降细节
        pad: kind !== 'yard' && kind !== 'hescoYard' ? rng.chance(0.6) : rng.chance(0.2),
        // 院墙：靠近相机的院子基本都围满，远处随机开口
        walls: {
          n: rng.chance(0.85), s: rng.chance(0.9),
          e: rng.chance(0.8), w: rng.chance(0.8),
        },
        gate: rng.pick(['n', 's', 'e', 'w']),
        rot: rng.chance(0.5) ? 0 : Math.PI / 2,
      };
      if (kind === 'helipad') { cell.pad = true; cell.walls = { n: false, s: true, e: true, w: true }; }
      if (kind === 'satcom') { cell.pad = true; }
      cells.push(cell);
    }
  }

  // 道路：网格间隙 + 周界外土路
  const roads = [];
  for (let col = SITE.COL0; col <= SITE.COL1 + 1; col++) {
    const x = col * SITE.PITCH_X + SITE.OX - SITE.PITCH_X / 2;
    roads.push({ x0: x - SITE.ROAD / 2, x1: x + SITE.ROAD / 2,
                 z0: SITE.ROW0 * SITE.PITCH_Z + SITE.OZ - 40,
                 z1: SITE.FRONT_WALL + 4, v: true });
  }
  for (let row = SITE.ROW0; row <= SITE.ROW1 + 1; row++) {
    const z = row * SITE.PITCH_Z + SITE.OZ - SITE.PITCH_Z / 2;
    roads.push({ x0: SITE.X_MIN, x1: SITE.X_MAX,
                 z0: z - SITE.ROAD / 2, z1: z + SITE.ROAD / 2, v: false });
  }
  // 前排院墙和 T 墙之间的通道
  roads.push({ x0: SITE.X_MIN, x1: SITE.X_MAX,
               z0: SITE.ROW1 * SITE.PITCH_Z + SITE.OZ + SITE.CELL_D / 2,
               z1: SITE.FRONT_WALL - 1, v: false });
  // 周界外土路
  const outer = { x0: SITE.X_MIN - 30, x1: SITE.X_MAX + 30,
                  z0: SITE.OUTER_ROAD - 7, z1: SITE.OUTER_ROAD + 7, dirt: true };

  // 岗楼：前周界均布 + 侧翼
  const towers = [
    { x: -118, z: SITE.FENCE - 5, ry: 0.06 },
    { x: -12, z: SITE.FENCE - 5, ry: -0.03 },
    { x: 108, z: SITE.FENCE - 5, ry: 0.02 },
    { x: 216, z: SITE.FENCE - 5, ry: 0.04 },
    { x: -228, z: SITE.FENCE - 5, ry: -0.05 },
    { x: SITE.X_MIN + 4, z: 96, ry: Math.PI / 2 },
    { x: SITE.X_MAX - 4, z: 74, ry: -Math.PI / 2 },
    { x: SITE.X_MIN + 4, z: -30, ry: Math.PI / 2 },
  ];

  const helipad = cells.find((c) => c.kind === 'helipad');

  return { cells, roads, outer, towers, helipad, SITE };
}
