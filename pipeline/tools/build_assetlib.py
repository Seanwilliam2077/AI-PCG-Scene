#!/usr/bin/env python3
"""资产库预处理（作者侧一次性，系统 Python 运行，评审机不需要）。

把 Asset\\ 下 40 个云端 3D 生成的原始模块（1.5M 面 OBJ + 4K PBR 贴图，单模块
~300MB）加工成可进 UE、可进 500MB 交付包的形态：

  网格   pymeshlab 带纹理保持的 QEM 减面到 ~8 万面 → 纯 Python 重写 OBJ：
         Y-up 米单位 → Z-up 厘米（旋转 X+90、×100、底部中心归位）。
         实测源资产 y_min=0、x/z 居中（glTF 系惯例），归位只是兜底。
  贴图   BC 4096→2048 存 JPG（uasset 无损保留源字节，JPG 源比 PNG 小 5 倍，
         这是 500MB 预算的关键——导入后设 max_texture_size 只影响 cook，
         救不了未 cook 的 uasset 体积）；N 1024 PNG；metallic+roughness 合并
         ORM 1024 PNG（R=AO 恒白 / G=Roughness / B=Metallic，与 build_scene
         的母材质通道约定一致）。
  统计   stats.json：变换后包围盒（cm）、面数、文件清单——注册表打标的几何底数。

用法：
    python tools/build_assetlib.py                       # 全量（跳过已完成的）
    python tools/build_assetlib.py --only M07,A02        # 指定模块
    python tools/build_assetlib.py --faces 60000         # 改减面目标
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path

ASSET_SRC = Path(r"C:\AI Pipeline Test\Asset")
OUT_ROOT = Path(r"C:\AI Pipeline Test\AssetLib_processed")

BC_SIZE, N_SIZE, ORM_SIZE, PREVIEW_SIZE = 2048, 1024, 1024, 512


def discover_modules() -> dict[str, Path]:
    """扫描源目录 → {安全ID: 模块目录}。

    「模块 N_Tex」→ M{N:02d}；裸「N_Tex」→ A{N:02d}（两批命名并存且内容不同，
    分别编号）。目录必须含 .obj 才算有效模块（「模块7」这类只有 fbx/glb 的
    残缺目录跳过）。安全 ID 无中文无空格——它就是 UE 资产路径的一段。
    """
    out: dict[str, Path] = {}
    for d in sorted(ASSET_SRC.iterdir()):
        if not d.is_dir():
            continue
        m = re.fullmatch(r"模块 (\d+)_Tex", d.name)
        a = re.fullmatch(r"(\d+)_Tex", d.name)
        if m:
            sid = f"M{int(m.group(1)):02d}"
        elif a:
            sid = f"A{int(a.group(1)):02d}"
        else:
            continue
        if any(d.glob("*.obj")):
            out[sid] = d
    return out


def _load_obj(path: Path):
    """流式解析 OBJ：返回 (v[N,3], vt 行原文, f 行原文, 其余保序无关行数)。

    面行原样透传（v/vt/vn 索引不变），所以只要 v 与 vn 的数值变了、行数没变，
    文件仍然自洽。1.5M 面的原始文件不在这里解析——本函数只喂减面后的小文件。
    """
    verts: list[list[float]] = []
    normals: list[list[float]] = []
    vt_lines: list[str] = []
    f_lines: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("v "):
            p = line.split()
            verts.append([float(p[1]), float(p[2]), float(p[3])])
        elif line.startswith("vn "):
            p = line.split()
            normals.append([float(p[1]), float(p[2]), float(p[3])])
        elif line.startswith("vt "):
            vt_lines.append(line)
        elif line.startswith("f "):
            f_lines.append(line)
    return verts, normals, vt_lines, f_lines


def _transform_and_write(src: Path, dst: Path) -> dict:
    """Y-up 米 → Z-up 厘米，底部中心归位，重写 OBJ。返回几何统计。"""
    import numpy as np

    verts, normals, vt_lines, f_lines = _load_obj(src)
    v = np.asarray(verts, dtype=np.float64)
    # 旋转 X+90（y→z, z→-y），再 ×100 米转厘米
    v = np.stack([v[:, 0], -v[:, 2], v[:, 1]], axis=1) * 100.0
    # 底部中心归位：x/y 取包围盒中心，z 取最低点
    lo, hi = v.min(axis=0), v.max(axis=0)
    v -= np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    lo, hi = v.min(axis=0), v.max(axis=0)

    lines: list[str] = [f"v {x:.4f} {y:.4f} {z:.4f}" for x, y, z in v]
    if normals:
        n = np.asarray(normals, dtype=np.float64)
        n = np.stack([n[:, 0], -n[:, 2], n[:, 1]], axis=1)
        lines += [f"vn {x:.6f} {y:.6f} {z:.6f}" for x, y, z in n]
    lines += vt_lines
    lines += f_lines
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"bbox_cm": [round(float(hi[i] - lo[i]), 2) for i in range(3)],
            "tri_count": len(f_lines)}


def process_module(sid: str, src_dir: Path, target_faces: int) -> dict:
    import cv2
    import numpy as np
    import pymeshlab

    t0 = time.time()
    out_dir = OUT_ROOT / sid
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 网格：减面（保 UV）→ 变换重写 ----
    # pymeshlab 对含中文的源路径（"模块 7_Tex"）在复用的 worker 进程里会加载失败
    # （首个任务能过、后续任务必挂，疑似 locale 相关）。统一先把 OBJ 拷到 ASCII
    # 路径再载入；QEM-with-texture 只需要 wedge UV，不需要 mtl/贴图同行。
    import shutil as _sh
    obj_src = next(iter(src_dir.glob("*.obj")))
    staged_obj = out_dir / "_src_staged.obj"
    _sh.copyfile(obj_src, staged_obj)
    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(str(staged_obj))
    src_faces = ms.current_mesh().face_number()
    if src_faces > target_faces:
        # 带纹理坐标保持的 QEM；不同 pymeshlab 版本滤镜名有别，逐个尝试
        for name in ("meshing_decimation_quadric_edge_collapse_with_texture",
                     "simplification_quadric_edge_collapse_decimation_with_texture"):
            try:
                ms.apply_filter(name, targetfacenum=target_faces)
                break
            except pymeshlab.PyMeshLabException:
                continue
    tmp_obj = out_dir / "_decimated_raw.obj"
    ms.save_current_mesh(str(tmp_obj))
    geo = _transform_and_write(tmp_obj, out_dir / "mesh.obj")
    # pymeshlab 导出会顺手拷一份 mtl + 源贴图（23MB），全部清掉——
    # 材质由 build_scene 的母材质体系接管，OBJ 只要几何与 UV。
    keep = {"mesh.obj", "T_BC.jpg", "T_N.png", "T_ORM.png", "preview.png", "stats.json"}
    for f in out_dir.iterdir():
        if f.is_file() and f.name not in keep:
            f.unlink()

    # ---- 贴图：BC 2K JPG / N 1K PNG / ORM 1K PNG（R=AO白 G=rough B=metal）----
    # cv2.imread/imwrite 在 Windows 上碰到非 ASCII 路径（"模块 7_Tex"）会静默失败，
    # 一律走 fromfile/imdecode + imencode/tofile。
    def _read(name: str, flags=cv2.IMREAD_COLOR):
        p = src_dir / f"{name}.png"
        if not p.is_file():
            raise RuntimeError(f"缺贴图 {p.name}")
        img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), flags)
        if img is None:
            raise RuntimeError(f"贴图解码失败 {p.name}")
        return img

    def _write(p: Path, img, params=None) -> None:
        ok, buf = cv2.imencode(p.suffix, img, params or [])
        if not ok:
            raise RuntimeError(f"贴图编码失败 {p.name}")
        buf.tofile(str(p))

    bc = cv2.resize(_read("pbr_image_url"), (BC_SIZE, BC_SIZE), interpolation=cv2.INTER_AREA)
    _write(out_dir / "T_BC.jpg", bc, [cv2.IMWRITE_JPEG_QUALITY, 92])

    nrm = cv2.resize(_read("pbr_normal_image_url"), (N_SIZE, N_SIZE), interpolation=cv2.INTER_AREA)
    _write(out_dir / "T_N.png", nrm)

    rough = cv2.resize(_read("pbr_roughness_image_url", cv2.IMREAD_GRAYSCALE),
                       (ORM_SIZE, ORM_SIZE), interpolation=cv2.INTER_AREA)
    metal = cv2.resize(_read("pbr_metallic_image_url", cv2.IMREAD_GRAYSCALE),
                       (ORM_SIZE, ORM_SIZE), interpolation=cv2.INTER_AREA)
    ao = np.full_like(rough, 255)
    _write(out_dir / "T_ORM.png", cv2.merge([metal, rough, ao]))  # BGR 存盘 → R=AO

    # ---- 预览图：注册表打标与面板缩略图共用 ----
    prev_src = src_dir / "image_url.png"
    if prev_src.is_file():
        prev = cv2.imdecode(np.fromfile(str(prev_src), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if prev is not None:
            prev = cv2.resize(prev, (PREVIEW_SIZE, PREVIEW_SIZE), interpolation=cv2.INTER_AREA)
            _write(out_dir / "preview.png", prev)

    stats = {
        "id": sid, "source_dir": str(src_dir), "source_faces": src_faces,
        **geo,
        "files": {"mesh": "mesh.obj", "BC": "T_BC.jpg", "N": "T_N.png",
                  "ORM": "T_ORM.png", "preview": "preview.png"},
        "seconds": round(time.time() - t0, 1),
    }
    (out_dir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    return stats


def _worker(args: tuple[str, str, int]) -> tuple[str, str]:
    sid, src, faces = args
    try:
        stats = process_module(sid, Path(src), faces)
        return sid, (f"ok faces={stats['tri_count']} bbox={stats['bbox_cm']} "
                     f"{stats['seconds']}s")
    except Exception:
        return sid, "FAIL\n" + traceback.format_exc()[-800:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔的模块 ID（如 M07,A02）")
    ap.add_argument("--faces", type=int, default=80000)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true", help="已完成的也重做")
    args = ap.parse_args()

    modules = discover_modules()
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        modules = {k: v for k, v in modules.items() if k in want}

    todo: list[tuple[str, str, int]] = []
    for sid, src in modules.items():
        if not args.force and (OUT_ROOT / sid / "stats.json").is_file():
            print(f"[skip] {sid}（已完成）")
            continue
        todo.append((sid, str(src), args.faces))

    print(f"待处理 {len(todo)} / 共 {len(modules)} 个模块，workers={args.workers}")
    failed = 0
    # maxtasksperchild=1：每个模块一个全新 worker 进程。pymeshlab 在复用进程里
    # 第二次加载会莫名失败（见 process_module 注释），干脆不给它复用的机会。
    import multiprocessing as mp
    with mp.Pool(processes=args.workers, maxtasksperchild=1) as pool:
        for sid, msg in pool.imap_unordered(_worker, todo):
            print(f"[{sid}] {msg}", flush=True)
            if msg.startswith("FAIL"):
                failed += 1
    print(f"完成：{len(todo) - failed} 成功 / {failed} 失败 → {OUT_ROOT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
