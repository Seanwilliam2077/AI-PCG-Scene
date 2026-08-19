# -*- coding: utf-8 -*-
"""在对齐参考图的机位截一张高分辨率图。

两个必须处理的异步点：
  1. -ExecutePythonScript 下脚本一返回，编辑器就在下个 tick 退出；截图本身也是
     异步的。所以先 set_keep_python_script_alive(True)，完成回调里再 quit。
  2. **新建/改过的材质需要编译着色器**，而编译是异步的。刚加载完关卡就截图，
     拿到的是尚未编译完成的默认材质——表现为"改了材质但截图纹丝不动"。
     所以先空转若干 tick 等编译队列排空，再截。
"""
import json, os, unreal

_side = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_args.json")
cfg = json.load(open(_side, encoding="utf-8"))
WARMUP_TICKS = int(cfg.get("warmup_ticks", 240))

unreal.EditorPythonScripting.set_keep_python_script_alive(True)
unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(cfg["level"])

eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
cam = next((a for a in eas.get_all_level_actors()
            if a.get_actor_label() == "Cam_RefMatch"), None)
if cam is None:
    unreal.log_error("[Shot] 找不到 Cam_RefMatch")
    unreal.SystemLibrary.quit_editor()
else:
    state = {"ticks": 0, "task": None}

    def _tick(dt):
        state["ticks"] += 1
        if state["task"] is None:
            # 等着色器编译队列排空（或到达上限）再截图
            remaining = unreal.SystemLibrary.get_shader_compilation_pending_count() \
                if hasattr(unreal.SystemLibrary, "get_shader_compilation_pending_count") else 0
            if state["ticks"] < WARMUP_TICKS or remaining > 0:
                if state["ticks"] % 60 == 0:
                    unreal.log(f"[Shot] 预热 {state['ticks']} tick，待编译着色器 {remaining}")
                return
            state["task"] = unreal.AutomationLibrary.take_high_res_screenshot(
                int(cfg["width"]), int(cfg["height"]), cfg["out"], camera=cam)
            unreal.log(f"[Shot] 预热完成（{state['ticks']} tick），开始截图")
            return
        if state["task"].is_task_done():
            unreal.unregister_slate_post_tick_callback(_h)
            unreal.log(f"[Shot] 完成 -> {cfg['out']}")
            unreal.SystemLibrary.quit_editor()

    _h = unreal.register_slate_post_tick_callback(_tick)
