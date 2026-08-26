/**
 * 几何求解 v2（与 whitebox-pipeline/server/solve.py 同算法的 TS 移植，无 VLM 路径）：
 *  1. 相机解析化（vFOV/俯仰/机高假设）⇒ 地面平面闭式已知
 *  2. 地面当标定靶：解析地面深度 vs DA 视差 → 单调分段线性「视差→深度」曲线
 *     （非参数吸收 DA 的非线性翘曲；带两轮残差修剪防前景家具污染）
 *  3. 点云（地面用解析深度，其余用曲线）→ yaw 最小包围矩形
 *  4. 壳 = 已标定地面的范围；墙 = 边界外竖直点堆积；视锥入射面强制开放
 *  5. 盲聚类 → 盒体；墙形聚类转墙；天花板碎片过滤
 */
import type { DepthResult, Detection, GeoParams, GeoResult, WhiteboxSpec, BoxInstance } from '../types';
import { vpCalibrate, pitchFromVp } from './calib';

const GRID_W = 288;
const FAR_CAP = 25.0;

function percentile(arr: number[] | Float64Array, q: number): number {
  const a = Array.from(arr).sort((x, y) => x - y);
  if (!a.length) return 0;
  return a[Math.min(a.length - 1, Math.max(0, Math.round(q * (a.length - 1))))];
}

class CameraRig {
  gw: number; gh: number; h: number; f: number;
  right: [number, number, number];
  down: [number, number, number];
  fwd: [number, number, number];
  ru: Float64Array; rv: Float64Array; // 每列/每行的射线系数
  constructor(gw: number, gh: number, vfovDeg: number, pitchDeg: number, h: number) {
    this.gw = gw; this.gh = gh; this.h = h;
    this.f = gh / 2 / Math.tan((vfovDeg / 2) * Math.PI / 180);
    const t = (pitchDeg * Math.PI) / 180;
    const ct = Math.cos(t), st = Math.sin(t);
    this.right = [1, 0, 0];
    this.down = [0, -ct, -st];
    this.fwd = [0, st, -ct];
    this.ru = new Float64Array(gw);
    this.rv = new Float64Array(gh);
    for (let x = 0; x < gw; x++) this.ru[x] = (x + 0.5 - gw / 2) / this.f;
    for (let y = 0; y < gh; y++) this.rv[y] = (y + 0.5 - gh / 2) / this.f;
  }
  /** 像素射线与地面 y=0 交点的相机系深度 t；不相交为 NaN */
  floorT(x: number, y: number): number {
    const Dy = this.rv[y] * this.down[1] + this.fwd[1];
    if (Dy >= -1e-6) return NaN;
    const t = -this.h / Dy;
    return t > 0.2 && t < 120 ? t : NaN;
  }
  /** (u,v)@相机系深度 z → 世界点 */
  pointAt(x: number, y: number, z: number): [number, number, number] {
    const xc = this.ru[x] * z, yc = this.rv[y] * z;
    return [
      xc * this.right[0] + yc * this.down[0] + z * this.fwd[0],
      this.h + xc * this.right[1] + yc * this.down[1] + z * this.fwd[1],
      xc * this.right[2] + yc * this.down[2] + z * this.fwd[2],
    ];
  }
}

class DepthCurve {
  kd: number[] = []; kz: number[] = [];
  constructor(d: number[], z: number[], bins = 24) {
    const idx = d.map((_, i) => i).sort((a, b) => d[a] - d[b]);
    const kd: number[] = [], kz: number[] = [];
    for (let i = 0; i < bins; i++) {
      const s = Math.floor((i * idx.length) / bins);
      const e = Math.min(idx.length, Math.floor(((i + 1) * idx.length) / bins) + 1);
      if (e - s < 3) continue;
      const seg = idx.slice(s, e);
      kd.push(percentile(seg.map((j) => d[j]), 0.5));
      kz.push(percentile(seg.map((j) => z[j]), 0.5));
    }
    // 强制随 d 递减（从远端回扫取累积最大）
    for (let i = kz.length - 2; i >= 0; i--) kz[i] = Math.max(kz[i], kz[i + 1]);
    // 去重 d
    for (let i = 0; i < kd.length; i++) {
      if (i === 0 || kd[i] - this.kd[this.kd.length - 1] > 1e-9) {
        this.kd.push(kd[i]); this.kz.push(kz[i]);
      }
    }
  }
  at(d: number): number {
    const kd = this.kd, kz = this.kz;
    if (!kd.length) return 5;
    let z: number;
    if (d <= kd[0]) {
      const slope = kd.length >= 2 ? Math.min((kz[1] - kz[0]) / (kd[1] - kd[0]), -1e-3) : -1e-3;
      z = kz[0] + (d - kd[0]) * slope;
    } else if (d >= kd[kd.length - 1]) {
      z = kz[kz.length - 1];
    } else {
      let lo = 0, hi = kd.length - 1;
      while (hi - lo > 1) {
        const m = (lo + hi) >> 1;
        if (kd[m] <= d) lo = m; else hi = m;
      }
      const t = (d - kd[lo]) / (kd[hi] - kd[lo]);
      z = kz[lo] + t * (kz[hi] - kz[lo]);
    }
    return Math.min(120, Math.max(0.15, z));
  }
}

export interface AutoCam {
  vfovDeg: number;
  pitchDeg: number;
  /** vp2=双正交消失点闭式解焦距；vp1=消失点定俯仰+焦距一维搜索；search=评分搜索兜底 */
  method: 'vp2' | 'vp1' | 'search';
}

/**
 * 自动相机估计（还原度闭环文档方案的浏览器版，按证据强度分层）：
 *  1. vp2：LSD-lite 线段 → RANSAC 双正交消失点 → 焦距闭式解 f²=-(v1-c)·(v2-c)，
 *     俯仰由纵深 VP 的行位置解出（地平线行 v_h = cy + f·tan(pitch)）
 *  2. vp1：一点透视（焦距不可观测）→ 俯仰与焦距绑定（同一 VP 行），
 *     焦距用「地面标定覆盖率 + 墙面竖直度」评分做一维搜索
 *  3. search：无可靠线段（涂抹画风）→ 原 (fov,pitch) 二维评分搜索
 * 机高不在此解：与焦距解耦，由检测尺度锚承担（对应文档的地平线比例法）。
 */
export function autoCalibrate(depth: DepthResult, image?: ImageData): AutoCam {
  const { d, gw, gh } = resample(depth);
  if (image) {
    const vp = vpCalibrate(image);
    if (vp) {
      if (vp.method === 'vp2' && vp.vfovDeg != null) {
        const pitch = pitchFromVp(vp.vpY, vp.detH, vp.vfovDeg);
        if (pitch > -35 && pitch < 25) {
          return { vfovDeg: Math.round(vp.vfovDeg), pitchDeg: Math.round(pitch), method: 'vp2' };
        }
      }
      // vp1：俯仰随焦距变化，但两者被 VP 行绑定成一维族 → 只搜焦距
      let best = { vfovDeg: 55, pitchDeg: 0, score: -Infinity };
      for (const fov of [36, 42, 48, 55, 62, 70, 78, 88, 98]) {
        const pitch = pitchFromVp(vp.vpY, vp.detH, fov);
        if (pitch < -35 || pitch > 25) continue;
        const s = scoreCamera(d, gw, gh, fov, pitch);
        if (s > best.score) best = { vfovDeg: fov, pitchDeg: pitch, score: s };
      }
      if (best.score > -Infinity) {
        return { vfovDeg: best.vfovDeg, pitchDeg: Math.round(best.pitchDeg), method: 'vp1' };
      }
    }
  }
  let best = { vfovDeg: 55, pitchDeg: -5, score: -Infinity };
  const tryCam = (fov: number, pitch: number) => {
    const s = scoreCamera(d, gw, gh, fov, pitch);
    if (s > best.score) best = { vfovDeg: fov, pitchDeg: pitch, score: s };
  };
  for (const fov of [40, 48, 55, 62, 70, 80, 92]) {
    for (const pitch of [-22, -16, -10, -5, 0, 6, 12]) tryCam(fov, pitch);
  }
  const f0 = best.vfovDeg, p0 = best.pitchDeg;
  for (const fov of [f0 - 4, f0, f0 + 4]) {
    for (const pitch of [p0 - 3, p0, p0 + 3]) tryCam(fov, pitch);
  }
  return { vfovDeg: best.vfovDeg, pitchDeg: best.pitchDeg, method: 'search' };
}

function scoreCamera(d: Float64Array, gw: number, gh: number, fov: number, pitch: number): number {
  if (fov < 25 || fov > 110 || pitch < -35 || pitch > 25) return -Infinity;
  const rig = new CameraRig(gw, gh, fov, pitch, 1.6);
  // 快速地面标定（粗 bin + 一轮修剪）
  const bd: number[] = [], bt: number[] = [];
  for (let y = Math.floor(gh * 0.72); y < gh; y++) {
    for (let x = Math.floor(gw * 0.25); x < Math.floor(gw * 0.75); x += 3) {
      const t = rig.floorT(x, y);
      if (!Number.isNaN(t)) { bd.push(d[y * gw + x]); bt.push(t); }
    }
  }
  if (bd.length < 60) return -Infinity;
  let curve = new DepthCurve(bd, bt, 16);
  const res = bd.map((dv, i) => Math.abs(curve.at(dv) - bt[i]) / Math.max(bt[i], 0.5));
  const thr = Math.max(0.12, percentile(res, 0.5) * 2);
  const kd: number[] = [], kt: number[] = [];
  for (let i = 0; i < bd.length; i++) if (res[i] < thr) { kd.push(bd[i]); kt.push(bt[i]); }
  if (kd.length >= 60) curve = new DepthCurve(kd, kt, 16);
  const isFloorLike = (x: number, y: number) => {
    const t = rig.floorT(x, y);
    if (Number.isNaN(t)) return false;
    return Math.abs(curve.at(d[y * gw + x]) - t) / Math.max(t, 0.5) < 0.12;
  };
  // 覆盖率：地平线以下抽样像素中与地面标定一致的比例
  let below = 0, cons = 0;
  for (let y = 0; y < gh; y += 3) {
    for (let x = 0; x < gw; x += 3) {
      if (Number.isNaN(rig.floorT(x, y))) continue;
      below++;
      if (isFloorLike(x, y)) cons++;
    }
  }
  const coverage = below > 20 ? cons / below : 0;
  // 竖直度：中带非地面像素的世界法向 |n_y| 均值（墙/立面应≈0）
  const y0 = Math.floor(gh * 0.12), y1 = Math.floor(gh * 0.68);
  const st = 2;
  let vsum = 0, vcnt = 0;
  for (let y = y0 + st; y < y1 - st; y += st) {
    for (let x = st; x < gw - st; x += st) {
      if (isFloorLike(x, y)) continue;
      const pC = rig.pointAt(x, y, curve.at(d[y * gw + x]));
      const pR = rig.pointAt(x + st, y, curve.at(d[y * gw + x + st]));
      const pD = rig.pointAt(x, y + st, curve.at(d[(y + st) * gw + x]));
      const ux = pR[0] - pC[0], uy = pR[1] - pC[1], uz = pR[2] - pC[2];
      const vx = pD[0] - pC[0], vy = pD[1] - pC[1], vz = pD[2] - pC[2];
      const ny = uz * vx - ux * vz;
      const mag = Math.hypot(uy * vz - uz * vy, ny, ux * vy - uy * vx);
      if (!(mag > 1e-9) || !Number.isFinite(mag)) continue;
      vsum += Math.abs(ny) / mag;
      vcnt++;
    }
  }
  const vert = vcnt > 30 ? vsum / vcnt : 1;
  const prior = 0.12 * ((fov - 60) / 40) ** 2 + 0.12 * ((pitch + 5) / 20) ** 2;
  return coverage + 0.8 * (1 - vert) - prior;
}

function resample(depth: DepthResult): { d: Float64Array; gw: number; gh: number } {
  const w0 = depth.width, h0 = depth.height;
  let gw = GRID_W;
  let gh = Math.round((gw * h0) / w0);
  if (gh < 64) { gh = 64; gw = Math.min(512, Math.round((gh * w0) / h0)); }
  const d = new Float64Array(gw * gh);
  for (let y = 0; y < gh; y++) {
    for (let x = 0; x < gw; x++) {
      const mx = Math.min(w0 - 1.001, Math.max(0, ((x + 0.5) * w0) / gw - 0.5));
      const my = Math.min(h0 - 1.001, Math.max(0, ((y + 0.5) * h0) / gh - 0.5));
      const x0 = Math.floor(mx), y0 = Math.floor(my);
      const fx = mx - x0, fy = my - y0;
      const i00 = y0 * w0 + x0;
      d[y * gw + x] =
        depth.data[i00] * (1 - fx) * (1 - fy) +
        depth.data[i00 + 1] * fx * (1 - fy) +
        depth.data[i00 + w0] * (1 - fx) * fy +
        depth.data[i00 + w0 + 1] * fx * fy;
    }
  }
  return { d, gw, gh };
}

export function solveGeometry(
  depth: DepthResult,
  params: GeoParams,
  detections?: Detection[],
): GeoResult {
  const t0 = performance.now();
  const { d, gw, gh } = resample(depth);
  const rig = new CameraRig(gw, gh, params.vfovDeg, params.pitchDeg, params.camHeightM);

  // ---- 1) 地面自标定曲线 ----
  const bootD: number[] = [], bootT: number[] = [];
  for (let y = Math.floor(gh * 0.72); y < gh; y++) {
    const t = rig.floorT(0, y);
    if (Number.isNaN(t)) continue;
    for (let x = Math.floor(gw * 0.25); x < Math.floor(gw * 0.75); x++) {
      const tf = rig.floorT(x, y);
      if (!Number.isNaN(tf)) { bootD.push(d[y * gw + x]); bootT.push(tf); }
    }
  }
  if (bootD.length < 80) throw new Error('画面底部没有可用的地面射线，请调整俯仰角');
  let bd = bootD, bt = bootT;
  let curve = new DepthCurve(bd, bt);
  for (let round = 0; round < 2; round++) {
    const res = bd.map((dv, i) => Math.abs(curve.at(dv) - bt[i]) / Math.max(bt[i], 0.5));
    const thr = Math.max(0.12, percentile(res, 0.5) * 2);
    const keepIdx = res.map((r, i) => i).filter((i) => res[i] < thr);
    if (keepIdx.length < 60 || keepIdx.length === bd.length) break;
    bd = keepIdx.map((i) => bd[i]);
    bt = keepIdx.map((i) => bt[i]);
    curve = new DepthCurve(bd, bt);
  }
  // 一致性检验扩大地面集合并重拟合一次
  const relErr = (x: number, y: number) => {
    const tf = rig.floorT(x, y);
    if (Number.isNaN(tf)) return Infinity;
    return Math.abs(curve.at(d[y * gw + x]) - tf) / Math.max(tf, 0.5);
  };
  const collectFloor = () => {
    const fd: number[] = [], ft: number[] = [];
    const mask = new Uint8Array(gw * gh);
    for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) {
      if (relErr(x, y) < 0.10) {
        mask[y * gw + x] = 1;
        fd.push(d[y * gw + x]); ft.push(rig.floorT(x, y));
      }
    }
    return { fd, ft, mask };
  };
  let fl = collectFloor();
  if (fl.fd.length > bootD.length * 1.2) {
    curve = new DepthCurve(fl.fd, fl.ft);
    fl = collectFloor();
  }
  const floorMask = fl.mask;

  // ---- 2) 点云 ----
  const world = new Float64Array(gw * gh * 3).fill(NaN);
  for (let y = 0; y < gh; y++) {
    for (let x = 0; x < gw; x++) {
      const i = y * gw + x;
      const isFloor = floorMask[i] === 1;
      const z = isFloor ? rig.floorT(x, y) : curve.at(d[i]);
      if (Number.isNaN(z)) continue;
      const p = rig.pointAt(x, y, z);
      if (isFloor) p[1] = 0;
      // 初筛用 2 倍宽松截断，尺度锚定标后再做严格截断
      if (Math.abs(p[0]) > FAR_CAP * 2 || Math.abs(p[2]) > FAR_CAP * 2 || p[1] < -2.0 || p[1] > 60) continue;
      world[i * 3] = p[0]; world[i * 3 + 1] = p[1]; world[i * 3 + 2] = p[2];
    }
  }
  const n = gw * gh;
  let validIdx: number[] = [];
  for (let i = 0; i < n; i++) if (!Number.isNaN(world[i * 3 + 2])) validIdx.push(i);
  if (validIdx.length < 100) throw new Error('有效点太少，可能是深度失效的图');

  // ---- 3) yaw ----
  const step = Math.max(1, Math.floor(validIdx.length / 4000));
  const subs = validIdx.filter((_, k) => k % step === 0);
  let bestYaw = 0, bestArea = Infinity;
  for (let deg = 0; deg < 90; deg++) {
    const th = (deg * Math.PI) / 180;
    const c = Math.cos(th), s = Math.sin(th);
    const xs: number[] = [], zs: number[] = [];
    for (const i of subs) {
      const x = world[i * 3], z = world[i * 3 + 2];
      xs.push(x * c - z * s);
      zs.push(x * s + z * c);
    }
    const area = (percentile(xs, 0.98) - percentile(xs, 0.02)) *
                 (percentile(zs, 0.98) - percentile(zs, 0.02));
    if (area < bestArea) { bestArea = area; bestYaw = deg; }
  }
  const th = (bestYaw * Math.PI) / 180;
  const c = Math.cos(th), s = Math.sin(th);
  for (const i of validIdx) {
    const x = world[i * 3], z = world[i * 3 + 2];
    world[i * 3] = x * c - z * s;
    world[i * 3 + 2] = x * s + z * c;
  }
  const rotV = (v: [number, number, number]): [number, number, number] =>
    [v[0] * c - v[2] * s, v[1], v[0] * s + v[2] * c];
  const camRight = rotV(rig.right), camDown = rotV(rig.down), camFwd = rotV(rig.fwd);

  // ---- 3.5) 检测实例：语义类别 → 复合模板 + footprint 闭式解 + 人因尺度锚 ----
  const detInstances: BoxInstance[] = [];
  const detMeta: { rawH: number; minH: number; maxH: number }[] = [];
  const claimed = new Uint8Array(n);
  const anchorRatios: number[] = [];
  if (detections && detections.length) {
    type Cat = { kind: NonNullable<BoxInstance['kind']>; grounded: boolean; canonH?: number; minH: number; maxH: number };
    const CAT: Record<string, Cat> = {
      'dining table': { kind: 'table', grounded: true, canonH: 0.75, minH: 0.5, maxH: 1.2 },
      bench: { kind: 'table', grounded: true, minH: 0.35, maxH: 1.0 },
      chair: { kind: 'chair', grounded: true, canonH: 0.85, minH: 0.55, maxH: 1.3 },
      couch: { kind: 'sofa', grounded: true, canonH: 0.8, minH: 0.5, maxH: 1.2 },
      'potted plant': { kind: 'plant', grounded: true, minH: 0.2, maxH: 3.5 },
      tv: { kind: 'tv', grounded: false, minH: 0.2, maxH: 1.5 },
      person: { kind: 'person', grounded: true, canonH: 1.68, minH: 1.2, maxH: 2.0 },
      refrigerator: { kind: 'box', grounded: true, minH: 1.2, maxH: 2.2 },
      vase: { kind: 'plant', grounded: true, minH: 0.15, maxH: 1.2 },
      bed: { kind: 'box', grounded: true, minH: 0.3, maxH: 1.0 },
    };
    const sorted = detections
      .filter((o) => CAT[o.label] && o.score >= 0.4)
      .sort((a, b) => b.score - a.score)
      .slice(0, 24);
    let dseq = 0;
    for (const o of sorted) {
      const cat = CAT[o.label];
      const bx0 = Math.max(0, Math.floor(o.box[0] * gw));
      const bx1 = Math.min(gw - 1, Math.max(bx0 + 2, Math.ceil(o.box[2] * gw)));
      const by0 = Math.max(0, Math.floor(o.box[1] * gh));
      const by1 = Math.min(gh - 1, Math.max(by0 + 2, Math.ceil(o.box[3] * gh)));
      const uc = (bx0 + bx1) >> 1;
      // bbox 内视差带的标定深度（既做兜底 z，也做进深与 footprint 病态校验）
      const dAll: number[] = [];
      for (let y = by0; y <= by1; y++) for (let x = bx0; x <= bx1; x += 2) dAll.push(d[y * gw + x]);
      if (!dAll.length) continue;
      const med = percentile(dAll, 0.5);
      const q1 = percentile(dAll, 0.25), q3 = percentile(dAll, 0.75);
      const bandArr = dAll.filter((v) => Math.abs(v - med) < Math.max(1.2 * (q3 - q1), 1e-4));
      const zb = (bandArr.length > 8 ? bandArr : dAll).map((v) => curve.at(v));
      const zD = curve.at(med);
      // 相机系深度：落地物 footprint 射线∩地面；脚部被遮挡时 floorT 病态偏大 → 回退视差深度
      let zObj = cat.grounded ? rig.floorT(uc, by1) : NaN;
      if (Number.isNaN(zObj) || zObj > 2 * zD) zObj = zD;
      if (!(zObj > 0.2) || zObj > FAR_CAP * 1.5) continue;
      const pbl = rig.pointAt(bx0, by1, zObj);
      const pbr = rig.pointAt(bx1, by1, zObj);
      const ptc = rig.pointAt(uc, by0, zObj);
      const pbc = rig.pointAt(uc, by1, zObj);
      const w = Math.hypot(pbr[0] - pbl[0], pbr[1] - pbl[1], pbr[2] - pbl[2]);
      let base: number, top: number;
      if (cat.grounded) { base = 0; top = Math.max(0.15, ptc[1]); }
      else {
        base = Math.max(0, Math.min(pbc[1], ptc[1]));
        top = Math.max(pbc[1], ptc[1], base + 0.1);
      }
      const rawH = top - base;
      const h = Math.min(Math.max(rawH, cat.minH), cat.maxH);
      const spread = percentile(zb, 0.9) - percentile(zb, 0.1);
      const dep = Math.min(Math.max(spread, 0.15), Math.max(0.3, 2.0 * w));
      // 落地物：bbox 底缘是前下沿接地线，体块向远离相机方向展开
      let center: [number, number, number] = [(pbl[0] + pbr[0]) / 2, 0, (pbl[2] + pbr[2]) / 2];
      if (cat.grounded) {
        const nrm = Math.hypot(center[0], center[2]);
        if (nrm > 1e-6) {
          center = [center[0] + (center[0] / nrm) * (dep / 2), 0, center[2] + (center[2] / nrm) * (dep / 2)];
        }
      }
      const cwp = rotV(center);
      detInstances.push({
        id: `det_${dseq++}_${o.label.replace(/\s+/g, '_')}`,
        pos: [cwp[0], base, cwp[2]],
        dims: [Math.max(0.1, w), h, dep],
        baseY: base,
        points: (bx1 - bx0) * (by1 - by0),
        kind: cat.kind,
        label: o.label,
        source: 'detect',
        yawYRad: -th, // 在预 yaw 系求解的朝向，渲染时回转
      });
      detMeta.push({ rawH, minH: cat.minH, maxH: cat.maxH });
      // 尺度锚：bbox 被画幅裁切（人半身等）时高度失真，不入锚
      const clipped = o.box[3] >= 0.99 || o.box[1] <= 0.005;
      if (cat.canonH && cat.grounded && !clipped && rawH > 0.25 && rawH < 4) {
        const r = cat.canonH / rawH;
        if (r > 0.4 && r < 2.5) anchorRatios.push(r);
      }
      // 认领：只认领与该实例深度一致的像素（防大 bbox 吞掉后方家具的分割段）
      for (let y = by0; y <= by1; y++) {
        for (let x = bx0; x <= bx1; x++) {
          if (Math.abs(curve.at(d[y * gw + x]) - zObj) < 0.3 * zObj) claimed[y * gw + x] = 1;
        }
      }
    }
  }
  // 人因尺度锚：已知高度类别（人/椅/桌）中位比值整体校尺
  let scaleK = 1;
  if (anchorRatios.length) {
    anchorRatios.sort((a2, b2) => a2 - b2);
    scaleK = anchorRatios[anchorRatios.length >> 1];
    for (const i of validIdx) {
      world[i * 3] *= scaleK;
      world[i * 3 + 1] *= scaleK;
      world[i * 3 + 2] *= scaleK;
    }
    detInstances.forEach((b, bi) => {
      const m = detMeta[bi];
      b.pos = [b.pos[0] * scaleK, b.pos[1] * scaleK, b.pos[2] * scaleK];
      // 高度钳制在定标后的米制下执行（先钳后缩会破坏锚语义）
      b.dims = [
        b.dims[0] * scaleK,
        Math.min(Math.max(m.rawH * scaleK, m.minH), m.maxH),
        b.dims[2] * scaleK,
      ];
      b.baseY *= scaleK;
    });
  }
  const camH = params.camHeightM * scaleK;
  // 定标后重新执行舞台截断（截断距离不随尺度漂移）
  for (const i of validIdx) {
    const X = world[i * 3], Y = world[i * 3 + 1], Z = world[i * 3 + 2];
    if (Math.abs(X) > FAR_CAP || Math.abs(Z) > FAR_CAP || Y < -1.0 || Y > 30) {
      world[i * 3 + 2] = NaN;
    }
  }
  validIdx = validIdx.filter((i) => !Number.isNaN(world[i * 3 + 2]));

  // ---- 4) 壳 ----
  const fx: number[] = [], fz: number[] = [];
  const ax: number[] = [], ay: number[] = [], az: number[] = [];
  for (const i of validIdx) {
    ax.push(world[i * 3]); ay.push(world[i * 3 + 1]); az.push(world[i * 3 + 2]);
    if (floorMask[i]) { fx.push(world[i * 3]); fz.push(world[i * 3 + 2]); }
  }
  let xmin: number, xmax: number, zmin: number, zmax: number;
  if (fx.length > 150) {
    xmin = percentile(fx, 0.02) - 0.1; xmax = percentile(fx, 0.98) + 0.1;
    zmin = percentile(fz, 0.02) - 0.1; zmax = percentile(fz, 0.98) + 0.1;
  } else {
    xmin = percentile(ax, 0.02); xmax = percentile(ax, 0.98);
    zmin = percentile(az, 0.02); zmax = percentile(az, 0.98);
  }
  // 天花板：高处 y 直方图峰
  const high = ay.filter((y) => y > 1.5);
  let ceilY = Math.max(2.4, percentile(ay, 0.98));
  let hasCeiling = false;
  if (high.length > validIdx.length * 0.02) {
    const hMax = Math.max(...high);
    const bins = Math.max(6, Math.floor((hMax - 1.5) / 0.15) + 1);
    const cnt = new Array(bins).fill(0);
    for (const y of high) {
      cnt[Math.min(bins - 1, Math.floor(((y - 1.5) / Math.max(1e-6, hMax - 1.5)) * bins))]++;
    }
    let peak = 0;
    for (let i = 1; i < bins; i++) if (cnt[i] > cnt[peak]) peak = i;
    if (cnt[peak] > validIdx.length * 0.015) {
      ceilY = 1.5 + ((peak + 0.5) / bins) * (hMax - 1.5);
      hasCeiling = true;
    }
  }
  ceilY = Math.max(2.0, ceilY);
  // 墙：边界带内的中高点堆积
  const midIdx = validIdx.filter((i) => {
    const y = world[i * 3 + 1];
    return y > 0.3 && y < Math.max(ceilY - 0.15, 1.0);
  });
  const thrW = Math.max(30, validIdx.length * 0.008);
  const countBand = (get: (i: number) => number, bound: number, hi: boolean) => {
    let cnt2 = 0;
    for (const i of midIdx) {
      const v = get(i);
      if (hi ? v > bound - 0.35 && v < bound + 0.8 : v < bound + 0.35 && v > bound - 0.8) cnt2++;
    }
    return cnt2 > thrW;
  };
  const gx = (i: number) => world[i * 3], gz = (i: number) => world[i * 3 + 2];
  const walls = {
    px: countBand(gx, xmax, true),
    nx: countBand(gx, xmin, false),
    pz: countBand(gz, zmax, true),
    nz: countBand(gz, zmin, false),
  };
  const openFace = () => {
    if (0 >= xmax) walls.px = false;
    if (0 <= xmin) walls.nx = false;
    if (0 >= zmax) walls.pz = false;
    if (0 <= zmin) walls.nz = false;
  };
  openFace();
  // 壳外裁剪
  for (const i of validIdx) {
    const x = world[i * 3], y = world[i * 3 + 1], z = world[i * 3 + 2];
    if (x < xmin - 0.25 || x > xmax + 0.25 || z < zmin - 0.25 || z > zmax + 0.25 || y > ceilY + 0.3) {
      world[i * 3 + 2] = NaN;
    }
  }

  // ---- 5) 视差分割 → 盒体 ----
  // 结构在【图像域】决定：视差不连续处切开，与相机参数无关。
  // 因此微调 FOV/俯仰/机高只会连续地移动/缩放盒体，不会重构切分（稳定性来源）。
  const flatSample: number[] = [];
  for (let k = 0; k < 8000; k++) flatSample.push(d[Math.floor((k * n) / 8000)]);
  const span = percentile(flatSample, 0.95) - percentile(flatSample, 0.05);
  const tau = (0.012 * Math.max(1e-6, span)) / Math.max(0.3, params.granularity);
  const label = new Int32Array(n).fill(-1);
  const segs: number[][] = [];
  for (let i0 = 0; i0 < n; i0++) {
    if (label[i0] !== -1) continue;
    const seg: number[] = [];
    const stack = [i0];
    label[i0] = segs.length;
    while (stack.length) {
      const cIdx = stack.pop()!;
      seg.push(cIdx);
      const cx = cIdx % gw, cy = Math.floor(cIdx / gw);
      const dc = d[cIdx];
      if (cx > 0) tryLink(cIdx - 1);
      if (cx < gw - 1) tryLink(cIdx + 1);
      if (cy > 0) tryLink(cIdx - gw);
      if (cy < gh - 1) tryLink(cIdx + gw);
      function tryLink(nb: number) {
        if (label[nb] === -1 && Math.abs(d[nb] - dc) <= tau) {
          label[nb] = segs.length;
          stack.push(nb);
        }
      }
    }
    segs.push(seg);
  }
  const instances: BoxInstance[] = [];
  let seq = 0;
  for (const seg of segs) {
    if (seg.length < 30) continue;
    let floorCnt = 0, claimedCnt = 0;
    const mx: number[] = [], my: number[] = [], mz: number[] = [];
    for (const i of seg) {
      if (floorMask[i]) floorCnt++;
      if (claimed[i]) claimedCnt++;
      if (!Number.isNaN(world[i * 3 + 2])) {
        mx.push(world[i * 3]); my.push(world[i * 3 + 1]); mz.push(world[i * 3 + 2]);
      }
    }
    if (claimedCnt > seg.length * 0.4) continue; // 已被检测实例认领
    if (floorCnt > seg.length * 0.5) continue;   // 地面段
    if (mx.length < 25) continue;                // 壳外/远景段
    const x0 = percentile(mx, 0.03), x1 = percentile(mx, 0.97);
    const z0 = percentile(mz, 0.03), z1 = percentile(mz, 0.97);
    let top = Math.min(percentile(my, 0.96), ceilY);
    let base = percentile(my, 0.06);
    if (base < 0.3) base = 0;
    const w = Math.max(0.05, x1 - x0), dd = Math.max(0.05, z1 - z0);
    const h = Math.max(0.05, top - base);
    if (top > ceilY * 0.85 && h < 0.25) continue; // 天花板皮
    if (Math.max(w, dd, h) < params.minObjSizeM) continue;
    // 高处悬浮的小体块 → 吊灯模板（锥罩+吊杆）
    const isLamp = base > Math.max(1.5, ceilY * 0.45) && w < 1.3 && dd < 1.3 && h < 1.3;
    instances.push({
      id: `seg_${seq++}`,
      pos: [(x0 + x1) / 2, base, (z0 + z1) / 2],
      dims: [w, h, dd],
      baseY: base,
      points: mx.length,
      kind: isLamp ? 'lamp' : 'box',
      source: 'segment',
    });
  }
  // 墙形聚类 → 墙
  const kept: BoxInstance[] = [];
  for (const b of instances) {
    const [w, h, dd] = b.dims;
    const [cx, , cz] = b.pos;
    let side: keyof typeof walls | null = null;
    if (h >= 1.2) {
      if (w > 0.55 * (xmax - xmin) && dd < Math.max(0.35 * w, 0.6)) {
        if (zmax - (cz + dd / 2) < 1.2) side = 'pz';
        else if (cz - dd / 2 - zmin < 1.2) side = 'nz';
      }
      if (!side && dd > 0.55 * (zmax - zmin) && w < Math.max(0.35 * dd, 0.6)) {
        if (xmax - (cx + w / 2) < 1.2) side = 'px';
        else if (cx - w / 2 - xmin < 1.2) side = 'nx';
      }
    }
    if (side) walls[side] = true;
    else kept.push(b);
  }
  openFace();
  // 墙皮碎片过滤（墙标志已定稿）：已检出墙面前 1m 内宽而薄的高片 = 深度曲线的墙涂抹，并入墙
  const dedupe = kept.filter((b) => {
    const [w, h, dd] = b.dims;
    const x0 = b.pos[0] - w / 2, x1 = b.pos[0] + w / 2;
    const z0 = b.pos[2] - dd / 2, z1 = b.pos[2] + dd / 2;
    const skin =
      (walls.nz && dd < 0.3 && h > 0.7 && w > 1.2 && z0 - zmin < 1.0) ||
      (walls.pz && dd < 0.3 && h > 0.7 && w > 1.2 && zmax - z1 < 1.0) ||
      (walls.nx && w < 0.3 && h > 0.7 && dd > 1.2 && x0 - xmin < 1.0) ||
      (walls.px && w < 0.3 && h > 0.7 && dd > 1.2 && xmax - x1 < 1.0);
    return !skin;
  });
  dedupe.sort((a, b) => b.dims[0] * b.dims[1] * b.dims[2] - a.dims[0] * a.dims[1] * a.dims[2]);
  dedupe.length = Math.min(dedupe.length, params.maxBoxes);

  // ---- 5.5) 天花板梁架：顶带横向梯度的周期性 → 梁阵列（设计书"重复结构走 1D 自相关"）----
  // 仅在 yaw 近对齐时启用（梁在图像列方向的周期只有此时对应最终系 x 向阵列）
  const structInstances: BoxInstance[] = [];
  if (hasCeiling && bestYaw <= 8) {
    const bandY = Math.max(4, Math.floor(gh * 0.2));
    const prof = new Float64Array(gw);
    for (let x = 1; x < gw; x++) {
      let g = 0;
      for (let y = 0; y < bandY; y++) g += Math.abs(d[y * gw + x] - d[y * gw + x - 1]);
      prof[x] = g / bandY;
    }
    // 统计一律跳过 prof[0]（伪样本），自相关按项数归一
    let mean = 0;
    for (let x = 1; x < gw; x++) mean += prof[x];
    mean /= gw - 1;
    const cen = new Float64Array(gw);
    for (let x = 1; x < gw; x++) cen[x] = prof[x] - mean;
    let var0 = 0;
    for (let x = 1; x < gw; x++) var0 += cen[x] * cen[x];
    const varN = var0 / (gw - 1);
    let bestP = 0, bestR = 0;
    for (let p = 8; p < gw / 4; p++) {
      let s2 = 0, cnt2 = 0;
      for (let x = 1; x + p < gw; x++) { s2 += cen[x] * cen[x + p]; cnt2++; }
      const r = s2 / Math.max(1, cnt2) / Math.max(1e-9, varN);
      if (r > bestR) { bestR = r; bestP = p; }
    }
    // 幅度门槛：梁是几何起伏，峰值梯度须显著高于顶带平均（防纹理泄漏进深度的伪周期）
    let profSorted = Array.from(prof.slice(1)).sort((a, b) => a - b);
    const profMed = profSorted[profSorted.length >> 1];
    const profP90 = profSorted[Math.floor(profSorted.length * 0.9)];
    const amplitudeOk = profP90 > 3.0 * Math.max(1e-9, profMed);
    if (amplitudeOk && bestR > 0.45 && bestP > 0 && gw / bestP >= 4) {
      // 相位对齐：取 prof 局部峰列，经天花板平面反投影出各梁的世界 x
      let std = Math.sqrt(varN);
      const minSep = Math.max(3, Math.floor(bestP * 0.6));
      const peaks: number[] = [];
      for (let x = 2; x < gw - 1; x++) {
        if (prof[x] > mean + 0.5 * std && prof[x] >= prof[x - 1] && prof[x] >= prof[x + 1]) {
          if (!peaks.length || x - peaks[peaks.length - 1] >= minSep) peaks.push(x);
          else if (prof[x] > prof[peaks[peaks.length - 1]]) peaks[peaks.length - 1] = x;
        }
      }
      // 几何判据：梁向下凸出 ⇒ 峰列的世界高度比谷列低 ≥7cm；纹理伪周期只有毫米级
      const colY = (x: number) => {
        let s3 = 0, c3 = 0;
        for (let y = 0; y < bandY; y++) {
          const i = y * gw + x;
          if (!Number.isNaN(world[i * 3 + 2])) { s3 += world[i * 3 + 1]; c3++; }
        }
        return c3 > 2 ? s3 / c3 : NaN;
      };
      const drops: number[] = [];
      for (const px of peaks) {
        const yp = colY(px);
        const yv = colY(Math.min(gw - 2, px + (bestP >> 1)));
        if (Number.isFinite(yp) && Number.isFinite(yv)) drops.push(yv - yp);
      }
      drops.sort((a2, b2) => a2 - b2);
      const dropMed = drops.length ? drops[drops.length >> 1] : 0;
      if (dropMed < 0.07) peaks.length = 0;

      const yRow = Math.max(1, Math.floor(gh * 0.06));
      const ceilPre = ceilY / scaleK; // rig 在预定标单位下工作
      const denom = rig.rv[yRow] * rig.down[1] + rig.fwd[1];
      let k = 0;
      for (const px of peaks) {
        if (Math.abs(denom) < 1e-6) break;
        const t = (ceilPre - rig.h) / denom;
        if (!(t > 0.2 && t < FAR_CAP * 2)) continue;
        const pw = rotV(rig.pointAt(px, yRow, t));
        const bx = pw[0] * scaleK;
        if (bx < xmin + 0.1 || bx > xmax - 0.1) continue;
        structInstances.push({
          id: `beam_${k++}`, kind: 'beam', source: 'struct', label: 'beam',
          pos: [bx, ceilY - 0.22, (zmin + zmax) / 2],
          dims: [0.16, 0.22, Math.max(0.5, zmax - zmin)],
          baseY: ceilY - 0.22, points: 0,
        });
        if (k >= 14) break;
      }
      if (k < 3) structInstances.length = 0; // 峰太少不成阵列
    }
  }
  const finalInstances = [...detInstances, ...structInstances, ...dedupe];

  // ---- 调试点云 ----
  const alive = validIdx.filter((i) => !Number.isNaN(world[i * 3 + 2]));
  const dstep = Math.max(1, Math.floor(alive.length / 50000));
  const dbg = new Float32Array(Math.ceil(alive.length / dstep) * 3);
  let di = 0;
  for (let k = 0; k < alive.length; k += dstep) {
    const i = alive[k];
    dbg[di++] = world[i * 3]; dbg[di++] = world[i * 3 + 1]; dbg[di++] = world[i * 3 + 2];
  }

  const spec: WhiteboxSpec = {
    meta: {
      generator: 'whitebox-web',
      version: '0.4.0',
      createdWith: { vfovDeg: params.vfovDeg, camHeightM: params.camHeightM },
    },
    camera: {
      vfovDeg: params.vfovDeg,
      pos: [0, camH, 0],
      basis: [...camRight, ...camDown, ...camFwd],
      aspect: (depth.origWidth ?? depth.width) / (depth.origHeight ?? depth.height),
    },
    room: {
      min: [xmin, 0, zmin],
      max: [xmax, hasCeiling ? ceilY : Math.max(ceilY, 2.4), zmax],
      walls,
      hasCeiling,
    },
    instances: finalInstances,
  };
  return {
    spec,
    debug: {
      points: dbg.subarray(0, di) as Float32Array,
      floorInlierRatio: fl.fd.length / Math.max(1, validIdx.length),
      affine: { a: 0, b: 0, zNear: 0, zFar: 0, score: 0 },
      yawDeg: bestYaw,
      ms: performance.now() - t0,
    },
  };
}
