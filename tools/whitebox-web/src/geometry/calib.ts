/**
 * 单目相机标定：LSD-lite 线段检测 + RANSAC 消失点（还原度闭环文档方案的浏览器版）。
 *
 * - 线段：梯度水平线(level-line)区域生长（LSD 思路的简化实现），PCA 拟合
 * - 消失点：随机抽两条线段求交，按角度残差计线段长度加权内点，取前两个 VP
 * - 双正交 VP ⇒ 焦距闭式解 f² = -(v1-c)·(v2-c)；俯仰 = atan((vpY-cy)/f)
 * - 机高不在此解（与焦距解耦，由检测尺度锚承担——对应文档的地平线比例法）
 */

export interface Seg {
  x0: number; y0: number; x1: number; y1: number;
  mx: number; my: number;   // 中点
  ux: number; uy: number;   // 单位方向
  len: number;
}

export interface VP { x: number; y: number; score: number; inliers: number }

const TAU_DEG = 22.5;          // 区域生长角容差
const MIN_LEN = 16;            // 最短线段（检测分辨率像素）
const INLIER_SIN = Math.sin((2.5 * Math.PI) / 180);

/** RGBA ImageData → 缩放灰度 */
function toGray(img: ImageData, tw: number): { g: Float32Array; w: number; h: number } {
  const w = tw;
  const h = Math.max(64, Math.round((img.height / img.width) * tw));
  const g = new Float32Array(w * h);
  const sx = img.width / w, sy = img.height / h;
  const src = img.data;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const px = Math.min(img.width - 1, Math.floor((x + 0.5) * sx));
      const py = Math.min(img.height - 1, Math.floor((y + 0.5) * sy));
      const i = (py * img.width + px) * 4;
      g[y * w + x] = 0.299 * src[i] + 0.587 * src[i + 1] + 0.114 * src[i + 2];
    }
  }
  return { g, w, h };
}

function angleDiffPi(a: number, b: number): number {
  let d = Math.abs(a - b) % Math.PI;
  return Math.min(d, Math.PI - d);
}

/** LSD-lite：返回线段（检测坐标系 w×h） */
export function detectSegments(img: ImageData, targetW = 512): { segs: Seg[]; w: number; h: number } {
  const { g, w, h } = toGray(img, targetW);
  const n = w * h;
  const mag = new Float32Array(n);
  const ang = new Float32Array(n); // level-line 方向（梯度垂直方向），[0,π)
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const gx =
        g[i - w + 1] + 2 * g[i + 1] + g[i + w + 1] -
        g[i - w - 1] - 2 * g[i - 1] - g[i + w - 1];
      const gy =
        g[i + w - 1] + 2 * g[i + w] + g[i + w + 1] -
        g[i - w - 1] - 2 * g[i - w] - g[i - w + 1];
      mag[i] = Math.hypot(gx, gy);
      let a = Math.atan2(gy, gx) + Math.PI / 2; // 垂直于梯度
      while (a < 0) a += Math.PI;
      while (a >= Math.PI) a -= Math.PI;
      ang[i] = a;
    }
  }
  // 阈值：均值的 2 倍
  let sum = 0;
  for (let i = 0; i < n; i++) sum += mag[i];
  const thr = Math.max(8, (sum / n) * 2);
  // 按幅值降序的种子（桶排序）
  const order: number[] = [];
  {
    const buckets: number[][] = Array.from({ length: 64 }, () => []);
    let mmax = 0;
    for (let i = 0; i < n; i++) if (mag[i] > mmax) mmax = mag[i];
    if (mmax <= 0) return { segs: [], w, h };
    for (let i = 0; i < n; i++) {
      if (mag[i] > thr) buckets[Math.min(63, Math.floor((mag[i] / mmax) * 63))].push(i);
    }
    for (let b = 63; b >= 0; b--) order.push(...buckets[b]);
  }
  const used = new Uint8Array(n);
  const segs: Seg[] = [];
  const tau = (TAU_DEG * Math.PI) / 180;
  const region: number[] = [];
  for (const seed of order) {
    if (used[seed]) continue;
    // 区域生长：双角度矢量平均维持区域方向（π 周期）
    region.length = 0;
    let c2 = Math.cos(2 * ang[seed]), s2 = Math.sin(2 * ang[seed]);
    let regAng = ang[seed];
    used[seed] = 1;
    region.push(seed);
    const stack = [seed];
    while (stack.length) {
      const p = stack.pop()!;
      const px = p % w, py = Math.floor(p / w);
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          if (!dx && !dy) continue;
          const nx = px + dx, ny = py + dy;
          if (nx < 1 || nx >= w - 1 || ny < 1 || ny >= h - 1) continue;
          const q = ny * w + nx;
          if (used[q] || mag[q] <= thr) continue;
          if (angleDiffPi(ang[q], regAng) > tau) continue;
          used[q] = 1;
          region.push(q);
          stack.push(q);
          c2 += Math.cos(2 * ang[q]);
          s2 += Math.sin(2 * ang[q]);
          regAng = Math.atan2(s2, c2) / 2;
          if (regAng < 0) regAng += Math.PI;
        }
      }
    }
    if (region.length < 20) continue;
    // PCA 拟合
    let sw = 0, sx = 0, sy = 0;
    for (const p of region) {
      const wgt = mag[p];
      sw += wgt; sx += wgt * (p % w); sy += wgt * Math.floor(p / w);
    }
    const cx = sx / sw, cy = sy / sw;
    let sxx = 0, sxy = 0, syy = 0;
    for (const p of region) {
      const wgt = mag[p];
      const dx = (p % w) - cx, dy = Math.floor(p / w) - cy;
      sxx += wgt * dx * dx; sxy += wgt * dx * dy; syy += wgt * dy * dy;
    }
    sxx /= sw; sxy /= sw; syy /= sw;
    const tr = sxx + syy, det = sxx * syy - sxy * sxy;
    const l1 = tr / 2 + Math.sqrt(Math.max(0, (tr * tr) / 4 - det));
    const l2 = tr - l1;
    if (l1 <= 0 || l2 / l1 > 0.15) continue; // 不够细长
    const theta = Math.atan2(l1 - sxx, sxy || 1e-9);
    const ux = Math.cos(theta), uy = Math.sin(theta);
    let tmin = Infinity, tmax = -Infinity;
    for (const p of region) {
      const t = ((p % w) - cx) * ux + (Math.floor(p / w) - cy) * uy;
      if (t < tmin) tmin = t;
      if (t > tmax) tmax = t;
    }
    const len = tmax - tmin;
    if (len < MIN_LEN || region.length < len * 0.8) continue;
    segs.push({
      x0: cx + ux * tmin, y0: cy + uy * tmin,
      x1: cx + ux * tmax, y1: cy + uy * tmax,
      mx: cx, my: cy, ux, uy, len,
    });
    if (segs.length >= 400) break;
  }
  return { segs, w, h };
}

/** 确定性 PRNG（mulberry32）：同一张图必须得到同一个标定结果 */
function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** RANSAC 求前两个消失点（线段长度加权，确定性） */
export function findVPs(segs: Seg[], w: number, h: number): VP[] {
  const rand = mulberry32(0x5eed);
  // 族预筛（还原度文档只用"纵深族"的教训）：
  // 近竖直属竖直族；近水平属横向族——横向 3D 线在图中互相平行，噪声会造出
  // 高票的退化伪 VP（所有水平线都给它投票），必须排除
  const cand = segs.filter((s) => Math.abs(s.uy) < 0.94 && Math.abs(s.uy) > 0.045);
  const vps: VP[] = [];
  let pool = cand;
  for (let round = 0; round < 2; round++) {
    if (pool.length < 8) break;
    let best: VP | null = null;
    let bestInl: Seg[] = [];
    for (let it = 0; it < 700; it++) {
      const a = pool[(rand() * pool.length) | 0];
      const b = pool[(rand() * pool.length) | 0];
      if (a === b) continue;
      // 直线交点
      const cross = a.ux * b.uy - a.uy * b.ux;
      if (Math.abs(cross) < 1e-4) continue;
      const dx = b.mx - a.mx, dy = b.my - a.my;
      const t = (dx * b.uy - dy * b.ux) / cross;
      const vx = a.mx + a.ux * t, vy = a.my + a.uy * t;
      if (!Number.isFinite(vx) || Math.abs(vx) > w * 40 || Math.abs(vy) > h * 40) continue;
      let score = 0;
      const inl: Seg[] = [];
      for (const s of pool) {
        let ex = vx - s.mx, ey = vy - s.my;
        const el = Math.hypot(ex, ey);
        if (el < s.len) continue; // VP 落在线段上：退化
        ex /= el; ey /= el;
        if (Math.abs(s.ux * ey - s.uy * ex) < INLIER_SIN) {
          score += s.len;
          inl.push(s);
        }
      }
      if (!best || score > best.score) {
        best = { x: vx, y: vy, score, inliers: inl.length };
        bestInl = inl;
      }
    }
    if (!best || best.inliers < 8) break;
    // 真 VP 的内点从不同斜率汇聚；同向线族的退化伪 VP 方向高度集中 → 拒绝
    {
      let c2s = 0, s2s = 0;
      for (const s of bestInl) {
        const a2 = Math.atan2(s.uy, s.ux);
        c2s += Math.cos(2 * a2);
        s2s += Math.sin(2 * a2);
      }
      const conc = Math.hypot(c2s, s2s) / Math.max(1, bestInl.length);
      if (conc > 0.99) {
        const inlSet0 = new Set(bestInl);
        pool = pool.filter((s) => !inlSet0.has(s));
        round--; // 本轮无效，剔除退化族后重试
        continue;
      }
    }
    // 用内点重估（最小二乘：所有内点直线的加权最近点）
    let a11 = 0, a12 = 0, a22 = 0, b1 = 0, b2 = 0;
    for (const s of bestInl) {
      // 直线法向 n=(-uy,ux)，方程 n·p = n·m，权重 len
      const nx2 = -s.uy, ny2 = s.ux, c0 = nx2 * s.mx + ny2 * s.my;
      a11 += s.len * nx2 * nx2; a12 += s.len * nx2 * ny2; a22 += s.len * ny2 * ny2;
      b1 += s.len * nx2 * c0; b2 += s.len * ny2 * c0;
    }
    const det = a11 * a22 - a12 * a12;
    if (Math.abs(det) > 1e-6) {
      best = { ...best, x: (a22 * b1 - a12 * b2) / det, y: (a11 * b2 - a12 * b1) / det };
    }
    vps.push(best);
    const inlSet = new Set(bestInl);
    pool = pool.filter((s) => !inlSet.has(s));
  }
  return vps;
}

export interface VpCalib {
  method: 'vp2' | 'vp1';
  /** 纵深消失点 y（检测坐标系），用于俯仰 */
  vpY: number;
  detH: number;
  detW: number;
  /** vp2 时的垂直 FOV（度），vp1 时为 null */
  vfovDeg: number | null;
  segCount: number;
  inliers: number;
  vps: VP[];
}

/** 从图像解消失点标定；线段/内点不足返回 null（调用方回退评分搜索） */
export function vpCalibrate(img: ImageData): VpCalib | null {
  const { segs, w, h } = detectSegments(img);
  if (segs.length < 12) return null;
  const vps = findVPs(segs, w, h);
  if (!vps.length) return null;
  // 纵深 VP：优先取落在画幅横向范围内、纵向不太离谱的（一点透视的中心 VP）
  const inFrame = vps.filter((v) => v.x > -w * 0.5 && v.x < w * 1.5 && Math.abs(v.y - h / 2) < h * 1.2);
  const depthVP = (inFrame.length ? inFrame : vps).sort((a, b) => b.score - a.score)[0];
  // 双 VP 正交解焦距：f² = -(v1-c)·(v2-c)
  let vfovDeg: number | null = null;
  if (vps.length >= 2) {
    const c = { x: w / 2, y: h / 2 };
    const [v1, v2] = vps;
    const f2 = -((v1.x - c.x) * (v2.x - c.x) + (v1.y - c.y) * (v2.y - c.y));
    if (f2 > 0) {
      const f = Math.sqrt(f2);
      const fov = (2 * Math.atan(h / 2 / f) * 180) / Math.PI;
      if (fov > 25 && fov < 110) vfovDeg = fov;
    }
  }
  return {
    method: vfovDeg != null ? 'vp2' : 'vp1',
    vpY: depthVP.y,
    detH: h,
    detW: w,
    vfovDeg,
    segCount: segs.length,
    inliers: depthVP.inliers,
    vps,
  };
}

/** 由 VP y 与给定 vfov 推俯仰（度）。约定：负=俯视；地平线行 v_h = cy + f·tan(pitch) */
export function pitchFromVp(vpY: number, detH: number, vfovDeg: number): number {
  const f = detH / 2 / Math.tan(((vfovDeg / 2) * Math.PI) / 180);
  return (Math.atan((vpY - detH / 2) / f) * 180) / Math.PI;
}
