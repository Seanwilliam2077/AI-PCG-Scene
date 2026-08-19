"""3D 资产的公共数据类与 proxy 代理网格后端。

从 provider 层拆出的原因：proxy 路径（含资产库模式下 NO_LIB 类别的代理盒）
是交付包的运行必需，而云端 provider 模块不随包分发——
两者拆开后，交付包只带本模块，云端模块按需懒加载。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np


# ---------------------------------------------------------------- 接口
@dataclass
class MeshRequest:
    asset_id: str
    front_image: Path                 # 清理后的正视图（RGBA PNG）
    views: dict[str, Path]            # {"left"/"right"/"back"/"top": path}
    size_cm: list[float]              # layout 给的真实尺寸，尺寸的最终裁决权在这
    category: str
    material_hint: str = "default"


@dataclass
class MeshResult:
    asset_id: str
    mesh_path: Path                   # .obj 或 .glb
    texture_paths: dict[str, Path]    # {"BC": ..., "N": ..., "ORM": ...}
    provider: str
    degraded: str | None = None
    meta: dict[str, Any] | None = None


class Mesh3DProvider(Protocol):
    name: str
    def available(self) -> bool: ...
    def generate(self, req: MeshRequest, out_dir: Path) -> MeshResult: ...


# ---------------------------------------------------------------- proxy
class ProxyMeshProvider:
    """确定性代理网格：按真实尺寸出盒体，正面贴资产图。

    不是占位符敷衍——尺寸、pivot、UV 都按最终规范生成，UE 里摆出来就是一个
    尺度正确、朝向正确、看得出是什么东西的场景。凭证到位后换 provider 即可，
    下游（Blender 清洗 / compile_layout / UE 装配）零改动。
    """

    name = "proxy"

    def available(self) -> bool:
        return True

    def generate(self, req: MeshRequest, out_dir: Path) -> MeshResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        obj_path = out_dir / f"{req.asset_id}.obj"
        mtl_path = out_dir / f"{req.asset_id}.mtl"
        tex_path = out_dir / f"{req.asset_id}_BC.png"

        # 贴图：把清理图铺到正方形画布（正面用），其余面用其主色调
        tex = self._make_texture(req.front_image)
        _imwrite(tex_path, tex)

        sx, sy, sz = (max(float(v), 1.0) / 100.0 for v in req.size_cm)  # cm → m
        self._write_box_obj(obj_path, mtl_path.name, sx, sy, sz)
        self._write_mtl(mtl_path, tex_path.name)

        return MeshResult(asset_id=req.asset_id, mesh_path=obj_path,
                          texture_paths={"BC": tex_path}, provider=self.name,
                          degraded=None,
                          meta={"kind": "proxy_box", "size_cm": req.size_cm})

    # ---- 贴图 ----
    @staticmethod
    def _make_texture(front_image: Path, size: int = 512) -> np.ndarray:
        import cv2
        raw = cv2.imread(str(front_image), cv2.IMREAD_UNCHANGED)
        if raw is None:
            return np.full((size, size, 3), 128, np.uint8)
        if raw.shape[2] == 4:                       # 透明区合成到主色底上，避免黑边
            bgr, alpha = raw[..., :3], raw[..., 3:4].astype(np.float32) / 255.0
            fg = bgr.reshape(-1, 3)[(alpha.reshape(-1) > 0.5)]
            base = fg.mean(axis=0) if len(fg) else np.array([128, 128, 128])
            canvas = np.full_like(bgr, base.astype(np.uint8))
            raw = (bgr * alpha + canvas * (1 - alpha)).astype(np.uint8)
        return cv2.resize(raw[..., :3], (size, size), interpolation=cv2.INTER_AREA)

    # ---- 几何 ----
    @staticmethod
    def _write_box_obj(path: Path, mtl_name: str, sx: float, sy: float, sz: float) -> None:
        """pivot 在底部中心（UE 摆放约定：原点=落地点），+X 为正面，米制。"""
        hx, hy = sx / 2.0, sy / 2.0
        v = [(-hx, -hy, 0.0), (hx, -hy, 0.0), (hx, hy, 0.0), (-hx, hy, 0.0),
             (-hx, -hy, sz), (hx, -hy, sz), (hx, hy, sz), (-hx, hy, sz)]
        # 每面独立 UV：正面(+X)整张贴图，其余面用贴图中心一小块（近似纯色）
        full = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        patch = [(0.45, 0.45), (0.55, 0.45), (0.55, 0.55), (0.45, 0.55)]
        faces = [
            ((2, 3, 7, 6), full),    # +X 正面
            ((4, 1, 5, 8), patch),   # -X
            ((1, 2, 6, 5), patch),   # -Y
            ((3, 4, 8, 7), patch),   # +Y
            ((5, 6, 7, 8), patch),   # +Z 顶
            ((4, 3, 2, 1), patch),   # -Z 底
        ]
        lines = [f"mtllib {mtl_name}", "o proxy", "usemtl proxy_mat"]
        for x, y, z in v:
            lines.append(f"v {x:.5f} {y:.5f} {z:.5f}")
        uv_index = {}
        uvs: list[tuple[float, float]] = []
        for _, uvset in faces:
            for uv in uvset:
                if uv not in uv_index:
                    uv_index[uv] = len(uvs) + 1
                    uvs.append(uv)
        for u, w in uvs:
            lines.append(f"vt {u:.5f} {w:.5f}")
        for idxs, uvset in faces:
            parts = [f"{vi}/{uv_index[uv]}" for vi, uv in zip(idxs, uvset)]
            lines.append("f " + " ".join(parts))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _write_mtl(path: Path, tex_name: str) -> None:
        path.write_text(
            "newmtl proxy_mat\nKd 1.000 1.000 1.000\nKs 0.100 0.100 0.100\n"
            f"Ns 20.0\nmap_Kd {tex_name}\n", encoding="utf-8")


def _imwrite(path: Path, img) -> None:
    import cv2
    ok, buf = cv2.imencode(path.suffix, img)
    if not ok:
        raise RuntimeError(f"编码失败：{path}")
    path.write_bytes(buf.tobytes())
