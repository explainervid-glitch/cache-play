# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: fill the cache from a 3D Viewport, then play it on a fixed clock."""

import bisect
import time

import bpy
from bpy.props import BoolProperty

from . import capture, display, draw, player, store, usage
from .store import scene_range
from .ui import configure_cache, get_prefs

# Seconds of caching per timer tick. Blender redraws the window between ticks (about 30 ms
# with a heavy scene), so longer slices cache faster; 0.25 s still answers Esc quickly.
FILL_SLICE = 0.25
TIMER_STEP = 0.001  # fine ticks; which frame shows comes from the clock, not the tick count

# Keys that never stop playback.
_PASS_KEYS = {
    'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM',
    'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_ALT', 'RIGHT_ALT', 'OSKEY',
}

_PLAY_HINT = "Cache Play   ·   Space: pause / resume   ·   ← →: step   ·   Esc or click: stop"
_CACHE_MODE_HINT = (
    "Cache Mode   ·   Drag in Timeline: move cache playhead   ·   Space: play / pause   ·   "
    "← →: step   ·   Shift ← →: first / last   ·   Esc: exit"
)


def _scrub_region_at(screen, x, y):
    """The time editor region under window position (x, y) where a drag moves the cache playhead.

    That is anywhere in a Timeline, or the frame-number band at the top of a Dope Sheet,
    Graph Editor or NLA, so keys can still be clicked there.
    """
    for area in screen.areas:
        if not store.is_frame_editor(area):
            continue
        for region in area.regions:
            if region.type != 'WINDOW':
                continue
            if not (region.x <= x < region.x + region.width and region.y <= y < region.y + region.height):
                continue
            band = draw.SCRUB_HEIGHT * bpy.context.preferences.system.ui_scale
            if area.ui_type == 'TIMELINE' or y >= region.y + region.height - band:
                return region
            return None
    return None


def _nearest_index(frames, frame):
    """Index of the cached frame closest to frame (frames is sorted)."""
    i = bisect.bisect_left(frames, frame)
    if i == 0:
        return 0
    if i == len(frames):
        return len(frames) - 1
    return i if frames[i] - frame < frame - frames[i - 1] else i - 1


class CACHEPLAY_OT_preview(bpy.types.Operator):
    bl_idname = "cacheplay.preview"
    bl_label = "Cache & Play"
    bl_description = (
        "Render every frame of the range from this viewport once, "
        "then play the stored pictures at exactly the scene frame rate"
    )

    fill_only: BoolProperty(
        name="Cache Only",
        description="Fill the cache without playing it",
        default=False,
        options={'SKIP_SAVE'},
    )
    cache_mode: BoolProperty(
        name="Cache Mode",
        description="After filling, stay paused for scrubbing instead of playing",
        default=False,
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        return not (player.state.active or player.state.filling)

    def _feature(self):
        """The name zeploy counts this run under."""
        return "cache mode" if self.cache_mode else "cache only" if self.fill_only else "cache and play"

    def invoke(self, context, event):
        result = self._start(context)
        if 'CANCELLED' not in result:
            usage.action(self._feature())
        return result

    def _start(self, context):
        found = capture.find_view3d(context)
        if found is None:
            self.report({'ERROR'}, "Cache Play needs a 3D Viewport")
            return {'CANCELLED'}
        area, region, space = found
        scene = context.scene
        settings = scene.cache_play
        if scene.camera is None:
            self.report({'ERROR'}, "Cache Play caches through the scene camera: add or set a camera first")
            return {'CANCELLED'}
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        if capture.ensure_camera_view(space):
            area.tag_redraw()

        # Fast GPU Caching: the graphics card packs the pixels and works while the next
        # frame is evaluated (see _fill_step). Off: the plain, one-step-at-a-time way.
        self._overlap = get_prefs(context).fast_caching
        self._capturer = capture.Capturer(scene, context.view_layer, region, space, settings.preview_size,
                                          gpu_pack=self._overlap)
        cap = self._capturer
        cache = store.frame_cache
        configure_cache(context)

        first, last = scene_range(scene)
        self._range = list(range(first, last + 1))
        _in_ram, cached = cache.snapshot()
        cached_here = len(cached.intersection(self._range))
        if self.cache_mode and cached_here:
            # Cache Mode reviews what is cached. It never clears or re-caches: that is
            # Cache & Play's job (or Cache When Idle's).
            if cap.key != cache.key:
                self.report({'INFO'}, "Cache Mode shows frames cached from another view or settings. "
                                      "Cache & Play updates them")
            elif cached_here < len(self._range):
                self.report({'INFO'}, f"{cached_here} of {len(self._range)} frames cached; "
                                      "Cache & Play fills the rest")
            self._todo = []
        else:
            cache.prepare(cap.key, scene.name, cap.width, cap.height, cap.mode, area.as_pointer())
            self._todo = [f for f in self._range if not cache.has(f)]
        self._total = len(self._todo)
        self._done = 0
        self._drawn = None  # frame drawn but not yet read back (see _fill_step)
        self._full = ""
        self._area = area
        self._orig_frame = scene.frame_current
        self._timer = None
        self._late_reads = 0
        self._generation = cache.generation
        self._added = cache.added
        self._drag_region = None

        if self._todo and not cache.has_room(self._todo[0]):
            self._full = cache.full_reason
            self._todo = []
        if not self._todo:
            self._free_capturer()
            if self.fill_only:
                if self._full:
                    self.report({'WARNING'}, f"{self._full}, nothing more can be cached")
                else:
                    self.report({'INFO'}, "Range is already cached")
                return {'FINISHED'}

        wm = context.window_manager
        self._timer = wm.event_timer_add(TIMER_STEP, window=context.window)
        wm.modal_handler_add(self)
        if self._todo:
            self._begin_fill(context)
        elif not self._begin_play(context):
            self._finish(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        state = player.state
        if state.filling:
            return self._modal_fill(context, event)
        if state.active:
            if store.frame_cache.generation != self._generation:
                self.report({'INFO'}, "Cache Play stopped: an edit cleared the cache")
                self._finish(context)
                return {'FINISHED', 'PASS_THROUGH'}
            if state.cache_mode:
                return self._modal_cache_mode(context, event)
            return self._modal_play(context, event)
        self._finish(context)
        return {'FINISHED'}

    def cancel(self, context):
        # Blender is closing the operator (file load, window closed): clean up, touch nothing else.
        if self._drag_region is not None:
            context.window.cursor_modal_restore()
            self._drag_region = None
        self._free_capturer()
        if player.state.filling:
            context.window_manager.progress_end()
        store.frame_cache.set_want(())
        player.state.stop()
        player.state.reset()
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    # Filling ----------------------------------------------------------------

    def _begin_fill(self, context):
        player.state.filling = True
        context.window_manager.progress_begin(0, self._total)
        self._area.header_text_set(f"Cache Play: caching {self._total} frames   ·   Esc to stop")

    def _modal_fill(self, context, event):
        if event.type == 'ESC' and event.value == 'PRESS':
            self._collect()  # the frame already drawn is kept
            self._end_fill(context)
            self.report({'INFO'}, f"Caching stopped, {self._done} frames cached")
            if self.cache_mode and self._begin_play(context):
                return {'RUNNING_MODAL'}  # review what was cached so far
            self._finish(context)
            return {'FINISHED'}
        if event.type == 'TIMER':
            try:
                done = self._fill_step(context)
            except Exception as ex:
                usage.error(self._feature())
                self._end_fill(context)
                self._finish(context)
                self.report({'ERROR'}, f"Cache Play failed while caching: {ex}")
                return {'CANCELLED'}
            if done:
                self._end_fill(context)
                if self._full:
                    self.report({'WARNING'}, f"{self._full}: cached {self._done} of {self._total} frames")
                if self.fill_only or not self._begin_play(context):
                    if not self._full:
                        self.report({'INFO'}, f"Cached {self._done} frames")
                    self._finish(context)
                    return {'FINISHED'}
            return {'PASS_THROUGH'}
        # Block clicks and keys while caching so nothing is edited halfway.
        return {'RUNNING_MODAL'} if event.value == 'PRESS' else {'PASS_THROUGH'}

    def _fill_step(self, context):
        """Cache frames for up to FILL_SLICE seconds. Returns True when finished or full.

        Overlapped: a frame is drawn, then the next one is evaluated while the graphics
        card finishes the drawing, and only then is the drawn frame read back and stored.
        """
        scene = context.scene
        deadline = time.perf_counter() + FILL_SLICE
        while self._todo:
            frame = self._todo.pop(0)
            scene.frame_set(frame)
            if not self._collect():
                break
            self._capturer.draw(context.evaluated_depsgraph_get())
            self._drawn = frame
            if not self._overlap and not self._collect():
                break
            if time.perf_counter() >= deadline:
                break
        if not self._todo:
            self._collect()
        context.window_manager.progress_update(self._done)
        self._area.header_text_set(
            f"Cache Play: cached {self._done} of {self._total} frames   ·   Esc to stop"
        )
        store.tag_time_editors()
        return not self._todo

    def _collect(self):
        """Read back and store the frame drawn last, if any. Returns False when the cache is full."""
        if self._drawn is None:
            return True
        frame, self._drawn = self._drawn, None
        cache = store.frame_cache
        if not cache.put(frame, self._capturer.read()):
            self._full = cache.full_reason
            self._todo = []
            return False
        self._done += 1
        return True

    def _end_fill(self, context):
        self._free_capturer()
        context.window_manager.progress_end()
        self._area.header_text_set(None)
        context.scene.frame_set(self._orig_frame)
        player.state.filling = False
        player.state.suppress(0.5)

    def _free_capturer(self):
        capturer = getattr(self, "_capturer", None)
        if capturer is not None:
            capturer.free()
            self._capturer = None

    # Playing ----------------------------------------------------------------

    def _begin_play(self, context):
        cache = store.frame_cache
        frames = [f for f in self._range if cache.has(f)]
        if not frames:
            self.report({'WARNING'}, "Nothing cached to play")
            return False
        scene = context.scene
        fps = scene.render.fps / scene.render.fps_base
        current = scene.frame_current
        self._late_reads = cache.sync_reads
        self._generation = cache.generation
        self._added = cache.added
        if self.cache_mode:
            # Start paused where Blender's own playhead is.
            start = _nearest_index(frames, current)
            player.state.start(self._area.as_pointer(), frames, fps, start, time.perf_counter(),
                               paused=True, cache_mode=True)
            context.workspace.status_text_set(_CACHE_MODE_HINT)
        else:
            start = frames.index(current) if current in frames else 0
            if start == len(frames) - 1:
                start = 0
            player.state.start(self._area.as_pointer(), frames, fps, start, time.perf_counter())
            context.workspace.status_text_set(_PLAY_HINT)
        self._redraw(context)
        return True

    def _modal_cache_mode(self, context, event):
        """Like paused playback, but Blender stays usable and Timeline drags move the cache playhead."""
        state = player.state
        now = time.perf_counter()
        if state.exit_requested:
            self._finish(context)
            return {'FINISHED', 'PASS_THROUGH'}
        if self._drag_region is not None:
            if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                self._scrub_to(context, event.mouse_x, now)
            elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                self._end_drag(context)
            elif event.type == 'ESC':
                self._end_drag(context)
                self._finish(context)
                return {'FINISHED'}
            elif event.type == 'TIMER':
                return {'PASS_THROUGH'}
            return {'RUNNING_MODAL'}
        if event.type == 'TIMER':
            if store.frame_cache.added != self._added:
                self._take_new_frames(now)  # Cache When Idle filled more frames
            if not state.advance(now, context.scene.cache_play.loop):
                state.seek(len(state.frames) - 1, now)  # stop at the end, stay in Cache Mode
            if state.dirty:
                self._redraw(context)
            return {'PASS_THROUGH'}
        if event.value != 'PRESS':
            return {'PASS_THROUGH'}
        plain = not (event.shift or event.ctrl or event.alt or event.oskey)
        if event.type == 'LEFTMOUSE' and plain:
            region = _scrub_region_at(context.window.screen, event.mouse_x, event.mouse_y)
            if region is None:
                return {'PASS_THROUGH'}
            self._drag_region = region
            context.window.cursor_modal_set('SCROLL_X')
            self._scrub_to(context, event.mouse_x, now)
            return {'RUNNING_MODAL'}
        if event.type == 'ESC':
            self._finish(context)
            return {'FINISHED'}
        if event.type == 'SPACE' and plain:
            last = len(state.frames) - 1
            if state.paused and state.index == last and not context.scene.cache_play.loop:
                state.index = 0
                state.dirty = True
            state.set_paused(not state.paused, now)
            self._redraw(context)
            return {'RUNNING_MODAL'}
        if event.type in {'LEFT_ARROW', 'RIGHT_ARROW'} and not (event.ctrl or event.alt or event.oskey):
            if event.shift:
                state.seek(0 if event.type == 'LEFT_ARROW' else len(state.frames) - 1, now)
            else:
                state.step(1 if event.type == 'RIGHT_ARROW' else -1, now)
            self._redraw(context)
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def _take_new_frames(self, now):
        """Make frames cached since Cache Mode started reachable, keeping the current frame."""
        cache = store.frame_cache
        self._added = cache.added
        state = player.state
        frame = state.frame
        frames = [f for f in self._range if cache.has(f)]
        if len(frames) == len(state.frames):
            return
        state.frames = frames
        state.stats.count = len(frames)
        state.index = _nearest_index(frames, frame)
        state.t0 = now - state.index / state.fps
        state.dirty = True

    def _scrub_to(self, context, mouse_x, now):
        region = self._drag_region
        frame = region.view2d.region_to_view(mouse_x - region.x, 0)[0]
        state = player.state
        state.seek(_nearest_index(state.frames, round(frame)), now)
        if state.dirty:
            self._redraw(context)

    def _end_drag(self, context):
        self._drag_region = None
        context.window.cursor_modal_restore()

    def _modal_play(self, context, event):
        state = player.state
        now = time.perf_counter()
        if event.type == 'TIMER':
            if not state.advance(now, context.scene.cache_play.loop):
                self._finish(context)
                return {'FINISHED'}
            if state.dirty:
                self._redraw(context)
            return {'PASS_THROUGH'}
        if event.value != 'PRESS' or event.type in _PASS_KEYS:
            return {'PASS_THROUGH'}
        if event.type == 'SPACE':
            state.set_paused(not state.paused, now)
            self._redraw(context)
            return {'RUNNING_MODAL'}
        if event.type in {'LEFT_ARROW', 'RIGHT_ARROW'}:
            state.step(1 if event.type == 'RIGHT_ARROW' else -1, now)
            self._redraw(context)
            return {'RUNNING_MODAL'}
        # Any other key or click stops playback, like After Effects. The event is used up,
        # so a click on a button only stops and does not also press the button.
        self._finish(context)
        return {'FINISHED'}

    def _redraw(self, context):
        state = player.state
        state.dirty = False
        self._read_ahead(context, state)
        display.display.prepare(store.frame_cache, state.frame)
        self._area.tag_redraw()
        store.tag_time_editors()

    def _read_ahead(self, context, state):
        """Tell the reader thread which frames come next, so they are in RAM in time.

        Paused (scrubbing), the window is centred on the playhead, nearest frames first.
        """
        cache = store.frame_cache
        frames = state.frames
        window = min(len(frames), cache.read_ahead)
        if state.paused:
            index = state.index
            nearby = sorted(range(len(frames)), key=lambda i: abs(i - index))[:window]
            cache.set_want([frames[i] for i in nearby])
            return
        wanted = frames[state.index:state.index + window]
        if len(wanted) < window and context.scene.cache_play.loop:
            wanted += frames[:window - len(wanted)]
        cache.set_want(wanted)

    def _finish(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self._drag_region is not None:
            self._end_drag(context)
        state = player.state
        cache = store.frame_cache
        # Cache Mode never moves Blender's own playhead.
        jump = state.active and not state.cache_mode and get_prefs(context).jump_on_stop
        jump_to = state.frame if jump else None
        was_active = state.active
        state.stop()
        cache.set_want(())
        if was_active:
            context.workspace.status_text_set(None)
            if state.last_summary is not None:
                state.last_summary["late_reads"] = cache.sync_reads - self._late_reads
        scene = context.scene
        if jump_to is not None and jump_to != scene.frame_current:
            state.suppress(0.5)
            scene.frame_set(jump_to)
        self._area.tag_redraw()
        store.tag_time_editors()


class CACHEPLAY_OT_clear(bpy.types.Operator):
    bl_idname = "cacheplay.clear"
    bl_label = "Clear Cache"
    bl_description = "Throw away all cached frames, in RAM and on disk"

    @classmethod
    def poll(cls, context):
        return not store.frame_cache.is_empty() and not (player.state.active or player.state.filling)

    @usage.track("clear cache")
    def execute(self, context):
        store.frame_cache.clear()
        display.display.reset()
        store.tag_time_editors()
        return {'FINISHED'}


class CACHEPLAY_OT_cache_mode(bpy.types.Operator):
    bl_idname = "cacheplay.cache_mode"
    bl_label = "Cache Mode"
    bl_description = (
        "Review the cache: the viewport shows cached frames, and dragging in the Timeline moves only "
        "the cache playhead. Blender's own playhead stays put. Never re-caches; only when nothing "
        "is cached yet is the range cached first"
    )

    @classmethod
    def poll(cls, context):
        state = player.state
        return state.cache_mode or not (state.active or state.filling)

    def invoke(self, context, event):
        state = player.state
        if state.cache_mode:
            state.exit_requested = True  # the running operator stops on its next event
            return {'FINISHED'}
        started = bpy.ops.cacheplay.preview('INVOKE_DEFAULT', cache_mode=True)
        # The preview operator owns the modal loop; this one is done either way.
        return {'FINISHED'} if started & {'RUNNING_MODAL', 'FINISHED'} else {'CANCELLED'}

    def execute(self, context):
        return self.invoke(context, None)


class CACHEPLAY_OT_settings(bpy.types.Operator):
    bl_idname = "cacheplay.settings"
    bl_label = "Cache Play Settings"
    bl_description = "Preview size, Loop, Cache When Idle, cache details and playback statistics"

    def invoke(self, context, event):
        # A toolbar button fires on press. Opening the drop-down then would close it again
        # on release, so wait for the release and open it like a normal menu button.
        if event is not None and event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        return self.execute(context)

    def modal(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            return self.execute(context)
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def execute(self, context):
        bpy.ops.wm.call_panel(name="VIEW3D_PT_cache_play_popover", keep_open=True)
        return {'FINISHED'}


classes = (CACHEPLAY_OT_preview, CACHEPLAY_OT_clear, CACHEPLAY_OT_cache_mode, CACHEPLAY_OT_settings)

# Default shortcuts. Blender 5.1/5.2's default keymap leaves F5-F8 free (only Shift+F5-F8
# are taken, for switching editors). They live in the Window keymap so they work in any
# editor; change them in the add-on preferences.
SHORTCUTS = (
    ("cacheplay.preview", 'F5'),
    ("cacheplay.cache_mode", 'F6'),
)
_keymap_items = []


def draw_keymap(layout, context):
    """Editable shortcut rows for the add-on preferences."""
    import rna_keymap_ui
    keyconfig = context.window_manager.keyconfigs.user
    keymap = keyconfig.keymaps.get("Window")
    if keymap is None:
        return
    col = layout.column()
    for idname, _key in SHORTCUTS:
        item = next((kmi for kmi in keymap.keymap_items if kmi.idname == idname), None)
        if item is not None:
            rna_keymap_ui.draw_kmi([], keyconfig, keymap, item, col, 0)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is not None:
        keymap = keyconfig.keymaps.new(name="Window", space_type='EMPTY')
        for idname, key in SHORTCUTS:
            _keymap_items.append((keymap, keymap.keymap_items.new(idname, key, 'PRESS')))


def unregister():
    for keymap, item in _keymap_items:
        keymap.keymap_items.remove(item)
    _keymap_items.clear()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
