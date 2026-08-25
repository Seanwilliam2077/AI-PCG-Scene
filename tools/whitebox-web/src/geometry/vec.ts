/** 小型线代工具：3x3 对称矩阵特征分解（Jacobi）、平面拟合、百分位 */

export type V3 = [number, number, number];

export function dot(a: V3, b: V3): number { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
export function cross(a: V3, b: V3): V3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}
export function sub(a: V3, b: V3): V3 { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
export function scale(a: V3, s: number): V3 { return [a[0] * s, a[1] * s, a[2] * s]; }
export function norm(a: V3): number { return Math.sqrt(dot(a, a)); }
export function normalize(a: V3): V3 {
  const n = norm(a);
  return n > 1e-12 ? scale(a, 1 / n) : [0, 0, 0];
}

/** 对称 3x3 的 Jacobi 特征分解。m 行主序长度 9。返回特征值升序及对应单位特征向量。 */
export function eigenSym3(m: number[]): { values: V3; vectors: [V3, V3, V3] } {
  // 拷贝上三角
  let a00 = m[0], a01 = m[1], a02 = m[2], a11 = m[4], a12 = m[5], a22 = m[8];
  // 特征向量累积矩阵（行主序）
  const v = [1, 0, 0, 0, 1, 0, 0, 0, 1];

  for (let iter = 0; iter < 32; iter++) {
    const off = a01 * a01 + a02 * a02 + a12 * a12;
    if (off < 1e-18) break;
    // 依次消去 (0,1) (0,2) (1,2)
    for (let k = 0; k < 3; k++) {
      let p: number, q: number, apq: number, app: number, aqq: number;
      if (k === 0) { p = 0; q = 1; apq = a01; app = a00; aqq = a11; }
      else if (k === 1) { p = 0; q = 2; apq = a02; app = a00; aqq = a22; }
      else { p = 1; q = 2; apq = a12; app = a11; aqq = a22; }
      if (Math.abs(apq) < 1e-18) continue;
      const theta = (aqq - app) / (2 * apq);
      const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
      const c = 1 / Math.sqrt(t * t + 1);
      const s = t * c;
      // 更新矩阵元素
      const app2 = app - t * apq;
      const aqq2 = aqq + t * apq;
      if (k === 0) {
        const n02 = c * a02 - s * a12, n12 = s * a02 + c * a12;
        a00 = app2; a11 = aqq2; a01 = 0; a02 = n02; a12 = n12;
      } else if (k === 1) {
        const n01 = c * a01 - s * a12, n12 = s * a01 + c * a12;
        a00 = app2; a22 = aqq2; a02 = 0; a01 = n01; a12 = n12;
      } else {
        const n01 = c * a01 - s * a02, n02 = s * a01 + c * a02;
        a11 = app2; a22 = aqq2; a12 = 0; a01 = n01; a02 = n02;
      }
      // 累积特征向量：v = v * G（G 为 (p,q) 平面 Givens）
      for (let r = 0; r < 3; r++) {
        const vp = v[r * 3 + p], vq = v[r * 3 + q];
        v[r * 3 + p] = c * vp - s * vq;
        v[r * 3 + q] = s * vp + c * vq;
      }
    }
  }
  const vals: [number, number][] = [[a00, 0], [a11, 1], [a22, 2]];
  vals.sort((x, y) => x[0] - y[0]);
  const values = [vals[0][0], vals[1][0], vals[2][0]] as V3;
  const vectors = vals.map(([, i]) =>
    normalize([v[0 * 3 + i], v[1 * 3 + i], v[2 * 3 + i]])
  ) as [V3, V3, V3];
  return { values, vectors };
}

export interface Plane { n: V3; d: number; } // n·p + d = 0，n 为单位向量

/** 最小二乘平面拟合。pts 为 xyz 连续数组，idx 为参与拟合的点下标（可为 null 表示全部）。 */
export function fitPlane(pts: Float32Array, idx: number[] | null): Plane | null {
  const n = idx ? idx.length : pts.length / 3;
  if (n < 3) return null;
  let cx = 0, cy = 0, cz = 0;
  for (let k = 0; k < n; k++) {
    const i = (idx ? idx[k] : k) * 3;
    cx += pts[i]; cy += pts[i + 1]; cz += pts[i + 2];
  }
  cx /= n; cy /= n; cz /= n;
  let xx = 0, xy = 0, xz = 0, yy = 0, yz = 0, zz = 0;
  for (let k = 0; k < n; k++) {
    const i = (idx ? idx[k] : k) * 3;
    const dx = pts[i] - cx, dy = pts[i + 1] - cy, dz = pts[i + 2] - cz;
    xx += dx * dx; xy += dx * dy; xz += dx * dz;
    yy += dy * dy; yz += dy * dz; zz += dz * dz;
  }
  const { vectors } = eigenSym3([xx, xy, xz, xy, yy, yz, xz, yz, zz]);
  const nrm = vectors[0]; // 最小特征值方向
  const d = -(nrm[0] * cx + nrm[1] * cy + nrm[2] * cz);
  return { n: nrm, d };
}

export function planeDist(p: Plane, x: number, y: number, z: number): number {
  return p.n[0] * x + p.n[1] * y + p.n[2] * z + p.d;
}

/** 取数组的 q 分位（0..1），会排序拷贝，注意规模 */
export function percentile(arr: ArrayLike<number>, q: number): number {
  const a = Array.from(arr).sort((x, y) => x - y);
  if (a.length === 0) return 0;
  const i = Math.min(a.length - 1, Math.max(0, Math.round(q * (a.length - 1))));
  return a[i];
}

/** 从 Float32Array 中等距抽样 n 个值 */
export function sampleValues(arr: Float32Array, n: number): Float32Array {
  if (arr.length <= n) return arr.slice();
  const out = new Float32Array(n);
  const step = arr.length / n;
  for (let i = 0; i < n; i++) out[i] = arr[Math.floor(i * step)];
  return out;
}
