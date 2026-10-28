# Kept caches across Blender sessions ("name tags"). One phase per Blender start; run them
# in order with tests/run_persist.sh, or by hand:
#   blender --factory-startup --enable-event-simulate [file.blend] --python test_persist.py --
#       <addon_parent_dir> <out_json> <phase> <blend_path> <cache_dir>
# Phases: make, reopen, reopen_unsaved, edit_save, after_save, touched, save_as, reopen_as, clear_all.
import json
import os
import sys
import time

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
ADDON_PARENT, OUT, PHASE, BLEND, CACHE_DIR = argv[:5]
sys.path.insert(0, ADDON_PARENT)
import cache_play  # noqa: E402

cache_play.register()
store = sys.modules["cache_play.store"]
player = sys.modules["cache_play.player"]
ui = sys.modules["cache_play.ui"]
disk = sys.modules["cache_play.disk"]
ui._DefaultPrefs.cache_dir = CACHE_DIR
cache = store.frame_cache
results = []
FRAMES = 24
FRAME_BYTES = 480 * 270 * 4  # Quarter of 1920x1080


def check(name, ok, detail=None):
    results.append({"test": f"{PHASE}: {name}", "ok": bool(ok), "detail": detail})


def window():
    return bpy.context.window_manager.windows[0]


def view():
    return max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)


def call(op, **kwargs):
    area = view()
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window(), area=area, region=region):
        return op('INVOKE_DEFAULT', **kwargs)


def kept():
    return sorted(os.path.basename(f) for f, _info in disk.kept_caches(CACHE_DIR))


def cached():
    return len(cache.snapshot()[1])


def wait(cond, timeout=30.0):
    start = time.perf_counter()
    while not cond() and time.perf_counter() - start < timeout:
        yield 0.05


def fill():
    call(bpy.ops.cacheplay.preview, fill_only=True)
    yield from wait(lambda: not player.state.filling)
    yield from wait(lambda: not cache.pending)  # all written to disk
    yield 0.3


def move_key():
    """A real edit: the cube's last key moves."""
    cube = bpy.data.objects["Cube"]
    fcurve = cube.animation_data.action.fcurves[0] if hasattr(cube.animation_data.action, "fcurves") else None
    if fcurve is None:
        cube.location.z += 1.0
        cube.keyframe_insert("location", index=2, frame=FRAMES)
    else:
        fcurve.keyframe_points[-1].co.y += 1.0
        fcurve.update()
    cube.update_tag()


def main():
    for value in ('PRESS', 'RELEASE'):  # the splash screen
        window().event_simulate(type='ESC', value=value, x=300, y=300)
    yield 1.0
    scene = bpy.context.scene

    if PHASE == "make":
        cube = bpy.data.objects["Cube"]
        cube.location.x = -3
        cube.keyframe_insert("location", index=0, frame=1)
        cube.location.x = 3
        cube.keyframe_insert("location", index=0, frame=FRAMES)
        scene.frame_start, scene.frame_end = 1, FRAMES
        scene.cache_play.preview_size = 'QUARTER'
        view().spaces.active.region_3d.view_perspective = 'CAMERA'
        bpy.ops.wm.save_as_mainfile(filepath=BLEND)
        yield 0.5
        yield from fill()
        check("cached all frames into one kept folder", cached() == FRAMES and len(kept()) == 1,
              {"cached": cached(), "kept": kept()})

    elif PHASE == "reopen":
        yield from wait(lambda: cache.key is not None, 5.0)
        check("reopening finds the kept cache", cached() == FRAMES and len(cache.disk) == FRAMES,
              {"cached": cached(), "kept": kept()})
        call(bpy.ops.cacheplay.preview)
        yield 0.2
        check("Cache & Play plays at once, caches nothing", player.state.active and not player.state.filling,
              {"active": player.state.active, "filling": player.state.filling})
        for value in ('PRESS', 'RELEASE'):
            window().event_simulate(type='ESC', value=value, x=300, y=300)
        yield 0.5
        folder_before = kept()
        move_key()
        yield 0.5
        check("an unsaved edit clears the cache but keeps the folder", cached() == 0 and kept() == folder_before,
              {"cached": cached(), "kept": kept()})
        # Quit without saving.

    elif PHASE == "reopen_unsaved":
        yield from wait(lambda: cache.key is not None, 5.0)
        check("the unsaved edit is gone, so the kept cache matches again", cached() == FRAMES,
              {"cached": cached(), "kept": kept()})

    elif PHASE == "edit_save":
        yield from wait(lambda: cache.key is not None, 5.0)
        old = kept()
        move_key()
        yield 0.5
        bpy.ops.wm.save_mainfile()
        yield 0.3
        # Room for one cache only: making the new one deletes the old one.
        ui._DefaultPrefs.disk_limit_gb = (FRAMES * FRAME_BYTES * 1.5) / 2 ** 30
        yield from fill()
        yield 1.0
        now = kept()
        check("after edit + save, caching makes a new kept folder", cached() == FRAMES and now != old,
              {"old": old, "now": now})
        check("the disk limit deletes the least recently used cache first", len(now) == 1, now)

    elif PHASE == "after_save":
        yield from wait(lambda: cache.key is not None, 5.0)
        check("reopening after save finds the new cache", cached() == FRAMES, {"cached": cached(), "kept": kept()})

    elif PHASE == "touched":
        yield 1.0
        check("a .blend changed elsewhere (new date) is not trusted", cache.key is None and cached() == 0,
              {"key": cache.key is not None, "cached": cached()})
        yield from fill()
        check("caching again replaces the old pictures", cached() == FRAMES and len(kept()) == 1, kept())

    elif PHASE == "save_as":
        yield from wait(lambda: cache.key is not None, 5.0)
        before = cached()
        bpy.ops.wm.save_as_mainfile(filepath=BLEND.replace(".blend", "_as.blend"))
        yield 0.3
        check("Save As keeps the cache in this session", before == FRAMES and cached() == FRAMES,
              {"before": before, "after": cached()})

    elif PHASE == "reopen_as":
        yield from wait(lambda: cache.key is not None, 5.0)
        check("the file saved under a new name finds the cache", cached() == FRAMES,
              {"file": bpy.data.filepath, "cached": cached(), "kept": kept()})

    elif PHASE == "clear_all":
        yield from wait(lambda: cache.key is not None, 5.0)
        ui.configure_cache()
        cache.clear_all()
        yield 1.5
        check("Clear All Caches deletes every kept cache", kept() == [] and cached() == 0,
              {"kept": kept(), "left": os.listdir(disk.caches_dir(CACHE_DIR))})

    with open(OUT, "w") as handle:
        json.dump(results, handle, indent=2)
    bpy.ops.wm.quit_blender()


_steps = main()


def runner():
    try:
        return next(_steps)
    except StopIteration:
        return None
    except Exception:
        import traceback
        check("raised", False, traceback.format_exc())
        with open(OUT, "w") as handle:
            json.dump(results, handle, indent=2)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(runner, first_interval=1.5)
