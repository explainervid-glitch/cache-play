# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw the 3D Viewport into an offscreen buffer and read the pixels back."""

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

from . import store

# Alpha that becomes the byte 0x40 in RGBA8, the top byte of every packed pixel.
PACK_ALPHA = 0x40 / 255
_stamp = {}


def _stamp_alpha():
    """Overwrite only the alpha channel of the bound offscreen with PACK_ALPHA.

    Viewport captures are opaque, so alpha carries nothing; with this byte in place the
    pixels read back are already in store.pack_pixels' format.
    """
    if not _stamp:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        _stamp["shader"] = shader
        _stamp["batch"] = batch_for_shader(
            shader, 'TRIS', {"pos": ((-1, -1), (1, -1), (1, 1), (-1, 1))}, indices=((0, 1, 2), (0, 2, 3)))
    shader = _stamp["shader"]
    with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
        gpu.matrix.load_identity()
        gpu.matrix.load_projection_matrix(Matrix.Identity(4))
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        gpu.state.blend_set('NONE')
        gpu.state.color_mask_set(False, False, False, True)
        shader.uniform_float("color", (0.0, 0.0, 0.0, PACK_ALPHA))
        _stamp["batch"].draw(shader)
        gpu.state.color_mask_set(True, True, True, True)

# Viewport shading settings that change the picture without a depsgraph update.
_SHADING_ATTRS = (
    "type", "light", "color_type", "single_color", "wireframe_color_type",
    "background_type", "background_color", "studio_light", "studiolight_rotate_z",
    "studiolight_intensity", "studiolight_background_alpha", "studiolight_background_blur",
    "use_world_space_lighting", "use_scene_lights", "use_scene_world",
    "use_scene_lights_render", "use_scene_world_render", "show_xray", "xray_alpha",
    "show_shadows", "shadow_intensity", "show_cavity", "cavity_type",
    "show_object_outline", "object_outline_color", "show_specular_highlight",
    "show_backface_culling", "use_dof", "render_pass", "use_compositor",
)


def _freeze(value):
    """Turn a property value into something hashable and comparable."""
    if isinstance(value, float):
        return round(value, 5)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(value))
    try:
        return tuple(_freeze(v) for v in value)
    except TypeError:
        return repr(value)


def _shading_key(space):
    shading = space.shading
    return tuple(_freeze(getattr(shading, name, None)) for name in _SHADING_ATTRS) + (
        space.overlay.show_overlays,
    )


def _color_key(scene):
    view = scene.view_settings
    return (
        view.view_transform, view.look, _freeze(view.exposure), _freeze(view.gamma),
        view.use_curve_mapping, scene.display_settings.display_device,
    )


def find_view3d(context):
    """(area, region, space) of the 3D Viewport to use.

    The current one; from another editor (e.g. the Timeline header), the viewport the
    cache was made from, else the biggest.
    """
    area = context.area
    if area is None or area.type != 'VIEW_3D':
        screen = context.screen
        areas = [a for a in screen.areas if a.type == 'VIEW_3D'] if screen else []
        if not areas:
            return None
        cached_from = store.frame_cache.area_ptr
        area = next((a for a in areas if a.as_pointer() == cached_from), None)
        if area is None:
            area = max(areas, key=lambda a: a.width * a.height)
    region, space = view3d_parts(area)
    return None if region is None else (area, region, space)


def view3d_parts(area):
    """(region, space) of a 3D Viewport area; region is None if it has no main region."""
    space = area.spaces.active
    regions = [r for r in area.regions if r.type == 'WINDOW']
    if not regions:
        return None, space
    # In quad view, use the region that owns the space's main view.
    return next((r for r in regions if r.data == space.region_3d), regions[-1]), space


def ensure_camera_view(space):
    """Switch the viewport to the scene camera's view if it is not there already.

    Cache Play only caches through the camera, so what you see while caching and while
    reviewing matches the cached pictures.
    """
    rv3d = space.region_3d
    if rv3d.view_perspective != 'CAMERA':
        rv3d.view_perspective = 'CAMERA'
        return True
    return False


def capture_size(scene, preset):
    render = scene.render
    return store.preview_size(render.resolution_x, render.resolution_y, preset)


class Capturer:
    """Renders frames through the scene camera, with one 3D Viewport's shading, into pixels.

    The viewport only supplies the look (shading, overlays); the picture always comes from
    the camera, so orbiting the viewport never makes the cache stale. Needs scene.camera.
    Creating one is cheap (it only works out the cache key); the offscreen buffer is made
    on the first capture.
    """

    mode = 'CAMERA'

    def __init__(self, scene, view_layer, region, space, preset, gpu_pack=True):
        render = scene.render
        self.gpu_pack = gpu_pack  # False: pack on the CPU (the Fast GPU Caching preference is off)
        self.scene = scene
        self.view_layer = view_layer
        self.region = region
        self.space = space
        self.width, self.height = capture_size(scene, preset)
        view_key = (render.resolution_x, render.resolution_y,
                    _freeze(render.pixel_aspect_x), _freeze(render.pixel_aspect_y))
        self.key = (
            scene.name, self.view_layer.name, self.mode, self.width, self.height, view_key,
            render.engine, _shading_key(space), _color_key(scene),
        )
        self.offscreen = None

    def capture(self, depsgraph):
        """Draw the current (already evaluated) frame and return it packed (see read())."""
        self.draw(depsgraph)
        return self.read()

    def draw(self, depsgraph):
        """Start drawing the current (already evaluated) frame into the offscreen buffer.

        The graphics card finishes it in the background; read() collects it. Evaluating the
        next frame in between hides the wait for the graphics card.
        """
        scene = self.scene
        # The camera may move or be switched by markers, so its matrices are read per frame.
        camera = scene.camera.evaluated_get(depsgraph)
        view_matrix = camera.matrix_world.normalized().inverted()
        projection_matrix = camera.calc_matrix_camera(
            depsgraph, x=self.width, y=self.height,
            scale_x=scene.render.pixel_aspect_x, scale_y=scene.render.pixel_aspect_y,
        )
        if self.offscreen is None:
            self.offscreen = gpu.types.GPUOffScreen(self.width, self.height, format='RGBA8')
        self.offscreen.draw_view3d(
            scene, self.view_layer, self.space, self.region, view_matrix, projection_matrix,
            do_color_management=True, draw_background=True,
        )
        if self.gpu_pack:
            with self.offscreen.bind():
                _stamp_alpha()

    def read(self):
        """The drawn frame as packed float32 pixels (store.pack_pixels format), bottom row first.

        With gpu_pack, _stamp_alpha() already made the alpha byte of every pixel 0x40, so the
        RGBA8 bytes read back are the packed floats: no packing on the CPU.
        """
        pixels = gpu.types.Buffer('UBYTE', self.width * self.height * 4)
        with self.offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.read_color(0, 0, self.width, self.height, 4, 0, 'UBYTE', data=pixels)
        if not self.gpu_pack:
            return store.pack_pixels(pixels)
        return np.frombuffer(pixels, dtype=np.float32)  # keeps the Buffer alive, no copy

    def free(self):
        if self.offscreen is not None:
            self.offscreen.free()
            self.offscreen = None
