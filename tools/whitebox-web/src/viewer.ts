/** Three.js 可环绕白盒视窗：壳用朝内法向的单面平面（外侧看穿、内侧可见），
 *  物体用实心 clay 盒/圆柱 + 轮廓线，附求解相机的视锥指示。 */
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFExporter } from 'three/examples/jsm/exporters/GLTFExporter.js';
import type { WhiteboxSpec } from './types';

const CLAY = 0xd3d4d8;
const EDGE = 0x54575f;

export class Viewer {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private group = new THREE.Group();
  private specCamHelper: THREE.CameraHelper | null = null;

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
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    };
    loop();
  }

  build(spec: WhiteboxSpec): void {
    this.group.clear();
    if (this.specCamHelper) { this.scene.remove(this.specCamHelper); this.specCamHelper = null; }

    const clay = new THREE.MeshStandardMaterial({ color: CLAY, roughness: 0.95 });
    const shellMat = new THREE.MeshStandardMaterial({
      color: 0xbfc1c6, roughness: 1.0, side: THREE.FrontSide,
    });
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

    // 实例
    for (const inst of spec.instances) {
      const [w, hh, dd] = inst.dims;
      const geo = new THREE.BoxGeometry(w, hh, dd);
      const mesh = new THREE.Mesh(geo, clay);
      mesh.position.set(inst.pos[0], inst.baseY + hh / 2, inst.pos[2]);
      this.group.add(mesh);
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(geo),
        new THREE.LineBasicMaterial({ color: EDGE }),
      );
      edges.position.copy(mesh.position);
      this.group.add(edges);
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
