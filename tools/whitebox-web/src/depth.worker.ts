/// <reference lib="webworker" />
/**
 * 深度推理 Worker：Depth Anything V2 Small（transformers.js）
 * 优先 WebGPU（fp16），失败回退 WASM（q8）。
 * 输入：ImageData（RGBA），输出：模型分辨率的 Float32 相对逆深度图。
 */
import { pipeline, env, RawImage } from '@huggingface/transformers';

env.allowLocalModels = false;

const MODEL = 'onnx-community/depth-anything-v2-small';
const DET_MODEL = 'Xenova/detr-resnet-50';

let dp: any = null;
let det: any = null;
let device: 'webgpu' | 'wasm' = 'wasm';

function post(msg: any, transfer?: Transferable[]) {
  (self as unknown as Worker).postMessage(msg, transfer ?? []);
}

async function ensurePipeline() {
  if (dp) return;
  const seen = new Map<string, number>();
  const progressCb = (x: any) => {
    if (x.status === 'progress' && typeof x.progress === 'number') {
      seen.set(x.file ?? '', x.progress);
      let sum = 0;
      for (const v of seen.values()) sum += v;
      const pct = sum / Math.max(1, seen.size);
      post({ type: 'status', msg: `下载模型 ${pct.toFixed(0)}%`, pct });
    }
  };
  const hasWebGPU = typeof (navigator as any).gpu !== 'undefined';
  if (hasWebGPU) {
    try {
      post({ type: 'status', msg: '初始化 WebGPU…' });
      dp = await pipeline('depth-estimation', MODEL, {
        device: 'webgpu',
        dtype: 'fp16',
        progress_callback: progressCb,
      });
      device = 'webgpu';
      return;
    } catch (e) {
      console.warn('[depth.worker] WebGPU 初始化失败，回退 WASM：', e);
      dp = null;
    }
  }
  post({ type: 'status', msg: '初始化 WASM 后端…' });
  dp = await pipeline('depth-estimation', MODEL, {
    device: 'wasm',
    dtype: 'q8',
    progress_callback: progressCb,
  });
  device = 'wasm';
}

async function ensureDetector() {
  if (det) return;
  post({ type: 'status', msg: '载入检测模型…' });
  const seen = new Map<string, number>();
  const progressCb = (x: any) => {
    if (x.status === 'progress' && typeof x.progress === 'number') {
      seen.set(x.file ?? '', x.progress);
      let sum = 0;
      for (const v of seen.values()) sum += v;
      post({ type: 'status', msg: `下载检测模型 ${(sum / Math.max(1, seen.size)).toFixed(0)}%` });
    }
  };
  // DETR 在 WebGPU 上兼容性一般，直接走 WASM q8（约 43MB，推理秒级）
  det = await pipeline('object-detection', DET_MODEL, {
    device: 'wasm',
    dtype: 'q8',
    progress_callback: progressCb,
  });
}

self.onmessage = async (ev: MessageEvent) => {
  const { type } = ev.data;
  if (type === 'detect') {
    const { data, width, height, reqId } = ev.data;
    try {
      await ensureDetector();
      post({ type: 'status', msg: '识别场景物体…' });
      const img = new RawImage(new Uint8ClampedArray(data), width, height, 4);
      const out = await det(img, { threshold: 0.35, percentage: true });
      const dets = (out as any[]).map((o) => ({
        label: String(o.label),
        score: Number(o.score),
        box: [o.box.xmin, o.box.ymin, o.box.xmax, o.box.ymax] as [number, number, number, number],
      }));
      post({ type: 'detections', reqId, dets });
    } catch (e: any) {
      console.warn('[detect] 失败（降级为无检测）', e);
      post({ type: 'detections', reqId, dets: [] });
    }
    return;
  }
  if (type !== 'infer') return;
  const { data, width, height, reqId } = ev.data as {
    data: Uint8ClampedArray; width: number; height: number; reqId: number;
  };
  try {
    await ensurePipeline();
    post({ type: 'status', msg: `深度推理中（${device}）…` });
    const t0 = performance.now();
    const img = new RawImage(new Uint8ClampedArray(data), width, height, 4);
    const out = await dp(img);
    const tensor = out.predicted_depth;
    const dims: number[] = tensor.dims;
    // 兼容 [h,w] / [1,h,w] / [1,1,h,w]
    const mh = dims[dims.length - 2];
    const mw = dims[dims.length - 1];
    const raw = tensor.data as Float32Array | Float64Array;
    const buf = new Float32Array(mh * mw);
    for (let i = 0; i < buf.length; i++) buf[i] = raw[i];
    const ms = performance.now() - t0;
    post(
      { type: 'result', reqId, width: mw, height: mh, data: buf, device, ms },
      [buf.buffer]
    );
  } catch (e: any) {
    console.error('[depth.worker]', e);
    post({ type: 'error', reqId, message: e?.message ?? String(e) });
  }
};
