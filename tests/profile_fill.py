# Where does caching time go? Times each step of caching one frame on the heavy benchmark
# scene, then the real Cache Only operator end to end. Run (needs a window, not -b):
#   blender --factory-startup [--gpu-backend vulkan] --python tests/profile_fill.py -- <addon_parent_dir> <out_json>
import json
import math
import statistics
import sys
import time

import bpy
import gpu
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
ADDON_PARENT, OUT = argv[0], argv[1]
sys.path.insert(0, ADDON_PARENT)
import cache_play  # noqa: E402

cache_play.register()
capture = sys.modules["cache_play.capture"]
player = sys.modules["cache_play.player"]
store = sys.modules["cache_play.store"]

report = {"blender": bpy.app.version_string, "backend": gpu.platform.backend_type_get()}
FRAMES = 48


def build_scene():
    """Same scene as bench_playback.py: 24 subdivided, wave-deformed monkeys."""
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
    scene.frame_start, scene.frame_end = 1, FRAMES
    scene.render.fps, scene.render.fps_base = 24, 1.0
    scene.frame_set(1)


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def summary(values):
    ms = [v * 1000 for v in values]
    return {"mean": round(statistics.fmean(ms), 1), "max": round(max(ms), 1)}


def profile_steps(preset):
    """Each step of caching one frame, with no screen redraws in between."""
    win, area, region = view3d()
    scene = bpy.context.scene
    space = area.spaces.active
    cap = capture.Capturer(scene, bpy.context.view_layer, region, space, preset)
    steps = {k: [] for k in ("frame_set", "draw", "gpu_wait", "read", "pack", "total")}
    for frame in range(1, FRAMES + 1):
        t0 = time.perf_counter()
        scene.frame_set(frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        t1 = time.perf_counter()
        camera = scene.camera.evaluated_get(depsgraph)
        view_matrix = camera.matrix_world.normalized().inverted()
        projection = camera.calc_matrix_camera(depsgraph, x=cap.width, y=cap.height)
        if cap.offscreen is None:
            cap.offscreen = gpu.types.GPUOffScreen(cap.width, cap.height, format='RGBA8')
        cap.offscreen.draw_view3d(scene, cap.view_layer, space, region, view_matrix, projection,
                                  do_color_management=True, draw_background=True)
        t2 = time.perf_counter()
        one = gpu.types.Buffer('UBYTE', 4)
        pixels = gpu.types.Buffer('UBYTE', cap.width * cap.height * 4)
        with cap.offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.read_color(0, 0, 1, 1, 4, 0, 'UBYTE', data=one)  # waits for the draw to finish
            t3 = time.perf_counter()
            fb.read_color(0, 0, cap.width, cap.height, 4, 0, 'UBYTE', data=pixels)
        t4 = time.perf_counter()
        store.pack_pixels(pixels)
        t5 = time.perf_counter()
        for name, value in (("frame_set", t1 - t0), ("draw", t2 - t1), ("gpu_wait", t3 - t2),
                            ("read", t4 - t3), ("pack", t5 - t4), ("total", t5 - t0)):
            steps[name].append(value)
    cap.free()
    scene.frame_set(1)
    return {"size": f"{cap.width}x{cap.height}", **{k: summary(v) for k, v in steps.items()}}


def profile_overlapped(preset):
    """The caching loop as ops._fill_step runs it (draw, evaluate the next frame, read back)."""
    win, area, region = view3d()
    scene = bpy.context.scene
    cap = capture.Capturer(scene, bpy.context.view_layer, region, area.spaces.active, preset)
    drawn = None
    t = time.perf_counter()
    for frame in range(1, FRAMES + 1):
        scene.frame_set(frame)
        if drawn is not None:
            cap.read()
        cap.draw(bpy.context.evaluated_depsgraph_get())
        drawn = frame
    cap.read()
    seconds = time.perf_counter() - t
    cap.free()
    scene.frame_set(1)
    return {"per_frame_ms": round(seconds / FRAMES * 1000, 1)}


def check_packing():
    """The graphics card's packing (capture._stamp_alpha) must equal store.pack_pixels bit for bit."""
    win, area, region = view3d()
    scene = bpy.context.scene
    space = area.spaces.active
    results = {}
    for preset in ('FULL', 'QUARTER'):
        scene.frame_set(7)
        cap = capture.Capturer(scene, bpy.context.view_layer, region, space, preset)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        cap.draw(depsgraph)
        packed = cap.read().view(np.uint32)
        # Same picture without the alpha stamp, packed on the CPU.
        camera = scene.camera.evaluated_get(depsgraph)
        cap.offscreen.draw_view3d(
            scene, cap.view_layer, space, region, camera.matrix_world.normalized().inverted(),
            camera.calc_matrix_camera(depsgraph, x=cap.width, y=cap.height),
            do_color_management=True, draw_background=True)
        raw = gpu.types.Buffer('UBYTE', cap.width * cap.height * 4)
        with cap.offscreen.bind():
            gpu.state.active_framebuffer_get().read_color(0, 0, cap.width, cap.height, 4, 0, 'UBYTE', data=raw)
        reference = store.pack_pixels(raw).view(np.uint32)
        cap.free()
        results[preset] = {
            "identical": bool(np.array_equal(packed, reference)),
            "differing_pixels": int(np.count_nonzero(packed != reference)),
            "top_byte_ok": bool(np.all((packed >> 24) == 0x40)),
            "not_blank": int(len(np.unique(packed & 0xFFFFFF))) > 10,
        }
    scene.frame_set(1)
    return results


def profile_redraw():
    """One full window redraw (what Blender does between our timer ticks)."""
    win, area, region = view3d()
    with bpy.context.temp_override(window=win, area=area, region=region):
        t = time.perf_counter()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=10)
        whole = (time.perf_counter() - t) / 10
        t = time.perf_counter()
        bpy.ops.wm.redraw_timer(type='DRAW', iterations=10)
        viewport = (time.perf_counter() - t) / 10
    return {"window_ms": round(whole * 1000, 1), "viewport_region_ms": round(viewport * 1000, 1)}


_op = {}


def start_operator(preset):
    store.frame_cache.clear()
    scene = bpy.context.scene
    scene.cache_play.preview_size = preset
    win, area, region = view3d()
    _op["preset"] = preset
    _op["start"] = time.perf_counter()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.cacheplay.preview('INVOKE_DEFAULT', fill_only=True)


def finish_operator():
    if player.state.filling:
        return False
    seconds = time.perf_counter() - _op["start"]
    report.setdefault("operator", {})[_op["preset"]] = {
        "seconds": round(seconds, 2), "per_frame_ms": round(seconds / FRAMES * 1000, 1)}
    return True


def close_splash():
    win, area, region = view3d()
    win.event_simulate(type='ESC', value='PRESS', x=region.x + 100, y=region.y + 100)
    win.event_simulate(type='ESC', value='RELEASE', x=region.x + 100, y=region.y + 100)


def run():
    close_splash()
    build_scene()
    win, area, region = view3d()
    area.spaces.active.region_3d.view_perspective = 'CAMERA'
    yield 1.0
    profile_steps('HALF')  # warm-up: shaders, first evaluation
    report["packing"] = check_packing()
    report["steps"] = {p: profile_steps(p) for p in ('FULL', 'HALF', 'QUARTER')}
    report["overlapped"] = {p: profile_overlapped(p) for p in ('FULL', 'HALF', 'QUARTER')}
    report["redraw"] = profile_redraw()
    yield 0.5
    prefs = sys.modules["cache_play.ui"]._DefaultPrefs
    for run_index in range(2):
        for preset in ('FULL', 'HALF'):
            for overlap in (True, False):
                prefs.fast_caching = overlap
                start_operator(preset)
                while not finish_operator():
                    yield 0.05
                result = report["operator"].pop(preset)
                report["operator"].setdefault(f"{preset} {'fast' if overlap else 'plain'}", []).append(
                    result["per_frame_ms"])
                yield 0.5
    prefs.fast_caching = True
    with open(OUT, "w") as f:
        json.dump(report, f, indent=1)
    bpy.ops.wm.quit_blender()


_runner = run()


def tick():
    try:
        return next(_runner)
    except StopIteration:
        return None
    except Exception:
        import traceback
        report["error"] = traceback.format_exc()
        with open(OUT, "w") as f:
            json.dump(report, f, indent=1)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=2.0)
