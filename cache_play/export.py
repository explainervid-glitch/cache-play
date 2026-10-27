# SPDX-License-Identifier: GPL-3.0-or-later
"""Export the cached preview as a video file.

The cached pictures are written as PNG files to a temporary folder, placed as one image
strip in a temporary scene, and rendered from the Video Sequencer to an MP4 (H.264) at the
scene's frame rate. Nothing in the user's scene changes; the temporary scene and files are
removed afterwards. Uncached frames repeat the nearest cached one, so the timing matches.
"""

import os
import shutil
import tempfile

import bpy
import numpy as np
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper

from . import player, store, usage
from .store import scene_range


def _unpack(packed, width, height):
    """Packed float32 pixels (see store.pack_pixels) -> RGBA floats 0..1, bottom row first."""
    words = packed.view(np.uint32)
    rgba = np.empty((width * height, 4), dtype=np.float32)
    rgba[:, 0] = words & 0xFF
    rgba[:, 1] = (words >> 8) & 0xFF
    rgba[:, 2] = (words >> 16) & 0xFF
    rgba[:, 3] = 255
    rgba *= 1.0 / 255.0
    return rgba.ravel()


def _sources(frames, cached):
    """For each frame of the range, the cached frame to show: itself, else the previous
    cached one, else the next one."""
    ordered = sorted(cached)
    result, previous = [], None
    for frame in frames:
        if frame in cached:
            previous = frame
        result.append(previous)
    first = next((f for f in ordered if f >= frames[0]), ordered[0])
    return [first if f is None else f for f in result]


class CACHEPLAY_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "cacheplay.export"
    bl_label = "Export Preview"
    bl_description = "Save the cached preview as an MP4 video at the scene frame rate"

    filename_ext = ".mp4"
    filter_glob: StringProperty(default="*.mp4", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        state = player.state
        return not store.frame_cache.is_empty() and not (state.filling or (state.active and not state.paused))

    def invoke(self, context, event):
        blend = bpy.data.filepath
        name = (os.path.splitext(os.path.basename(blend))[0] if blend else "preview") + "_preview.mp4"
        self.filepath = os.path.join(os.path.dirname(blend) if blend else os.path.expanduser("~"), name)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    @usage.track("export")
    def execute(self, context):
        scene = context.scene
        cache = store.frame_cache
        first, last = scene_range(scene)
        frames = list(range(first, last + 1))
        _in_ram, cached = cache.snapshot()
        cached = cached.intersection(frames)
        if not cached:
            self.report({'ERROR'}, "Nothing of the frame range is cached yet")
            return {'CANCELLED'}
        sources = _sources(frames, cached)
        folder = tempfile.mkdtemp(prefix="cacheplay_export_")
        wm = context.window_manager
        wm.progress_begin(0, len(cached) + 1)
        try:
            names = self._write_pictures(cache, sources, folder, wm)
            self._encode(context, scene, cache, folder, names)
        except Exception as ex:
            usage.error("export")
            self.report({'ERROR'}, f"Export failed: {ex}")
            return {'CANCELLED'}
        finally:
            wm.progress_end()
            shutil.rmtree(folder, ignore_errors=True)
        missing = len(frames) - len(cached)
        note = f", {missing} uncached frames repeat their neighbour" if missing else ""
        self.report({'INFO'}, f"Exported {len(frames)} frames to {self.filepath}{note}")
        return {'FINISHED'}

    def _write_pictures(self, cache, sources, folder, wm):
        """One PNG per cached frame used, copies for repeats. Returns the file names in order."""
        image = bpy.data.images.new("CachePlay Export", cache.width, cache.height, alpha=False)
        image.file_format = 'PNG'
        written = {}
        names = []
        try:
            for i, frame in enumerate(sources):
                name = f"{i:06d}.png"
                path = os.path.join(folder, name)
                if frame in written:
                    shutil.copyfile(written[frame], path)
                else:
                    packed = cache.get(frame)
                    if packed is None:
                        raise RuntimeError(f"frame {frame} could not be read from the cache")
                    image.pixels.foreach_set(_unpack(packed, cache.width, cache.height))
                    image.filepath_raw = path
                    image.save()
                    written[frame] = path
                    wm.progress_update(len(written))
                names.append(name)
        finally:
            bpy.data.images.remove(image)
        return names

    def _encode(self, context, source, cache, folder, names):
        """Render the pictures from a temporary scene's Video Sequencer to the video file."""
        export = bpy.data.scenes.new("CachePlay Export")
        try:
            render = export.render
            render.fps, render.fps_base = source.render.fps, source.render.fps_base
            # H.264 needs even sizes.
            render.resolution_x = cache.width - cache.width % 2
            render.resolution_y = cache.height - cache.height % 2
            render.resolution_percentage = 100
            export.frame_start, export.frame_end = 1, len(names)
            settings = render.image_settings
            if hasattr(settings, "media_type"):
                settings.media_type = 'VIDEO'
            settings.file_format = 'FFMPEG'
            ffmpeg = render.ffmpeg
            ffmpeg.format = 'MPEG4'
            ffmpeg.codec = 'H264'
            ffmpeg.constant_rate_factor = 'HIGH'
            ffmpeg.audio_codec = 'NONE'
            render.use_file_extension = False
            render.filepath = bpy.path.abspath(self.filepath)
            render.use_sequencer = True
            render.use_compositing = False
            # The pictures already have the viewport's colours: pass them through unchanged.
            export.view_settings.view_transform = 'Standard'
            export.view_settings.look = 'None'
            export.view_settings.exposure = 0.0
            export.view_settings.gamma = 1.0
            editor = export.sequence_editor_create()
            strip = editor.strips.new_image(
                name="Cache Play", filepath=os.path.join(folder, names[0]), channel=1, frame_start=1)
            for name in names[1:]:
                strip.elements.append(name)
            bpy.ops.render.render(animation=True, scene=export.name)
            context.window_manager.progress_update(len(names) + 1)
        finally:
            bpy.data.scenes.remove(export)


classes = (CACHEPLAY_OT_export,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
