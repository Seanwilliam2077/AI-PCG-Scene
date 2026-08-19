# -*- coding: utf-8 -*-
"""在 UE5 编辑器内全自动装配场景。读 build_manifest.json，写 scene_manifest.json。

运行（由 s05 经命令行调起，也可在编辑器 Output Log 里手动 py 执行）：
    UnrealEditor-Cmd.exe <uproject> -ExecutePythonScript="build_scene.py --manifest ... --manifest-out ..."

本层零 AI：上游所有不确定性都已收敛进 JSON，这里 100% 确定性、可 diff、可复跑。

两个 UE Python 的著名陷阱，本文件全程规避：
  1. `unreal.Rotator` 的 Python 构造顺序是 (roll, pitch, yaw)，与 C++ FRotator
     (Pitch, Yaw, Roll) 相反 —— 一律用关键字传参；
  2. StaticMeshEditorSubsystem 配套的碰撞枚举是 `ScriptCollisionShapeType`
     （无 -ing），`ScriptingCollisionShapeType` 是已弃用旧库的枚举，传错会 TypeError。
"""

import argparse
import json
import math
import os
import sys

import unreal

# ---------------------------------------------------------------- 参数
# 优先读同目录的 args.json：UE 的 -ExecutePythonScript 按空格切「脚本+参数」，
# 仓库路径一旦含空格（如 "C:\AI Pipeline Test\..."）命令行传参就会被切坏。
# 编排器因此把本脚本暂存到无空格目录并把参数写在旁边，命令行上只留一个路径。
_ap = argparse.ArgumentParser()
_ap.add_argument("--manifest", default="")
_ap.add_argument("--manifest-out", dest="manifest_out", default="")
_ap.add_argument("--level", default="/Game/Maps/GenScene")
_ap.add_argument("--clean", action="store_true", help="装配前清掉上一轮的 GEN_* actor")
_args, _ = _ap.parse_known_args()

# 参数来源优先级：ASB_ARGS_JSON 环境变量（编辑器内嵌路径，embedded_runner.assemble
# 设置，指向 run 目录，无共享位置竞态）> 同目录 args.json（命令行冷启动路径）。
_side = os.environ.get("ASB_ARGS_JSON") or \
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "args.json")
if os.path.isfile(_side):
    try:
        with open(_side, "r", encoding="utf-8") as _f:
            _cfg = json.load(_f)
        _args.manifest = _cfg.get("manifest", _args.manifest)
        _args.manifest_out = _cfg.get("manifest_out", _args.manifest_out)
        _args.level = _cfg.get("level", _args.level)
        _args.clean = bool(_cfg.get("clean", _args.clean))
    except Exception as _e:  # noqa: BLE001
        unreal.log_error(f"[AutoScene] args.json 读取失败：{_e}")

CONTENT_DIR = "/Game/AutoScene"

actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
mesh_sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
lvl_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
atools = unreal.AssetToolsHelpers.get_asset_tools()

_errors = []


def log(msg):
    unreal.log(f"[AutoScene] {msg}")


def _try_set(obj, prop, value):
    """设属性但不因属性名不存在而中断。

    UE 各小版本之间 Python 属性名偶有变动（尤其是 b 前缀的布尔量）。装饰性属性
    对不上时应当降级继续，而不是让整场装配失败——真正的失败要留给"资产没导入"
    这类实质问题。返回是否设置成功。
    """
    try:
        obj.set_editor_property(prop, value)
        return True
    except Exception as e:  # noqa: BLE001
        unreal.log_warning(f"[AutoScene] 跳过属性 {prop}：{e}")
        return False


# ---------------------------------------------------------------- 资产导入
def _file_hash(path):
    """源文件内容哈希（8 位）。"""
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def import_texture(path, dest_name, dest_dir=None, unique=True):
    """按后缀设色彩空间：_BC 走 sRGB；_N/_ORM 是数据贴图，必须关 sRGB。

    unique=True（per-run 资产的默认）给资产名加内容哈希后缀：同名复用的语义
    变成「同内容才复用」。没有它，换参考图重跑时 T_wall_BC 会静默加载上一轮
    的旧贴图（实测：白砖墙渲成蓝墙、海报黑块，查了半天全是僵尸资产）。
    资产库这类稳定命名的库资产传 unique=False。
    """
    if not path or not os.path.isfile(path):
        return None
    logical = dest_name                      # 色彩空间按逻辑名后缀判断
    if unique:
        dest_name = f"{dest_name}_{_file_hash(path)}"
    dest = dest_dir or f"{CONTENT_DIR}/Textures"
    full = f"{dest}/{dest_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        return unreal.load_asset(full)
    task = unreal.AssetImportTask()
    task.filename = path
    task.destination_path = dest
    task.destination_name = dest_name
    task.automated = True
    task.save = True
    task.replace_existing = True
    atools.import_asset_tasks([task])
    paths = list(task.get_editor_property("imported_object_paths"))
    if not paths:
        return None
    tex = unreal.load_asset(paths[0])
    if logical.endswith("_N"):
        tex.set_editor_property("srgb", False)
        tex.set_editor_property("compression_settings",
                                unreal.TextureCompressionSettings.TC_NORMALMAP)
    elif logical.endswith("_ORM") or logical.endswith("_H"):
        tex.set_editor_property("srgb", False)
        tex.set_editor_property("compression_settings",
                                unreal.TextureCompressionSettings.TC_MASKS)
    unreal.EditorAssetLibrary.save_loaded_asset(tex)
    return tex


def import_mesh(path, dest_name, dest_dir=None, unique=True):
    if not path or not os.path.isfile(path):
        return None
    if unique:                               # 语义同 import_texture：同内容才复用
        dest_name = f"{dest_name}_{_file_hash(path)}"
    dest = dest_dir or f"{CONTENT_DIR}/Meshes"
    full = f"{dest}/{dest_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        return unreal.load_asset(full)
    task = unreal.AssetImportTask()
    task.filename = path
    task.destination_path = dest
    task.destination_name = dest_name
    task.automated = True
    task.save = True
    task.replace_existing = True
    atools.import_asset_tasks([task])
    paths = list(task.get_editor_property("imported_object_paths"))
    return unreal.load_asset(paths[0]) if paths else None


def make_material_instance(mi_name, master_path, textures, scalars=None, dest_dir=None):
    """给母材质做实例并绑贴图。缺通道就不绑参数，由母材质的默认值兜底。"""
    pkg = dest_dir or f"{CONTENT_DIR}/Materials"
    full = f"{pkg}/{mi_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        mi = unreal.load_asset(full)
    else:
        mi = atools.create_asset(mi_name, pkg, unreal.MaterialInstanceConstant,
                                 unreal.MaterialInstanceConstantFactoryNew())
    master = unreal.load_asset(master_path)
    if master is None:
        return None
    unreal.MaterialEditingLibrary.set_material_instance_parent(mi, master)
    for pname, tex in (textures or {}).items():
        if tex:
            unreal.MaterialEditingLibrary.set_material_instance_texture_parameter_value(
                mi, pname, tex)
    for pname, val in (scalars or {}).items():
        unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(
            mi, pname, float(val))
    unreal.EditorAssetLibrary.save_loaded_asset(mi)
    return mi


def _make_master(name, emissive=False):
    """程序化创建母材质。

    母材质必须由本脚本建出来，不能退回引擎自带的 BasicShapeMaterial——那个材质
    没有 BaseColorTex/NormalTex/ORMTex 参数，绑贴图会**静默失败**，整场景就会渲成
    一片灰。参数名与 create_material_instance() 里用的必须一一对应。
    """
    pkg = f"{CONTENT_DIR}/Materials"
    full = f"{pkg}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        return full
    mat = atools.create_asset(name, pkg, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        return "/Engine/BasicShapes/BasicShapeMaterial"
    mel = unreal.MaterialEditingLibrary

    def tex_param(param, x, y, default_tex,
                  sampler=unreal.MaterialSamplerType.SAMPLERTYPE_COLOR):
        node = mel.create_material_expression(
            mat, unreal.MaterialExpressionTextureSampleParameter2D, x, y)
        node.set_editor_property("parameter_name", param)
        # 必须给默认贴图：没有默认值的 TextureSampleParameter2D 编译不过，
        # 材质会静默退回引擎的 Default Material（表现就是整场景一片灰）。
        tex = unreal.load_asset(default_tex)
        if tex:
            node.set_editor_property("texture", tex)
        node.set_editor_property("sampler_type", sampler)
        return node

    def scalar(param, value, x, y):
        node = mel.create_material_expression(
            mat, unreal.MaterialExpressionScalarParameter, x, y)
        node.set_editor_property("parameter_name", param)
        node.set_editor_property("default_value", value)
        return node

    WHITE = "/Engine/EngineResources/WhiteSquareTexture"
    if unreal.load_asset(WHITE) is None:
        WHITE = "/Engine/EngineResources/DefaultTexture"
    # 引擎 Cube 每个面的 UV 都是 0~1，与实际尺寸无关。一张 1024 贴图直接铺满
    # 10 米墙 = 一块砖 1 米宽（看起来就是一片棋盘）。所以必须有 Tiling：
    # 房间壳按"每米重复一次"设置，代理盒保持 1。
    tiling = mel.create_material_expression(
        mat, unreal.MaterialExpressionScalarParameter, -1500, -60)
    tiling.set_editor_property("parameter_name", "Tiling")
    tiling.set_editor_property("default_value", 1.0)
    texcoord = mel.create_material_expression(
        mat, unreal.MaterialExpressionTextureCoordinate, -1500, -200)
    uv = mel.create_material_expression(
        mat, unreal.MaterialExpressionMultiply, -1200, -160)
    mel.connect_material_expressions(texcoord, "", uv, "A")
    mel.connect_material_expressions(tiling, "", uv, "B")

    def _conn_prop(node, out, prop, label):
        ok = mel.connect_material_property(node, out, prop)
        if not ok:
            unreal.log_error(f"[AutoScene] 母材质连线失败: {label} (output={out!r})")
        return ok

    def _conn_expr(a, out_a, b, in_b, label):
        ok = mel.connect_material_expressions(a, out_a, b, in_b)
        if not ok:
            unreal.log_error(f"[AutoScene] 母材质连线失败: {label}")
        return ok

    base = tex_param("BaseColorTex", -900, -200, WHITE)
    _conn_expr(uv, "", base, "UVs", "UV->BaseColor")
    # 输出名用 "" 取默认输出（贴图采样的默认输出即 RGB）。传 "RGB" 在部分版本上
    # 匹配不到输出名会静默返回 False，材质照样编译，但 BaseColor 是空的——
    # 表现就是整场景纯灰、看不出任何贴图。
    _conn_prop(base, "", unreal.MaterialProperty.MP_BASE_COLOR, "BaseColor")

    nrm = tex_param("NormalTex", -900, 120, "/Engine/EngineMaterials/DefaultNormal",
                    unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
    _conn_expr(uv, "", nrm, "UVs", "UV->Normal")
    _conn_prop(nrm, "", unreal.MaterialProperty.MP_NORMAL, "Normal")

    # ORM 打包约定：R=AO / G=Roughness / B=Metallic
    # 采样器类型必须与**默认贴图**匹配，否则母材质编译不过、整场退回默认材质。
    # WhiteSquareTexture 是 sRGB 彩色贴图，所以这里用 COLOR 而不是 MASKS；
    # 实例绑上真正的 ORM（sRGB=false/TC_MASKS）后只会有一条运行时警告，不影响编译。
    orm = tex_param("ORMTex", -900, 440, WHITE,
                    unreal.MaterialSamplerType.SAMPLERTYPE_COLOR)
    _conn_expr(uv, "", orm, "UVs", "UV->ORM")
    _conn_prop(orm, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION, "AO")
    _conn_prop(orm, "G", unreal.MaterialProperty.MP_ROUGHNESS, "Roughness")
    _conn_prop(orm, "B", unreal.MaterialProperty.MP_METALLIC, "Metallic")

    if emissive:
        # 自发光 = BaseColor × 强度，Lumen 下屏幕本身就成了光源
        inten = scalar("EmissiveIntensity", 6.0, -900, 760)
        mul = mel.create_material_expression(
            mat, unreal.MaterialExpressionMultiply, -500, 700)
        _conn_expr(base, "", mul, "A", "Emissive.A")
        _conn_expr(inten, "", mul, "B", "Emissive.B")
        _conn_prop(mul, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR, "Emissive")
    else:
        scalar("EmissiveIntensity", 0.0, -900, 760)   # 实例里可覆盖，保持参数一致

    mel.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    log(f"母材质已创建 {full}")
    return full


def ensure_master_materials():
    return _make_master("M_Master", emissive=False)


# ---------------------------------------------------------------- 房间壳
def build_room_shell(room):
    """引擎自带 Cube（100cm）缩放拼地面/四墙/天花板，不依赖任何外部建模。"""
    L, W, H = room["size_cm"]
    t = room.get("wall_thickness_cm", 20.0)
    cx, cy, _ = room.get("center_cm", [0, 0, 0])
    cube = unreal.load_asset("/Engine/BasicShapes/Cube")
    master = ensure_master_materials()
    mats = room.get("materials", {})

    def mi_for(slot, tiling):
        paths = mats.get(slot)
        if not paths:
            return None
        texs = {}
        for key, param in (("BC", "BaseColorTex"), ("N", "NormalTex"), ("ORM", "ORMTex")):
            p = paths.get(key)
            if p:
                texs[param] = import_texture(p, f"T_{slot}_{key}")
        # 每面单独一个 MI：Tiling 取该面的最大边长（米），即"一张贴图铺一平米"
        return make_material_instance(f"MI_Shell_{slot}_{int(tiling)}", master, texs,
                                      {"Tiling": max(1.0, round(tiling, 1))})

    def panel(name, center, size, slot, tag_id):
        a = actor_sub.spawn_actor_from_object(cube, unreal.Vector(*center))
        a.set_actor_scale3d(unreal.Vector(size[0] / 100.0, size[1] / 100.0, size[2] / 100.0))
        a.set_actor_label(name)
        # tag 逐面唯一（shell_wall_n/s/e/w）：回执 id 是闭环增量修补的寻址依据，
        # 四面墙共用 shell_wall 会让回执里 74 个 actor 只有 71 个唯一 id。
        a.set_editor_property("tags", [unreal.Name(f"GEN_{tag_id}")])
        mi = mi_for(slot, max(size[0], size[1], size[2]) / 100.0)
        if mi:
            a.static_mesh_component.set_material(0, mi)
        return a

    panel("Shell_Floor", [cx, cy, -t / 2], [L, W, t], "floor", "shell_floor")
    panel("Shell_Ceiling", [cx, cy, H + t / 2], [L, W, t], "ceiling", "shell_ceiling")
    panel("Shell_Wall_N", [cx + L / 2 + t / 2, cy, H / 2], [t, W, H], "wall", "shell_wall_n")
    panel("Shell_Wall_S", [cx - L / 2 - t / 2, cy, H / 2], [t, W, H], "wall", "shell_wall_s")
    panel("Shell_Wall_E", [cx, cy + W / 2 + t / 2, H / 2], [L, t, H], "wall", "shell_wall_e")
    panel("Shell_Wall_W", [cx, cy - W / 2 - t / 2, H / 2], [L, t, H], "wall", "shell_wall_w")
    log(f"房间壳 {L:.0f}x{W:.0f}x{H:.0f}cm")


# ---------------------------------------------------------------- 资产与摆放
def build_assets(manifest):
    """proxy_box 用引擎 Cube 缩放（零导入风险），云端真网格才 import。"""
    master = ensure_master_materials()
    screen_master = _make_master("M_Screen_Emissive", emissive=True)

    db = {}
    cube = unreal.load_asset("/Engine/BasicShapes/Cube")
    for a in manifest.get("assets", []):
        aid = a["id"]

        # 库资产已预导入的快路径：直接 load_asset，零导入零贴图搬运。
        # 这是评审机上装配秒级完成的关键——重活全在作者侧一次性做完了。
        if a.get("mesh_kind") == "library" and a.get("ue_asset"):
            mesh = unreal.load_asset(a["ue_asset"])
            mi = unreal.load_asset(a["ue_mi"]) if a.get("ue_mi") else None
            if mesh is None:
                _errors.append(f"库资产加载失败：{aid} ({a['ue_asset']})")
                continue
            db[aid] = {"mesh": mesh, "mi": mi, "proxy": False,
                       "size_cm": a.get("size_cm")}
            continue

        texs = {}
        for key, param, suffix in (("tex_bc", "BaseColorTex", "_BC"),
                                   ("tex_n", "NormalTex", "_N"),
                                   ("tex_orm", "ORMTex", "_ORM")):
            if a.get(key):
                tex = import_texture(a[key], f"T_{aid}{suffix}")
                if tex:
                    texs[param] = tex
        use_master = screen_master if a.get("is_screen") else master
        scalars = {"EmissiveIntensity": 6.0} if a.get("is_screen") else None
        mi = make_material_instance(f"MI_{aid}", use_master, texs, scalars)

        if a.get("mesh_kind") == "proxy_box":
            db[aid] = {"mesh": cube, "mi": mi, "proxy": True,
                       "size_cm": a.get("size_cm") or [100, 100, 100]}
        else:
            mesh = import_mesh(a.get("mesh"), f"SM_{aid}")
            if mesh is None:
                _errors.append(f"资产导入失败：{aid}")
                continue
            try:
                ns = mesh.get_editor_property("nanite_settings")
                ns.enabled = True
                mesh.set_editor_property("nanite_settings", ns)
                # 注意枚举名：ScriptCollisionShapeType（无 -ing）
                mesh_sub.add_simple_collisions(mesh, unreal.ScriptCollisionShapeType.BOX)
                unreal.EditorAssetLibrary.save_loaded_asset(mesh)
            except Exception as e:
                log(f"{aid} Nanite/碰撞设置跳过：{e}")
            db[aid] = {"mesh": mesh, "mi": mi, "proxy": False, "size_cm": a.get("size_cm")}
    return db


def spawn_instances(manifest, db):
    for inst in manifest.get("instances", []):
        rec = db.get(inst["asset_id"])
        if rec is None:
            continue
        r = inst.get("rotation_deg", {"pitch": 0.0, "yaw": 0.0, "roll": 0.0})
        # Python 的 Rotator 构造顺序是 (roll, pitch, yaw) —— 一律关键字传参
        rot = unreal.Rotator(roll=float(r.get("roll", 0.0)),
                             pitch=float(r.get("pitch", 0.0)),
                             yaw=float(r.get("yaw", 0.0)))
        loc = unreal.Vector(*[float(v) for v in inst["location_cm"]])
        actor = actor_sub.spawn_actor_from_object(rec["mesh"], loc, rot)
        if actor is None:
            continue
        if rec["proxy"]:
            # 引擎 Cube 是 100cm 且中心在原点：先按尺寸缩放，再把底面抬到落点
            sx, sy, sz = [max(float(v), 1.0) for v in inst["size_cm"]]
            actor.set_actor_scale3d(unreal.Vector(sx / 100.0, sy / 100.0, sz / 100.0))
            actor.set_actor_location(
                unreal.Vector(loc.x, loc.y, loc.z + sz / 2.0), False, False)
        else:
            actor.set_actor_scale3d(unreal.Vector(*[float(v) for v in inst.get("scale", [1, 1, 1])]))
        actor.set_actor_label(inst["name"])
        tags = [unreal.Name(f"GEN_{inst['id']}")]
        if inst.get("pcg_surface"):
            tags.append(unreal.Name(f"PCG_{str(inst['pcg_surface']).capitalize()}"))
        actor.set_editor_property("tags", tags)
        if rec.get("mi"):
            # 库网格可能不止一个材质槽，逐槽全设——只设 0 号会让其余槽保持默认灰
            comp = actor.static_mesh_component
            try:
                n_slots = max(1, comp.get_num_materials())
            except Exception:
                n_slots = 1
            for slot in range(n_slots):
                comp.set_material(slot, rec["mi"])
    log(f"摆放 {len(manifest.get('instances', []))} 个实例")


# ---------------------------------------------------------------- 贴花
def spawn_decals(manifest):
    room = manifest["room_box"]
    L, W, H = room["size_cm"]
    cx, cy, _ = room.get("center_cm", [0, 0, 0])
    master = ensure_master_materials()
    # DecalActor 沿本地 +X 投影；引擎贴花 UV 是 U←本地Z / V←本地Y，
    # 所以除了让 X 指向墙内，还要 Roll=90 把本地 Y 转到世界竖直，否则海报横躺。
    walls = {
        "N": (lambda u, v: (cx + L / 2 - 2, cy + (u - 0.5) * W, v * H), 180.0),
        "S": (lambda u, v: (cx - L / 2 + 2, cy + (0.5 - u) * W, v * H), 0.0),
        "E": (lambda u, v: (cx + (0.5 - u) * L, cy + W / 2 - 2, v * H), 270.0),
        "W": (lambda u, v: (cx + (u - 0.5) * L, cy - W / 2 + 2, v * H), 90.0),
    }
    decal_master = _make_decal_master()
    for i, d in enumerate(manifest.get("decals", [])):
        fn, yaw = walls.get(d.get("wall", "N"), walls["N"])
        loc = fn(*d.get("uv", [0.5, 0.5]))
        actor = actor_sub.spawn_actor_from_class(
            unreal.DecalActor, unreal.Vector(*loc),
            unreal.Rotator(roll=90.0, pitch=0.0, yaw=yaw))
        if actor is None:
            continue
        actor.set_actor_label(f"Decal_{d.get('name', i)}")
        actor.set_editor_property("tags", [unreal.Name(f"GEN_{d['id']}")])
        w, h = d.get("size_cm", [60, 90])
        dc = actor.decal
        dc.set_editor_property("decal_size", unreal.Vector(12.0, h / 2.0, w / 2.0))
        tex = import_texture(d.get("texture"), f"T_Decal_{d['id']}")
        if tex and decal_master:
            mi = make_material_instance(f"MI_Decal_{d['id']}", decal_master,
                                        {"BaseColorTex": tex})
            if mi:
                dc.set_decal_material(mi)
    log(f"贴花 {len(manifest.get('decals', []))} 张")


def _make_decal_master():
    """Deferred Decal 域母材质。Surface 域材质挂到 DecalActor 上**必然渲成黑块**
    ——贴花走 GBuffer 投影路径，材质域必须是 MD_DEFERRED_DECAL + 半透混合。"""
    pkg = f"{CONTENT_DIR}/Materials"
    full = f"{pkg}/M_Decal"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        return full
    mat = atools.create_asset("M_Decal", pkg, unreal.Material,
                              unreal.MaterialFactoryNew())
    if mat is None:
        return None
    mat.set_editor_property("material_domain",
                            unreal.MaterialDomain.MD_DEFERRED_DECAL)
    mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    ME = unreal.MaterialEditingLibrary
    tex_node = ME.create_material_expression(
        mat, unreal.MaterialExpressionTextureSampleParameter2D, -400, 0)
    tex_node.set_editor_property("parameter_name", "BaseColorTex")
    tex_node.set_editor_property(
        "texture", unreal.load_asset("/Engine/EngineResources/WhiteSquareTexture"))
    if not ME.connect_material_property(tex_node, "",
                                        unreal.MaterialProperty.MP_BASE_COLOR):
        unreal.log_error("[AutoScene] M_Decal BaseColor 连线失败")
    const = ME.create_material_expression(
        mat, unreal.MaterialExpressionConstant, -400, 240)
    const.set_editor_property("r", 1.0)
    ME.connect_material_property(const, "", unreal.MaterialProperty.MP_OPACITY)
    ME.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return full


# ---------------------------------------------------------------- 灯光与后期
def setup_lighting(lighting):
    plane = unreal.load_asset("/Engine/BasicShapes/Plane")
    for i, p in enumerate(lighting.get("ceiling_panels", [])):
        loc = unreal.Vector(*[float(v) for v in p["location_cm"]])
        panel = actor_sub.spawn_actor_from_object(
            plane, loc, unreal.Rotator(roll=180.0, pitch=0.0, yaw=0.0))  # 法线朝下
        if panel:
            panel.set_actor_scale3d(unreal.Vector(p["size_cm"][0] / 100.0,
                                                  p["size_cm"][1] / 100.0, 1.0))
            panel.set_actor_label(f"LightPanel_{i}")
            panel.set_editor_property("tags", [unreal.Name(f"GEN_lightpanel_{i}")])
            # 灯板本体必须自发光：参考图里"看得见的日光灯管"就是它。不发光的
            # 灯板在俯拍视角下是一块黑板，天花板会显得像不存在。
            glow = make_material_instance("MI_LightPanel",
                                          _make_master("M_Screen_Emissive", emissive=True),
                                          {}, {"EmissiveIntensity": 12.0})
            if glow:
                panel.static_mesh_component.set_material(0, glow)
        rl = actor_sub.spawn_actor_from_class(
            unreal.RectLight, loc - unreal.Vector(0, 0, 3),
            unreal.Rotator(roll=0.0, pitch=-90.0, yaw=0.0))              # 俯角朝下
        if rl is None:
            continue
        lc = rl.light_component
        lc.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
        lc.set_editor_property("intensity", float(p.get("lumen", 3500)))
        lc.set_editor_property("use_temperature", True)
        lc.set_editor_property("temperature", float(p.get("kelvin", 6500)))
        lc.set_editor_property("source_width", float(p["size_cm"][0]))
        lc.set_editor_property("source_height", float(p["size_cm"][1]))
        rl.set_actor_label(f"RectLight_{i}")
        rl.set_editor_property("tags", [unreal.Name(f"GEN_rectlight_{i}")])
        # 天花板补光：主灯全部朝下，天花板只吃地面反弹，会黑成一片虚空。
        # 每盏灯在贴近天花板处放一颗小点光——反平方衰减让它主要照亮顶面。
        up = actor_sub.spawn_actor_from_class(
            unreal.PointLight, loc - unreal.Vector(0, 0, 25))
        if up:
            uc = up.light_component
            uc.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
            uc.set_editor_property("intensity", 600.0)
            uc.set_editor_property("attenuation_radius", 340.0)
            uc.set_editor_property("use_temperature", True)
            uc.set_editor_property("temperature", float(p.get("kelvin", 6500)))
            up.set_actor_label(f"CeilingFill_{i}")
            up.set_editor_property("tags", [unreal.Name(f"GEN_ceilfill_{i}")])

    for i, s in enumerate(lighting.get("screen_fill_lights", [])):
        pl = actor_sub.spawn_actor_from_class(
            unreal.PointLight, unreal.Vector(*[float(v) for v in s["location_cm"]]))
        if pl is None:
            continue
        c = pl.light_component
        c.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
        c.set_editor_property("intensity", float(s.get("lumen", 150)))
        rgb = s.get("rgb", [0.5, 0.9, 0.9])
        c.set_light_color(unreal.LinearColor(float(rgb[0]), float(rgb[1]), float(rgb[2]), 1.0))
        c.set_editor_property("attenuation_radius", float(s.get("radius_cm", 180)))
        pl.set_actor_label(f"ScreenFill_{i}")
        pl.set_editor_property("tags", [unreal.Name(f"GEN_screenfill_{i}")])

    for i, s in enumerate(lighting.get("spots", [])):
        r = s.get("rotation_deg", {"pitch": -60.0, "yaw": 0.0, "roll": 0.0})
        sp = actor_sub.spawn_actor_from_class(
            unreal.SpotLight, unreal.Vector(*[float(v) for v in s["location_cm"]]),
            unreal.Rotator(roll=float(r.get("roll", 0.0)), pitch=float(r.get("pitch", -60.0)),
                           yaw=float(r.get("yaw", 0.0))))
        if sp is None:
            continue
        sc = sp.light_component
        sc.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
        sc.set_editor_property("intensity", float(s.get("lumen", 1200)))
        sc.set_editor_property("use_temperature", True)
        sc.set_editor_property("temperature", float(s.get("kelvin", 4500)))
        sp.set_actor_label(f"Spot_{i}")
        sp.set_editor_property("tags", [unreal.Name(f"GEN_spot_{i}")])

    # 体积雾：霓虹光锥与彩色散射，是这类场景氛围的关键。
    # 属性名随引擎版本会变（bEnableVolumetricFog 的 Python 名在 5.x 各版不完全一致），
    # 逐个 try：雾是装饰性的，一个属性名对不上不该让整场装配失败。
    fog = actor_sub.spawn_actor_from_class(unreal.ExponentialHeightFog,
                                           unreal.Vector(0.0, 0.0, 0.0))
    if fog:
        fc = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
        for prop in ("enable_volumetric_fog", "volumetric_fog"):
            if _try_set(fc, prop, True):
                break
        _try_set(fc, "fog_density", float(lighting.get("fog_density", 0.04)))
        _try_set(fc, "volumetric_fog_scattering_distribution", 0.6)
        # 雾的散射色必须显式设成中性暗灰：UE 默认是天蓝（为室外调的），
        # 室内 10m 纵深足以把白砖墙糊成蓝墙——实测三轮排查（贴图/白平衡/雾）
        # 的最终真凶就是它。
        _try_set(fc, "fog_inscattering_luminance",
                 unreal.LinearColor(0.035, 0.033, 0.03, 1.0))
        _try_set(fc, "fog_inscattering_color",
                 unreal.LinearColor(0.035, 0.033, 0.03, 1.0))
        fog.set_actor_label("Fog_Volumetric")
        fog.set_editor_property("tags", [unreal.Name("GEN_fog")])
    log("灯光与体积雾就绪")


def setup_postprocess(post):
    """Lumen 低照度场景必须锁手动曝光，否则自动曝光会把暗部提亮、毁掉影调。"""
    ppv = actor_sub.spawn_actor_from_class(unreal.PostProcessVolume,
                                           unreal.Vector(0.0, 0.0, 0.0))
    if ppv is None:
        return
    ppv.set_editor_property("unbound", True)
    ppv.set_actor_label("PP_Global")
    ppv.set_editor_property("tags", [unreal.Name("GEN_postprocess")])
    s = ppv.get_editor_property("settings")
    s.override_auto_exposure_method = True
    s.auto_exposure_method = unreal.AutoExposureMethod.AEM_MANUAL
    # AEM_MANUAL 默认应用物理相机曝光(ISO100/f4/1/60 ≈ EV100 9.9)，bias 只需小幅微调；
    # +9~+12 是未开物理曝光的旧经验值，两套口径不能混用。
    s.override_auto_exposure_bias = True
    s.auto_exposure_bias = float(post.get("ev_bias", 1.0))
    s.override_bloom_intensity = True
    s.bloom_intensity = float(post.get("bloom", 0.9))
    s.override_vignette_intensity = True
    s.vignette_intensity = float(post.get("vignette", 0.5))
    s.override_white_temp = True
    s.white_temp = float(post.get("white_temp", 6300.0))
    ppv.set_editor_property("settings", s)


def setup_camera(cam):
    r = cam.get("rotation_deg", {"pitch": 0.0, "yaw": 0.0, "roll": 0.0})
    actor = actor_sub.spawn_actor_from_class(
        unreal.CineCameraActor, unreal.Vector(*[float(v) for v in cam["location_cm"]]),
        unreal.Rotator(roll=float(r.get("roll", 0.0)), pitch=float(r.get("pitch", 0.0)),
                       yaw=float(r.get("yaw", 0.0))))
    if actor is None:
        return
    actor.set_actor_label("Cam_RefMatch")
    actor.set_editor_property("tags", [unreal.Name("GEN_camera")])
    cc = actor.get_cine_camera_component()
    fb = cc.get_editor_property("filmback")
    # focal 与解算必须共用同一 filmback 口径，否则「截图视角=参考图视角」不成立
    fb.sensor_width = float(cam.get("sensor_width_mm", 36.0))
    fb.sensor_height = fb.sensor_width / float(cam.get("aspect", 16.0 / 9.0))
    cc.set_editor_property("filmback", fb)
    cc.set_editor_property("current_focal_length", float(cam.get("focal_mm", 24.0)))
    log(f"相机 focal={cam.get('focal_mm')}mm hfov={cam.get('hfov_deg')}")


# ---------------------------------------------------------------- 回执
def clean_generated_assets():
    """把 per-run 资产目录整树删掉，重建时全新导入。

    「同名已存在就复用」的旧语义被证实不可靠：换一轮生成后，Shell 材质实例
    仍绑着上一轮的旧贴图（白砖墙渲成蓝墙查了四轮，最后一环就是它）。
    资产库（AssetLib/）在别的目录，不受影响；母材质会被 ensure 重建。
    """
    for sub in ("Textures", "Materials", "Meshes"):
        path = f"{CONTENT_DIR}/{sub}"
        try:
            if unreal.EditorAssetLibrary.does_directory_exist(path):
                unreal.EditorAssetLibrary.delete_directory(path)
                log(f"清理资产目录 {path}")
        except Exception as e:  # noqa: BLE001
            unreal.log_warning(f"[AutoScene] 清理 {path} 失败：{e}")


def clean_generated():
    n = 0
    for a in actor_sub.get_all_level_actors():
        try:
            if any(str(t).startswith("GEN_") for t in a.tags):
                actor_sub.destroy_actor(a)
                n += 1
        except Exception:
            pass
    log(f"清理上一轮 {n} 个 actor")


def detect_overlaps():
    """穿模检测：GEN_ 网格 actor 两两做**真实包围盒**求交（compile 侧的 AABB
    去穿插用的是布局意图尺寸，网格实际外形可能更大）。椅↔桌豁免；重叠体积
    比小于 5% 的接触不算。结果进回执供人工复核。"""
    items = []
    for a in actor_sub.get_all_level_actors():
        try:
            gen = next((str(t)[4:] for t in a.tags if str(t).startswith("GEN_")), None)
            if gen is None or gen.startswith("shell") or "Light" in a.get_class().get_name():
                continue
            if not isinstance(a, unreal.StaticMeshActor):
                continue
            origin, extent = a.get_actor_bounds(False)
            items.append((gen, a.get_actor_label(), origin, extent))
        except Exception:  # noqa: BLE001
            continue
    overlaps = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            gi, li, oi, ei = items[i]
            gj, lj, oj, ej = items[j]
            # 椅×桌：整椅塞桌下合法；椅顶高出桌面板且水平相交 = 椅背穿桌面，
            # 必须上报（曾被无条件豁免遮蔽，用户肉眼可见）
            chair_i, chair_j = "chair" in gi, "chair" in gj
            table_i = "table" in gi or "band" in gi
            table_j = "table" in gj or "band" in gj
            if (chair_i and table_j) or (chair_j and table_i):
                c_top = (oi.z + ei.z) if chair_i else (oj.z + ej.z)
                t_top = (oj.z + ej.z) if chair_i else (oi.z + ei.z)
                if c_top <= (t_top - 12.0) + 2.0:
                    continue
            # 地面杂物豁免（与 compile 侧一致）：线缆/垃圾/管线从家具底下
            # 穿过是参考图的物理常态，proxy 盒相交不是视觉穿模
            if any(k in gi or k in gj for k in ("cable", "litter", "pipe")):
                continue
            # 桌下净空豁免：物件顶端低于桌面（顶面下 5cm 余量）= 合法的桌下
            # 摆放（参考图的主机塔就在桌底下），不算穿模
            ti, tj = "table" in gi or "band" in gi, "table" in gj or "band" in gj
            if ti != tj:
                table_top = (oi.z + ei.z) if ti else (oj.z + ej.z)
                item_top = (oj.z + ej.z) if ti else (oi.z + ei.z)
                if item_top < table_top - 5.0:
                    continue
            # 真实相交盒：穿透深度公式 (ei+ej)-|d| 在"一方沿某轴整个被包含"
            # 时会夸大（实测细柱穿桌报成 68cm，真实相交只有柱径 10cm）
            ox = min(2 * ei.x, 2 * ej.x, (ei.x + ej.x) - abs(oi.x - oj.x))
            oy = min(2 * ei.y, 2 * ej.y, (ei.y + ej.y) - abs(oi.y - oj.y))
            oz = min(2 * ei.z, 2 * ej.z, (ei.z + ej.z) - abs(oi.z - oj.z))
            if ox <= 0 or oy <= 0 or oz <= 0:
                continue
            vol = ox * oy * oz
            min_vol = 8 * min(ei.x * ei.y * ei.z, ej.x * ej.y * ej.z)
            if vol < 0.01 * min_vol:
                continue                                # 亚厘米接触不算穿模
            overlaps.append({"a": gi, "b": gj,
                             "overlap_cm": [round(ox, 1), round(oy, 1), round(oz, 1)]})
    if overlaps:
        log(f"穿模检测：{len(overlaps)} 处（详见回执 overlaps）")
        for o in overlaps[:8]:
            log(f"  {o['a']} × {o['b']} 重叠 {o['overlap_cm']}")
    else:
        log("穿模检测：0 处")
    return overlaps


def apply_gravity():
    """真实包围盒重力吸附 + 残余悬空回执。

    compile 的模型高度与库网格真实高度有落差（矮桌变体实测顶面比模型
    低 35cm），模型级吸附后物品仍会悬浮在真实桌面上方。终局用 UE 实测
    bounds 向下贴合：底面落到脚印重叠 ≥30% 的最高支撑面顶（无支撑落地）。
    两轮迭代让叠放物随支撑连锁下落。灯类/结构物/底高 >170cm 的墙顶挂件不动。
    """
    exempt = ("light", "lamp", "fan", "vent", "duct", "pillar", "column", "band")
    items = []
    for a in actor_sub.get_all_level_actors():
        try:
            gen = next((str(t)[4:] for t in a.tags if str(t).startswith("GEN_")), None)
            if gen is None or gen.startswith("shell") or "Light" in a.get_class().get_name():
                continue
            if not isinstance(a, unreal.StaticMeshActor):
                continue
            items.append((gen, a))
        except Exception:  # noqa: BLE001
            continue

    def _supports(gen, o, e, bounds):
        best = 0.0
        for gj, (oj, ej) in bounds.items():
            if gj == gen:
                continue
            top = oj.z + ej.z
            if top > (o.z - e.z) + 6.0:
                continue
            ox = min((e.x + ej.x) - abs(o.x - oj.x), 2 * e.x, 2 * ej.x)
            oy = min((e.y + ej.y) - abs(o.y - oj.y), 2 * e.y, 2 * ej.y)
            if ox > 0 and oy > 0 and ox * oy >= 0.3 * (2 * e.x) * (2 * e.y):
                best = max(best, top)
        return best

    n_fix = 0
    for _pass in range(2):
        bounds = {}
        for gen, a in items:
            o, e = a.get_actor_bounds(False)
            bounds[gen] = (o, e)
        for gen, a in items:
            if any(k in gen for k in exempt):
                continue
            o, e = bounds[gen]
            bottom = o.z - e.z
            if bottom <= 2.0 or bottom > 170.0:
                continue
            gap = bottom - _supports(gen, o, e, bounds)
            if gap > 2.0:
                loc = a.get_actor_location()
                a.set_actor_location(unreal.Vector(loc.x, loc.y, loc.z - gap), False, False)
                n_fix += 1

    floating = []                                       # 残余悬空进回执
    bounds = {}
    for gen, a in items:
        o, e = a.get_actor_bounds(False)
        bounds[gen] = (o, e)
    for gen, a in items:
        if any(k in gen for k in exempt):
            continue
        o, e = bounds[gen]
        bottom = o.z - e.z
        if bottom <= 4.0 or bottom > 170.0:
            continue
        gap = bottom - _supports(gen, o, e, bounds)
        if gap > 4.0:
            floating.append({"id": gen, "gap_cm": round(gap, 1)})
    log(f"重力吸附：下落 {n_fix} 处，残余悬空 {len(floating)} 处")
    return floating


def resolve_residuals():
    """真实包围盒微推挤：终局清理厘米级残余穿插。

    compile 模型与真实网格的落差会留下模型看不见的小穿插（实测：椅背穿
    桌面 8.6cm、邻座双屏擦碰 4.1cm）。规则：椅×桌只在椅顶高出桌面板且
    水平相交时算穿（塞桌下合法），推椅子；一般对推体积较小者；一律沿
    最小穿透水平轴远离对方推开 +2cm 间隙。"""
    n_total = 0
    for _pass in range(4):
        items = []
        for a in actor_sub.get_all_level_actors():
            try:
                gen = next((str(t)[4:] for t in a.tags if str(t).startswith("GEN_")), None)
                if gen is None or gen.startswith("shell") or "Light" in a.get_class().get_name():
                    continue
                if not isinstance(a, unreal.StaticMeshActor):
                    continue
                o, e = a.get_actor_bounds(False)
                items.append([gen, a, o, e])
            except Exception:  # noqa: BLE001
                continue
        moved = 0
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                gi, ai, oi, ei = items[i]
                gj, aj, oj, ej = items[j]
                if any(k in gi or k in gj for k in ("cable", "litter", "pipe")):
                    continue
                if any(k in gi for k in ("pillar", "column")) and any(k in gj for k in ("pillar", "column")):
                    continue
                ox = min((ei.x + ej.x) - abs(oi.x - oj.x), 2 * ei.x, 2 * ej.x)
                oy = min((ei.y + ej.y) - abs(oi.y - oj.y), 2 * ei.y, 2 * ej.y)
                oz = min((ei.z + ej.z) - abs(oi.z - oj.z), 2 * ei.z, 2 * ej.z)
                if ox <= 0.5 or oy <= 0.5 or oz <= 0.5:
                    continue
                chair_i, chair_j = "chair" in gi, "chair" in gj
                table_i = "table" in gi or "band" in gi
                table_j = "table" in gj or "band" in gj
                mover_k = None
                if (chair_i and table_j) or (chair_j and table_i):
                    c_top = (oi.z + ei.z) if chair_i else (oj.z + ej.z)
                    t_top = (oj.z + ej.z) if chair_i else (oi.z + ei.z)
                    if c_top <= (t_top - 12.0) + 2.0:
                        continue                        # 完整塞入桌下，合法
                    mover_k = i if chair_i else j       # 椅背穿桌面：推椅子
                else:
                    ti, tj = table_i, table_j
                    if ti != tj:                        # 桌下净空合法
                        t_top = (oi.z + ei.z) if ti else (oj.z + ej.z)
                        it_top = (oj.z + ej.z) if ti else (oi.z + ei.z)
                        if it_top < t_top - 5.0:
                            continue
                    vol_i = ei.x * ei.y * ei.z
                    vol_j = ej.x * ej.y * ej.z
                    if ox * oy * oz < 0.01 * 8 * min(vol_i, vol_j):
                        continue                        # 亚厘米接触不动
                    # 结构物（柱/桌带）不动
                    imm_i = any(k in gi for k in ("pillar", "column", "band"))
                    imm_j = any(k in gj for k in ("pillar", "column", "band"))
                    if imm_i and imm_j:
                        continue
                    mover_k = (j if imm_i else
                               (i if imm_j else (i if vol_i <= vol_j else j)))
                g_m, a_m, o_m, _ = items[mover_k]
                o_o = oj if mover_k == i else oi
                axis = 0 if ox <= oy else 1
                delta = (ox if axis == 0 else oy) + 2.0
                cur = o_m.x if axis == 0 else o_m.y
                other = o_o.x if axis == 0 else o_o.y
                sign = 1.0 if cur >= other else -1.0
                loc = a_m.get_actor_location()
                if axis == 0:
                    a_m.set_actor_location(
                        unreal.Vector(loc.x + sign * delta, loc.y, loc.z), False, False)
                else:
                    a_m.set_actor_location(
                        unreal.Vector(loc.x, loc.y + sign * delta, loc.z), False, False)
                moved += 1
                n_total += 1
        if not moved:
            break
    if n_total:
        log(f"微推挤：清理残余穿插 {n_total} 处")
    return n_total


def write_scene_manifest(path):
    """UE 向管线回传的唯一真相；也是闭环增量修补的寻址依据。"""
    floating = apply_gravity()                          # 先落地
    if resolve_residuals():                             # 微推挤清残余穿插
        floating = apply_gravity()                      # 推挤可能解除支撑，复吸附
    actors = []
    for a in actor_sub.get_all_level_actors():
        try:
            gen = next((str(t)[4:] for t in a.tags if str(t).startswith("GEN_")), None)
        except Exception:
            gen = None
        if gen is None:
            continue
        loc, rot = a.get_actor_location(), a.get_actor_rotation()
        actors.append({"id": gen, "label": a.get_actor_label(),
                       "class": a.get_class().get_name(),
                       "loc_cm": [round(loc.x, 1), round(loc.y, 1), round(loc.z, 1)],
                       "rot_deg": {"pitch": round(rot.pitch, 1), "yaw": round(rot.yaw, 1),
                                   "roll": round(rot.roll, 1)}})
    payload = {"status": "ok" if not _errors else "partial",
               "level": _args.level, "errors": _errors,
               "floating": floating,
               "overlaps": detect_overlaps(), "actors": actors}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"回执写出：{path}（{len(actors)} actors，{len(_errors)} errors）")


def main():
    manifest_path = _args.manifest
    if not manifest_path or not os.path.isfile(manifest_path):
        unreal.log_error(f"[AutoScene] 找不到 build_manifest：{manifest_path}")
        return
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    global CONTENT_DIR
    CONTENT_DIR = manifest.get("content_dir", CONTENT_DIR)

    # 冷启动打开的是工程默认地图，先确保目标关卡存在并加载
    if not unreal.EditorAssetLibrary.does_asset_exist(_args.level):
        lvl_sub.new_level(_args.level)
    else:
        lvl_sub.load_level(_args.level)

    if _args.clean:
        clean_generated()
        clean_generated_assets()

    build_room_shell(manifest["room_box"])
    db = build_assets(manifest)
    spawn_instances(manifest, db)
    spawn_decals(manifest)
    setup_lighting(manifest.get("lighting", {}))
    setup_postprocess(manifest.get("post", {}))
    setup_camera(manifest["camera"])

    # 回执阶段的重力吸附会移动 actor，必须先回执后存盘——反过来会导致
    # "回执报 0 悬空、存盘地图仍悬浮"的分裂（实测踩过：临时会话真相
    # 与磁盘真相不一致，评审打开的是磁盘）。
    if _args.manifest_out:
        write_scene_manifest(_args.manifest_out)
    lvl_sub.save_current_level()
    log("=== 装配完成 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # 任何异常都要落进回执，否则编排器只能看到一个空退出码
        import traceback
        unreal.log_error(f"[AutoScene] 装配异常：{exc}\n{traceback.format_exc()}")
        if _args.manifest_out:
            try:
                with open(_args.manifest_out, "w", encoding="utf-8") as f:
                    json.dump({"status": "error", "errors": [str(exc)], "actors": []},
                              f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        sys.exit(1)
