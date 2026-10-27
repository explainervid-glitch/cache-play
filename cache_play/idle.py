# SPDX-License-Identifier: GPL-3.0-or-later
"""Cache When Idle: fill missing frames one at a time while nobody is working.

Idle means none of the chosen kinds of activity (preference Idle Activity) for the Idle
Delay. By default, like After Effects, only working *in Blender* counts: keys and clicks
while Blender is in front, and edits, frame changes and view changes. Moving the mouse and
using other programs can be added. Blender's Python API cannot see keys, clicks or the
mouse, so the operating system is asked:

- Windows: which program is in front, which keys and buttons are down, when the last input
  happened and where the cursor is. Input without the cursor moving is a key tap or a wheel
  turn.
- macOS (untested): only "any input in the last half second", in any program.
- Elsewhere: only edits, frame changes and view changes.

Each step caches one frame: jump the scene to a missing frame, capture it, jump straight
back. Nothing redraws in between, so the playhead never visibly moves. A step costs two
scene evaluations, so input during one can feel like a short hitch.
"""

import ctypes
import os
import sys
import time

import bpy
from bpy.app.handlers import persistent

from . import capture, player, store
from .store import scene_range
from .ui import configure_cache, get_prefs

CHECK_INTERVAL = 0.25         # seconds between checks while not idle
BACKGROUND_MODAL_AGE = 20.0   # modal operators running longer than this are other add-ons' helpers
_ALLOWED_MODES = {'OBJECT', 'POSE'}
_BLOCKING_JOBS = ('RENDER', 'OBJECT_BAKE', 'COMPOSITE')

status = ""              # what idle caching is doing, shown in the panel
_last_activity = 0.0
_last_view = None       # view matrix of the target viewport at the previous check
_offscreen_owner = None  # Capturer whose offscreen buffer is reused between steps
_modal_seen = {}         # modal operator pointer -> when first seen


def touch():
    global _last_activity
    _last_activity = time.perf_counter()


def _counts(kind):
    """True when the user chose this kind of activity to count as working."""
    return kind in get_prefs().idle_activity


def _make_windows_probe():
    class LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
    user32.GetAsyncKeyState.restype = ctypes.c_short
    blender_pid = os.getpid()
    previous = {"input": None, "cursor": None}

    def activity():
        info = LastInputInfo(ctypes.sizeof(LastInputInfo), 0)
        user32.GetLastInputInfo(ctypes.byref(info))
        point = Point()
        user32.GetCursorPos(ctypes.byref(point))
        last_input, cursor = info.dwTime, (point.x, point.y)
        input_before, cursor_before = previous["input"], previous["cursor"]
        previous["input"], previous["cursor"] = last_input, cursor
        if input_before is None:
            return set()
        any_input = last_input != input_before
        moved = cursor != cursor_before
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(owner))
        if owner.value != blender_pid:
            return {'OTHER_APPS'} if any_input or moved else set()
        found = set()
        # 0x8000: held now; 0x0001: pressed since the last check (catches quick taps).
        # A key tap or wheel turn shows up as input without the cursor moving.
        if any(user32.GetAsyncKeyState(key) & 0x8001 for key in range(1, 255)) or (any_input and not moved):
            found.add('KEYS')
        if moved:
            found.add('MOUSE')
        return found
    return activity


def _make_interaction_probe():
    """A function returning the kinds of activity ('KEYS', 'MOUSE', 'OTHER_APPS') seen since
    its previous call."""
    try:
        if sys.platform == "win32":
            return _make_windows_probe()
        if sys.platform == "darwin":
            quartz = ctypes.CDLL(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
            since = quartz.CGEventSourceSecondsSinceLastEventType
            since.restype = ctypes.c_double
            since.argtypes = (ctypes.c_uint32, ctypes.c_uint32)
            # kCGEventSourceStateHIDSystemState, kCGAnyInputEventType. macOS cannot tell
            # where the input went, so it counts as keys and clicks.
            return lambda: {'KEYS'} if since(1, 0xFFFFFFFF) < 0.5 else set()
    except (AttributeError, OSError):
        pass
    return set


_interaction_probe = _make_interaction_probe()


def idle_seconds():
    """Seconds since the last activity of a kind the user chose."""
    if _interaction_probe() & set(get_prefs().idle_activity):
        touch()
    return time.perf_counter() - _last_activity


@persistent
def _on_change(*_args):
    if not player.state.busy() and _counts('EDITS'):
        touch()


def _free_capturer():
    global _offscreen_owner
    if _offscreen_owner is not None:
        _offscreen_owner.free()
        _offscreen_owner = None


def _interactive_modal_running():
    """True while a transform, modal tool or similar runs (and the mouse may be still).

    Some add-ons keep a modal operator running all the time; those that have been
    running for BACKGROUND_MODAL_AGE seconds are ignored.
    """
    now = time.perf_counter()
    seen = {}
    for window in bpy.context.window_manager.windows:
        for operator in window.modal_operators:
            if operator.bl_idname == "CACHEPLAY_OT_preview":
                continue  # our own Cache Mode; handled by the caller
            pointer = operator.as_pointer()
            seen[pointer] = _modal_seen.get(pointer, now)
    _modal_seen.clear()
    _modal_seen.update(seen)
    return any(now - first < BACKGROUND_MODAL_AGE for first in seen.values())


def _job_running():
    for job in _BLOCKING_JOBS:
        try:
            if bpy.app.is_job_running(job):
                return True
        except (TypeError, ValueError):
            pass
    return False


def _target():
    """(window, area) of the 3D Viewport to cache from: the last one used, else the biggest."""
    wanted = store.frame_cache.area_ptr
    best = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            if area.as_pointer() == wanted:
                return window, area
            if best is None or area.width * area.height > best[1].width * best[1].height:
                best = (window, area)
    return best


def _set_status(text):
    """Remember what idle caching is doing; the sidebar panel shows it."""
    global status
    if text != status:
        status = text
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()


def _step(window, area, region, space):
    """Cache one missing frame. Returns (cached a frame, status text)."""
    global _offscreen_owner
    mode = bpy.context.mode
    if mode not in _ALLOWED_MODES:
        return False, f"Paused in {mode.replace('_', ' ').title()} mode (works in Object and Pose)"
    scene = window.scene
    if scene.camera is None:
        return False, "No scene camera to cache through"
    cache = store.frame_cache
    # Frames are shot through the camera, so the viewport may be orbited freely meanwhile.
    capturer = capture.Capturer(scene, window.view_layer, region, space, scene.cache_play.preview_size,
                                gpu_pack=get_prefs().fast_caching)
    if capturer.key != cache.key:
        if not cache.is_empty():
            # A cache made with other settings: leave it for the user to replace.
            return False, "Cache is from other settings. Cache & Play replaces it"
        cache.prepare(capturer.key, scene.name, capturer.width, capturer.height, capturer.mode,
                      area.as_pointer())

    first, last = scene_range(scene)
    current = min(max(scene.frame_current, first), last)
    _in_ram, cached = cache.snapshot()
    order = [*range(current, last + 1), *range(first, current)]
    frame = next((f for f in order if f not in cached), None)
    if frame is None:
        return False, "All frames cached"
    if not cache.has_room(frame):
        return False, cache.full_reason

    # Fresh view references every step (layouts change); only the GPU buffer is reused.
    if _offscreen_owner is not None and (_offscreen_owner.width, _offscreen_owner.height) == (
            capturer.width, capturer.height):
        capturer.offscreen, _offscreen_owner.offscreen = _offscreen_owner.offscreen, None
    _free_capturer()
    _offscreen_owner = capturer

    state = player.state
    state.idle_step = True
    try:
        original = scene.frame_current, scene.frame_subframe
        scene.frame_set(frame)
        cache.put(frame, capturer.capture(bpy.context.evaluated_depsgraph_get()))
        scene.frame_set(original[0], subframe=original[1])
    finally:
        state.idle_step = False
        state.suppress(0.3)
    store.tag_time_editors()
    return True, f"Caching frame {frame}"


def _range_cached(scene):
    cache = store.frame_cache
    if cache.key is None:
        return False
    first, last = scene_range(scene)
    _in_ram, cached = cache.snapshot()
    return cached.issuperset(range(first, last + 1))


def _tick():
    try:
        return _tick_unsafe()
    except Exception as ex:
        print(f"Cache Play idle caching: {ex}")
        _free_capturer()
        return 2.0


def _view_moved(space):
    """True when the viewport was orbited, panned or zoomed since the last check."""
    global _last_view
    rv3d = space.region_3d
    view = (tuple(map(tuple, rv3d.view_matrix)), rv3d.view_distance, rv3d.view_camera_zoom,
            tuple(rv3d.view_camera_offset))
    moved = _last_view is not None and view != _last_view
    _last_view = view
    return moved


def _tick_unsafe():
    interactive = _interactive_modal_running()
    found = _target()
    if found is None or not found[0].scene.cache_play.cache_when_idle:
        _set_status("")
        _free_capturer()
        return 1.0
    window, area = found
    region, space = capture.view3d_parts(area)
    if region is None:
        return 1.0
    if _view_moved(space) and _counts('EDITS'):
        touch()
    state = player.state
    wm = bpy.context.window_manager
    delay = get_prefs().idle_delay
    idle_for = idle_seconds()  # read every tick so the input check stays current
    # Cache Mode while paused is fine: it only shows stored pictures.
    reviewing = state.active and state.cache_mode and state.paused
    if state.filling:
        waiting = "Paused while Cache & Play caches"
    elif state.active and not reviewing:
        waiting = "Paused while cached playback runs"
    elif interactive:
        waiting = "Paused while a tool is running"
    elif any(w.screen.is_animation_playing for w in wm.windows):
        waiting = "Paused while Blender plays"
    elif _job_running():
        waiting = "Paused while rendering or baking"
    elif idle_for < delay:
        waiting = f"Waiting for {delay:g} s without activity"
    else:
        waiting = None
    if waiting:
        if not waiting.startswith("Paused") and _range_cached(window.scene):
            waiting = "All frames cached"
        _set_status(waiting)
        _free_capturer()
        return CHECK_INTERVAL
    configure_cache()
    with bpy.context.temp_override(window=window, area=area, region=region):
        cached, text = _step(window, area, region, space)
    _set_status(text)
    if not cached:
        _free_capturer()
        return 1.0
    return 0.0  # next frame right away; input in between still stops it


@persistent
def _on_load_pre(*_args):
    global _last_view
    _free_capturer()
    _last_view = None
    touch()


_HANDLERS = (
    ("depsgraph_update_post", _on_change),
    ("frame_change_post", _on_change),
    ("load_pre", _on_load_pre),
)


def register():
    touch()
    for name, func in _HANDLERS:
        getattr(bpy.app.handlers, name).append(func)
    bpy.app.timers.register(_tick, first_interval=1.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for name, func in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if func in handlers:
            handlers.remove(func)
    _free_capturer()
