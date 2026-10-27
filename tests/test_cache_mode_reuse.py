# Cache Mode must reuse an existing cache, never re-cache it. Run:
#   blender --factory-startup --enable-event-simulate --python test_cache_mode_reuse.py -- <addon_parent_dir> <out_json>
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
cache = store.frame_cache
results = []


def check(name, ok, detail=None):
    results.append({"test": name, "ok": bool(ok), "detail": detail})


def window():
    return bpy.context.window_manager.windows[0]


def main_view():
    return max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)


def second_view():
    views = sorted((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return views[0]


def region_of(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def call(op, area, **kwargs):
    with bpy.context.temp_override(window=window(), area=area, region=region_of(area)):
        return op('INVOKE_DEFAULT', **kwargs)


def esc():
    region = region_of(main_view())
    for value in ('PRESS', 'RELEASE'):
        window().event_simulate(type='ESC', value=value, x=region.x + 50, y=region.y + 50)


def count():
    return cache.stats()["frames"]


def setup():
    scene = bpy.context.scene
    cube = bpy.data.objects["Cube"]
    cube.location.x = -3
    cube.keyframe_insert("location", index=0, frame=1)
    cube.location.x = 3
    cube.keyframe_insert("location", index=0, frame=48)
    scene.frame_start, scene.frame_end = 1, 48
    scene.cache_play.preview_size = 'QUARTER'
    main_view().spaces.active.region_3d.view_perspective = 'CAMERA'
    # A second, smaller 3D Viewport with different shading (like a layout with two views).
    outliner = next(a for a in window().screen.areas if a.type == 'OUTLINER')
    outliner.type = 'VIEW_3D'
    second_view().spaces.active.shading.type = 'WIREFRAME'
    scene.frame_set(10)


def s_fill():
    call(bpy.ops.cacheplay.preview, main_view(), fill_only=True)


def s_cache_mode_other_view():
    s_cache_mode_other_view.before = (count(), cache.generation)
    call(bpy.ops.cacheplay.cache_mode, second_view())


def s_check_other_view():
    st = player.state
    check("Cache Mode from another viewport reuses the cache",
          st.cache_mode and not st.filling and (count(), cache.generation) == s_cache_mode_other_view.before,
          {"before": s_cache_mode_other_view.before, "after": (count(), cache.generation), "filling": st.filling})
    esc()


def s_cache_mode_from_timeline():
    s_cache_mode_from_timeline.before = cache.generation
    timeline = next(a for a in window().screen.areas if a.ui_type == 'TIMELINE')
    call(bpy.ops.cacheplay.cache_mode, timeline)


def s_check_timeline():
    st = player.state
    check("Cache Mode from the Timeline header uses the cache's viewport",
          st.cache_mode and st.area_ptr == main_view().as_pointer()
          and cache.generation == s_cache_mode_from_timeline.before,
          {"area_is_main": st.area_ptr == main_view().as_pointer(), "generation_same":
           cache.generation == s_cache_mode_from_timeline.before})
    esc()


def s_free_view():
    s_free_view.before = (count(), cache.generation)
    main_view().spaces.active.region_3d.view_perspective = 'PERSP'
    call(bpy.ops.cacheplay.cache_mode, main_view())


def s_check_free_view():
    check("Cache Mode from a free view switches to camera and reuses the cache",
          player.state.cache_mode and not player.state.filling
          and (count(), cache.generation) == s_free_view.before
          and main_view().spaces.active.region_3d.view_perspective == 'CAMERA',
          {"before": s_free_view.before, "after": (count(), cache.generation)})
    esc()
    main_view().spaces.active.region_3d.view_perspective = 'CAMERA'


def s_partial():
    # 48 frames cached; the range grows to 96, so half of it is missing.
    bpy.context.scene.frame_end = 96


def s_partial_cache_mode():
    s_partial_cache_mode.before = (count(), cache.generation)
    call(bpy.ops.cacheplay.cache_mode, main_view())


def s_check_partial():
    st = player.state
    check("partial cache: Cache Mode starts at once without caching more",
          s_partial_cache_mode.before[0] == 48 and st.cache_mode and not st.filling
          and (count(), cache.generation) == s_partial_cache_mode.before,
          {"before": s_partial_cache_mode.before, "after": (count(), cache.generation), "filling": st.filling})
    check("only cached frames are scrubbed", len(st.frames) == s_partial_cache_mode.before[0], len(st.frames))
    esc()


def s_empty():
    bpy.context.scene.frame_end = 48
    bpy.ops.cacheplay.clear()
    call(bpy.ops.cacheplay.cache_mode, main_view())


def s_check_empty():
    check("empty cache: Cache Mode caches first, then reviews",
          player.state.cache_mode and count() == 48, {"cache_mode": player.state.cache_mode, "frames": count()})
    esc()


def s_finish():
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2)
    bpy.ops.wm.quit_blender()


STEPS = [
    (1.0, setup), (0.5, s_fill),
    (3.0, s_cache_mode_other_view), (0.5, s_check_other_view),
    (0.5, s_cache_mode_from_timeline), (0.5, s_check_timeline),
    (0.5, s_free_view), (0.5, s_check_free_view),
    (0.5, s_partial), (0.3, s_partial_cache_mode), (0.5, s_check_partial),
    (0.5, s_empty), (3.0, s_check_empty),
    (0.5, s_finish),
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
