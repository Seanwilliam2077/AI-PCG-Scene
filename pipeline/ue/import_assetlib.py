# -*- coding: utf-8 -*-
"""资产库一次性预导入（作者侧，UE 编辑器内执行）。

把 AssetLib_processed 里 40 个模块导成 /Game/AutoScene/AssetLib/<SID>/ 下的
StaticMesh + 贴图 + 材质实例，并写 ue_import_manifest.json 供 s04 引用——
装配时 build_scene 走 load_asset 快路径，评审机上零导入、秒级摆场。

运行（路径有空格所以经无空格暂存目录）：
    UnrealEditor-Cmd.exe <uproject> -ExecutePythonScript="<无空格路径>/import_assetlib.py"

平面印刷品（is_flat_print）不导网格：它们走 Decal 贴图路径。
"""

import json
import os
import sys

import unreal

PIPE_UE = r"C:\AI Pipeline Test\pipeline\ue"
LIB_ROOT = r"C:\AI Pipeline Test\AssetLib_processed"
CONTENT_ROOT = "/Game/AutoScene/AssetLib"

if PIPE_UE not in sys.path:
    sys.path.insert(0, PIPE_UE)

# 复用 build_scene 的导入/母材质/材质实例机制（它以模块形式被 import 时
# argparse 走 parse_known_args、args.json 旁路不存在，均安全无副作用）。
import build_scene as bs  # noqa: E402


def main() -> None:
    registry_path = os.path.join(LIB_ROOT, "library_registry.json")
    with open(registry_path, "r", encoding="utf-8") as fh:
        registry = json.load(fh)

    manifest: dict = {}
    done = skipped = failed = 0
    modules = registry.get("modules", {})
    task = unreal.ScopedSlowTask(len(modules), "导入资产库…")
    task.make_dialog(True)

    for sid, mod in sorted(modules.items()):
        task.enter_progress_frame(1, f"AssetLib {sid}")
        if mod.get("is_flat_print"):
            skipped += 1
            continue
        try:
            mod_dir = mod["dir"]
            pkg = f"{CONTENT_ROOT}/{sid}"

            texs = {}
            for key, param, suffix in (("BC", "BaseColorTex", "_BC"),
                                       ("N", "NormalTex", "_N"),
                                       ("ORM", "ORMTex", "_ORM")):
                src = os.path.join(mod_dir, mod["textures"][key])
                # 库资产是稳定命名（ue_import_manifest 按名引用），不加内容哈希
                tex = bs.import_texture(src, f"T_{sid}{suffix}", dest_dir=pkg,
                                        unique=False)
                if tex:
                    texs[param] = tex

            mesh = bs.import_mesh(os.path.join(mod_dir, mod["mesh"]),
                                  f"SM_{sid}", dest_dir=pkg, unique=False)
            if mesh is None:
                raise RuntimeError("网格导入失败")
            try:
                ns = mesh.get_editor_property("nanite_settings")
                ns.enabled = True
                mesh.set_editor_property("nanite_settings", ns)
                bs.mesh_sub.add_simple_collisions(
                    mesh, unreal.ScriptCollisionShapeType.BOX)
                unreal.EditorAssetLibrary.save_loaded_asset(mesh)
            except Exception as e:  # noqa: BLE001
                bs.log(f"{sid} Nanite/碰撞跳过：{e}")

            master = bs.ensure_master_materials()
            mi = bs.make_material_instance(f"MI_{sid}", master, texs, None,
                                           dest_dir=pkg)
            manifest[sid] = {
                "ue_asset": f"{pkg}/SM_{sid}.SM_{sid}",
                "ue_mi": f"{pkg}/MI_{sid}.MI_{sid}" if mi else None,
            }
            done += 1
            bs.log(f"{sid} 导入完成")
        except Exception as e:  # noqa: BLE001
            failed += 1
            unreal.log_error(f"[AssetLib] {sid} 失败：{e}")

    out_path = os.path.join(LIB_ROOT, "ue_import_manifest.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    unreal.EditorAssetLibrary.save_directory(CONTENT_ROOT, recursive=True)
    bs.log(f"资产库导入：成功 {done} / 平面跳过 {skipped} / 失败 {failed} → {out_path}")


if __name__ == "__main__":
    main()
