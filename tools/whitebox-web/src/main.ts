import { DepthClient } from './depthClient';
import { autoCalibrate } from './geometry/pipeline';

console.log('[whitebox-web] v0.4.0');
import { solveGeometry } from './geometry/pipeline';
import { Viewer } from './viewer';
import type { DepthResult, Detection } from './types';

const $ = (id: string) => document.getElementById(id)!;
const statusEl = $('status');
const viewer = new Viewer($('viewport') as HTMLCanvasElement);
const depthClient = new DepthClient();

let cachedDepth: DepthResult | null = null;
let cachedDets: Detection[] = [];
let busy = false;

function setStatus(msg: string, err = false) {
  statusEl.textContent = msg;
  statusEl.className = err ? 'err' : '';
}
depthClient.onStatus = (msg) => setStatus(msg);

function params() {
  return {
    vfovDeg: parseFloat(($('fov') as HTMLInputElement).value),
    pitchDeg: parseFloat(($('pitch') as HTMLInputElement).value),
    camHeightM: parseFloat(($('camh') as HTMLInputElement).value),
    minObjSizeM: 0.12,
    maxBoxes: 64,
    granularity: parseFloat(($('grain') as HTMLInputElement).value),
  };
}

/** 滑杆标签刷新函数表（自动相机估计写入滑杆后统一刷新） */
const labelUpdaters: Array<() => void> = [];

async function imageToData(src: Blob | string): Promise<ImageData> {
  // createImageBitmap 不依赖合成器（img.decode 在后台/隐藏标签页会被节流挂起）
  const blob = typeof src === 'string' ? await (await fetch(src)).blob() : src;
  const bmp = await createImageBitmap(blob);
  const maxSide = 1280;
  const s = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
  const w = Math.round(bmp.width * s), h = Math.round(bmp.height * s);
  const cv = document.createElement('canvas');
  cv.width = w; cv.height = h;
  const ctx = cv.getContext('2d')!;
  ctx.drawImage(bmp, 0, 0, w, h);
  bmp.close();
  ($('thumb') as HTMLImageElement).src = cv.toDataURL('image/jpeg', 0.85);
  $('thumb').hidden = false;
  return ctx.getImageData(0, 0, w, h);
}

async function run(src: Blob | string) {
  if (busy) return;
  busy = true;
  try {
    const imageData = await imageToData(src);
    setStatus('深度推理中…（首次运行需下载约 25MB 模型）');
    const t0 = performance.now();
    cachedDepth = await depthClient.infer(imageData);
    setStatus('识别场景物体…（首次需下载检测模型）');
    cachedDets = await depthClient.detect(imageData);
    if (new URLSearchParams(location.search).has('fakedet')) {
      // 调试：注入假检测验证 模板/尺度锚 链路
      cachedDets = [
        { label: 'dining table', score: 0.9, box: [0.55, 0.71, 0.92, 0.99] },
        { label: 'chair', score: 0.85, box: [0.69, 0.8, 0.86, 0.99] },
        { label: 'potted plant', score: 0.8, box: [0.0, 0.62, 0.06, 0.99] },
        { label: 'person', score: 0.8, box: [0.4, 0.35, 0.5, 0.9] },
      ];
    }
    setStatus('自动估计相机（FOV/俯仰）…');
    await new Promise((r) => setTimeout(r, 30)); // 让状态渲染出来
    const cam = autoCalibrate(cachedDepth);
    ($('fov') as HTMLInputElement).value = String(cam.vfovDeg);
    ($('pitch') as HTMLInputElement).value = String(cam.pitchDeg);
    for (const u of labelUpdaters) u();
    setStatus('几何求解中…');
    await new Promise((r) => setTimeout(r, 30));
    const spec = resolve();
    const dt = ((performance.now() - t0) / 1000).toFixed(1);
    const nDet = spec?.instances.filter((i) => i.source === 'detect').length ?? 0;
    setStatus(
      `完成 ✓ ${dt}s（${cachedDepth.device}）· 自动相机 FOV ${cam.vfovDeg}° 俯仰 ${cam.pitchDeg}° · ` +
      `${spec?.instances.length ?? 0} 个体块（${nDet} 个语义模板）。拖动旋转 / 滚轮缩放 / 右键平移`,
    );
    $('controls').hidden = false;
  } catch (e: any) {
    console.error(e);
    setStatus(`失败：${e?.message ?? e}`, true);
    cachedDepth = null; // 缓存与缩略图必须一致，防滑杆重解旧图
    cachedDets = [];
  } finally {
    busy = false;
  }
}

function resolve() {
  if (!cachedDepth) return null;
  const result = solveGeometry(cachedDepth, params(), cachedDets);
  viewer.build(result.spec);
  (window as any).__spec = result.spec; // 调试可取
  return result.spec;
}

// ---- 输入 ----
$('file-input').addEventListener('change', (e) => {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (f) run(f);
});
$('demo-btn').addEventListener('click', () => run('demo.jpg'));

const overlay = $('drop-overlay');
let dragDepth = 0;
window.addEventListener('dragenter', (e) => { e.preventDefault(); dragDepth++; overlay.classList.add('on'); });
window.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; overlay.classList.remove('on'); } });
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  dragDepth = 0;
  overlay.classList.remove('on');
  const f = e.dataTransfer?.files?.[0];
  if (f && f.type.startsWith('image/')) run(f);
});

// ---- 参数滑杆：改动后仅重跑几何（深度缓存） ----
const bind = (id: string, vid: string, fmt: (v: number) => string) => {
  const el = $(id) as HTMLInputElement;
  const upd = () => ($(vid).textContent = fmt(parseFloat(el.value)));
  labelUpdaters.push(upd);
  el.addEventListener('input', () => { upd(); if (cachedDepth && !busy) resolve(); });
  upd();
};
bind('fov', 'fov-v', (v) => `${v.toFixed(0)}°`);
bind('pitch', 'pitch-v', (v) => `${v.toFixed(0)}°`);
bind('camh', 'camh-v', (v) => `${v.toFixed(2)}m`);
bind('grain', 'grain-v', (v) => v.toFixed(1));

// ---- 导出 ----
$('export-glb').addEventListener('click', async () => {
  const blob = await viewer.exportGLB();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'whitebox.glb';
  a.click();
  URL.revokeObjectURL(a.href);
});
