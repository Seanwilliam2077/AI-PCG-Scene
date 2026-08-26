/** Venus VLM 实例解析（与 whitebox-pipeline/server/vlm_venus.py 同 prompt/schema）。
 *
 * 两种模式（按 URL 自动判别）：
 *  - 直连：OpenAI 兼容网关 <url>/chat/completions（需网关允许浏览器 CORS）
 *  - 本地代理：URL 含 /api/vlm 时 POST {image} 给本地 whitebox-pipeline 服务，
 *    Key 存在本地服务端，浏览器不携带
 * 配置存 localStorage，仅在用户自己的浏览器里。失败返回 null（调用方回退 DETR）。
 */
import type { Detection } from './types';

export interface VlmConfig { url: string; key: string; model: string }

const LS_KEY = 'whitebox_venus_cfg';

export function getVlmConfig(): VlmConfig | null {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const c = JSON.parse(raw);
    return c && c.url ? c : null;
  } catch { return null; }
}

export function setVlmConfig(c: VlmConfig | null): void {
  if (c && c.url) localStorage.setItem(LS_KEY, JSON.stringify(c));
  else localStorage.removeItem(LS_KEY);
}

const PROMPT = `You are a 3D layout annotator. Analyze the image and output STRICT JSON only (no markdown, no commentary):
{
  "objects": [
    {
      "label": "short_snake_case_name",
      "category": "one of: door,person,chair,table,desk,counter,bar_counter,bar_stool,sofa,bed,shelf,cabinet,plant,tree,lamp,pendant_lamp,tv,window,painting,box,car,other",
      "bbox": [x0,y0,x1,y1],
      "support": "floor"|"surface"|"wall"|"hanging",
      "shape": "box"|"cylinder",
      "approx_height_m": number or null
    }
  ]
}
Rules:
- bbox in normalized coordinates (0..1), x right, y down, tight around the visible object.
- One entry PER VISIBLE INSTANCE (5 stools = 5 entries), at most 28 objects, largest/most structural first.
- Include only physical objects with volume; skip decals, shadows, text overlays.
- approx_height_m: your best real-world height guess for THIS object, null if unsure.`;

function extractJson(text: string): any | null {
  for (let i = text.indexOf('{'); i >= 0; i = text.indexOf('{', i + 1)) {
    // 逐字符括号配对（比贪婪正则稳：尾注里的 } 不会打败它）
    let depth = 0, inStr = false, esc = false;
    for (let j = i; j < text.length; j++) {
      const ch = text[j];
      if (esc) { esc = false; continue; }
      if (ch === '\\') { esc = true; continue; }
      if (ch === '"') inStr = !inStr;
      else if (!inStr && ch === '{') depth++;
      else if (!inStr && ch === '}') {
        depth--;
        if (depth === 0) {
          try { return JSON.parse(text.slice(i, j + 1)); } catch { break; }
        }
      }
    }
  }
  return null;
}

function sanitize(objects: any[]): Detection[] {
  const out: Detection[] = [];
  for (const o of (objects ?? []).slice(0, 28)) {
    try {
      const b = (o.bbox as number[]).map((v) => Math.max(0, Math.min(1, Number(v))));
      if (b.length !== 4) continue;
      const [x0r, y0r, x1r, y1r] = b;
      const x0 = Math.min(x0r, x1r), x1 = Math.max(x0r, x1r);
      const y0 = Math.min(y0r, y1r), y1 = Math.max(y0r, y1r);
      if (x1 - x0 < 0.01 || y1 - y0 < 0.01) continue;
      const h = Number(o.approx_height_m);
      out.push({
        label: String(o.category ?? o.label ?? 'other'),
        score: 0.9,
        box: [x0, y0, x1, y1],
        support: ['floor', 'surface', 'wall', 'hanging'].includes(o.support) ? o.support : undefined,
        shape: o.shape === 'cylinder' ? 'cylinder' : 'box',
        heightM: Number.isFinite(h) && h > 0.05 && h < 30 ? h : undefined,
      });
    } catch { /* 单条坏数据跳过 */ }
  }
  return out;
}

export async function vlmDetect(
  dataURL: string,
  log: (m: string) => void = () => {},
): Promise<Detection[] | null> {
  const cfg = getVlmConfig();
  if (!cfg) return null;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 90000);
  try {
    if (cfg.url.includes('/api/vlm')) {
      // 本地代理模式
      const r = await fetch(cfg.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: dataURL }),
        signal: ctrl.signal,
      });
      if (!r.ok) throw new Error(`代理 ${r.status}`);
      const j = await r.json();
      const dets = sanitize(j.objects ?? []);
      log(`[vlm] 代理解析 ${dets.length} 个实例`);
      return dets.length ? dets : null;
    }
    // 直连 OpenAI 兼容网关
    const url = cfg.url.replace(/\/+$/, '') + '/chat/completions';
    const r = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(cfg.key ? { Authorization: `Bearer ${cfg.key}` } : {}),
      },
      body: JSON.stringify({
        model: cfg.model || 'gpt-4o',
        temperature: 0,
        messages: [{
          role: 'user',
          content: [
            { type: 'text', text: PROMPT },
            { type: 'image_url', image_url: { url: dataURL } },
          ],
        }],
      }),
      signal: ctrl.signal,
    });
    if (!r.ok) throw new Error(`网关 ${r.status}`);
    const j = await r.json();
    const text = j?.choices?.[0]?.message?.content ?? '';
    const parsed = extractJson(text);
    if (!parsed) throw new Error('返回无法解析为 JSON');
    const dets = sanitize(parsed.objects ?? []);
    log(`[vlm] 解析 ${dets.length} 个实例`);
    return dets.length ? dets : null;
  } catch (e: any) {
    log(`[vlm] 失败：${e?.message ?? e}`);
    return null;
  } finally {
    clearTimeout(timer);
  }
}
