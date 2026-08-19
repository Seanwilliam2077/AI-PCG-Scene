"""s03 二维生成：Gemini 出全部素材，本地确定性代码兜住它的三个硬约束。

四条子工作流：
  hero      参考图 crop → 补遮挡/置绿幕/匀光 → 抠像 → 一次调用出 2×2 四视角
  tileable  真实墙面/地面 crop → 去透视 → 匀光 → 半幅偏移 → 修缝 → Sobel 质检
  decal     海报：按 text_content 逐字重绘（文字渲染是 Gemini 最强项）
  screen    CRT 屏幕 UI：同上，扫描线留给材质层做，不烘进贴图

纪律：多视角**必须一次调用出一张画布**。逐视角独立生成的跨帧不一致（亮度标准差
可达 0.12）会直接毁掉 3D 重建 —— 四个视角在同一次前向里联合采样才有一致性。
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from core import imaging as IM
from core.vlm_gateway import GatewayClient
from stages.s02_layout import _surface_kind

VIEW_MAP = {(0, 0): "left", (0, 1): "right", (1, 0): "back", (1, 1): "top"}


def _p(prompts: Path, name: str) -> str:
    return (prompts / name).read_text(encoding="utf-8")


def _crop_by_bbox(img: np.ndarray, bbox: list[float], expand: float = 0.12) -> np.ndarray:
    h, w = img.shape[:2]
    x0, y0, x1, y1 = bbox
    bw, bh = (x1 - x0) * w, (y1 - y0) * h
    px0 = int(max(0, x0 * w - bw * expand))
    py0 = int(max(0, y0 * h - bh * expand))
    px1 = int(min(w, x1 * w + bw * expand))
    py1 = int(min(h, y1 * h + bh * expand))
    return img[py0:py1, px0:px1]


def _poly_mask(shape: tuple[int, int], poly: list[list[float]]) -> np.ndarray:
    h, w = shape
    m = np.zeros((h, w), np.uint8)
    if len(poly) >= 3:
        pts = np.array([[int(p[0] * w), int(p[1] * h)] for p in poly], np.int32)
        cv2.fillPoly(m, [pts], 255)
    return m


def run(ctx: dict) -> dict:
    run_dir: Path = ctx["run_dir"]
    root: Path = ctx["root"]
    cfg: dict = ctx["config"]
    prompts = root / "prompts"
    layout = json.loads((run_dir / "scene_layout.json").read_text(encoding="utf-8"))
    ref = cv2.imread(str(run_dir / "ref.jpg"))

    vlm_gateway = GatewayClient.from_config(cfg, root, run_dir=run_dir, stage="s03_gen2d")
    m_img = cfg["models"]["image"]
    m_img_fast = cfg["models"].get("image_fast", m_img)
    chroma = cfg["gen2d"]["chroma_preset"]

    gen = run_dir / "gen2d"
    for sub in ("hero_views", "materials", "decals", "screens"):
        (gen / sub).mkdir(parents=True, exist_ok=True)

    manifest: dict = {"hero": {}, "materials": {}, "decals": {}, "screens": {},
                      "skipped": []}
    limit = int(cfg["gen2d"].get("max_hero_assets", 12))

    heroes = [o for o in layout["objects"] if o["asset_strategy"] == "hero"]
    heroes.sort(key=lambda o: -o["confidence"])
    heroes = heroes[:limit]
    # 资产库模式（gen3d.provider=library）下 hero 视图无用武之地——真模型来自库，
    # 不再需要清理图/turnaround。用配置门控而不是删代码，云端 3D 路径仍可回切。
    if not cfg["gen2d"].get("hero_enabled", True):
        print(f"  [hero] 已禁用（资产库模式），跳过 {len(heroes)} 个 hero 生成")
        heroes = []

    decal_objs = [o for o in layout["objects"] if o["asset_strategy"] == "decal"]
    progress = ctx.get("progress")
    # 材质数量要扫完 perception 才知道，先按房间三面估；进度条只求单调不求精确。
    total_units = max(1, len(heroes) + len(decal_objs) + 3)
    done_units = 0

    def bump(msg: str) -> None:
        nonlocal done_units
        done_units += 1
        if progress:
            progress.tick(min(0.99, done_units / total_units), msg)

    # ---------------- hero：清理正视图 + turnaround ----------------
    for obj in heroes:
        aid = obj["id"]
        try:
            crop = _crop_by_bbox(ref, obj["bbox_2d"])
            if min(crop.shape[:2]) < 32:
                manifest["skipped"].append({"id": aid, "why": "crop 过小"})
                continue
            crop_path = gen / "hero_views" / f"{aid}_crop.png"
            cv2.imwrite(str(crop_path), crop)

            desc = obj.get("description") or obj["label"]
            clean_prompt = _p(prompts, "s03_hero_clean.txt").format(
                description=desc, chroma=IM.CHROMA_PRESETS[chroma]["hex"])
            raw = vlm_gateway.gen_image(clean_prompt, [crop_path], model=m_img)
            clean = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            # 注意：这里**不做** align_back。清理步是刻意重新构图的（物体被抠出来、
            # 居中、换底），强行对齐回原 crop 的几何会把它扭碎。align_back 只用于
            # 「应保持像素配准的局部编辑」——即下面材质环节的匀光与修缝。
            bgra = IM.tight_crop(IM.chroma_key(clean, chroma))
            front_path = gen / "hero_views" / f"{aid}_front.png"
            cv2.imwrite(str(front_path), IM.bgra_to_rgba_png(bgra))

            views: dict[str, str] = {}
            if cfg["gen2d"].get("turnaround", True):
                sheet_prompt = _p(prompts, "s03_turnaround.txt").format(
                    description=desc, chroma=IM.CHROMA_PRESETS[chroma]["hex"])
                sraw = vlm_gateway.gen_image(sheet_prompt, [front_path], model=m_img)
                sheet = cv2.imdecode(np.frombuffer(sraw, np.uint8), cv2.IMREAD_COLOR)
                cv2.imwrite(str(gen / "hero_views" / f"{aid}_sheet.png"), sheet)
                H, W = sheet.shape[:2]
                for (r, c), vname in VIEW_MAP.items():
                    cell = sheet[r * H // 2:(r + 1) * H // 2, c * W // 2:(c + 1) * W // 2]
                    vb = IM.tight_crop(IM.chroma_key(cell, chroma))
                    vp = gen / "hero_views" / f"{aid}_{vname}.png"
                    cv2.imwrite(str(vp), IM.bgra_to_rgba_png(vb))
                    views[vname] = str(vp)

            manifest["hero"][aid] = {"front": str(front_path), "views": views,
                                     "crop": str(crop_path)}
            print(f"  [hero] {aid} ok（{len(views)} 视角）")
            bump(f"hero {aid}")
        except Exception as e:                                  # 单件失败不阻塞整批
            manifest["skipped"].append({"id": aid, "why": str(e)[:200]})
            print(f"  [warn] hero {aid} 失败：{str(e)[:160]}")
            bump(f"hero {aid} 跳过")

    # ---------------- tileable：真实墙面/地面 → 无缝 ----------------
    surfaces = [o for o in layout["objects"] if o["asset_strategy"] == "tileable"]
    # 墙/地/顶被 s02 排除出物体清单，这里从原始 detection 里再找一次。标签是自由
    # 文本（"brick wall"/"concrete floor"），必须用与 s02 同一套词尾判面规则——
    # 精确匹配会漏掉它们，房间壳就会拿"墙上的架子"当墙面材质。
    perception = json.loads((run_dir / "perception.json").read_text(encoding="utf-8"))
    for det in perception["detections"]:
        kind = _surface_kind(det["label"])
        if kind and det.get("mask_poly"):
            surfaces.append({"id": kind, "label": det["label"], "mask_poly": det["mask_poly"],
                             "material_tags": [kind], "bbox_2d": det["bbox_2d"]})

    seen: set[str] = set()
    for surf in surfaces:
        sid = surf["id"]
        if sid in seen:
            continue
        seen.add(sid)
        try:
            res = _make_tileable(vlm_gateway, prompts, ref, surf, gen / "materials",
                                 m_img_fast, cfg)
            manifest["materials"][sid] = res
            print(f"  [tile] {sid} seam={res['seam_score']:.2f} "
                  f"({'pass' if res['seam_pass'] else 'best-effort'})")
            bump(f"材质 {sid}")
        except Exception as e:
            manifest["skipped"].append({"id": sid, "why": str(e)[:200]})
            print(f"  [warn] tileable {sid} 失败：{str(e)[:160]}")
            bump(f"材质 {sid} 跳过")

    # ---------------- decal / screen：文字逐字重绘 ----------------
    for obj in decal_objs:
        aid = obj["id"]
        is_screen = any(k in obj["label"].lower() for k in ("screen", "monitor", "crt", "ui"))
        # bucket 必须在 try 之外绑定：except 分支也要用它，绑在 try 里会在
        # 首件失败时 NameError（插件分叉版踩过的坑，此处为修复后的合并版）。
        bucket = "screens" if is_screen else "decals"
        try:
            crop = _crop_by_bbox(ref, obj["bbox_2d"], expand=0.02)
            if min(crop.shape[:2]) < 24:
                bump(f"{bucket[:-1]} {aid} 过小跳过")
                continue
            cpath = gen / bucket / f"{aid}_crop.png"
            cv2.imwrite(str(cpath), crop)
            text = obj.get("text_content") or ""
            tmpl = "s03_screen.txt" if is_screen else "s03_poster.txt"
            prompt = _p(prompts, tmpl).format(
                description=obj.get("description") or obj["label"],
                text_line=(f'The text reads exactly: "{text}".' if text
                           else "Keep any text unreadable / abstract."))
            raw = vlm_gateway.gen_image(prompt, [cpath], model=m_img)
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            img = IM.to_pow2(img, cap=cfg["gen2d"].get("decal_cap", 1024))
            out = gen / bucket / f"{aid}_BC.png"
            cv2.imwrite(str(out), img)
            manifest[bucket][aid] = {"BC": str(out), "text": text}
            print(f"  [{bucket[:-1]}] {aid} ok" + (f"（文字：{text[:24]}）" if text else ""))
            bump(f"{bucket[:-1]} {aid}")
        except Exception as e:
            manifest["skipped"].append({"id": aid, "why": str(e)[:200]})
            print(f"  [warn] {bucket[:-1]} {aid} 失败：{str(e)[:160]}")
            bump(f"{bucket[:-1]} {aid} 跳过")

    # ---------------- 氛围估计（v2 新增，服务还原度）----------------
    # 一次视觉调用从参考图估主光方向/色温/雾密度/曝光倾向，写进 manifest 供
    # compile_layout 调整灯光与后期。失败退回 None——build 侧有默认值兜底。
    try:
        amb = vlm_gateway.chat_json(_p(prompts, "s06_ambience.txt"),
                              [run_dir / "ref.jpg"], model=cfg["models"]["vision"],
                              validator=_validate_ambience)
        manifest["ambience"] = amb
        print(f"  [amb ] {amb.get('mood', '?')}  key={amb['key_light']['color_temp_k']}K "
              f"fog={amb.get('fog_density')} ev={amb.get('ev_bias')}")
    except Exception as e:  # noqa: BLE001
        manifest["ambience"] = None
        print(f"  [warn] 氛围估计失败（用默认值兜底）：{str(e)[:120]}")
    bump("氛围估计")

    manifest["gateway_stats"] = vlm_gateway.stats
    (run_dir / "gen2d_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"hero": len(manifest["hero"]), "materials": len(manifest["materials"]),
            "decals": len(manifest["decals"]), "screens": len(manifest["screens"]),
            "skipped": len(manifest["skipped"]), "vlm_gateway": vlm_gateway.stats}


def _validate_ambience(obj) -> None:
    if not isinstance(obj, dict):
        raise ValueError("不是 JSON object")
    if not isinstance(obj.get("key_light"), dict):
        raise ValueError("缺 key_light")
    for key in ("color_temp_k", "relative_intensity"):
        if key not in obj["key_light"]:
            raise ValueError(f"key_light 缺 {key}")


def _make_tileable(vlm_gateway: GatewayClient, prompts: Path, ref: np.ndarray, surf: dict,
                   out_dir: Path, model: str, cfg: dict) -> dict:
    """真实像素 → 去透视 → 匀光 → 偏移修缝 → 质检。材质来自参考图本身，还原度最高。"""
    sid = surf["id"]
    h, w = ref.shape[:2]
    mask = _poly_mask((h, w), surf.get("mask_poly") or [])
    rect = IM.largest_inner_rect(mask) if mask.any() else None
    if rect is None:
        x0, y0, x1, y1 = surf["bbox_2d"]
        rect = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
    x0, y0, x1, y1 = rect
    side = max(64, min(x1 - x0, y1 - y0))
    # 在内接矩形里滑窗挑**最亮**的子方块：墙面常被屏幕溢光/暗角局部染色，
    # 随手取左上角会把整面墙的基色带偏（白砖墙取到蓝光区 → 全屋发蓝，实测翻过车）。
    xs = ([x0 + (x1 - x0 - side) * t // 2 for t in range(3)]
          if x1 - x0 > side else [x0])
    ys = ([y0 + (y1 - y0 - side) * t // 2 for t in range(3)]
          if y1 - y0 > side else [y0])
    patch, best_v = None, -1.0
    for yy in ys:
        for xx in xs:
            cand = ref[yy:yy + side, xx:xx + side]
            if cand.size and float(cand.mean()) > best_v:
                patch, best_v = cand, float(cand.mean())
    if patch is None or patch.size == 0:
        raise RuntimeError("裁不出有效的表面块")
    patch = cv2.resize(patch, (1024, 1024), interpolation=cv2.INTER_AREA)
    patch_path = out_dir / f"{sid}_patch.png"
    cv2.imwrite(str(patch_path), patch)

    # 1) 匀光去污（透视矫正已由裁正方形块近似；严格去透视需四点单应，见 imaging.rectify_plane）
    flat_prompt = (prompts / "s03_material_flat.txt").read_text(encoding="utf-8").format(
        material=", ".join(surf.get("material_tags") or []) or surf["label"])
    raw = vlm_gateway.gen_image(flat_prompt, [patch_path], model=model)
    tile = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    tile = cv2.resize(tile, (1024, 1024), interpolation=cv2.INTER_AREA)

    # 2) 偏移 → 修缝 → 质检，最多 N 轮
    best, best_score = tile, IM.seam_score(tile)
    max_rounds = int(cfg["gen2d"].get("seam_max_rounds", 3))
    thr = float(cfg["gen2d"].get("seam_threshold", 1.25))
    rounds = 0
    while best_score > thr and rounds < max_rounds:
        rounds += 1
        offset = IM.offset_half(best)
        opath = out_dir / f"{sid}_offset_{rounds}.png"
        cv2.imwrite(str(opath), offset)
        fix_prompt = (prompts / "s03_material_seam.txt").read_text(encoding="utf-8")
        # 修缝各轮输入的 offset 图字节都不同 → 缓存键天然不同，开缓存不会短路
        # 迭代；关缓存反而让复跑每轮都真花钱且不可复现（P0 修复 A6）。
        raw = vlm_gateway.gen_image(fix_prompt, [opath], model=model)
        patched = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        patched = cv2.resize(patched, offset.shape[1::-1], interpolation=cv2.INTER_AREA)
        patched = IM.align_back(patched, offset)
        merged = IM.blend_cross_band(offset, patched, band=48)
        cand = IM.offset_half(merged)                    # 滚回去，接缝复位到四边
        score = IM.seam_score(cand)
        if score < best_score:
            best, best_score = cand, score

    # 3) PBR 通道：全部确定性推导，绝不让模型画法线
    mat_class = _mat_class(surf)
    albedo = IM.to_pow2(best, cap=int(cfg["gen2d"].get("material_cap", 1024)))
    height = IM.derive_height(albedo)
    normal = IM.height_to_normal(height)
    rough = IM.derive_roughness(albedo, height, mat_class)
    metal = IM.derive_metallic(albedo.shape[:2], mat_class)
    ao = IM.ao_from_height(height)

    paths = {}
    for suffix, img in (("BC", albedo), ("N", normal),
                        ("ORM", IM.pack_orm(ao, rough, metal)),
                        ("H", (height * 255).astype(np.uint8))):
        p = out_dir / f"T_{sid}_{suffix}.png"
        cv2.imwrite(str(p), img)
        paths[suffix] = str(p)

    return {"paths": paths, "seam_score": round(best_score, 3),
            "seam_pass": bool(best_score <= thr), "rounds": rounds,
            "material_class": mat_class, "tile_size_cm": 100}


def _mat_class(surf: dict) -> str:
    tags = " ".join(surf.get("material_tags") or []).lower() + " " + surf["label"].lower()
    for key in ("glazed_tile", "ceramic", "concrete", "brushed_metal", "painted_metal",
                "metal", "plastic", "paper", "wood", "fabric"):
        if key.replace("_", " ") in tags or key in tags:
            return key
    if "wall" in tags:
        return "glazed_tile"
    if "floor" in tags:
        return "concrete"
    return "default"
