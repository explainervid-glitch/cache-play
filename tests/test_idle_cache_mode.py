# Cache When Idle while Cache Mode is open. Run:
#   blender --factory-startup --python test_idle_cache_mode.py -- <addon_parent_dir> <out_json>
# The "is the user working in Blender" input check is replaced by a value the test controls,
# so real mouse or keyboard use during the run does not matter.
import json
import sys
import time

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
ADDON_PARENT, OUT = argv[0], argv[1]
sys.path.insert(0, ADDON_PARENT)
import cache_play  # noqa: E402

cache_play.register()
store = sys.modules["cache_play.store"]
player = sys.modules["cache_play.player"]
idle = sys.modules["cache_play.idle"]
ui = sys.modules["cache_play.ui"]
ui._DefaultPrefs.idle_delay = 0.5
cache = store.frame_cache
results = []
fake = {"kinds": set()}  # activity kinds the stubbed input check reports
idle._interaction_probe = lambda: set(fake["kinds"])
ORIGINAL = 10


def check(name, ok, detail=None):
    results.append({"test": name, "ok": bool(ok), "detail": detail})


def count():
    return cache.stats()["frames"]


def view():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return win, area, next(r for r in area.regions if r.type == 'WINDOW')


def call(op, **kwargs):
    win, area, region = view()
    with bpy.context.temp_override(window=win, area=area, region=region):
        return op('INVOKE_DEFAULT', **kwargs)


def setup():
    scene = bpy.context.scene
    cube = bpy.data.objects["Cube"]
    cube.location.x = -3
    cube.keyframe_insert("location", index=0, frame=1)
    cube.location.x = 3
    cube.keyframe_insert("location", index=0, frame=48)
    scene.frame_start, scene.frame_end = 1, 48
    scene.cache_play.preview_size = 'QUARTER'
    view()[1].spaces.active.region_3d.view_perspective = 'CAMERA'
    scene.frame_set(ORIGINAL)
    call(bpy.ops.cacheplay.preview, fill_only=True)


def s_extend_and_review():
    bpy.context.scene.frame_end = 300  # 48 cached, 252 missing
    s_extend_and_review.before = count()
    call(bpy.ops.cacheplay.cache_mode)
    bpy.context.scene.cache_play.cache_when_idle = True


def s_check_review_started():
    st = player.state
    check("Cache Mode opens on the partial cache without caching",
          st.cache_mode and not st.filling and s_extend_and_review.before == 48,
          {"cache_mode": st.cache_mode, "before": s_extend_and_review.before})


def s_check_idle_in_cache_mode():
    st = player.state
    n = count()
    check("idle caching fills frames while Cache Mode is paused", n > 48 and st.cache_mode, n)
    check("new frames become scrubbable in Cache Mode", abs(len(st.frames) - n) <= 2,
          {"scrubbable": len(st.frames), "cached": n})
    check("Blender's playhead untouched", bpy.context.scene.frame_current == ORIGINAL,
          bpy.context.scene.frame_current)
    check("panel status shows caching", idle.status.startswith(("Caching", "All frames")), idle.status)


def poke(kinds, seconds):
    """Report the given activity kinds for a while, as if the user were doing that."""
    until = time.perf_counter() + seconds

    def tick():
        fake["kinds"] = set(kinds) if time.perf_counter() < until else set()
        return 0.05 if fake["kinds"] else None
    bpy.app.timers.register(tick, first_interval=0.0)


def s_activity():
    poke({'KEYS'}, 1.0)


def s_mouse_default():
    poke({'MOUSE'}, 1.5)


def s_check_mouse_default():
    check("by default, moving the mouse over Blender counts as working", idle.idle_seconds() < 0.3,
          round(idle.idle_seconds(), 2))


def s_other_apps_default():
    poke({'OTHER_APPS'}, 1.5)


def s_check_other_apps_default():
    check("by default, input in other programs does not count as working", idle.idle_seconds() > 0.5,
          round(idle.idle_seconds(), 2))


def s_mouse_unticked():
    ui._DefaultPrefs.idle_activity = {'KEYS', 'EDITS'}
    poke({'MOUSE'}, 1.5)


def s_check_mouse_unticked():
    check("with Mouse Movement unticked, moving the mouse does not count", idle.idle_seconds() > 0.5,
          round(idle.idle_seconds(), 2))


def s_edits_unticked():
    ui._DefaultPrefs.idle_activity = {'KEYS'}
    idle.touch()
    s_edits_unticked.start = time.perf_counter()


def s_frame_change():
    bpy.context.scene.frame_set(ORIGINAL)  # a frame change: counts only with Edits ticked


def s_check_edits_unticked():
    check("with Edits unticked, a frame change does not count as working",
          idle.idle_seconds() >= time.perf_counter() - s_edits_unticked.start - 0.05,
          round(idle.idle_seconds(), 2))
    ui._DefaultPrefs.idle_activity = {'KEYS', 'EDITS', 'MOUSE'}


def s_check_waiting():
    check("panel status says it is waiting while input happens", idle.status.startswith("Waiting"), idle.status)


def s_finish():
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2)
    bpy.ops.wm.quit_blender()


STEPS = [
    (1.0, setup), (2.0, s_extend_and_review), (0.3, s_check_review_started),
    (3.0, s_check_idle_in_cache_mode),
    (0.1, s_activity), (0.5, s_check_waiting),
    (1.0, s_mouse_default), (1.0, s_check_mouse_default),
    (2.0, s_other_apps_default), (1.0, s_check_other_apps_default),
    (1.0, s_mouse_unticked), (1.0, s_check_mouse_unticked),
    (1.0, s_edits_unticked), (0.3, s_frame_change), (0.3, s_check_edits_unticked),
    (1.0, s_finish),
]


def runner():
    if not STEPS:
        return None
    _delay, func = STEPS.pop(0)
    try:
        func()
    except Exception as ex:
        check(f"{func.__name__} raised", False, repr(ex))
    return STEPS[0][0] if STEPS else None


bpy.app.timers.register(runner, first_interval=2.0)
