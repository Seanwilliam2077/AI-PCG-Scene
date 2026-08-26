/** Three.js 可环绕白盒视窗：壳用朝内法向的单面平面（外侧看穿、内侧可见），
 *  物体用实心 clay 盒/圆柱 + 轮廓线，附求解相机的视锥指示。 */
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFExporter } from 'three/examples/jsm/exporters/GLTFExporter.js';
import type { BoxInstance, WhiteboxSpec } from './types';

const CLAY = 0xd3d4d8;
const EDGE = 0x54575f;

/** 加一个带轮廓线的盒子到组（局部坐标：center 为体心） */
function addBox(
  g: THREE.Group, clay: THREE.Material, edgeMat: THREE.LineBasicMaterial,
  cx: number, cy: number, cz: number, w: number, h: number, d: number,
) {
  const geo = new THREE.BoxGeometry(Math.max(0.02, w), Math.max(0.02, h), Math.max(0.02, d));
  const mesh = new THREE.Mesh(geo, clay);
  mesh.position.set(cx, cy, cz);
  g.add(mesh);
  const e = new THREE.LineSegments(new THREE.EdgesGeometry(geo), edgeMat);
  e.position.copy(mesh.position);
  g.add(e);
}

/** 检测语义 → 复合 primitive 模板（设计书 §05 词表的 web 版） */
function buildInstance(
  inst: BoxInstance, clay: THREE.Material, edgeMat: THREE.LineBasicMaterial, ceilY: number,
): THREE.Group {
  const g = new THREE.Group();
  g.name = inst.label ?? inst.id;
  const [w, h, d] = inst.dims;
  const base = inst.baseY;
  const kind = inst.kind && inst.kind !== 'box' ? inst.kind : 'box';
  const legW = Math.min(0.07, w * 0.12, d * 0.12);

  switch (kind) {
    case 'table': {
      const topT = Math.min(0.08, h * 0.15);
      addBox(g, clay, edgeMat, 0, base + h - topT / 2, 0, w, topT, d);
      const ix = w / 2 - legW, iz = d / 2 - legW;
      for (const [sx, sz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
        addBox(g, clay, edgeMat, sx * ix, base + (h - topT) / 2, sz * iz, legW, h - topT, legW);
      }
      break;
    }
    case 'chair': {
      const seatY = base + Math.min(Math.max(h * 0.5, 0.3), 0.55);
      const seatT = 0.06;
      addBox(g, clay, edgeMat, 0, seatY - seatT / 2, 0, w, seatT, d);
      const ix = w / 2 - legW, iz = d / 2 - legW;
      for (const [sx, sz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
        addBox(g, clay, edgeMat, sx * ix, base + (seatY - base - seatT) / 2, sz * iz,
               legW, seatY - base - seatT, legW);
      }
      const backH = base + h - seatY;
      if (backH > 0.1) addBox(g, clay, edgeMat, 0, seatY + backH / 2, -d / 2 + 0.03, w, backH, 0.06);
      break;
    }
    case 'sofa': {
      const seatH = h * 0.45;
      addBox(g, clay, edgeMat, 0, base + seatH / 2, 0, w, seatH, d);
      addBox(g, clay, edgeMat, 0, base + h / 2, -d / 2 + d * 0.12, w, h, d * 0.24);
      for (const sx of [-1, 1]) {
        addBox(g, clay, edgeMat, sx * (w / 2 - w * 0.08), base + h * 0.35, 0, w * 0.16, h * 0.7, d);
      }
      break;
    }
    case 'plant':
    case 'tree': {
      const tall = kind === 'tree' || h > 1.6;
      const potR = Math.max(0.06, Math.min(w, d) * (tall ? 0.12 : 0.3));
      const potH = tall ? h * 0.45 : h * 0.25;
      const pot = new THREE.Mesh(new THREE.CylinderGeometry(potR, potR * 0.85, potH, 12), clay);
      pot.position.set(0, base + potH / 2, 0);
      g.add(pot);
      const crownR = Math.max(0.12, Math.min(w, d) * 0.42);
      const cy0 = base + potH + crownR * 0.7;
      const offsets: [number, number, number][] = tall
        ? [[0, crownR * 0.9, 0], [-crownR * 0.8, 0.2, 0.1], [crownR * 0.75, 0.1, -0.15], [0.1, 0.3, crownR * 0.7], [-0.15, 0.25, -crownR * 0.7]]
        : [[0, 0, 0], [-crownR * 0.6, crownR * 0.5, 0.05], [crownR * 0.55, crownR * 0.45, -0.05]];
      for (const [ox, oy, oz] of offsets) {
        const s = new THREE.Mesh(new THREE.IcosahedronGeometry(crownR, 1), clay);
        s.position.set(ox, cy0 + oy, oz);
        g.add(s);
      }
      break;
    }
    case 'lamp': {
      const shadeR = Math.max(0.08, Math.max(w, d) / 2);
      const shadeH = Math.min(0.35, Math.max(0.15, h));
      const cone = new THREE.Mesh(new THREE.ConeGeometry(shadeR, shadeH, 14, 1, true), clay);
      cone.position.set(0, base + shadeH / 2, 0);
      g.add(cone);
      const rodTop = Math.max(ceilY, base + shadeH + 0.1);
      const rodH = rodTop - (base + shadeH);
      if (rodH > 0.05) {
        const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, rodH, 6), clay);
        rod.position.set(0, base + shadeH + rodH / 2, 0);
        g.add(rod);
      }
      break;
    }
    case 'tv': {
      addBox(g, clay, edgeMat, 0, base + h / 2, 0, w, h, Math.min(0.1, d));
      break;
    }
    case 'person': {
      const r = Math.min(0.22, Math.max(0.12, w / 2));
      const body = new THREE.Mesh(new THREE.CapsuleGeometry(r, Math.max(0.2, h - 2 * r), 4, 10), clay);
      body.position.set(0, base + h / 2, 0);
      g.add(body);
      break;
    }
    default:
      addBox(g, clay, edgeMat, 0, base + h / 2, 0, w, h, d);
  }
  g.position.set(inst.pos[0], 0, inst.pos[2]);
  g.rotation.y = inst.yawYRad ?? 0; // 检测实例在预 yaw 系求解，绕体心回转
  return g;
}

export class Viewer {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private group = new THREE.Group();
  private specCamHelper: THREE.CameraHelper | null = null;
  private mode: 'orbit' | 'match' = 'orbit';
  private matchCam: THREE.PerspectiveCamera | null = null;
  private overlayEl: HTMLImageElement | null = null;
  // 材质复用，重建只重建几何
  private clayMat = new THREE.MeshStandardMaterial({ color: CLAY, roughness: 0.95 });
  private shellMat = new THREE.MeshStandardMaterial({
    color: 0xbfc1c6, roughness: 1.0, side: THREE.FrontSide,
  });
  private edgeMat = new THREE.LineBasicMaterial({ color: EDGE });

  constructor(canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    this.scene.background = new THREE.Color(0x17181b);
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.05, 300);
    this.camera.position.set(6, 6, 8);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;

    this.scene.add(new THREE.HemisphereLight(0xdfe3ea, 0x30333a, 1.1));
    const dir = new THREE.DirectionalLight(0xffffff, 1.2);
    dir.position.set(4, 8, 5);
    this.scene.add(dir);
    const grid = new THREE.GridHelper(40, 40, 0x2c2f35, 0x22242a);
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.6;
    this.scene.add(grid);
    this.scene.add(this.group);

    const resize = () => {
      const w = canvas.clientWidth || window.innerWidth;
      const h = canvas.clientHeight || window.innerHeight;
      this.renderer.setSize(w, h, false);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
    };
    window.addEventListener('resize', resize);
    new ResizeObserver(resize).observe(canvas); // 面板从隐藏变可见时补触发
    resize();

    const loop = () => {
      requestAnimationFrame(loop);
      this.renderFrame();
    };
    loop();
  }

  /** 渲一帧（对位模式信箱式渲染 + 叠加图定位；抽出便于无 rAF 环境驱动/测试） */
  renderFrame(): void {
    const canvas = this.renderer.domElement;
    const w = canvas.clientWidth || window.innerWidth;
    const hh = canvas.clientHeight || window.innerHeight;
    if (w < 2 || hh < 2) return; // 面板隐藏/未布局

    if (this.specCamHelper) this.specCamHelper.visible = this.mode !== 'match';
    if (this.mode === 'match' && this.matchCam) {
      // 对位视角：按输入图画幅信箱式渲染，叠加参考图可直接验证对齐
      const a = this.matchCam.aspect;
      let vw = w, vh = Math.round(w / a);
      if (vh > hh) { vh = hh; vw = Math.round(hh * a); }
      const vx = (w - vw) >> 1, vy = (hh - vh) >> 1;
      this.renderer.setScissorTest(false);
      this.renderer.setViewport(0, 0, w, hh);
      this.renderer.clear();
      this.renderer.setScissorTest(true);
      this.renderer.setViewport(vx, vy, vw, vh);
      this.renderer.setScissor(vx, vy, vw, vh);
      this.renderer.render(this.scene, this.matchCam);
      this.renderer.setScissorTest(false);
      this.placeOverlay(vx, vy, vw, vh, hh);
    } else {
      this.renderer.setViewport(0, 0, w, hh);
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      // 相机画面 PIP（左下角）：环绕时也随时能看到求解相机看到什么
      if (this.matchCam) {
        const pw = Math.min(Math.round(w * 0.28), 380);
        const ph = Math.max(2, Math.round(pw / this.matchCam.aspect));
        const px = 14, py = 14; // GL 视口坐标（左下原点）
        if (this.specCamHelper) this.specCamHelper.visible = false;
        this.renderer.setScissorTest(true);
        this.renderer.setScissor(px - 2, py - 2, pw + 4, ph + 4);
        this.renderer.setViewport(px - 2, py - 2, pw + 4, ph + 4);
        this.renderer.setClearColor(0xf09242);
        this.renderer.clear();
        this.renderer.setClearColor(0x17181b);
        this.renderer.setScissor(px, py, pw, ph);
        this.renderer.setViewport(px, py, pw, ph);
        this.renderer.render(this.scene, this.matchCam);
        this.renderer.setScissorTest(false);
        if (this.specCamHelper) this.specCamHelper.visible = true;
      }
    }
  }

  private overlayOpacity = 0; // 默认纯白盒相机画面，不叠原图

  /** 对位模式下把参考图叠到渲染区域上（WebGL 视口 y 向上，CSS y 向下） */
  private placeOverlay(vx: number, vy: number, vw: number, vh: number, canvasH: number) {
    if (!this.overlayEl) return;
    const st = this.overlayEl.style;
    if (this.overlayOpacity <= 0) { st.display = 'none'; return; }
    st.display = 'block';
    st.left = `${vx}px`;
    st.top = `${canvasH - vy - vh}px`;
    st.width = `${vw}px`;
    st.height = `${vh}px`;
  }

  setOverlayElement(el: HTMLImageElement) { this.overlayEl = el; }

  setOverlayOpacity(o: number) {
    this.overlayOpacity = o;
    if (this.overlayEl) this.overlayEl.style.opacity = String(o);
  }

  setMode(m: 'orbit' | 'match') {
    this.mode = m;
    this.controls.enabled = m === 'orbit';
    if (m === 'orbit' && this.overlayEl) this.overlayEl.style.display = 'none';
  }

  getMode() { return this.mode; }

  build(spec: WhiteboxSpec): void {
    // 释放上一次的几何（滑杆反复重建时防显存泄漏）
    this.group.traverse((o: any) => { o.geometry?.dispose?.(); });
    this.group.clear();
    if (this.specCamHelper) {
      this.scene.remove(this.specCamHelper);
      this.specCamHelper.dispose();
      this.specCamHelper = null;
    }

    const clay = this.clayMat;
    const shellMat = this.shellMat;
    const r = spec.room;
    const cx = (r.min[0] + r.max[0]) / 2;
    const cz = (r.min[2] + r.max[2]) / 2;
    const sx = Math.max(0.1, r.max[0] - r.min[0]);
    const sz = Math.max(0.1, r.max[2] - r.min[2]);
    const h = Math.max(0.5, r.max[1]);

    // 地面（法向朝上）
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(sx, sz), shellMat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(cx, 0, cz);
    this.group.add(floor);
    // 墙（法向朝内 ⇒ 从外侧被剔除，能看进房间）
    const addWall = (w: number, x: number, z: number, ry: number) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), shellMat);
      m.position.set(x, h / 2, z);
      m.rotation.y = ry;
      this.group.add(m);
    };
    if (r.walls.px) addWall(sz, r.max[0], cz, -Math.PI / 2);
    if (r.walls.nx) addWall(sz, r.min[0], cz, Math.PI / 2);
    if (r.walls.pz) addWall(sx, cx, r.max[2], Math.PI);
    if (r.walls.nz) addWall(sx, cx, r.min[2], 0);
    if (r.hasCeiling) {
      const ceil = new THREE.Mesh(new THREE.PlaneGeometry(sx, sz), shellMat);
      ceil.rotation.x = Math.PI / 2; // 法向朝下
      ceil.position.set(cx, h, cz);
      this.group.add(ceil);
    }

    // 实例（按复合模板构建）
    for (const inst of spec.instances) {
      this.group.add(buildInstance(inst, clay, this.edgeMat, h));
    }

    // 求解相机视锥
    const b = spec.camera.basis;
    const right = new THREE.Vector3(b[0], b[1], b[2]);
    const down = new THREE.Vector3(b[3], b[4], b[5]);
    const fwd = new THREE.Vector3(b[6], b[7], b[8]);
    const specCam = new THREE.PerspectiveCamera(spec.camera.vfovDeg, spec.camera.aspect, 0.2, 4);
    specCam.position.set(...spec.camera.pos);
    const m = new THREE.Matrix4().makeBasis(right, down.clone().negate(), fwd.clone().negate());
    specCam.quaternion.setFromRotationMatrix(m);
    specCam.updateMatrixWorld();
    specCam.updateProjectionMatrix();
    this.specCamHelper = new THREE.CameraHelper(specCam);
    this.scene.add(this.specCamHelper);

    // 对位渲染相机：与求解相机同位姿同 FOV，画幅取输入图宽高比
    this.matchCam = new THREE.PerspectiveCamera(spec.camera.vfovDeg, spec.camera.aspect, 0.05, 300);
    this.matchCam.position.copy(specCam.position);
    this.matchCam.quaternion.copy(specCam.quaternion);
    this.matchCam.updateMatrixWorld();
    this.matchCam.updateProjectionMatrix();

    // 视角复位到房间 3/4
    const target = new THREE.Vector3(cx, h * 0.35, cz);
    this.controls.target.copy(target);
    const diag = Math.max(sx, sz, 3);
    this.camera.position.set(cx - diag * 0.9, h * 1.5 + 1, cz + diag * 1.1);
    this.camera.lookAt(target);
  }

  async exportGLB(): Promise<Blob> {
    const exporter = new GLTFExporter();
    const buf = (await exporter.parseAsync(this.group, { binary: true })) as ArrayBuffer;
    return new Blob([buf], { type: 'model/gltf-binary' });
  }
}
