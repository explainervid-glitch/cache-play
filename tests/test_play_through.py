# Cache & Play plays cached frames at once and caches each missing stretch when playback
# reaches it; stopping a cache run leaves Blender's playhead on the last cached frame. Run:
#   blender --factory-startup --enable-event-simulate --python test_play_through.py -- <addon_parent_dir> <out_json>
import sys, json, time, bpy
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0]); OUT = argv[1]
import cache_play; cache_play.register()
ui = sys.modules["cache_play.ui"]; player = sys.modules["cache_play.player"]; store = sys.modules["cache_play.store"]
log = {"events": []}
results = []
def check(name, ok, detail=None):
    results.append({"test": name, "ok": bool(ok), "detail": detail})
st = player.state
def win(): return bpy.context.window_manager.windows[0]
def view(): return max((a for a in win().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
def region(a): return next(r for r in a.regions if r.type == 'WINDOW')
def key(t):
    r = region(view())
    for v in ("PRESS", "RELEASE"):
        win().event_simulate(type=t, value=v, x=r.x + 300, y=r.y + 300)
def run(op, **kw):
    with bpy.context.temp_override(window=win(), area=view(), region=region(view())):
        return list(op('INVOKE_DEFAULT', **kw))
def cached():
    return sorted(store.frame_cache.snapshot()[1])
def wait_until(cond, timeout=30):
    t = time.perf_counter()
    while not cond():
        if time.perf_counter() - t > timeout:
            log["events"].append("TIMEOUT"); return
        yield 0.02

def main():
    key("ESC"); yield 0.5
    scene = bpy.context.scene
    cube = bpy.data.objects["Cube"]
    cube.location.x = -3; cube.keyframe_insert("location", index=0, frame=1)
    cube.location.x = 3; cube.keyframe_insert("location", index=0, frame=400)
    scene.cache_play.preview_size = 'QUARTER'
    # 1. Cache Only from 30, stop with Esc: playhead stays on the last cached frame.
    scene.frame_start, scene.frame_end = 1, 400
    scene.frame_set(30)
    run(bpy.ops.cacheplay.preview, fill_only=True)
    yield 0.6
    key("ESC"); yield 0.5
    c = cached()
    log["stop_keeps_last"] = {"first": c[0], "last": c[-1], "count": len(c), "scene": scene.frame_current,
                              "ok": scene.frame_current == c[-1] and c[0] == 30}
    # 2. Cached 1-56, playhead 10: plays at once from 10, caches 57-120 when it reaches 56.
    bpy.ops.cacheplay.clear()
    scene.frame_end = 56; scene.frame_set(1)
    run(bpy.ops.cacheplay.preview, fill_only=True)
    yield from wait_until(lambda: not st.filling)
    yield 0.3
    scene.frame_end = 120; scene.frame_set(10)
    run(bpy.ops.cacheplay.preview)
    yield 0.05
    log["starts_playing"] = {"active": st.active, "filling": st.filling, "frame": st.frame,
                             "cached_before": len(cached())}
    yield from wait_until(lambda: st.filling)
    log["fill_starts"] = {"cached": len(cached()), "last_cached": cached()[-1], "scene": scene.frame_current}
    yield from wait_until(lambda: not st.filling)
    yield 0.1
    c = cached()
    log["after_fill"] = {"active": st.active, "frame": st.frame, "count": len(c), "last": c[-1]}
    yield 0.5
    log["playing_new_frames"] = {"active": st.active, "frame": st.frame}
    key("ESC"); yield 0.4
    log["stop_play"] = {"active": st.active, "scene": scene.frame_current}
    # 3. Space-started run stopped with Space during a gap fill: playhead on last cached frame.
    ui._DefaultPrefs.space_plays_cache = True
    scene.frame_end = 300; scene.frame_set(100)
    run(bpy.ops.cacheplay.space_play)
    yield from wait_until(lambda: st.filling)
    yield 0.5
    key("SPACE"); yield 0.4
    c = cached()
    log["space_stop_gap"] = {"filling": st.filling, "active": st.active, "scene": scene.frame_current,
                             "last": c[-1], "ok": scene.frame_current == c[-1] and not st.filling}
    check("stopping Cache Only keeps the playhead on the last cached frame", log["stop_keeps_last"]["ok"], log["stop_keeps_last"])
    s = log["starts_playing"]
    check("partial cache: Cache & Play plays at once from the playhead", s["active"] and not s["filling"] and 10 <= s["frame"] <= 12, s)
    a = log["after_fill"]
    check("reaching the last cached frame caches the rest of the range", a["count"] == 120 and a["last"] == 120, a)
    check("playback resumes at the first newly cached frame", a["active"] and 57 <= a["frame"] <= 70, a)
    check("stopping playback leaves the playhead on the shown frame", not log["stop_play"]["active"] and log["stop_play"]["scene"] == log["playing_new_frames"]["frame"], [log["playing_new_frames"], log["stop_play"]])
    check("Space during a gap fill stops and keeps the playhead on the last cached frame", log["space_stop_gap"]["ok"], log["space_stop_gap"])
    check("no timeouts", not log["events"], log["events"])
    json.dump(results, open(OUT, "w"), indent=2)
    bpy.ops.wm.quit_blender()

gen = main()
def runner():
    try:
        return next(gen)
    except StopIteration:
        return None
    except Exception:
        import traceback; log["error"] = traceback.format_exc(); json.dump(log, open(OUT, "w"), indent=1)
        bpy.ops.wm.quit_blender(); return None
bpy.app.timers.register(runner, first_interval=2.0)
