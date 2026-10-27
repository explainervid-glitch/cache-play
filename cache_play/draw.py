# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw callbacks: the cached frame over the 3D Viewport, cache bar and playhead in time editors."""

import time

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from gpu_extras.presets import draw_texture_2d
from mathutils import Matrix

from . import display, player, store

BACKDROP_COLOR = (0.09, 0.09, 0.09, 1.0)
RAM_COLOR = (0.30, 0.78, 0.35, 0.9)   # green: in RAM, ready to play (as in After Effects)
DISK_COLOR = (0.25, 0.50, 0.95, 0.9)  # blue: on disk only, streamed in while playing
PLAYHEAD_COLOR = (1.0, 0.62, 0.15, 1.0)
SCRUB_HEIGHT = 23  # height of the frame-number band at the top of time editors, before UI scale

_handles = []


def _ui_scale():
    return bpy.context.preferences.system.ui_scale


def _draw_rects(rects, color):
    if not rects:
        return
    coords = []
    indices = []
    for x0, y0, x1, y1 in rects:
        k = len(coords)
        coords += ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        indices += ((k, k + 1, k + 2), (k, k + 2, k + 3))
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": coords}, indices=indices)
    shader.uniform_float("color", color)
    batch.draw(shader)


def _camera_rect(scene, region, rv3d):
    """Where the camera frame sits in the region (follows zoom and pan in camera view)."""
    camera = scene.camera
    if camera is None or camera.type != 'CAMERA':
        return None
    matrix = camera.matrix_world.normalized()
    points = [
        location_3d_to_region_2d(region, rv3d, matrix @ corner)
        for corner in camera.data.view_frame(scene=scene)
    ]
    if any(p is None for p in points):
        return None
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _fit_rect(region, width, height):
    scale = min(region.width / width, region.height / height)
    w, h = width * scale, height * scale
    x0 = (region.width - w) / 2
    y0 = (region.height - h) / 2
    return x0, y0, x0 + w, y0 + h


def _draw_text(text, x, y, size, color, shadow=True):
    font = 0
    blf.size(font, size)
    blf.color(font, *color)
    if shadow:
        blf.enable(font, blf.SHADOW)
        blf.shadow(font, 3, 0.0, 0.0, 0.0, 0.8)
        blf.shadow_offset(font, 1, -1)
    blf.position(font, x, y, 0)
    blf.draw(font, text)
    if shadow:
        blf.disable(font, blf.SHADOW)


def _showing_here(context):
    """True when this region is the 3D Viewport region the cached picture covers."""
    state = player.state
    if not state.active:
        return False
    area = context.area
    if area is None or area.as_pointer() != state.area_ptr:
        return False
    return context.region_data == context.space_data.region_3d  # not another quad-view region


def _draw_view3d_picture():
    """The cached picture, drawn in the scene pass (POST_VIEW) so on-screen buttons such as
    the floating toolbar and the navigation controls still draw on top of it."""
    context = bpy.context
    if not _showing_here(context):
        return
    state = player.state
    region = context.region
    cache = store.frame_cache

    texture = display.display.texture(cache)  # prepared by the playback operator
    w, h = region.width, region.height
    pixels = Matrix(((2 / w, 0, 0, -1), (0, 2 / h, 0, -1), (0, 0, 1, 0), (0, 0, 0, 1)))
    with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
        gpu.matrix.load_identity()
        gpu.matrix.load_projection_matrix(pixels)
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        gpu.state.blend_set('ALPHA')
        _draw_rects([(0, 0, w, h)], BACKDROP_COLOR)
        if texture is not None:
            # Frames are always shot through the camera: sit them on the camera frame, or
            # centred if the viewport has been orbited out of camera view.
            rect = None
            if context.region_data.view_perspective == 'CAMERA':
                rect = _camera_rect(context.scene, region, context.region_data)
            if rect is None:
                rect = _fit_rect(region, cache.width, cache.height)
            x0, y0, x1, y1 = rect
            draw_texture_2d(texture, (x0, y0), x1 - x0, y1 - y0)
        gpu.state.blend_set('NONE')


HUD_FONT = 10
HUD_HEIGHT = 17   # label height, px at UI scale 1
HUD_PAD = 8       # text to label edge
HUD_ABOVE = 4     # gap between the label and the toolbar pill


def _hud_parts(context, state):
    """(title, long details, short details) for the status label, e.g.
    "CACHE MODE  12 · scene 0 · paused"."""
    title = "CACHE MODE" if state.cache_mode else "CACHE PLAY"
    if state.cache_mode:
        details = [str(state.frame), f"scene {context.scene.frame_current}"]
    else:
        details = [str(state.frame), f"{state.fps:.4g} fps"]
    if state.stats is not None and state.stats.dropped:
        details.append(f"{state.stats.dropped} dropped")
    short = [str(state.frame)]
    if state.paused:
        details.append("paused")
        short.append("paused")
    return title, "  " + " · ".join(details), "  " + " · ".join(short)


def _draw_hud(context, state):
    """A small label in the toolbar's style, centred just above the toolbar pill (or where
    the pill would be), so it never covers the buttons. Narrow viewports get shorter text."""
    from . import toolbar  # toolbar imports ui; import late to keep start-up order simple
    region = context.region
    s = _ui_scale()
    blf.size(0, HUD_FONT * s)
    title, long, short = _hud_parts(context, state)
    pad, height = HUD_PAD * s, HUD_HEIGHT * s
    room = region.width - 2 * toolbar.SIDE_ROOM * s
    title_w = blf.dimensions(0, title)[0]
    details = long if title_w + blf.dimensions(0, long)[0] + 2 * pad <= room else short
    if title_w + blf.dimensions(0, details)[0] + 2 * pad > room:
        title = ""
    width = blf.dimensions(0, title)[0] + blf.dimensions(0, details)[0] + 2 * pad

    pill_shown = (toolbar.get_prefs().toolbar in {'VIEWPORT', 'BOTH'}
                  and context.space_data.show_gizmo)
    if pill_shown:
        geo = toolbar.Geometry(context)
        y = geo.y + geo.height / 2 + HUD_ABOVE * s + height / 2
    else:
        y = toolbar.MARGIN * s + height / 2
    x0 = region.width / 2 - width / 2
    toolbar._pill(x0 - s, x0 + width + s, y, height + 2 * s, toolbar.PILL_EDGE)
    toolbar._pill(x0, x0 + width, y, height, toolbar.PILL_COLOR)

    blf.size(0, HUD_FONT * s)  # toolbar.Geometry() above sets its own font size
    text_h = blf.dimensions(0, "Hg")[1]
    x = x0 + pad
    for text, color in ((title, PLAYHEAD_COLOR), (details, (0.9, 0.9, 0.9, 0.95))):
        if text:
            blf.color(0, *color)
            blf.position(0, x, y - text_h / 2 + s, 0)
            blf.draw(0, text)
            x += blf.dimensions(0, text)[0]


def _draw_view3d():
    """Status label over the cached picture, and frame-timing measurement."""
    context = bpy.context
    if not _showing_here(context):
        return
    state = player.state
    gpu.state.blend_set('ALPHA')
    _draw_hud(context, state)
    gpu.state.blend_set('NONE')

    if state.drawn_index != state.index:
        state.drawn_index = state.index
        now = time.perf_counter()
        state.stats.presented(state.index, now, now - state.tag_time)


def _frame_runs(frames):
    runs = []
    for frame in sorted(frames):
        if runs and frame == runs[-1][1] + 1:
            runs[-1][1] = frame
        else:
            runs.append([frame, frame])
    return runs


def _draw_time_editor():
    """Cache bar and cache playhead in the Timeline, Dope Sheet, Graph Editor and NLA."""
    context = bpy.context
    if not store.is_frame_editor(context.area):
        return  # e.g. the Drivers editor, whose horizontal axis is not frames
    cache = store.frame_cache
    state = player.state
    in_ram = cached = None
    if cache.key is not None and context.scene.name == cache.scene_name:
        in_ram, cached = cache.snapshot()
    has_bar = bool(cached)
    if not (has_bar or state.active):
        return
    region = context.region
    v2d = region.view2d
    s = _ui_scale()
    top = region.height - SCRUB_HEIGHT * s

    def to_x(frame):
        return v2d.view_to_region(frame, 0, clip=False)[0]

    gpu.state.blend_set('ALPHA')
    if has_bar:
        for frames, color in ((cached - in_ram, DISK_COLOR), (in_ram, RAM_COLOR)):
            rects = [(to_x(a), top - 4 * s, to_x(b + 1), top) for a, b in _frame_runs(frames)]
            _draw_rects(rects, color)
    if state.active:
        x = to_x(state.frame)
        _draw_rects([(x - s, 0, x + s, top)], PLAYHEAD_COLOR)
        label = str(state.frame)
        blf.size(0, 11 * s)
        text_w, text_h = blf.dimensions(0, label)
        pad = 5 * s
        _draw_rects(
            [(x - text_w / 2 - pad, top + 2 * s, x + text_w / 2 + pad, region.height - 2 * s)],
            PLAYHEAD_COLOR,
        )
        _draw_text(label, x - text_w / 2, top + (SCRUB_HEIGHT * s - text_h) / 2, 11 * s,
                   (0.0, 0.0, 0.0, 1.0), shadow=False)
    gpu.state.blend_set('NONE')


_SPACES = (
    (bpy.types.SpaceView3D, _draw_view3d_picture, 'POST_VIEW'),
    (bpy.types.SpaceView3D, _draw_view3d, 'POST_PIXEL'),
    (bpy.types.SpaceDopeSheetEditor, _draw_time_editor, 'POST_PIXEL'),  # Timeline and Dope Sheet
    (bpy.types.SpaceGraphEditor, _draw_time_editor, 'POST_PIXEL'),
    (bpy.types.SpaceNLA, _draw_time_editor, 'POST_PIXEL'),
)


def register():
    for space, callback, draw_type in _SPACES:
        _handles.append((space, space.draw_handler_add(callback, (), 'WINDOW', draw_type)))


def unregister():
    for space, handle in _handles:
        space.draw_handler_remove(handle, 'WINDOW')
    _handles.clear()
