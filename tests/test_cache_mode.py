# Cache Mode test. Run:
#   blender --factory-startup --enable-event-simulate --python test_cache_mode.py -- <addon_parent_dir> <out_json>
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
ORIGINAL = 10


def check(name, ok, detail=None):
    results.append({"test": name, "ok": bool(ok), "detail": detail})


def areas():
    win = bpy.context.window_manager.windows[0]
    view = max((a for a in win.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    timeline = next(a for a in win.screen.areas if a.ui_type == 'TIMELINE')
    return win, view, timeline


def window_region(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def toggle():
    win, view, _timeline = areas()
    with bpy.context.temp_override(window=win, area=view, region=window_region(view)):
        return bpy.ops.cacheplay.cache_mode('INVOKE_DEFAULT')


def at_frame(frame):
    """Window coordinates of frame in the middle of the Timeline."""
    _win, _view, timeline = areas()
    region = window_region(timeline)
    x, _y = region.view2d.view_to_region(frame, 0, clip=False)
    return region.x + x, region.y + region.height // 2


def send(type_, value, x=None, y=None, shift=False):
    win, view, _timeline = areas()
    if x is None:
        region = window_region(view)
        x, y = region.x + region.width // 2, region.y + region.height // 2
    win.event_simulate(type=type_, value=value, x=int(x), y=int(y), shift=shift)


def key(type_, shift=False):
    send(type_, 'PRESS', shift=shift)
    send(type_, 'RELEASE', shift=shift)


def scene_frame():
    return bpy.context.scene.frame_current


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
    _win, view, _timeline = areas()
    view.spaces.active.region_3d.view_perspective = 'CAMERA'
    scene.frame_set(ORIGINAL)


def s_enter():
    s_enter.result = list(toggle())


def s_check_enter():
    st = player.state
    check("toggle caches missing frames, then enters Cache Mode paused",
          st.active and st.cache_mode and st.paused and store.frame_cache.stats()["frames"] == 48,
          {"op": s_enter.result, "active": st.active, "cache_mode": st.cache_mode, "paused": st.paused})
    check("cache playhead starts at Blender's frame", st.frame == ORIGINAL, st.frame)


def s_drag():
    x, y = at_frame(30)
    send('LEFTMOUSE', 'PRESS', x, y)
    x2, _ = at_frame(36)
    send('MOUSEMOVE', 'NOTHING', x2, y)
    x3, _ = at_frame(40)
    send('MOUSEMOVE', 'NOTHING', x3, y)


def s_check_dragging():
    check("dragging in the Timeline moves the cache playhead", player.state.frame == 40, player.state.frame)
    check("Blender's playhead stays put while dragging", scene_frame() == ORIGINAL, scene_frame())


def s_release():
    x, y = at_frame(40)
    send('LEFTMOUSE', 'RELEASE', x, y)


def s_check_release():
    st = player.state
    check("still in Cache Mode after the drag", st.active and st.cache_mode and st.paused)


def s_play():
    s_play.index = player.state.index
    key('SPACE')


def s_check_play():
    st = player.state
    check("Space plays from the cache playhead", not st.paused and st.index != s_play.index,
          {"paused": st.paused, "from": s_play.index, "now": st.index})
    check("Blender's playhead stays put while playing", scene_frame() == ORIGINAL, scene_frame())


def s_click_viewport():
    send('LEFTMOUSE', 'PRESS')
    send('LEFTMOUSE', 'RELEASE')


def s_check_click():
    check("a click outside the Timeline does not leave Cache Mode", player.state.active and player.state.cache_mode)
    key('SPACE')


def s_step():
    s_step.index = player.state.index
    key('RIGHT_ARROW')


def s_check_step():
    st = player.state
    check("Space pauses, right arrow steps one frame", st.paused and st.index == (s_step.index + 1) % 48,
          {"paused": st.paused, "before": s_step.index, "after": st.index})
    key('LEFT_ARROW', shift=True)


def s_check_first():
    check("Shift+left jumps to the first frame", player.state.frame == 1, player.state.frame)


def graph_region():
    graph = next(a for a in bpy.context.window_manager.windows[0].screen.areas if a.type == 'GRAPH_EDITOR')
    return graph, window_region(graph)


def s_graph_setup():
    outliner = next(a for a in bpy.context.window_manager.windows[0].screen.areas if a.type == 'OUTLINER')
    outliner.ui_type = 'FCURVES'
    # The area is narrow; without the channel list the graph gets enough pixels per frame.
    outliner.spaces.active.show_region_channels = False


def s_graph_view_all():
    win = bpy.context.window_manager.windows[0]
    graph, region = graph_region()
    with bpy.context.temp_override(window=win, area=graph, region=region):
        bpy.ops.graph.view_all()


def graph_xy(frame, in_band):
    _graph, region = graph_region()
    x, _y = region.view2d.view_to_region(frame, 0, clip=False)
    y = region.y + region.height - 6 if in_band else region.y + region.height // 2
    return region.x + x, y


def s_graph_drag():
    x, y = graph_xy(20, True)
    send('LEFTMOUSE', 'PRESS', x, y)
    x2, _ = graph_xy(25, True)
    send('MOUSEMOVE', 'NOTHING', x2, y)
    send('LEFTMOUSE', 'RELEASE', x2, y)


def s_check_graph_drag():
    check("dragging in the Graph Editor's frame-number bar moves the cache playhead",
          player.state.frame == 25, player.state.frame)
    check("Blender's playhead stays put (Graph Editor)", scene_frame() == ORIGINAL, scene_frame())


def s_graph_low_click():
    x, y = graph_xy(40, False)
    send('LEFTMOUSE', 'PRESS', x, y)
    send('LEFTMOUSE', 'RELEASE', x, y)


def s_check_graph_low_click():
    check("a click lower in the Graph Editor is left to Blender (selects keys)",
          player.state.frame == 25 and player.state.cache_mode, player.state.frame)


def s_exit_toggle():
    toggle()


def s_check_exit():
    check("toggle again leaves Cache Mode", not player.state.active, player.state.active)
    check("Blender's playhead untouched after leaving", scene_frame() == ORIGINAL, scene_frame())


def s_enter_again():
    toggle()


def s_check_instant():
    check("re-entering a full cache needs no caching", player.state.active and not player.state.filling)
    key('ESC')


def s_check_esc():
    check("Esc leaves Cache Mode", not player.state.active)
    check("Blender's playhead still untouched", scene_frame() == ORIGINAL, scene_frame())


def s_enter_for_edit():
    toggle()


def s_edit():
    cube = bpy.data.objects["Cube"]
    fcurve = cube.animation_data.action.layers[0].strips[0].channelbags[0].fcurves[0]
    fcurve.keyframe_points[-1].co.y += 1.0
    fcurve.update()


def s_check_edit():
    check("an edit in Cache Mode clears the cache and leaves Cache Mode",
          not player.state.active and store.frame_cache.is_empty(),
          {"active": player.state.active, "frames": store.frame_cache.stats()["frames"]})


def s_finish():
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2)
    bpy.ops.wm.quit_blender()


STEPS = [
    (1.0, setup), (0.5, s_enter), (3.0, s_check_enter),
    (0.2, s_drag), (0.3, s_check_dragging), (0.1, s_release), (0.3, s_check_release),
    (0.2, s_play), (0.5, s_check_play), (0.1, s_click_viewport), (0.3, s_check_click),
    (0.3, s_step), (0.3, s_check_step), (0.3, s_check_first),
    (0.2, s_graph_setup), (0.3, s_graph_view_all), (0.3, s_graph_drag), (0.3, s_check_graph_drag),
    (0.2, s_graph_low_click), (0.3, s_check_graph_low_click),
    (0.2, s_exit_toggle), (0.3, s_check_exit),
    (0.2, s_enter_again), (0.5, s_check_instant), (0.3, s_check_esc),
    (0.2, s_enter_for_edit), (0.5, s_edit), (0.5, s_check_edit),
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
