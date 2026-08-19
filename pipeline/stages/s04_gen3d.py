"""s04 三维生成：hero 资产 → 网格 + 贴图 → asset_registry.json。

provider 由配置选（公共接口见 core/mesh_proxy.py，云端后端不随包分发）。默认
走 proxy 代理网格——尺寸/pivot/UV 都按最终规范生成，UE 里摆出来是一个尺度正确的
场景；凭证到位后改一行配置即可切换，下游零改动。

无论哪种后端，产物都过同一道清洗：**尺寸以 layout 的 size_cm 为准、pivot 重定到
底部中心**。这既是「尺寸最终裁决权在规则」的落实，也顺带吸收了云端图生 3D 输出
规范未知（pivot/轴向/归一化均无官方说明）的风险。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.mesh_proxy import MeshRequest


def run(ctx: dict) -> dict:
    run_dir: Path = ctx["run_dir"]
    cfg: dict = ctx["config"]
    layout = json.loads((run_dir / "scene_layout.json").read_text(encoding="utf-8"))
    gen2d = json.loads((run_dir / "gen2d_manifest.json").read_text(encoding="utf-8"))

    # 资产库模式：不生成，匹配已有库（外部预生成的 40 个模块）。
    if cfg.get("gen3d", {}).get("provider") == "library":
        return _run_library(ctx, layout)

    # 云端 provider 模块不随交付包分发，仅云端路径懒加载
    from core.providers3d import make_provider
    provider, fallback_reason = make_provider(cfg.get("gen3d", {}))
    if fallback_reason:
        print(f"  [3d] provider={provider.name} —— {fallback_reason}")
    else:
        print(f"  [3d] provider={provider.name}")

    raw_dir = run_dir / "assets_raw"
    clean_dir = run_dir / "assets_clean"
    raw_dir.mkdir(exist_ok=True)
    clean_dir.mkdir(exist_ok=True)

    by_id = {o["id"]: o for o in layout["objects"]}
    registry: dict = {"provider": provider.name, "fallback_reason": fallback_reason,
                      "assets": {}, "failed": []}

    for aid, entry in gen2d.get("hero", {}).items():
        obj = by_id.get(aid)
        if obj is None:
            continue
        front = Path(entry["front"])
        if not front.exists():
            registry["failed"].append({"id": aid, "why": "缺正视图"})
            continue
        views = {k: Path(v) for k, v in (entry.get("views") or {}).items()}
        req = MeshRequest(asset_id=aid, front_image=front, views=views,
                          size_cm=obj["size_cm"], category=obj["category"],
                          material_hint=" ".join(obj.get("material_tags") or []))
        try:
            # 文生 3D 需要一句描述：感知层的 description 比类别名信息量大得多
            if provider.name == "cloud_text2gen":
                res = provider.generate(req, raw_dir,
                                        description=obj.get("description") or obj["label"])
            else:
                res = provider.generate(req, raw_dir)
        except Exception as e:                       # 云端失败 → 降级 proxy，不断链
            print(f"  [warn] {aid} 生成失败，降级 proxy：{str(e)[:160]}")
            from core.mesh_proxy import ProxyMeshProvider
            res = ProxyMeshProvider().generate(req, raw_dir)
            res.degraded = f"provider_failed: {str(e)[:120]}"

        # 清洗：拷进 assets_clean 并登记规范化信息
        mesh_out = clean_dir / res.mesh_path.name
        shutil.copy2(res.mesh_path, mesh_out)
        tex_out: dict[str, str] = {}
        for kind, p in res.texture_paths.items():
            dst = clean_dir / Path(p).name
            shutil.copy2(p, dst)
            tex_out[kind] = str(dst)
        # OBJ 的 mtl 与贴图必须同目录，否则 UE 导入丢材质
        mtl = res.mesh_path.with_suffix(".mtl")
        if mtl.exists():
            shutil.copy2(mtl, clean_dir / mtl.name)

        registry["assets"][aid] = {
            "id": aid, "category": obj["category"],
            "mesh": str(mesh_out), "textures": tex_out,
            "size_cm": obj["size_cm"],           # 尺寸的最终裁决权在 layout，不在生成结果
            "pivot": "bottom_center", "forward_axis": "+X", "unit": "m",
            "polygon_type": "quad" if provider.name == "proxy" else "unknown",
            "uv_source": "procedural" if provider.name == "proxy" else "provider",
            "pbr_maps": sorted(tex_out.keys()),
            "provider": res.provider, "degraded": res.degraded,
            "is_screen": bool(obj.get("is_emissive")) and
                         any(k in obj["label"].lower() for k in ("screen", "monitor", "crt")),
            "meta": res.meta or {},
        }
        print(f"  [3d] {aid} -> {mesh_out.name}")

    (run_dir / "asset_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"assets": len(registry["assets"]), "failed": len(registry["failed"]),
            "provider": provider.name}


# ============================================================ 资产库模式

def _run_library(ctx: dict, layout: dict) -> dict:
    """匹配式 s04：感知对象 → 库模块 → asset_registry.json（契约不变，s05 零改动）。

    与生成式路径的关键差异：hero 遍历的是 **layout 全量 hero 对象**而不是 gen2d
    的产出（库模式下 hero 视图生成已关闭），所以不再受 max_hero_assets 钳制——
    这直接消灭了旧实现「35 个对象静默丢 21 个」的问题。
    """
    import cv2
    import numpy as np

    from core import libmatch
    from core.mesh_proxy import ProxyMeshProvider

    run_dir: Path = ctx["run_dir"]
    root: Path = ctx["root"]
    cfg: dict = ctx["config"]
    progress = ctx.get("progress")

    lib_dir = Path(cfg["gen3d"].get("library_dir") or
                   r"C:\AI Pipeline Test\AssetLib_processed")
    reg_path = lib_dir / "library_registry.json"
    if not reg_path.is_file():
        raise RuntimeError(f"找不到资产库注册表：{reg_path}（先跑 tools/register_library.py）")
    lib = json.loads(reg_path.read_text(encoding="utf-8"))
    taxonomy = libmatch.load_taxonomy(root)

    # UE 预导入清单（可选）：存在则装配直接 load_asset，不再逐 run 导 OBJ
    ue_manifest: dict = {}
    ue_manifest_path = lib_dir / "ue_import_manifest.json"
    if ue_manifest_path.is_file():
        ue_manifest = json.loads(ue_manifest_path.read_text(encoding="utf-8"))

    overrides: dict = {}
    ov_path = run_dir / "match_overrides.json"
    if ov_path.is_file():                                # 面板改指：存在即覆盖
        overrides = json.loads(ov_path.read_text(encoding="utf-8"))

    heroes = [o for o in layout["objects"] if o["asset_strategy"] == "hero"]
    ref = cv2.imread(str(run_dir / "ref.jpg"))

    raw_dir = run_dir / "assets_raw"
    clean_dir = run_dir / "assets_clean"
    raw_dir.mkdir(exist_ok=True)
    clean_dir.mkdir(exist_ok=True)

    llm_match = _make_llm_matcher(cfg, root, run_dir, lib, ref)
    matches = libmatch.match_objects(heroes, lib, taxonomy,
                                     overrides=overrides, llm_match=llm_match)
    by_id = {o["id"]: o for o in heroes}

    registry: dict = {"provider": "library", "fallback_reason": None,
                      "assets": {}, "failed": []}
    proxy = ProxyMeshProvider()
    n_lib = n_proxy = 0

    for i, rec in enumerate(matches):
        oid = rec["object_id"]
        obj = by_id[oid]
        if progress:
            progress.tick(min(0.99, (i + 1) / max(1, len(matches))), f"匹配 {oid}")

        if rec["module"]:
            mod = lib["modules"][rec["module"]]
            mod_dir = Path(mod["dir"])
            entry = {
                "id": oid, "category": obj["category"],
                "mesh": str(mod_dir / mod["mesh"]),
                "textures": {k: str(mod_dir / v) for k, v in mod["textures"].items()},
                "size_cm": obj["size_cm"],
                "pivot": "bottom_center", "forward_axis": "+X", "unit": "cm",
                "polygon_type": "tri", "uv_source": "provider",
                "pbr_maps": sorted(mod["textures"].keys()),
                "provider": "library", "degraded": None,
                "is_screen": bool(mod.get("is_screen")) or (
                    bool(obj.get("is_emissive")) and
                    any(k in obj["label"].lower() for k in ("screen", "monitor", "crt"))),
                "meta": {"kind": "library", "module": rec["module"],
                         "method": rec["method"], "confidence": rec["confidence"],
                         "scale": rec["scale"], "flags": rec["flags"],
                         "mesh_bbox_cm": mod["mesh_bbox_cm"],
                         **(ue_manifest.get(rec["module"]) or {})},
            }
            registry["assets"][oid] = entry
            n_lib += 1
            print(f"  [lib] {oid} → {rec['module']}（{rec['method']}"
                  + (f"，{'/'.join(rec['flags'])}" if rec["flags"] else "") + "）")
        else:
            # 未命中：proxy 盒兜底，贴图用参考图里该物体的 crop——比纯灰盒好看
            front = _crop_for_proxy(ref, obj, raw_dir)
            req = MeshRequest(asset_id=oid, front_image=front, views={},
                              size_cm=obj["size_cm"], category=obj["category"],
                              material_hint=" ".join(obj.get("material_tags") or []))
            res = proxy.generate(req, raw_dir)
            mesh_out = clean_dir / res.mesh_path.name
            shutil.copy2(res.mesh_path, mesh_out)
            tex_out = {}
            for kind, p in res.texture_paths.items():
                dst = clean_dir / Path(p).name
                shutil.copy2(p, dst)
                tex_out[kind] = str(dst)
            mtl = res.mesh_path.with_suffix(".mtl")
            if mtl.exists():
                shutil.copy2(mtl, clean_dir / mtl.name)
            registry["assets"][oid] = {
                "id": oid, "category": obj["category"],
                "mesh": str(mesh_out), "textures": tex_out,
                "size_cm": obj["size_cm"],
                "pivot": "bottom_center", "forward_axis": "+X", "unit": "m",
                "polygon_type": "quad", "uv_source": "procedural",
                "pbr_maps": sorted(tex_out.keys()),
                "provider": "proxy", "degraded": "library_no_match",
                "is_screen": False,
                "meta": {"kind": "proxy_box", "flags": rec["flags"]},
            }
            n_proxy += 1
            print(f"  [lib] {oid} 未命中 → proxy（{'/'.join(rec['flags'])}）")

    # ---- decal 对象的库贴图兜底 ----
    # 库里的平面印刷品（poster/newspaper/sign）正是参考图拆分出来的那批海报。
    # gen2d 没产出贴图的 decal 对象，从库里按 类别 + 文字相似 匹配一张 T_BC——
    # 这直接填掉 compile_layout 里「decal 无贴图被丢弃」的缺口。
    taxonomy_flat = set(taxonomy.get("flat_print_categories") or [])
    flat_mods = [(sid, m) for sid, m in lib["modules"].items()
                 if m.get("is_flat_print") and m.get("enabled", True)]
    decal_tex: dict[str, str] = {}
    flat_cursor: dict[str, int] = {}
    if flat_mods:
        for obj in sorted((o for o in layout["objects"]
                           if o["asset_strategy"] == "decal"), key=lambda o: o["id"]):
            cat = libmatch.normalize_category(obj.get("category") or obj["label"],
                                              taxonomy)
            cands = [(sid, m) for sid, m in flat_mods if m["category"] == cat] \
                or ([(sid, m) for sid, m in flat_mods] if cat in taxonomy_flat else [])
            if not cands:
                continue
            text = (obj.get("text_content") or "").lower()
            if text:                                   # 同一张海报优先回到原位
                def _overlap(m):
                    pt = (m.get("poster_text") or "").lower()
                    return len(set(text.split()) & set(pt.split())) if pt else 0
                best = max(cands, key=lambda sm: _overlap(sm[1]))
                if _overlap(best[1]) > 0:
                    cands = [best]
            idx = flat_cursor.get(cat, 0)
            sid, mod = cands[idx % len(cands)]
            flat_cursor[cat] = idx + 1
            # 贴花源用 preview.png（白底完整海报渲染），**不能用 T_BC**——
            # 库里的平面印刷品是皱纸 3D 模型，T_BC 是碎片化的 UV 图集，
            # 贴上墙是一块马赛克（实测翻过车）。
            decal_tex[obj["id"]] = str(Path(mod["dir"]) / mod.get("preview", "preview.png"))
    registry["decal_textures"] = decal_tex
    # 全量平面印刷品贴图池：compile_layout 给 count>1 的海报组做贴图轮换用——
    # 参考图的墙是"贴满各式海报"，同一张贴 15 遍会假
    registry["decal_texture_pool"] = sorted(
        str(Path(m["dir"]) / m.get("preview", "preview.png")) for _, m in flat_mods)

    report = {"run": run_dir.name, "registry_version": lib.get("version"),
              "matches": matches, "decal_textures": len(decal_tex),
              "summary": {"library": n_lib, "proxy": n_proxy,
                          "flagged": sum(1 for m in matches if m["flags"])}}
    (run_dir / "match_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "asset_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"assets": len(registry["assets"]), "matched": n_lib, "proxy": n_proxy,
            "decal_tex": len(decal_tex),
            "flagged": report["summary"]["flagged"], "provider": "library"}


def _crop_for_proxy(ref, obj: dict, raw_dir: Path):
    """proxy 盒的贴图源：参考图里该物体的 bbox crop；裁不出就给中性灰。"""
    import cv2
    import numpy as np

    out = raw_dir / f"{obj['id']}_crop.png"
    try:
        h, w = ref.shape[:2]
        x0, y0, x1, y1 = obj["bbox_2d"]
        crop = ref[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
        if crop.size and min(crop.shape[:2]) >= 16:
            cv2.imwrite(str(out), crop)
            return out
    except (KeyError, TypeError, ValueError):
        pass
    cv2.imwrite(str(out), np.full((64, 64, 3), 128, np.uint8))
    return out


def _make_llm_matcher(cfg: dict, root: Path, run_dir: Path, lib: dict, ref):
    """L3 兜底：参考图 crop vs 候选预览拼图，视觉 LLM 选最像的。vlm_gateway 缓存兜费用。"""
    import cv2
    import numpy as np

    from core.vlm_gateway import GatewayClient

    mods = [(sid, m) for sid, m in lib["modules"].items()
            if m.get("enabled", True) and not m.get("is_flat_print")]
    if not mods or ref is None:
        return None

    cell = 128
    cols = 8
    rows = (len(mods) + cols - 1) // cols
    sheet = np.full((rows * cell, cols * cell, 3), 255, np.uint8)
    for i, (sid, m) in enumerate(mods):
        img = cv2.imread(str(Path(m["dir"]) / m.get("preview", "preview.png")))
        if img is None:
            continue
        img = cv2.resize(img, (cell, cell), interpolation=cv2.INTER_AREA)
        cv2.putText(img, str(i), (4, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 0, 255), 2)
        r, c = divmod(i, cols)
        sheet[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = img
    sheet_path = run_dir / "assets_raw" / "_lib_contact_sheet.png"
    sheet_path.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(sheet_path), sheet)

    vlm_gateway = GatewayClient.from_config(cfg, root, run_dir=run_dir, stage="s04_gen3d")
    model = cfg["models"]["vision"]

    def _match(obj: dict):
        crop_path = _crop_for_proxy(ref, obj, run_dir / "assets_raw")
        prompt = (
            "Image 1 is an object cropped from a scene photo. Image 2 is a numbered "
            "contact sheet of 3D assets (red index at each cell's top-left). "
            "Which numbered asset is the SAME KIND of object as image 1? "
            'Return ONLY JSON: {"index": <int or -1 if none>, "confidence": <0..1>, '
            '"reason": "<short>"}')
        ans = vlm_gateway.chat_json(prompt, [crop_path, sheet_path], model=model)
        idx = int(ans.get("index", -1))
        if 0 <= idx < len(mods):
            return mods[idx][0], float(ans.get("confidence", 0.0)), str(ans.get("reason", ""))[:120]
        return None, 0.0, "no_candidate"

    return _match
