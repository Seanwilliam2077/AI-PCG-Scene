// 可复现的伪随机源 —— 同一 seed 永远生成同一个基地。
export function makeRng(seed) {
  let a = (seed >>> 0) || 1;
  const r = () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  r.range = (lo, hi) => lo + (hi - lo) * r();
  r.int = (lo, hi) => lo + Math.floor((hi - lo + 1) * r());
  r.pick = (arr) => arr[Math.floor(r() * arr.length) % arr.length];
  r.chance = (p) => r() < p;
  r.sign = () => (r() < 0.5 ? -1 : 1);
  // 对称抖动: ±a
  r.j = (amp) => (r() * 2 - 1) * amp;
  // 洗牌（原地）
  r.shuffle = (arr) => {
    for (let i = arr.length - 1; i > 0; i--) {
      const k = Math.floor(r() * (i + 1));
      [arr[i], arr[k]] = [arr[k], arr[i]];
    }
    return arr;
  };
  // 从带权表里抽: [[item,w],...]
  r.weighted = (table) => {
    let tot = 0;
    for (const t of table) tot += t[1];
    let x = r() * tot;
    for (const t of table) {
      x -= t[1];
      if (x <= 0) return t[0];
    }
    return table[table.length - 1][0];
  };
  return r;
}

export const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
export const lerp = (a, b, t) => a + (b - a) * t;
export const smooth = (t) => t * t * (3 - 2 * t);
export const TAU = Math.PI * 2;
export const D2R = Math.PI / 180;
