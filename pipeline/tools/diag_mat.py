# -*- coding: utf-8 -*-
"""诊断：每个 GEN_ actor 实际挂的材质是什么，母材质编译是否成功。"""
import json, os, unreal

_side = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_args.json")
cfg = json.load(open(_side, encoding="utf-8"))
unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(cfg["level"])

eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
lines = []
for a in eas.get_all_level_actors():
    try:
        tags = [str(t) for t in a.tags]
    except Exception:
        tags = []
    if not any(t.startswith("GEN_shell") for t in tags):
        continue
    smc = a.static_mesh_component
    mats = smc.get_materials()
    names = [m.get_name() if m else "None" for m in mats]
    lines.append(f"{a.get_actor_label():18s} num_mat={smc.get_num_materials()} -> {names}")

# 母材质与实例状态
for path in ("/Game/AutoScene/Materials/M_Master",
             "/Game/AutoScene/Materials/MI_Shell_wall_15"):
    asset = unreal.load_asset(path)
    if asset is None:
        lines.append(f"{path}: 不存在")
        continue
    if isinstance(asset, unreal.MaterialInstanceConstant):
        par = asset.get_editor_property("parent")
        lines.append(f"{path}: parent={par.get_name() if par else None}")
        tp = unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(
            asset, "BaseColorTex")
        sp = unreal.MaterialEditingLibrary.get_material_instance_scalar_parameter_value(
            asset, "Tiling")
        lines.append(f"    BaseColorTex={tp.get_name() if tp else None}  Tiling={sp}")
    else:
        lines.append(f"{path}: 类型={type(asset).__name__}")

open(cfg["out"], "w", encoding="utf-8").write("\n".join(lines))
unreal.log("[Diag] " + " | ".join(lines))
