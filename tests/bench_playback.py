# Automated Cache Play benchmark. Run:
#   blender --factory-startup --python bench_playback.py -- <addon_parent_dir> <out_json>
import json
import os
import math
import statistics
import sys
import time

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
ADDON_PARENT, OUT = argv[0], argv[1]
sys.path.insert(0, ADDON_PARENT)
import cache_play  # noqa: E402

cache_play.register()
player = sys.modules["cache_play.player"]
store = sys.modules["cache_play.store"]
ui = sys.modules["cache_play.ui"]
disk = sys.modules["cache_play.disk"]

report = {"blender": bpy.app.version_string}
PLAY_SECONDS = 10.0


def build_scene():
    scene = bpy.context.scene
    for ob in list(bpy.data.objects):
        if ob.type == 'MESH':
            bpy.data.objects.remove(ob)
    bpy.ops.mesh.primitive_monkey_add()
    tmp = bpy.context.active_object
    mesh = tmp.data
    bpy.data.objects.remove(tmp)
    for ix in range(6):
        for iy in range(4):
            ob = bpy.data.objects.new(f"M_{ix}_{iy}", mesh)
            scene.collection.objects.link(ob)
            ob.location = (ix * 2.5 - 6.25, iy * 2.5 - 3.75, 0)
            ob.modifiers.new("Subsurf", 'SUBSURF').levels = 3
            wave = ob.modifiers.new("Wave", 'WAVE')
            wave.height, wave.width, wave.speed = 0.3, 1.0, 0.15
            ob.keyframe_insert("rotation_euler", index=2, frame=1)
            ob.rotation_euler[2] = math.tau
            ob.keyframe_insert("rotation_euler", index=2, frame=120)
    cam = scene.camera
    cam.location = (0, -18, 11)
    cam.rotation_euler = (math.radians(58), 0, 0)
    cam.keyframe_insert("location", frame=1)
    cam.location = (4, -16, 9)
    cam.keyframe_insert("location", frame=120)
    scene.frame_start, scene.frame_end = 1, 120
    scene.render.fps, scene.render.fps_base = 24, 1.0
    scene.frame_set(1)


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def gaps(times):
    d = [(b - a) * 1000 for a, b in zip(times, times[1:])]
    return {
        "mean_ms": round(statistics.fmean(d), 2),
        "max_ms": round(max(d), 1),
        "over_10ms": sum(1 for x in d if x > 10),
        "seconds": round(times[-1] - times[0], 2),
    }


def measure_eval():
    scene = bpy.context.scene
    t = time.perf_counter()
    for f in range(1, 25):
        scene.frame_set(f)
    scene.frame_set(1)
    return round((time.perf_counter() - t) / 24 * 1000, 1)


# Each step is a timer callback; returning a float re-runs it after that many seconds.
steps = []
idle = {"t": []}


def step_setup():
    build_scene()
    _win, area, _region = view3d()
    space = area.spaces.active
    space.region_3d.view_perspective = 'CAMERA'
    space.shading.type = 'SOLID'
    report["eval_ms_per_frame"] = measure_eval()
    report["region"] = [area.width, area.height]
    report["gpu"] = __import__("gpu").platform.backend_type_get()
    return None


def idle_probe():
    now = time.perf_counter()
    idle["t"].append(now)
    if now - idle["t"][0] < 3.0:
        return 0.0
    report["idle_main_loop"] = gaps(idle["t"])
    return None


# (preview size, RAM limit GB, disk cache on). FULL with 0.25 GB holds 31 of 120 frames,
# so most of the range streams from disk while playing.
runs = [("HALF", 4.0, True), ("FULL", 0.25, True), ("HALF", 4.0, False)]
run_state = {"i": 0, "phase": "start", "t": 0.0, "ticks": []}


def run_step():
    rs = run_state
    if rs["i"] >= len(runs):
        # Cost of one idle-caching step (jump to frame, capture, jump back): the longest the
        # UI can stall when the user comes back.
        idle = sys.modules["cache_play.idle"]
        scene = bpy.context.scene
        scene.cache_play.preview_size = 'HALF'
        ui._DefaultPrefs.use_disk_cache = True
        ui.configure_cache()
        store.frame_cache.clear()
        win, area, region = view3d()
        space = area.spaces.active
        steps_ms = []
        with bpy.context.temp_override(window=win, area=area, region=region):
            for _ in range(6):
                t = time.perf_counter()
                idle._step(win, area, region, space)
                steps_ms.append(round((time.perf_counter() - t) * 1000, 1))
        report["idle_step_ms"] = steps_ms
        with open(OUT, "w") as fh:
            json.dump(report, fh, indent=2)
        bpy.ops.wm.quit_blender()
        return None
    size, ram_gb, use_disk = runs[rs["i"]]
    name = f"{size}_ram{ram_gb}_{'disk' if use_disk else 'nodisk'}"
    state = player.state
    if rs["phase"] == "start":
        scene = bpy.context.scene
        scene.cache_play.preview_size = size
        ui._DefaultPrefs.memory_limit_gb = ram_gb
        ui._DefaultPrefs.use_disk_cache = use_disk
        scene.cache_play.loop = True
        scene.frame_set(1)
        win, area, region = view3d()
        rs["t"] = time.perf_counter()
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.ops.cacheplay.preview('INVOKE_DEFAULT')
        rs["phase"] = "filling"
        return 0.05
    if rs["phase"] == "filling":
        if state.filling:
            return 0.05
        cache = store.frame_cache
        time.sleep(0.5)  # let the writer finish the last frames
        stats = cache.stats()
        files = len([f for f in os.listdir(cache.folder) if f.endswith(".raw")]) if cache.folder else 0
        report[name] = {"fill_s": round(time.perf_counter() - rs["t"], 2),
                        "size": [cache.width, cache.height], "stats": stats, "files_on_disk": files,
                        "ram_capacity": cache.capacity}
        rs["sync0"] = cache.sync_reads
        rs["phase"] = "settle"
        rs["t"] = time.perf_counter()
        return 1.0
    if rs["phase"] in {"settle", "playing"} and state.stats is None:
        report[name]["error"] = "playback stopped early"
        rs["i"] += 1
        rs["phase"] = "start"
        return 0.5
    if rs["phase"] == "settle":
        # Start the measured window after 1 s of playback.
        state.stats.errors.clear()
        state.stats.latency.clear()
        state.stats.dropped = 0
        state.stats.shown = 0
        state.stats.restart()
        rs["ticks"] = []
        orig = type(state).advance

        def advance(self, now, loop, _orig=orig, _ticks=rs["ticks"]):
            _ticks.append(now)
            return _orig(self, now, loop)
        state.advance = advance.__get__(state)
        rs["t"] = time.perf_counter()
        rs["phase"] = "playing"
        return PLAY_SECONDS
    if rs["phase"] == "playing":
        st = state.stats
        errs = st.errors
        report[name].update({
            "active": state.active,
            "seconds": round(time.perf_counter() - rs["t"], 2),
            "shown": st.shown,
            "dropped": st.dropped,
            "jitter_ms": round(statistics.pstdev(errs) * 1000, 2) if errs else None,
            "worst_ms": round(max(abs(e) for e in errs) * 1000, 1) if errs else None,
            "within_2ms_pct": round(100 * sum(1 for e in errs if abs(e) < 0.002) / len(errs), 1) if errs else None,
            "redraw_ms": round(statistics.fmean(st.latency) * 1000, 2) if st.latency else None,
            "redraw_max_ms": round(max(st.latency) * 1000, 1) if st.latency else None,
            "ticks": gaps(rs["ticks"]) if len(rs["ticks"]) > 2 else None,
            "late_reads": store.frame_cache.sync_reads - rs["sync0"],
            "read_MB_s": round(store.frame_cache.read_mb_s),
        })
        del state.advance
        # Stop playback the way a key press would.
        state.stop()
        store.frame_cache.clear()
        rs["i"] += 1
        rs["phase"] = "start"
        return 0.5
    return None


def chain():
    step_setup()
    bpy.app.timers.register(idle_probe, first_interval=1.0)
    bpy.app.timers.register(run_step, first_interval=5.0)
    return None


bpy.app.timers.register(chain, first_interval=2.0)
