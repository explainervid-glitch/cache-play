# Cache When Idle test. Run:
#   blender --factory-startup --python test_idle.py -- <addon_parent_dir> <out_json>
# "The user is working in Blender" is faked by replacing the input check, so real mouse or
# keyboard use during the run does not matter.
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
ui = sys.modules["cache_play.ui"]
ui._DefaultPrefs.idle_delay = 0.5
cache = store.frame_cache
idle = sys.modules["cache_play.idle"]
results = []
probe = {"frames_seen": set(), "moving": False}
idle._interaction_probe = lambda: {'KEYS'} if probe["moving"] else set()  # "moving" = the user is working
RANGE_END = 600


def check(name, ok, detail=None):
    results.append({"test": name, "ok": bool(ok), "detail": detail})


def count():
    return cache.stats()["frames"]


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def watch_frame():
    """Record every scene frame seen between idle steps; it must never leave 10."""
    probe["frames_seen"].add(bpy.context.scene.frame_current)
    return 0.02


def setup():
    scene = bpy.context.scene
    cube = bpy.data.objects["Cube"]
    cube.location.x = -3
    cube.keyframe_insert("location", index=0, frame=1)
    cube.location.x = 3
    cube.keyframe_insert("location", index=0, frame=48)
    scene.frame_start, scene.frame_end = 1, RANGE_END
    scene.cache_play.preview_size = 'QUARTER'
    _win, area, _region = view3d()
    area.spaces.active.region_3d.view_perspective = 'CAMERA'
    scene.frame_set(10)
    scene.cache_play.cache_when_idle = True
    bpy.app.timers.register(watch_frame, first_interval=0.0)


def s_first_check():
    check("idle caching starts by itself (range not finished yet)", 0 < count() < RANGE_END, count())
    check("playhead never visibly moves", probe["frames_seen"] == {10}, sorted(probe["frames_seen"]))
    check("caches from the playhead forward", cache.has(10) and cache.has(11), None)


def s_start_moving():
    probe["moving"] = True
    s_start_moving.before = None


def s_measure_moving():
    s_start_moving.before = count()


def s_check_moving():
    after = count()
    check("working in Blender pauses idle caching", after - s_start_moving.before <= 1,
          {"before": s_start_moving.before, "after": after})
    probe["moving"] = False


def s_wait_complete():
    """Poll until the range is cached (at most 60 s)."""
    s_wait_complete.start = getattr(s_wait_complete, "start", time.perf_counter())
    if count() < RANGE_END and time.perf_counter() - s_wait_complete.start < 60.0:
        STEPS.insert(0, (0.5, s_wait_complete))


def s_check_complete():
    check("idle caching finishes the range", count() == RANGE_END, count())
    time.sleep(0.5)
    files = cache.stats()["disk_frames"]
    check("idle frames are saved to disk", files == RANGE_END, files)


def s_edit():
    s_edit.generation = cache.generation
    cube = bpy.data.objects["Cube"]
    fcurve = cube.animation_data.action.layers[0].strips[0].channelbags[0].fcurves[0]
    fcurve.keyframe_points[-1].co.y += 1.0
    fcurve.update()


def s_check_edit_cleared():
    check("edit clears the cache, idle caching rebuilds it",
          cache.generation > s_edit.generation and count() > 0,
          {"generation": [s_edit.generation, cache.generation], "frames": count()})


def s_free_view():
    _win, area, _region = view3d()
    area.spaces.active.region_3d.view_perspective = 'PERSP'
    cache.clear()


def s_check_free_view():
    check("idle caching also works while orbiting in a free view (shot through the camera)",
          count() > 0, count())
    _win, area, _region = view3d()
    check("idle caching leaves the viewport in free view",
          area.spaces.active.region_3d.view_perspective == 'PERSP')
    check("playhead still never moved", probe["frames_seen"] == {10}, sorted(probe["frames_seen"]))


def s_finish():
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2)
    bpy.ops.wm.quit_blender()


STEPS = [
    (1.0, setup), (3.0, s_first_check),
    (0.0, s_start_moving), (0.5, s_measure_moving), (2.0, s_check_moving),
    (0.5, s_wait_complete), (0.1, s_check_complete),
    (0.2, s_edit), (2.5, s_check_edit_cleared),
    (4.0, s_free_view), (2.5, s_check_free_view),
    (0.2, s_finish),
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
