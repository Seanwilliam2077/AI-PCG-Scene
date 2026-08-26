import { DepthClient } from './depthClient';
import { autoCalibrate, type AutoCam } from './geometry/pipeline';
import { getVlmConfig, setVlmConfig, vlmDetect } from './vlm';

console.log('[whitebox-web] v0.4.0');
import { solveGeometry } from './geometry/pipeline';
import { Viewer } from './viewer';
import type { DepthResult, Detection } from './types';

const $ = (id: string) => document.getElementById(id)!;
const statusEl = $('status');
const viewer = new Viewer($('viewport') as HTMLCanvasElement);
viewer.setOverlayElement($('overlay-img') as HTMLImageElement);
(window as any).__viewer = viewer; // 调试句柄
const depthClient = new DepthClient();

let cachedDepth: DepthResult | null = null;
let cachedDets: Detection[] = [];
let autoCam: AutoCam | null = null;
let busy = false;

function setStatus(msg: string, err = false) {
  statusEl.textContent = msg;
  statusEl.className = err ? 'err' : '';
}
depthClient.onStatus = (msg) => setStatus(msg);

function params() {
  return {
    vfovDeg: autoCam?.vfovDeg ?? 55,
    pitchDeg: autoCam?.pitchDeg ?? 0,
    camHeightM: 1.6, // 初值；实际尺度由检测尺度锚（人/椅/桌）解出
    minObjSizeM: 0.12,
    maxBoxes: 64,
    granularity: parseFloat(($('grain') as HTMLInputElement).value),
  };
}

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
  const url = cv.toDataURL('image/jpeg', 0.85);
  ($('thumb') as HTMLImageElement).src = url;
  ($('overlay-img') as HTMLImageElement).src = url;
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
    cachedDepth.origWidth = imageData.width;
    cachedDepth.origHeight = imageData.height;
    // 实例解析：Venus VLM 优先（类别/支撑关系/高度先验），失败回退浏览器内 DETR
    let detSource = '无';
    cachedDets = [];
    if (getVlmConfig()) {
      setStatus('VLM(Venus) 实例解析…');
      const dets = await vlmDetect(($('thumb') as HTMLImageElement).src, (m) => console.log(m));
      if (dets) { cachedDets = dets; detSource = 'Venus VLM'; }
      else setStatus('VLM 失败，回退 DETR…');
    }
    if (!cachedDets.length) {
      setStatus('识别场景物体…（DETR，首次需下载检测模型）');
      cachedDets = await depthClient.detect(imageData);
      if (cachedDets.length) detSource = 'DETR';
    }
    (window as any).__detSource = detSource;
    if (new URLSearchParams(location.search).has('fakedet')) {
      // 调试：注入假检测验证 模板/尺度锚 链路
      cachedDets = [
        { label: 'dining table', score: 0.9, box: [0.55, 0.71, 0.92, 0.99] },
        { label: 'chair', score: 0.85, box: [0.69, 0.8, 0.86, 0.99] },
        { label: 'potted plant', score: 0.8, box: [0.0, 0.62, 0.06, 0.99] },
        { label: 'person', score: 0.8, box: [0.4, 0.35, 0.5, 0.9] },
      ];
    }
    setStatus('解算相机（消失点）…');
    await new Promise((r) => setTimeout(r, 30)); // 让状态渲染出来
    autoCam = autoCalibrate(cachedDepth, imageData);
    (window as any).__autoCam = autoCam;
    const METHOD_LABEL = { vp2: '双消失点', vp1: '消失点+搜索', search: '评分搜索' } as const;
    $('cam-info').textContent =
      `相机：FOV ${autoCam.vfovDeg}° 俯仰 ${autoCam.pitchDeg}°（${METHOD_LABEL[autoCam.method]}）`;
    setStatus('几何求解中…');
    await new Promise((r) => setTimeout(r, 30));
    const spec = resolve();
    const dt = ((performance.now() - t0) / 1000).toFixed(1);
    const nDet = spec?.instances.filter((i) => i.source === 'detect').length ?? 0;
    setStatus(
      `完成 ✓ ${dt}s（${cachedDepth.device}）· ${spec?.instances.length ?? 0} 个体块` +
      `（${nDet} 个语义模板 · ${detSource}）。对位视角；切自由环绕看三维`,
    );
    $('controls').hidden = false;
    setMode('match');
  } catch (e: any) {
    console.error(e);
    setStatus(`失败：${e?.message ?? e}`, true);
    cachedDepth = null; // 缓存与缩略图必须一致，防重解旧图
    cachedDets = [];
    autoCam = null;
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
  el.addEventListener('input', () => { upd(); if (cachedDepth && !busy) resolve(); });
  upd();
};
bind('grain', 'grain-v', (v) => v.toFixed(1));

// ---- 视角模式 ----
function setMode(m: 'match' | 'orbit') {
  viewer.setMode(m);
  $('mode-match').classList.toggle('active', m === 'match');
  $('mode-orbit').classList.toggle('active', m === 'orbit');
  ($('overlay-row') as HTMLElement).style.display = m === 'match' ? '' : 'none';
}
$('mode-match').addEventListener('click', () => setMode('match'));
$('mode-orbit').addEventListener('click', () => setMode('orbit'));
$('overlay').addEventListener('input', (e) => {
  const v = parseInt((e.target as HTMLInputElement).value, 10);
  $('overlay-v').textContent = `${v}%`;
  viewer.setOverlayOpacity(v / 100);
});

// ---- Venus VLM 设置 ----
function refreshVlmBadge() {
  const b = $('vlm-state');
  const cfg = getVlmConfig();
  b.textContent = cfg ? (cfg.url.includes('/api/vlm') ? '代理' : '直连') : '未配置';
  b.className = 'badge ' + (cfg ? 'on' : 'off');
}
$('vlm-btn').addEventListener('click', () => {
  const p = $('vlm-panel') as HTMLElement;
  p.hidden = !p.hidden;
  const cfg = getVlmConfig();
  if (!p.hidden && cfg) {
    ($('vlm-url') as HTMLInputElement).value = cfg.url;
    ($('vlm-key') as HTMLInputElement).value = cfg.key;
    ($('vlm-model') as HTMLInputElement).value = cfg.model;
  }
});
$('vlm-save').addEventListener('click', () => {
  const url = ($('vlm-url') as HTMLInputElement).value.trim();
  setVlmConfig(url ? {
    url,
    key: ($('vlm-key') as HTMLInputElement).value.trim(),
    model: ($('vlm-model') as HTMLInputElement).value.trim() || 'gpt-4o',
  } : null);
  refreshVlmBadge();
  ($('vlm-panel') as HTMLElement).hidden = true;
});
$('vlm-clear').addEventListener('click', () => {
  setVlmConfig(null);
  refreshVlmBadge();
  ($('vlm-panel') as HTMLElement).hidden = true;
});
refreshVlmBadge();

// ---- 导出 ----
$('export-glb').addEventListener('click', async () => {
  const blob = await viewer.exportGLB();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'whitebox.glb';
  a.click();
  URL.revokeObjectURL(a.href);
});
