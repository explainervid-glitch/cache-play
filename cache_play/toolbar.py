# SPDX-License-Identifier: GPL-3.0-or-later
"""Cache Play toolbar: a pill at the bottom centre of every 3D Viewport.

Built from gizmos, the same kind of on-screen buttons as Blender's navigation controls, so
it needs no running operator (which would block autosave). A chevron on the left folds the
pill down to a small tab and back. The pill adapts to the viewport's width: on narrower
viewports it first drops the cached count, then the Clear and Settings buttons.
"""

import math

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

from . import player, store
from .ui import get_prefs

BUTTON = 28       # button diameter, px at UI scale 1
GAP = 2
CHEVRON_WIDTH = 22
INSET = 3         # pill edge to buttons
TEXT_GAP = 6      # last button to the cached count
MARGIN = 10       # distance from the bottom edge
SIDE_ROOM = 16    # free space kept on both sides of the pill
PILL_COLOR = (0.11, 0.11, 0.11, 0.88)
PILL_EDGE = (0.30, 0.30, 0.30, 0.90)
HOVER_COLOR = (0.32, 0.32, 0.32)
ACTIVE_COLOR = (1.0, 0.62, 0.15)  # the cache playhead's colour
FONT_SIZE = 11

# (operator, icon); Cache Mode is lit while it is on. On narrow viewports only the first
# ESSENTIAL buttons stay. EXPORT sits after the cached count.
BUTTONS = (
    ("cacheplay.preview", 'PLAY'),
    ("cacheplay.cache_mode", 'FILE_MOVIE'),
    ("cacheplay.clear", 'TRASH'),
    ("cacheplay.settings", 'PREFERENCES'),
)
ESSENTIAL = 2
EXPORT = ("cacheplay.export", 'EXPORT')

_button_state = []  # (under the mouse, switched on) per gizmo button, set in draw_prepare


def _scale():
    return bpy.context.preferences.system.ui_scale


def _status_text(context):
    first, last = store.scene_range(context.scene)
    _in_ram, cached = store.frame_cache.snapshot()
    done = len(cached.intersection(range(first, last + 1)))
    return f"{done}/{last - first + 1}"


class Geometry:
    """Where the pill and its parts sit for this viewport, in region pixels.

    Left to right: chevron, BUTTONS, cached count, export button. On narrower viewports
    the count goes first, then export with Clear and Settings.
    """

    def __init__(self, context):
        region = context.region
        s = _scale()
        self.size = BUTTON * s
        self.gap = GAP * s
        inset = INSET * s
        self.height = self.size + 2 * inset
        chevron = CHEVRON_WIDTH * s
        end = self.height / 2  # round right end
        self.text = _status_text(context)
        blf.size(0, FONT_SIZE * s)
        self.text_w, self.text_h = blf.dimensions(0, self.text)
        step = self.size + self.gap

        def width(buttons, text, export):
            w = inset + chevron + buttons * step - self.gap + end
            if text:
                w += TEXT_GAP * s + self.text_w
            if export:
                w += (self.gap if text else 0) + step
            return w

        room = region.width - 2 * SIDE_ROOM * s
        n = len(BUTTONS)
        if get_prefs().toolbar_collapsed:
            layout = (0, False, False)
        else:
            layout = next((option for option in ((n, True, True), (n, False, True), (ESSENTIAL, False, False))
                           if width(*option) <= room), (ESSENTIAL, False, False))
        self.buttons, self.show_text, self.show_export = layout
        total = width(*layout) if self.buttons else self.height  # folded: a round tab
        self.left = region.width / 2 - total / 2
        self.right = self.left + total
        self.y = MARGIN * s + self.height / 2
        if self.buttons:
            self.chevron = (self.left + inset, self.left + inset + chevron)
        else:
            self.chevron = (self.left, self.right)
        first = self.chevron[1] + self.size / 2
        # x of every gizmo button (BUTTONS, then EXPORT); None when hidden.
        self.button_x = [first + i * step if i < self.buttons else None for i in range(n)]
        x = first + (self.buttons - 1) * step + self.size / 2  # right edge of the last button
        self.text_x = x + TEXT_GAP * s
        if self.show_text:
            x = self.text_x + self.text_w + self.gap
        self.button_x.append(x + self.gap + self.size / 2 if self.show_export else None)

    def on_chevron(self, x, y):
        return self.chevron[0] <= x <= self.chevron[1] and abs(y - self.y) <= self.height / 2


def _fill(coords, indices, color):
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": coords}, indices=indices)
    shader.uniform_float("color", color)
    batch.draw(shader)


def _pill(x0, x1, y, height, color, segments=12):
    """A rectangle with fully round ends (a circle when it is as wide as it is high)."""
    r = height / 2
    points = []
    for i in range(segments + 1):  # right end, bottom to top
        a = -math.pi / 2 + math.pi * i / segments
        points.append((x1 - r + r * math.cos(a), y + r * math.sin(a)))
    for i in range(segments + 1):  # left end, top to bottom
        a = math.pi / 2 + math.pi * i / segments
        points.append((x0 + r + r * math.cos(a), y + r * math.sin(a)))
    coords = [((x0 + x1) / 2, y)] + points
    indices = [(0, i, i + 1) for i in range(1, len(points))] + [(0, len(points), 1)]
    _fill(coords, indices, color)


def _thick_line(p0, p1, width, color):
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length * width / 2, dx / length * width / 2
    coords = ((p0[0] + nx, p0[1] + ny), (p1[0] + nx, p1[1] + ny), (p1[0] - nx, p1[1] - ny), (p0[0] - nx, p0[1] - ny))
    _fill(coords, ((0, 1, 2), (0, 2, 3)), color)


def _chevron(cx, cy, s, pointing_down, color):
    """A chevron: pointing down folds the pill away, pointing up opens it."""
    half, rise = 4.5 * s, 2.5 * s
    tip = (cx, cy - rise) if pointing_down else (cx, cy + rise)
    arm_y = cy + rise if pointing_down else cy - rise
    _thick_line((cx - half, arm_y), tip, 1.8 * s, color)
    _thick_line(tip, (cx + half, arm_y), 1.8 * s, color)


class CACHEPLAY_GT_pill(bpy.types.Gizmo):
    """The pill behind the buttons, the chevron toggle and the cached count."""
    bl_idname = "CACHEPLAY_GT_pill"

    def draw(self, context):
        geo = Geometry(context)
        s = _scale()
        gpu.state.blend_set('ALPHA')
        _pill(geo.left - s, geo.right + s, geo.y, geo.height + 2 * s, PILL_EDGE)
        _pill(geo.left, geo.right, geo.y, geo.height, PILL_COLOR)
        brightness = 1.0 if self.is_highlight else 0.75
        _chevron((geo.chevron[0] + geo.chevron[1]) / 2, geo.y, s, pointing_down=bool(geo.buttons),
                 color=(brightness, brightness, brightness, 1.0))
        # Round backgrounds behind the icons: orange while on, grey under the mouse. The
        # buttons draw icons only, because Blender fades a button's icon with its background.
        radius = geo.size / 2
        for x, (hover, on) in zip(geo.button_x, _button_state):
            if x is not None and (hover or on):
                _pill(x - radius, x + radius, geo.y, geo.size, ACTIVE_COLOR + (0.95,) if on else HOVER_COLOR + (0.9,))
        if geo.show_text:
            blf.size(0, FONT_SIZE * s)
            blf.color(0, 0.9, 0.9, 0.9, 0.9)
            blf.position(0, geo.text_x, geo.y - geo.text_h / 2, 0)
            blf.draw(0, geo.text)
        gpu.state.blend_set('NONE')

    def test_select(self, context, location):
        return 0 if Geometry(context).on_chevron(*location) else -1

    def invoke(self, context, event):
        # Blender only runs a custom gizmo's click as a press-and-hold; fold on press.
        prefs = get_prefs()
        prefs.toolbar_collapsed = not prefs.toolbar_collapsed
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event, tweak):
        return {'RUNNING_MODAL'}

    def exit(self, context, cancel):
        pass


class CACHEPLAY_GT_hit(bpy.types.Gizmo):
    """The clickable round area of one button; draws nothing.

    Blender's own icon buttons (GIZMO_GT_button_2d) switch the mouse cursor to the arrow
    while hovered. Gizmos made in Python leave the cursor alone, so the icon buttons only
    draw and these take the clicks.
    """
    bl_idname = "CACHEPLAY_GT_hit"

    def draw(self, context):
        pass

    def test_select(self, context, location):
        centre = self.matrix_basis.translation
        radius = BUTTON / 2 * _scale()
        return 0 if (location[0] - centre.x) ** 2 + (location[1] - centre.y) ** 2 <= radius ** 2 else -1


class VIEW3D_GGT_cache_play(bpy.types.GizmoGroup):
    bl_idname = "VIEW3D_GGT_cache_play"
    bl_label = "Cache Play Toolbar"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'PERSISTENT', 'SCALE'}

    @classmethod
    def poll(cls, context):
        return get_prefs(context).toolbar in {'VIEWPORT', 'BOTH'}

    def setup(self, context):
        self.buttons = []  # icons
        self.hits = []     # click areas
        for operator, icon in BUTTONS + (EXPORT,):
            gz = self.gizmos.new("GIZMO_GT_button_2d")
            gz.icon = icon
            gz.draw_options = set()  # icon only; the pill draws the round backgrounds
            gz.color = gz.color_highlight = (1.0, 1.0, 1.0)
            gz.alpha = gz.alpha_highlight = 1.0
            gz.hide_select = True  # never hovered, so it never changes the cursor
            self.buttons.append(gz)
            hit = self.gizmos.new(CACHEPLAY_GT_hit.bl_idname)
            hit.target_set_operator(operator)
            self.hits.append(hit)
        # Blender draws a group's gizmos newest first, so the pill, made last, ends up behind.
        self.pill = self.gizmos.new(CACHEPLAY_GT_pill.bl_idname)

    def draw_prepare(self, context):
        geo = Geometry(context)
        for gz, hit, x in zip(self.buttons, self.hits, geo.button_x):
            gz.hide = hit.hide = x is None
            if x is not None:
                gz.scale_basis = BUTTON / 2  # Blender applies the UI scale to this itself
                gz.matrix_basis = hit.matrix_basis = Matrix.Translation((x, geo.y, 0.0))
        state = player.state
        lit = (state.active and not state.cache_mode, state.cache_mode, False, False, False)
        _button_state[:] = [(hit.is_highlight, on) for hit, on in zip(self.hits, lit)]


classes = (CACHEPLAY_GT_pill, CACHEPLAY_GT_hit, VIEW3D_GGT_cache_play)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
