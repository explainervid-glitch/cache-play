# Cache Play keyboard/mouse control test. Run:
#   blender --factory-startup --enable-event-simulate --python test_controls.py -- <addon_parent_dir> <out_json>
import json
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
ADDON_PARENT, OUT = argv[0], argv[1]
sys.path.insert(0, ADDON_PARENT)
import cache_play  # noqa: E402

cache_play.register()
player = sys.modules["cache_play.player"]
store = sys.modules["cache_play.store"]
results = []


def check(name, ok, detail=None):
    results.append({"test": name, "ok": bool(ok), "detail": detail})


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def press(type_, value='PRESS'):
    win, area, region = view3d()
    x = region.x + region.width // 2
    y = region.y + region.height // 2
    win.event_simulate(type=type_, value=value, x=x, y=y)


def start(fill_only=False):
    win, area, region = view3d()
    with bpy.context.temp_override(window=win, area=area, region=region):
        return bpy.ops.cacheplay.preview('INVOKE_DEFAULT', fill_only=fill_only)


def setup():
    scene = bpy.context.scene
    cube = bpy.data.objects["Cube"]
    cube.location.x = -3
    cube.keyframe_insert("location", index=0, frame=1)
    cube.location.x = 3
    cube.keyframe_insert("location", index=0, frame=48)
    scene.frame_start, scene.frame_end = 1, 48
    scene.cache_play.preview_size = 'QUARTER'
    scene.cache_play.loop = True
    _win, area, _region = view3d()
    area.spaces.active.region_3d.view_perspective = 'PERSP'  # Cache & Play must switch to camera
    scene.frame_set(10)


# A list of (delay, action) steps run one after another by a timer.
def s_start():
    start()


def s_check_playing():
    st = player.state
    check("fill then play", st.active and store.frame_cache.stats()["frames"] == 48,
          {"active": st.active, "frames": store.frame_cache.stats()["frames"]})
    check("starts at current frame", 10 <= st.frame <= 48, st.frame)
    _win, area, _region = view3d()
    check("Cache & Play switched the viewport to camera view",
          area.spaces.active.region_3d.view_perspective == 'CAMERA',
          area.spaces.active.region_3d.view_perspective)


def s_space():
    press('SPACE')
    press('SPACE', 'RELEASE')


def s_check_paused():
    st = player.state
    check("space pauses", st.active and st.paused)
    s_check_paused.index = st.index


def s_right():
    press('RIGHT_ARROW')
    press('RIGHT_ARROW', 'RELEASE')


def s_check_step():
    st = player.state
    check("right arrow steps one frame", st.index == (s_check_paused.index + 1) % 48,
          {"before": s_check_paused.index, "after": st.index})
    s_check_step.index = st.index


def s_hold_frame():
    st = player.state
    check("paused frame holds", st.paused and st.index == s_check_step.index)


def s_modifier_and_wheel():
    press('SPACE')
    press('SPACE', 'RELEASE')
    press('LEFT_SHIFT')
    press('LEFT_SHIFT', 'RELEASE')
    press('WHEELUPMOUSE')


def s_check_still_playing():
    st = player.state
    check("space resumes, shift and wheel do not stop", st.active and not st.paused,
          {"active": st.active, "paused": st.paused})


def s_esc():
    s_esc.frame = player.state.frame
    press('ESC')
    press('ESC', 'RELEASE')


def s_check_stopped():
    st = player.state
    frame = bpy.context.scene.frame_current
    check("esc stops", not st.active)
    check("playhead moves to shown frame on stop", abs(frame - s_esc.frame) <= 1,
          {"scene": frame, "shown": s_esc.frame})
    check("cache kept after stop", store.frame_cache.stats()["frames"] == 48, store.frame_cache.stats()["frames"])
    check("stats recorded", player.state.last_summary is not None, player.state.last_summary)


def s_start_again():
    check("cached replay starts without refill", 'RUNNING_MODAL' in start() and not player.state.filling)


def s_click():
    press('LEFTMOUSE')
    press('LEFTMOUSE', 'RELEASE')


def s_check_click_stopped():
    check("click stops", not player.state.active)


def s_no_loop():
    bpy.context.scene.cache_play.loop = False
    bpy.context.scene.frame_set(40)
    start()


def s_check_no_loop_end():
    check("non-looping play ends by itself", not player.state.active,
          {"active": player.state.active, "scene_frame": bpy.context.scene.frame_current})


def s_fill_cancel():
    bpy.ops.cacheplay.clear()
    scene = bpy.context.scene
    scene.frame_end = 400
    scene.frame_set(7)
    start(fill_only=True)


def s_fill_esc():
    press('ESC')
    press('ESC', 'RELEASE')


def s_check_fill_cancel():
    n = store.frame_cache.stats()["frames"]
    check("esc stops caching, keeps frames done", not player.state.filling and 0 < n < 400, n)
    check("frame restored after caching", bpy.context.scene.frame_current == 7, bpy.context.scene.frame_current)


def s_view_change():
    # Changing preview size must refill rather than reuse frames of another size.
    scene = bpy.context.scene
    scene.frame_end = 48
    scene.cache_play.preview_size = 'THIRD'
    start(fill_only=True)


def s_check_view_change():
    c = store.frame_cache
    check("size change refills at new size", (c.width, c.height) == (640, 360) and c.stats()["frames"] == 48,
          {"size": [c.width, c.height], "frames": c.stats()["frames"]})


def s_finish():
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2)
    bpy.ops.wm.quit_blender()


STEPS = [
    (1.0, setup), (0.5, s_start), (3.0, s_check_playing),
    (0.0, s_space), (0.3, s_check_paused), (0.0, s_right), (0.3, s_check_step), (0.5, s_hold_frame),
    (0.0, s_modifier_and_wheel), (0.5, s_check_still_playing),
    (0.0, s_esc), (0.3, s_check_stopped),
    (0.3, s_start_again), (0.5, s_click), (0.3, s_check_click_stopped),
    (0.3, s_no_loop), (1.5, s_check_no_loop_end),
    (0.3, s_fill_cancel), (0.4, s_fill_esc), (0.5, s_check_fill_cancel),
    (0.3, s_view_change), (4.0, s_check_view_change),
    (0.2, s_finish),
]


def runner():
    if not STEPS:
        return None
    _delay, func = STEPS.pop(0)
    try:
        func()
    except Exception as ex:  # keep going so the report is complete
        check(f"{func.__name__} raised", False, repr(ex))
    return STEPS[0][0] if STEPS else None


bpy.app.timers.register(runner, first_interval=2.0)
