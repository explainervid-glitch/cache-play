# Cache & Play caches while it plays, following the scene's Sync setting; stopping a cache
# run leaves Blender's playhead on the last cached frame. Run:
#   blender --factory-startup --enable-event-simulate --python test_play_through.py -- <addon_parent_dir> <out_json>
import json
import sys
import time

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
OUT = argv[1]
import cache_play  # noqa: E402

cache_play.register()
ui = sys.modules["cache_play.ui"]
player = sys.modules["cache_play.player"]
store = sys.modules["cache_play.store"]
st = player.state
results = []


def check(name, ok, detail=None):
    results.append({"test": name, "ok": bool(ok), "detail": detail})


def win():
    return bpy.context.window_manager.windows[0]


def view():
    return max((a for a in win().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)


def region(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def key(type_):
    r = region(view())
    for value in ("PRESS", "RELEASE"):
        win().event_simulate(type=type_, value=value, x=r.x + 300, y=r.y + 300)


def run(op, **kwargs):
    with bpy.context.temp_override(window=win(), area=view(), region=region(view())):
        return list(op('INVOKE_DEFAULT', **kwargs))


def cached():
    return sorted(store.frame_cache.snapshot()[1])


def wait_until(cond, timeout=30.0):
    start = time.perf_counter()
    while not cond() and time.perf_counter() - start < timeout:
        yield 0.01


_slow = {"seconds": 0.0}


def _slow_scene(*_args):
    """Makes every frame change take a while, like a heavy scene."""
    if _slow["seconds"]:
        time.sleep(_slow["seconds"])


def fresh(sync, end, frame, slow):
    bpy.ops.cacheplay.clear()
    scene = bpy.context.scene
    scene.sync_mode = sync
    scene.frame_start, scene.frame_end = 1, end
    scene.frame_set(frame)
    _slow["seconds"] = slow


def main():
    key("ESC")
    yield 0.5
    scene = bpy.context.scene
    cube = bpy.data.objects["Cube"]
    cube.location.x = -3
    cube.keyframe_insert("location", index=0, frame=1)
    cube.location.x = 3
    cube.keyframe_insert("location", index=0, frame=400)
    scene.cache_play.preview_size = 'QUARTER'
    scene.cache_play.loop = True

    # 1. Cache Only from 30, stopped with Esc: the playhead stays on the last cached frame.
    scene.frame_start, scene.frame_end = 1, 400
    scene.frame_set(30)
    run(bpy.ops.cacheplay.preview, fill_only=True)
    yield 0.6
    key("ESC")
    yield 0.5
    c = cached()
    check("stopping Cache Only keeps the playhead on the last cached frame",
          c and c[0] == 30 and scene.frame_current == c[-1],
          {"first": c[0] if c else None, "last": c[-1] if c else None, "scene": scene.frame_current})

    bpy.app.handlers.frame_change_pre.append(_slow_scene)

    # 2. Play Every Frame: every frame is cached in order as the playhead reaches it.
    fresh('NONE', 24, 1, 0.04)
    start = time.perf_counter()
    run(bpy.ops.cacheplay.preview)
    holes, samples = [], 0
    while st.active and len(cached()) < 24 and time.perf_counter() - start < 30:
        frame, have = st.frame, set(cached())
        missing = [f for f in range(1, frame) if f not in have]
        if missing:
            holes.append((frame, missing[:3]))
        samples += 1
        yield 0.01
    took = time.perf_counter() - start
    check("Play Every Frame: no frame skipped, cached in order", not holes and samples > 5,
          {"holes": holes[:5], "samples": samples})
    check("Play Every Frame: the first pass waits for each frame (slower than real time)",
          len(cached()) == 24 and took >= 24 * 0.04, {"cached": len(cached()), "seconds": round(took, 2)})
    check("Play Every Frame: playback goes on after caching", st.active, st.active)
    key("ESC")
    yield 0.4

    # 3. Part cached (1-12), playhead on 5: plays at once, caches 13-24 when it gets there.
    fresh('NONE', 12, 1, 0.0)
    run(bpy.ops.cacheplay.preview, fill_only=True)
    yield from wait_until(lambda: not st.filling)
    yield 0.2
    scene.frame_end = 24
    scene.frame_set(5)
    _slow["seconds"] = 0.04
    run(bpy.ops.cacheplay.preview)
    yield 0.05
    check("partial cache: plays at once from the playhead", st.active and 5 <= st.frame <= 7,
          {"active": st.active, "frame": st.frame, "cached": len(cached())})
    yield from wait_until(lambda: len(cached()) == 24, 15)
    check("partial cache: the rest is cached when playback reaches it", len(cached()) == 24, len(cached()))
    key("ESC")
    yield 0.4

    # 4. Frame Dropping: real speed, skipped frames left for later loops.
    fresh('FRAME_DROP', 48, 1, 0.08)
    start = time.perf_counter()
    run(bpy.ops.cacheplay.preview)
    yield from wait_until(lambda: st.frame >= 40 or time.perf_counter() - start > 5, 6)
    first_pass = time.perf_counter() - start
    count = len(cached())
    check("Frame Dropping: the playhead keeps real speed", st.frame >= 40 and first_pass < 48 / 24 + 1.0,
          {"frame": st.frame, "seconds": round(first_pass, 2)})
    check("Frame Dropping: frames it had no time for are skipped at first", 0 < count < 40, count)
    yield from wait_until(lambda: len(cached()) == 48, 40)
    check("Frame Dropping: later loops cache the skipped frames", len(cached()) == 48,
          {"cached": len(cached()), "seconds": round(time.perf_counter() - start, 1)})
    key("ESC")
    yield 0.4

    # 5. Esc while caching: playback stops, the playhead stays on the frame shown.
    fresh('NONE', 200, 1, 0.03)
    run(bpy.ops.cacheplay.preview)
    yield 0.6
    shown = st.frame
    key("ESC")
    yield 0.4
    c = cached()
    check("Esc while caching stops on the frame shown, the last one cached",
          not st.active and scene.frame_current in (shown, c[-1]) and scene.frame_current == c[-1],
          {"shown": shown, "scene": scene.frame_current, "last_cached": c[-1] if c else None})

    # 6. Started with Space: Space stops it the same way.
    ui._DefaultPrefs.space_plays_cache = True
    fresh('NONE', 200, 100, 0.03)
    run(bpy.ops.cacheplay.space_play)
    yield 0.6
    key("SPACE")
    yield 0.4
    c = cached()
    check("Space while caching stops on the last cached frame",
          not st.active and c and scene.frame_current == c[-1],
          {"active": st.active, "scene": scene.frame_current, "last_cached": c[-1] if c else None})

    _slow["seconds"] = 0.0
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


bpy.app.timers.register(runner, first_interval=2.0)
