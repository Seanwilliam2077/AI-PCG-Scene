"""云端 VLM 网关客户端：本管线唯一的 AI 入口。

网关是 OpenAI 兼容的（`/v1/chat/completions`），后面转发 Gemini
系列。它与 Google 原生 google-genai SDK 的差异是本文件存在的全部理由：

  * 调用形态是 chat/completions，不是 `client.interactions.create`；
  * 没有 `response_schema` 硬约束 —— 结构化输出只能靠 prompt 约定 + 本地校验 +
    一次修复重试（见 chat_json）；
  * 图像生成同样走 chat 端点，图片以 base64 data URL 挂在
    `choices[0].message.content[]` 的 `vlm_gateway_multimodal_url` / `image_url` 部件上。

无 seed 是既定事实（见方案第 5 章）：可复现性靠**请求哈希缓存**兑现——同样的
(模型, 参数, 输入字节) 复跑直接命中缓存、字节一致且零成本。cache/ 随交付归档。
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 网关端点不入库不入包（端点不外发）：按 配置文件 vlm_gateway.url → 环境变量
# VLM_GATEWAY_URL 的顺序解析；两者皆空时只能走离线回放（评审机的默认姿势）。
DEFAULT_URL = ""

def _discover_ini_candidates() -> list[tuple[str, str, str]]:
    """key 的 ini 回退位置，按优先级排列。

    首选**所在 UE 工程**的 Config/DefaultEditor.ini 的 [AISceneBuilder.Gateway]——
    与插件 C++ 侧（AISceneBuilderSettings.cpp）读的是同一节，插件面板显示
    「Key 已配置」时这里必然也读得到。管线根可能在两个位置：
      <Project>/Plugins/AISceneBuilder/Python  （插件内嵌形态）
      <任意目录>/pipeline                       （CLI 开发形态，同级找 *.uproject）
    所以从本文件向上逐级找带 Config/DefaultEditor.ini 的目录。
    末位保留历史遗留的 LegacyProject 路径作垫底。
    """
    out: list[tuple[str, str, str]] = []
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:6]:
        ini = parent / "Config" / "DefaultEditor.ini"
        if ini.is_file():
            out.append((str(ini), "AISceneBuilder.Gateway", "ApiKey"))
        for sibling in parent.glob("*/Config/DefaultEditor.ini"):
            entry = (str(sibling), "AISceneBuilder.Gateway", "ApiKey")
            if entry not in out:
                out.append(entry)
    out.append((r"C:\LegacyProject\Config\DefaultEditor.ini",
                "LegacyProject.Gateway", "ApiKey"))
    return out


_INI_CANDIDATES = _discover_ini_candidates()


class GatewayError(RuntimeError):
    pass


def _read_key_from_ini(path: str, section: str, option: str) -> str:
    """手扫 UE 的 ini。不用 configparser：UE 允许重复键与 `+Key=` 前缀，会让它抛错。"""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            in_section = False
            for line in fh:
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    in_section = s[1:-1].strip() == section
                    continue
                if not in_section or "=" not in s:
                    continue
                name, _, value = s.partition("=")
                if name.strip().lstrip("+-").strip() == option:
                    return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def load_api_key() -> str:
    key = os.environ.get("VLM_API_KEY", "").strip()
    if key:
        return key
    for path, section, option in _INI_CANDIDATES:
        if os.path.isfile(path):
            key = _read_key_from_ini(path, section, option).strip()
            if key:
                return key
    raise GatewayError(
        "找不到 云端 VLM 网关 API Key：设置环境变量 VLM_API_KEY，"
        "或在工程 Config/DefaultEditor.ini 的 [AISceneBuilder.Gateway] ApiKey 提供"
    )


def encode_data_url(image_path: str | Path) -> str:
    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    return f"data:{mime};base64," + base64.b64encode(Path(image_path).read_bytes()).decode("ascii")


def _bytes_data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def extract_json(text: str) -> Any:
    """从模型答复里抠出 JSON。

    代理没有 schema 硬约束，模型经常裹 ```json 围栏或加一句解释。按
    「去围栏 → 直接解析 → 括号配平扫描」三级降级，尽量不为一句废话重跑一次。
    """
    if not isinstance(text, str):
        raise GatewayError(f"期望字符串答复，实际是 {type(text).__name__}")
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 括号配平扫描：抓第一个完整的 {...} 或 [...]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = s.find(opener)
        if start < 0:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise GatewayError(f"答复中找不到合法 JSON：{text[:300]!r}")


class GatewayClient:
    """带哈希缓存与退避重试的 云端 VLM 网关 客户端。

    offline=True 是**离线回放模式**：只许缓存命中，miss 即显式报错——评审机
    没有云端 云端网关访问权限时，凭随工程附带的 cache/vlm_gateway 也能整条复跑。
    log_path 是逐调用 JSONL 日志（阶段/模型/缓存命中/耗时），既是排障现场，
    也是测试题"AI 使用标注"的机器可读证据，UE 面板直接展示它。
    """

    def __init__(self, cache_dir: str | Path, url: str = DEFAULT_URL,
                 timeout: float = 300.0, max_retries: int = 3,
                 min_interval: float = 1.0, offline: bool = False,
                 log_path: str | Path | None = None, stage: str = "") -> None:
        self.url = url
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self.offline = bool(offline)
        self.log_path = Path(log_path) if log_path else None
        self.stage = stage
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._key = None if self.offline else load_api_key()   # 离线模式不需要 Key
        self._last_call = 0.0
        self.stats = {"calls": 0, "cache_hits": 0, "retries": 0}

    @classmethod
    def from_config(cls, cfg: dict, root: Path, run_dir: Path | None = None,
                    stage: str = "") -> "GatewayClient":
        """统一的构造入口（原先 s01/s03 各抄一份五行构造，收编到这里）。"""
        v = cfg["vlm_gateway"]
        url = (v.get("url") or "").strip() or os.environ.get("VLM_GATEWAY_URL", "").strip()
        return cls(cache_dir=Path(root) / "cache" / "vlm_gateway",
                   url=url, timeout=v["timeout_seconds"],
                   max_retries=v["max_retries"],
                   min_interval=v["min_interval_seconds"],
                   offline=bool(v.get("offline", False)),
                   log_path=(Path(run_dir) / "api_calls.jsonl") if run_dir else None,
                   stage=stage)

    def _log_call(self, kind: str, model: str, cache_hit: bool,
                  seconds: float, prompt: str) -> None:
        if not self.log_path:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                # endpoint 刻意不落盘：调用留痕是"AI 使用标注"证据，
                # 模型/阶段/命中/耗时足够，云端网关 URL 不外泄
                fh.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "stage": self.stage, "kind": kind, "model": model,
                    "cache_hit": cache_hit,
                    "seconds": round(seconds, 2),
                    "prompt_head": prompt[:80],
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _offline_miss(self, kind: str) -> GatewayError:
        return GatewayError(
            f"离线回放模式下缓存未命中（{kind}）。评审机上只允许回放随工程附带的 "
            "cache/vlm_gateway；要发起真实调用请关闭 Offline 开关并配置 VLM_API_KEY。")

    # ---------------- 缓存 ----------------
    def _cache_key(self, model: str, parts: list[Any], params: dict) -> str:
        # URL 不参与键：存量缓存全部产自同一网关且键中无 URL，保持键稳定
        # 才能让评审机离线回放与本机在线（URL 走环境变量）命中同一批缓存。
        h = hashlib.sha256(model.encode())
        h.update(json.dumps(params, sort_keys=True, ensure_ascii=False).encode())
        for p in parts:
            h.update(p if isinstance(p, bytes) else str(p).encode())
        return h.hexdigest()[:32]

    # ---------------- 底层 POST ----------------
    def _post(self, body: dict) -> dict:
        if not self.url:
            raise GatewayError(
                "未配置网关端点（configs vlm_gateway.url 或环境变量 VLM_GATEWAY_URL）。"
                "评审机请开启离线回放开关，凭随工程附带的 cache/vlm_gateway 复跑。")
        # 节流：相邻两次调用的最小间隔，避免撞代理限频
        gap = time.time() - self._last_call
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)

        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(
                    self.url, data=payload,
                    headers={"Authorization": f"Bearer {self._key}",
                             "Content-Type": "application/json"},
                    method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    self._last_call = time.time()
                    self.stats["calls"] += 1
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                last_err = GatewayError(f"HTTP {e.code}: {detail}")
                # 4xx 里只有 429 值得重试，其余是请求本身的问题，重试只是浪费
                if e.code < 500 and e.code != 429:
                    raise last_err from e
            except Exception as e:  # noqa: BLE001 网络类异常统一退避重试
                last_err = e
            if attempt < self.max_retries:
                self.stats["retries"] += 1
                time.sleep(min(2.0 * (2 ** attempt), 30.0))
        raise GatewayError(f"云端 VLM 网关 调用失败（重试 {self.max_retries} 次）：{last_err}")

    # ---------------- 文本 / 视觉 ----------------
    def chat(self, prompt: str, images: list[str | Path] | None = None,
             model: str = "gemini-2.5-flash", temperature: float = 0.2,
             max_tokens: int = 16384, use_cache: bool = True) -> str:
        images = [str(p) for p in (images or [])]
        img_bytes = [Path(p).read_bytes() for p in images]
        params = {"temperature": temperature, "max_tokens": max_tokens, "kind": "chat"}
        ck = self._cache_key(model, [prompt, *img_bytes], params)
        hit = self.cache_dir / f"{ck}.txt"
        if use_cache and hit.exists():
            self.stats["cache_hits"] += 1
            self._log_call("chat", model, True, 0.0, prompt)
            return hit.read_text(encoding="utf-8")
        if self.offline:
            raise self._offline_miss("chat")

        _t0 = time.time()
        if images:
            content: Any = [{"type": "text", "text": prompt}]
            for p in images:
                content.append({"type": "image_url",
                                "image_url": {"url": encode_data_url(p)}})
        else:
            content = prompt

        data = self._post({"model": model,
                           "messages": [{"role": "user", "content": content}],
                           "temperature": temperature, "max_tokens": max_tokens})
        text = _message_text(data)
        self._log_call("chat", model, False, time.time() - _t0, prompt)
        if use_cache:
            hit.write_text(text, encoding="utf-8")
            (self.cache_dir / f"{ck}.meta.json").write_text(
                json.dumps({"model": model, "params": params, "kind": "chat"},
                           ensure_ascii=False), encoding="utf-8")
        return text

    def chat_json(self, prompt: str, images: list[str | Path] | None = None,
                  model: str = "gemini-2.5-flash", validator=None,
                  **kw) -> Any:
        """要 JSON 的 chat。代理无 schema 约束，故本地校验 + 一次修复重试。

        修复成功后把合法文本**回写主缓存槽**：否则坏响应永久占着缓存位，
        每次复跑都要真花一次修复调用（P0 修复 A2）。
        """
        use_cache = bool(kw.pop("use_cache", True))     # pop 掉，防与下面的显式传参撞车
        text = self.chat(prompt, images, model=model, use_cache=use_cache, **kw)
        try:
            obj = extract_json(text)
            if validator:
                validator(obj)
            return obj
        except Exception as first:  # noqa: BLE001
            repair = (f"{prompt}\n\n---\n你上一次的输出无法解析或不合规："
                      f"{str(first)[:200]}\n请只输出合法 JSON，不要任何解释文字、"
                      f"不要 markdown 围栏。")
            text2 = self.chat(repair, images, model=model, use_cache=False, **kw)
            obj = extract_json(text2)
            if validator:
                validator(obj)
            if use_cache:
                # 与 chat() 相同的键推导（默认参数保持一致），覆盖投毒条目
                img_bytes = [Path(p).read_bytes() for p in (images or [])]
                params = {"temperature": kw.get("temperature", 0.2),
                          "max_tokens": kw.get("max_tokens", 16384), "kind": "chat"}
                ck = self._cache_key(model, [prompt, *img_bytes], params)
                (self.cache_dir / f"{ck}.txt").write_text(text2, encoding="utf-8")
            return obj

    # ---------------- 图像生成 ----------------
    def gen_image(self, prompt: str, ref_images: list[str | Path] | None = None,
                  model: str = "gemini-3-pro-image", temperature: float = 0.2,
                  max_tokens: int = 8192, use_cache: bool = True) -> bytes:
        """返回 PNG/JPEG 原始字节。ref_images 作为参考图一并送入（image editing）。"""
        refs = [str(p) for p in (ref_images or [])]
        ref_bytes = [Path(p).read_bytes() for p in refs]
        params = {"temperature": temperature, "max_tokens": max_tokens, "kind": "image"}
        ck = self._cache_key(model, [prompt, *ref_bytes], params)
        hit = self.cache_dir / f"{ck}.png"
        if use_cache and hit.exists():
            self.stats["cache_hits"] += 1
            self._log_call("image", model, True, 0.0, prompt)
            return hit.read_bytes()
        if self.offline:
            raise self._offline_miss("image")

        _t0 = time.time()
        if refs:
            content: Any = [{"type": "text", "text": prompt}]
            for p in refs:
                content.append({"type": "image_url",
                                "image_url": {"url": encode_data_url(p)}})
        else:
            content = prompt

        data = self._post({"model": model,
                           "messages": [{"role": "user", "content": content}],
                           "temperature": temperature, "max_tokens": max_tokens})
        images = _extract_images(data)
        if not images:
            raise GatewayError(f"云端 VLM 网关 未返回图片，模型答复：{_message_text(data)[:300]!r}")
        head, _, payload = images[0].partition(",")
        if ";base64" not in head:
            raise GatewayError("返回的图片不是 base64 data URL")
        raw = base64.b64decode(payload)
        self._log_call("image", model, False, time.time() - _t0, prompt)
        if use_cache:
            hit.write_bytes(raw)
            (self.cache_dir / f"{ck}.meta.json").write_text(
                json.dumps({"model": model, "params": params, "kind": "image",
                            "prompt": prompt[:2000]}, ensure_ascii=False),
                encoding="utf-8")
        return raw


# ---------------- 响应解析 ----------------
def _message_text(data: dict) -> str:
    """取答复正文。content 可能是 str，也可能是多模态部件数组。"""
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return json.dumps(data, ensure_ascii=False)[:500]
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content
                       if isinstance(p, dict) and p.get("type") == "text")
    return str(content)


def _extract_images(data: dict) -> list[str]:
    """图片部件有两种 type：Nanobanana 走 vlm_gateway_multimodal_url，OpenAI 兼容走 image_url。"""
    out: list[str] = []
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return out
    if not isinstance(content, list):
        return out
    for part in content:
        if not isinstance(part, dict):
            continue
        for field in ("vlm_gateway_multimodal_url", "image_url"):
            if part.get("type") == field:
                img = part.get(field, {})
                if isinstance(img, dict) and img.get("url"):
                    out.append(img["url"])
    return out
