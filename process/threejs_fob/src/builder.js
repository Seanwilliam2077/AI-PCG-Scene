// 几何累加器。
//
// 所有静态物件都在一个变换栈里就地生成，按材质分桶，最后 merge 成
// 每桶一个 Mesh —— 整个基地几万个构件最终只有十来个 draw call。
// 顶点色承担所有的颜色变化（同一材质可以有无数种色调），
// 同时在发射时按世界高度烘一层接地暗部（廉价 AO，让物体“坐”在地上）。
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const _m = new THREE.Matrix4();
const _mLocal = new THREE.Matrix4();
const _v = new THREE.Vector3();
const _q = new THREE.Quaternion();
const _e = new THREE.Euler();
const _one = new THREE.Vector3(1, 1, 1);
const _col = new THREE.Color();

// BoxGeometry 的顶点顺序: +X,-X,+Y,-Y,+Z,-Z，每面 4 个顶点（1 段时）。
// 把每个面的 UV 按世界尺寸重排，纹理就能按米平铺而不是被拉伸。
function boxUV(geo, w, h, d, tile) {
  if (!tile) return;
  const uv = geo.attributes.uv;
  const s = 1 / tile;
  const set = (i, su, sv, ou, ov) => {
    for (let k = 0; k < 4; k++) {
      const j = i + k;
      uv.setXY(j, (uv.getX(j) - 0.5) * su * s + 0.5 + ou,
                  (uv.getY(j) - 0.5) * sv * s + 0.5 + ov);
    }
  };
  set(0, d, h, 0, 0);   // +X
  set(4, d, h, 0, 0);   // -X
  set(8, w, d, 0, 0);   // +Y
  set(12, w, d, 0, 0);  // -Y
  set(16, w, h, 0, 0);  // +Z
  set(20, w, h, 0, 0);  // -Z
  uv.needsUpdate = true;
}

export class Builder {
  constructor(opts = {}) {
    this.buckets = new Map();
    this.stack = [new THREE.Matrix4()];
    this.aoHeight = opts.aoHeight ?? 1.5;   // 接地暗部的作用高度
    this.aoStrength = opts.aoStrength ?? 0.26;
    this.verts = 0;
    this.parts = 0;
  }

  // ---- 变换栈 -------------------------------------------------------
  get m() { return this.stack[this.stack.length - 1]; }
  save() { this.stack.push(this.m.clone()); return this; }
  restore() { if (this.stack.length > 1) this.stack.pop(); return this; }
  reset() { this.stack.length = 1; this.stack[0].identity(); return this; }
  at(x, y, z) { this.m.multiply(_m.makeTranslation(x, y, z)); return this; }
  rx(a) { this.m.multiply(_m.makeRotationX(a)); return this; }
  ry(a) { this.m.multiply(_m.makeRotationY(a)); return this; }
  rz(a) { this.m.multiply(_m.makeRotationZ(a)); return this; }
  sc(x, y = x, z = x) { this.m.multiply(_m.makeScale(x, y, z)); return this; }

  // ---- 核心发射 -----------------------------------------------------
  // o: {x,y,z, rx,ry,rz, color, base:true 表示 y 是底面而非中心}
  emit(bucket, geo, o = {}) {
    _e.set(o.rx || 0, o.ry || 0, o.rz || 0, 'YXZ');
    _q.setFromEuler(_e);
    _v.set(o.x || 0, o.y || 0, o.z || 0);
    _mLocal.compose(_v, _q, _one);
    _m.multiplyMatrices(this.m, _mLocal);
    geo.applyMatrix4(_m);

    const pos = geo.attributes.position;
    const n = pos.count;
    if (o.color !== undefined) _col.set(o.color); else _col.setRGB(0.7, 0.7, 0.7);
    const arr = new Float32Array(n * 3);
    const aoH = this.aoHeight, aoS = this.aoStrength;
    for (let i = 0; i < n; i++) {
      let f = 1;
      if (aoS > 0) {
        const y = pos.getY(i);
        f = 1 - aoS * (1 - Math.min(1, Math.max(0, y / aoH)));
      }
      arr[i * 3] = _col.r * f;
      arr[i * 3 + 1] = _col.g * f;
      arr[i * 3 + 2] = _col.b * f;
    }
    geo.setAttribute('color', new THREE.BufferAttribute(arr, 3));
    if (!geo.attributes.uv) {
      geo.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(n * 2), 2));
    }
    // 只保留 merge 需要的四个属性，属性集必须完全一致
    for (const k of Object.keys(geo.attributes)) {
      if (k !== 'position' && k !== 'normal' && k !== 'uv' && k !== 'color') {
        geo.deleteAttribute(k);
      }
    }
    geo.clearGroups();
    // mergeGeometries 要求所有几何“要么全有 index，要么全没有”。
    // ExtrudeGeometry / PolyhedronGeometry 是非索引的，这里补一条平凡索引，
    // 比把其它几何全部展开成非索引省一半以上顶点。
    if (geo.index === null) {
      const idx = n > 65535 ? new Uint32Array(n) : new Uint16Array(n);
      for (let i = 0; i < n; i++) idx[i] = i;
      geo.setIndex(new THREE.BufferAttribute(idx, 1));
    }
    let list = this.buckets.get(bucket);
    if (!list) this.buckets.set(bucket, (list = []));
    list.push(geo);
    this.verts += n;
    this.parts++;
    return this;
  }

  // ---- 图元 ---------------------------------------------------------
  box(bucket, w, h, d, x = 0, y = 0, z = 0, o = {}) {
    const g = new THREE.BoxGeometry(w, h, d);
    boxUV(g, w, h, d, o.tile);
    return this.emit(bucket, g, { ...o, x, y: o.base ? y + h / 2 : y, z });
  }

  // 沿 X 轴放置的板（便于做墙）
  cyl(bucket, rt, rb, h, seg, x = 0, y = 0, z = 0, o = {}) {
    const g = new THREE.CylinderGeometry(rt, rb, h, seg, 1, !!o.open);
    if (o.tile) {
      const uv = g.attributes.uv;
      const circ = Math.PI * (rt + rb);
      for (let i = 0; i < uv.count; i++) {
        uv.setXY(i, uv.getX(i) * circ / o.tile, uv.getY(i) * h / o.tile);
      }
    }
    return this.emit(bucket, g, { ...o, x, y: o.base ? y + h / 2 : y, z });
  }

  sphere(bucket, r, x = 0, y = 0, z = 0, o = {}) {
    const g = new THREE.SphereGeometry(r, o.seg || 12, o.seg2 || 8,
      0, Math.PI * 2, 0, o.phi || Math.PI);
    return this.emit(bucket, g, { ...o, x, y, z });
  }

  cone(bucket, r, h, seg, x = 0, y = 0, z = 0, o = {}) {
    const g = new THREE.ConeGeometry(r, h, seg);
    return this.emit(bucket, g, { ...o, x, y: o.base ? y + h / 2 : y, z });
  }

  torus(bucket, r, t, x = 0, y = 0, z = 0, o = {}) {
    const g = new THREE.TorusGeometry(r, t, o.seg2 || 6, o.seg || 20,
      o.arc === undefined ? Math.PI * 2 : o.arc);
    return this.emit(bucket, g, { ...o, x, y, z });
  }

  lathe(bucket, pts, seg, x = 0, y = 0, z = 0, o = {}) {
    const g = new THREE.LatheGeometry(pts, seg || 24);
    return this.emit(bucket, g, { ...o, x, y, z });
  }

  plane(bucket, w, h, x = 0, y = 0, z = 0, o = {}) {
    const g = new THREE.PlaneGeometry(w, h, o.wseg || 1, o.hseg || 1);
    if (o.tile) {
      const uv = g.attributes.uv;
      for (let i = 0; i < uv.count; i++) {
        uv.setXY(i, uv.getX(i) * w / o.tile, uv.getY(i) * h / o.tile);
      }
    }
    return this.emit(bucket, g, { ...o, x, y, z });
  }

  // 二维截面沿 Z 挤出（山墙、异形梁）
  prism(bucket, pts2d, depth, x = 0, y = 0, z = 0, o = {}) {
    const shape = new THREE.Shape();
    shape.moveTo(pts2d[0][0], pts2d[0][1]);
    for (let i = 1; i < pts2d.length; i++) shape.lineTo(pts2d[i][0], pts2d[i][1]);
    shape.closePath();
    const g = new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: false, curveSegments: 2 });
    g.translate(0, 0, -depth / 2);
    return this.emit(bucket, g, { ...o, x, y, z });
  }

  tube(bucket, curve, r, tubSeg, radSeg, o = {}) {
    const g = new THREE.TubeGeometry(curve, tubSeg, r, radSeg, false);
    return this.emit(bucket, g, o);
  }

  // 两点之间的杆件（斜撑、拉索、栏杆）—— 世界坐标直接给
  strut(bucket, a, b, r, o = {}) {
    const dx = b[0] - a[0], dy = b[1] - a[1], dz = b[2] - a[2];
    const len = Math.hypot(dx, dy, dz);
    if (len < 1e-4) return this;
    const g = o.square
      ? new THREE.BoxGeometry(r * 2, len, r * 2)
      : new THREE.CylinderGeometry(r, r, len, o.seg || 5);
    // 把 +Y 轴对齐到 a→b
    _v.set(dx / len, dy / len, dz / len);
    _q.setFromUnitVectors(new THREE.Vector3(0, 1, 0), _v);
    _mLocal.compose(
      new THREE.Vector3((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2), _q, _one);
    g.applyMatrix4(_mLocal);
    return this.emit(bucket, g, { color: o.color });
  }

  // ---- 合并输出 -----------------------------------------------------
  build(materials) {
    const group = new THREE.Group();
    group.name = 'site';
    for (const [name, list] of this.buckets) {
      if (!list.length) continue;
      const mat = materials[name] || materials.concrete;
      const merged = mergeGeometries(list, false);
      if (!merged) { console.warn('merge failed:', name, list.length); continue; }
      merged.computeBoundingSphere();
      const mesh = new THREE.Mesh(merged, mat);
      mesh.name = name;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      group.add(mesh);
      for (const g of list) g.dispose();
    }
    this.buckets.clear();
    return group;
  }
}
