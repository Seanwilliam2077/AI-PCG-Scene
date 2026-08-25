import type { DepthResult, Detection } from './types';

/** 深度 Worker 的 Promise 封装；status 回调用于 UI 进度 */
export class DepthClient {
  private worker: Worker;
  private reqSeq = 0;
  private pending = new Map<
    number,
    { resolve: (r: DepthResult) => void; reject: (e: Error) => void }
  >();
  private pendingDet = new Map<number, (dets: Detection[]) => void>();
  onStatus: (msg: string, pct?: number) => void = () => {};

  constructor() {
    this.worker = new Worker(new URL('./depth.worker.ts', import.meta.url), {
      type: 'module',
    });
    this.worker.onmessage = (ev) => {
      const d = ev.data;
      if (d.type === 'status') {
        this.onStatus(d.msg, d.pct);
      } else if (d.type === 'detections') {
        const p = this.pendingDet.get(d.reqId);
        if (p) {
          this.pendingDet.delete(d.reqId);
          p(d.dets);
        }
      } else if (d.type === 'result') {
        const p = this.pending.get(d.reqId);
        if (p) {
          this.pending.delete(d.reqId);
          p.resolve({
            width: d.width,
            height: d.height,
            data: d.data,
            device: d.device,
            ms: d.ms,
          });
        }
      } else if (d.type === 'error') {
        const p = this.pending.get(d.reqId);
        if (p) {
          this.pending.delete(d.reqId);
          p.reject(new Error(d.message));
        } else {
          // 初始化阶段的错误：拒绝所有等待者；检测请求降级为空结果防悬挂
          for (const [, pp] of this.pending) pp.reject(new Error(d.message));
          this.pending.clear();
          for (const [, r] of this.pendingDet) r([]);
          this.pendingDet.clear();
        }
      }
    };
    this.worker.onerror = (ev) => {
      for (const [, p] of this.pending) p.reject(new Error(ev.message || 'worker error'));
      this.pending.clear();
      for (const [, r] of this.pendingDet) r([]);
      this.pendingDet.clear();
    };
  }

  /** 目标检测（失败时 resolve 为空数组，不阻断管线） */
  detect(imageData: ImageData): Promise<Detection[]> {
    const reqId = ++this.reqSeq;
    return new Promise((resolve) => {
      this.pendingDet.set(reqId, resolve);
      const copy = new Uint8ClampedArray(imageData.data);
      this.worker.postMessage(
        { type: 'detect', reqId, data: copy, width: imageData.width, height: imageData.height },
        [copy.buffer]
      );
    });
  }

  infer(imageData: ImageData): Promise<DepthResult> {
    const reqId = ++this.reqSeq;
    return new Promise((resolve, reject) => {
      this.pending.set(reqId, { resolve, reject });
      // 拷贝一份可转移的数据（ImageData.data 的 buffer 转移后原图不可再用）
      const copy = new Uint8ClampedArray(imageData.data);
      this.worker.postMessage(
        { type: 'infer', reqId, data: copy, width: imageData.width, height: imageData.height },
        [copy.buffer]
      );
    });
  }
}
