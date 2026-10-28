# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings, add-on preferences and the sidebar panel."""

import bpy
from bpy.props import (
    BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty,
)

from . import capture, disk, player, store


class CachePlaySettings(bpy.types.PropertyGroup):
    preview_size: EnumProperty(
        name="Preview Size",
        description="Size of the cached pictures. Smaller fills faster and uses less memory",
        items=store.PRESETS,
        default='THIRD',
    )
    loop: BoolProperty(name="Loop", description="Start again at the end of the range", default=True)
    cache_when_idle: BoolProperty(
        name="Cache When Idle",
        description=(
            "Like After Effects: after a few seconds without working in Blender, fill missing frames "
            "one at a time. What counts as working is set in the add-on preferences (Idle Activity)"
        ),
        default=False,
    )
    # Changes on every edit and is saved with the file: part of a kept cache's name tag.
    edit_id: StringProperty(default="", options={'HIDDEN'})


def is_on(context=None):
    """False while Cache Play is switched off with the power button."""
    return get_prefs(context).enabled


def _on_power(enabled):
    """Switched off: stop playback / Cache Mode (the running operator ends on its next
    event), and redraw everything so the buttons and cache bar change at once."""
    if not enabled and player.state.active:
        player.state.exit_requested = True
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


class _DefaultPrefs:
    """Used when the add-on is loaded without being installed (development, tests)."""
    enabled = True
    memory_limit_gb = 4.0
    use_disk_cache = True
    cache_dir = ""
    disk_limit_gb = 20.0
    idle_delay = 3.0
    idle_activity = {'KEYS', 'EDITS', 'MOUSE'}
    jump_on_stop = True
    fast_caching = False
    space_plays_cache = False
    space_sizes = {'QUARTER'}
    toolbar = 'VIEWPORT'
    toolbar_collapsed = False


class CachePlayPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    enabled: BoolProperty(
        name="Cache Play On",
        description=(
            "Turn all of Cache Play on or off: buttons, shortcuts, the cache bar and Cache When Idle. "
            "Cached frames stay; the power button on the viewport toolbar does the same"
        ),
        default=True,
        update=lambda self, context: _on_power(self.enabled),
    )
    memory_limit_gb: FloatProperty(
        name="RAM Limit (GB)",
        description="Most memory the cached frames may use. With the disk cache on, older frames move out to disk",
        default=4.0,
        min=0.25,
        soft_max=64.0,
    )
    use_disk_cache: BoolProperty(
        name="Disk Cache",
        description="Save every cached frame to disk, so ranges longer than the RAM limit still play",
        default=True,
    )
    cache_dir: StringProperty(
        name="Cache Folder",
        description="Where frames are saved. Empty uses Blender's temporary folder. A fast SSD is best",
        subtype='DIR_PATH',
        default="",
    )
    disk_limit_gb: FloatProperty(
        name="Disk Limit (GB)",
        description=(
            "Most disk space for all cached frames together, kept caches of other files included. "
            "When it is reached, the least recently used caches are deleted first"
        ),
        default=20.0,
        min=1.0,
        soft_max=500.0,
    )
    idle_delay: FloatProperty(
        name="Idle Delay",
        description="Seconds without any Idle Activity before Cache When Idle starts or continues",
        default=3.0,
        min=0.5,
        max=30.0,
        subtype='TIME_ABSOLUTE',
        unit='TIME_ABSOLUTE',
    )
    idle_activity: EnumProperty(
        name="Idle Activity",
        description="What counts as working: any of these pauses Cache When Idle and restarts the Idle Delay",
        items=(
            ('KEYS', "Keys & Clicks in Blender",
             "Pressing a key or mouse button, or turning the wheel, while Blender is the window in front"),
            ('EDITS', "Edits, Frame & View Changes",
             "Changing the scene, moving to another frame, or orbiting, panning or zooming the viewport"),
            ('MOUSE', "Mouse Movement over Blender",
             "Just moving the mouse while Blender is the window in front"),
            ('OTHER_APPS', "Input in Other Programs",
             "Any key, click or mouse movement while another program is in front"),
        ),
        options={'ENUM_FLAG'},
        default={'KEYS', 'EDITS', 'MOUSE'},
    )
    jump_on_stop: BoolProperty(
        name="Move Playhead on Stop",
        description="When playback stops, move the scene to the frame that was showing",
        default=True,
    )
    fast_caching: BoolProperty(
        name="Fast GPU Caching",
        description=(
            "Let the graphics card pack the pixels and work on one frame while Blender calculates "
            "the next (about 30% faster). Off by default; turn it on if your graphics driver handles it well"
        ),
        default=False,
    )
    space_plays_cache: BoolProperty(
        name="Spacebar: Cache & Play",
        description=(
            "Space runs Cache & Play instead of Blender's normal playback, only while the scene's "
            "Preview Size is one of the sizes ticked below (and the scene has a camera). "
            "Any other size: Space plays as usual"
        ),
        default=False,
    )
    space_sizes: EnumProperty(
        name="Spacebar Sizes",
        description="Preview Sizes at which Space runs Cache & Play; bigger ones cache slower and use more memory",
        items=[item for item in store.PRESETS if item[0] != 'FULL'],
        options={'ENUM_FLAG'},
        default={'QUARTER'},
    )
    toolbar: EnumProperty(
        name="Toolbar",
        description="Where the Cache Play buttons appear (the sidebar panel is always there too)",
        items=(
            ('VIEWPORT', "3D Viewport", "A pill at the bottom centre of each 3D Viewport; the chevron folds it away"),
            ('TIMELINE', "Timeline Header", "A bar in the Timeline header, after the Playback menu"),
            ('BOTH', "Both", "The viewport pill and the Timeline bar"),
        ),
        default='VIEWPORT',
    )
    toolbar_collapsed: BoolProperty(
        name="Toolbar Folded",
        description="The viewport toolbar is folded down to its chevron",
        default=False,
    )

    def draw(self, context):
        """Four folding sections under one big on/off switch."""
        layout = self.layout
        row = layout.row()
        row.scale_y = 1.4
        row.prop(self, "enabled", toggle=True, icon='QUIT',
                 text="Cache Play is On" if self.enabled else "Cache Play is Off")

        def section(idname, title, icon):
            header, body = layout.panel(f"cacheplay_prefs_{idname}", default_closed=False)
            header.label(text=title, icon=icon)
            if body is not None:
                body.use_property_split = True
                body.use_property_decorate = False
                body.active = self.enabled
            return body

        body = section("playback", "Playback", 'PLAY')
        if body:
            body.prop(self, "jump_on_stop")
            body.prop(self, "fast_caching")

        body = section("storage", "Storage", 'DISK_DRIVE')
        if body:
            body.prop(self, "memory_limit_gb")
            body.prop(self, "use_disk_cache")
            col = body.column()
            col.active = self.use_disk_cache and self.enabled
            col.prop(self, "cache_dir", text="Folder")
            col.prop(self, "disk_limit_gb")
            count, size = _kept_usage()
            split = col.split(factor=0.4)
            left = split.row()
            left.alignment = 'RIGHT'
            left.label(text="Kept Caches")
            right = split.row(align=True)
            right.label(text=f"{count} · {size / 2 ** 30:.2f} GB")
            right.operator("cacheplay.clear_all", text="Clear All", icon='TRASH')

        body = section("idle", "Cache When Idle", 'TIME')
        if body:
            body.prop(self, "idle_delay")
            col = body.column(heading="Counts as Working", align=True)
            col.prop(self, "idle_activity", expand=True)

        body = section("controls", "Toolbar & Shortcuts", 'TOOL_SETTINGS')
        if body:
            body.row().prop(self, "toolbar", expand=True)
            body.prop(self, "space_plays_cache", text="Spacebar Runs Cache & Play")
            row = body.row(heading="At Sizes", align=True)
            row.active = self.space_plays_cache and self.enabled
            row.prop(self, "space_sizes", expand=True)
            from . import ops  # ops imports this module, so import late
            box = body.box()
            box.use_property_split = False
            ops.draw_keymap(box, context)


def get_prefs(context=None):
    addon = (context or bpy.context).preferences.addons.get(__package__)
    return addon.preferences if addon is not None else _DefaultPrefs


def configure_cache(context=None):
    """Pass the current preferences on to the frame cache."""
    preferences = (context or bpy.context).preferences
    prefs = get_prefs(context)
    if prefs.cache_dir:
        base_dir = bpy.path.abspath(prefs.cache_dir)
    else:
        base_dir = disk.default_base_dir(preferences)
    store.frame_cache.configure(
        int(prefs.memory_limit_gb * 2 ** 30), int(prefs.disk_limit_gb * 2 ** 30),
        prefs.use_disk_cache, base_dir,
    )


_usage_cache = {"time": 0.0, "value": (0, 0)}


def _kept_usage():
    """Kept caches on disk, measured at most every few seconds (the folder may be on a network)."""
    import time
    now = time.monotonic()
    if now - _usage_cache["time"] > 5.0:
        configure_cache()
        _usage_cache["value"] = store.frame_cache.kept_usage()
        _usage_cache["time"] = now
    return _usage_cache["value"]


def _megabytes(n_bytes):
    return n_bytes / (1024 * 1024)


def _draw_cache_mode_toggle(layout):
    layout.operator("cacheplay.cache_mode", text="Cache Mode", icon='FILE_MOVIE',
                    depress=player.state.cache_mode)


TIMELINE_BAR_WIDE = 1750  # Timeline width (px at 1x UI scale) from which the bar shows button text


def _cached_in_range(scene):
    """(cached frames in the scene range, frames in the range)."""
    first, last = store.scene_range(scene)
    _in_ram, cached = store.frame_cache.snapshot()
    total = last - first + 1
    return len(cached.intersection(range(first, last + 1))), total


# Timeline bar placement
# ----------------------
# The bar belongs between Blender's "Playback" drop-down and the Auto Keying button. Both
# are drawn by bl_ui's playback_controls(layout, context), which an add-on cannot add to.
# So the Dope Sheet module's reference to it is wrapped: inside, the layout is replaced by
# a stand-in that draws the bar right after the Playback popover. If a future Blender
# draws its header differently, the bar is not drawn there and the header append below
# draws it at the right end instead.

_bar = {"drawn": False}       # drawn after the Playback popover in this header redraw
_original_playback_controls = None


class _AfterPlaybackLayout:
    """Stands in for the Timeline header layout inside Blender's playback_controls()."""

    def __init__(self, layout, context):
        self._layout = layout
        self._context = context

    def __getattr__(self, name):
        return getattr(self._layout, name)

    def popover(self, *args, **kwargs):
        result = self._layout.popover(*args, **kwargs)
        if kwargs.get("panel") == "TIME_PT_playback":
            self._layout.separator()
            _draw_bar(self._layout, self._context)
            _bar["drawn"] = True
        return result


def _timeline_bar_enabled(context):
    return get_prefs(context).toolbar in {'TIMELINE', 'BOTH'}


def _playback_controls(layout, context):
    space, region = context.space_data, context.region
    if (space.type == 'DOPESHEET_EDITOR' and space.mode == 'TIMELINE' and region.type == 'HEADER'
            and _timeline_bar_enabled(context)):
        layout = _AfterPlaybackLayout(layout, context)
    _original_playback_controls(layout, context)


def _wrap_playback_controls():
    global _original_playback_controls
    from bl_ui import space_dopesheet
    if getattr(space_dopesheet, "playback_controls", None) is None:
        return
    _original_playback_controls = space_dopesheet.playback_controls
    space_dopesheet.playback_controls = _playback_controls


def _unwrap_playback_controls():
    global _original_playback_controls
    from bl_ui import space_dopesheet
    if _original_playback_controls is not None and space_dopesheet.playback_controls is _playback_controls:
        space_dopesheet.playback_controls = _original_playback_controls
    _original_playback_controls = None


def _draw_timeline_header(self, context):
    """Appended to the Dope Sheet header: Cache Mode toggle in Dope Sheet modes, and the
    Timeline bar at the right end only if it could not go after the Playback drop-down."""
    layout = self.layout
    if not _timeline_bar_enabled(context):
        return
    if context.space_data.mode != 'TIMELINE':
        if is_on(context):
            layout.separator()
            _draw_cache_mode_toggle(layout)
        return
    if _bar["drawn"]:
        _bar["drawn"] = False
        return
    layout.separator()
    _draw_bar(layout, context)


def _draw_bar(layout, context):
    """The Cache Play controls laid out horizontally like transport controls, in joined
    groups: play/review | count + export, clear | size | loop, idle | drop-down."""
    settings = context.scene.cache_play
    # Blender's own Timeline header takes about 1100 px; on narrower editors the two main
    # buttons drop their text so the whole bar still fits.
    wide = context.region.width >= TIMELINE_BAR_WIDE * context.preferences.system.ui_scale
    if not is_on(context):
        _draw_power(layout)
        return

    row = layout.row(align=True)
    row.operator("cacheplay.preview", text="Cache & Play" if wide else "", icon='PLAY')
    row.operator("cacheplay.cache_mode", text="Cache Mode" if wide else "",
                 icon='FILE_MOVIE', depress=player.state.cache_mode)

    cached, total = _cached_in_range(context.scene)
    problem = store.frame_cache.disk_error or store.frame_cache.full_reason
    row = layout.row(align=True)
    row.label(text=f"{cached}/{total}",
              icon='ERROR' if problem else ('CHECKMARK' if cached == total else 'SEQ_STRIP_DUPLICATE'))
    row.operator("cacheplay.export", text="", icon='EXPORT')
    row.operator("cacheplay.clear", text="", icon='TRASH')

    row = layout.row(align=True)
    sub = row.row(align=True)
    sub.ui_units_x = 4 if wide else 3.2
    sub.prop(settings, "preview_size", text="")
    row.prop(settings, "loop", text="", toggle=True, icon='FILE_REFRESH')
    row.prop(settings, "cache_when_idle", text="", toggle=True, icon='TIME')
    layout.popover(panel="DOPESHEET_PT_cache_play", text="", icon='PREFERENCES')
    _draw_power(layout)


def _draw_power(layout, text=""):
    """The power button: all of Cache Play on / off."""
    layout.operator("cacheplay.power", text=text, icon='QUIT', depress=is_on())


def _draw_off(layout):
    """What a panel shows while Cache Play is switched off."""
    row = layout.row()
    row.scale_y = 1.4
    _draw_power(row, text="Turn Cache Play On")
    layout.label(text="Off: no buttons, shortcuts, cache bar or idle caching", icon='INFO')


def _draw_actions(layout):
    """Cache Only and Export, two equal buttons under Cache & Play and Cache Mode."""
    row = layout.row(align=True)
    row.operator("cacheplay.preview", text="Cache Only", icon='RENDER_ANIMATION').fill_only = True
    row.operator("cacheplay.export", text="Export", icon='EXPORT')


def _draw_settings(layout, context):
    """Preview size and the two toggles, joined into one block."""
    settings = context.scene.cache_play
    col = layout.column(align=True)
    col.row(align=True).prop(settings, "preview_size", expand=True)
    row = col.row(align=True)
    row.prop(settings, "loop", toggle=True, icon='FILE_REFRESH')
    row.prop(settings, "cache_when_idle", text="Cache When Idle", toggle=True, icon='TIME')


def _draw_status(layout, context):
    """How much is cached (with the Clear button beside it), any problem, and what idle
    caching is doing, in one box."""
    cached, total = _cached_in_range(context.scene)
    col = layout.box().column(align=True)
    row = col.row()
    if cached == total:
        row.label(text=f"All {total} frames cached", icon='CHECKMARK')
    elif cached:
        row.label(text=f"{cached} of {total} frames cached", icon='SEQ_STRIP_DUPLICATE')
    else:
        row.label(text="Nothing cached yet", icon='RADIOBUT_OFF')
    row.operator("cacheplay.clear", text="", icon='TRASH')
    problem = store.frame_cache.disk_error or store.frame_cache.full_reason
    if problem:
        col.label(text=problem, icon='ERROR')
    if context.scene.cache_play.cache_when_idle and cached < total:
        from . import idle  # idle imports this module, so import late
        if idle.status:
            col.label(text=idle.status, icon='TIME')


def _value_rows(layout, rows):
    """Label / value pairs in two aligned columns."""
    col = layout.column(align=True)
    for label, value in rows:
        split = col.split(factor=0.4, align=True)
        left = split.row()
        left.alignment = 'RIGHT'
        left.label(text=label)
        split.label(text=value)


def _draw_details(layout, context):
    scene = context.scene
    camera = scene.camera
    width, height = capture.capture_size(scene, scene.cache_play.preview_size)
    stats = store.frame_cache.stats()
    disk = (f"{stats['disk_frames']} frames · {_megabytes(stats['disk_bytes']):.0f} MB"
            if get_prefs(context).use_disk_cache else "off")
    _value_rows(layout, (
        ("Camera", camera.name if camera else "none"),
        ("Size", f"{width}×{height}"),
        ("Per frame", f"{_megabytes(width * height * 4):.1f} MB"),
        ("RAM", f"{stats['ram_frames']} frames · {_megabytes(stats['ram_bytes']):.0f} MB"),
        ("Disk", disk),
    ))
    layout.operator("preferences.addon_show", text="Limits & Folder…", icon='PREFERENCES').module = __package__


def _draw_stats(layout):
    summary = player.state.last_summary
    if not summary:
        layout.label(text="Play the cache once to measure it", icon='INFO')
        return
    rows = [
        ("Frames shown", str(summary['shown'])),
        ("Dropped", str(summary['dropped'])),
        ("Jitter", f"{summary['jitter_ms']:.2f} ms (worst {summary['worst_ms']:.1f})"),
        ("Redraw", f"{summary['latency_ms']:.1f} ms (worst {summary['latency_max_ms']:.1f})"),
    ]
    if summary.get("late_reads"):
        rows.append(("Read late", str(summary['late_reads'])))
    _value_rows(layout, rows)


def _draw_popover(layout, context, with_settings):
    """Drop-down shared by the Timeline bar and the viewport toolbar."""
    if not is_on(context):
        _draw_off(layout)
        return
    _draw_actions(layout)
    if with_settings:
        # The viewport toolbar only has round buttons, so its drop-down holds the settings.
        _draw_settings(layout, context)
    _draw_status(layout, context)
    header, body = layout.panel("cacheplay_details", default_closed=True)
    header.label(text="Cache Details")
    if body is not None:
        _draw_details(body, context)
    header, body = layout.panel("cacheplay_stats", default_closed=True)
    header.label(text="Statistics")
    if body is not None:
        _draw_stats(body)


class DOPESHEET_PT_cache_play(bpy.types.Panel):
    """Drop-down from the Timeline bar."""
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_region_type = 'HEADER'
    bl_label = "Cache Play"
    bl_ui_units_x = 14

    def draw(self, context):
        _draw_popover(self.layout, context, with_settings=False)


class VIEW3D_PT_cache_play_popover(bpy.types.Panel):
    """Drop-down from the floating toolbar's settings button."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Cache Play"
    bl_ui_units_x = 14

    def draw(self, context):
        _draw_popover(self.layout, context, with_settings=True)


class VIEW3D_PT_cache_play(bpy.types.Panel):
    """Main panel: the buttons and settings used every day."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Cache Play"
    bl_label = "Cache Play"

    def draw_header_preset(self, context):
        _draw_power(self.layout)

    def draw(self, context):
        layout = self.layout
        if not is_on(context):
            _draw_off(layout)
            return
        col = layout.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.4
        row.operator("cacheplay.preview", text="Cache & Play", icon='PLAY')
        _draw_cache_mode_toggle(row)
        _draw_actions(col)
        _draw_settings(layout, context)
        _draw_status(layout, context)


class VIEW3D_PT_cache_play_storage(bpy.types.Panel):
    """Where the pictures come from and where they are kept."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Cache Play"
    bl_label = "Cache Details"
    bl_parent_id = "VIEW3D_PT_cache_play"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return is_on(context)

    def draw(self, context):
        _draw_details(self.layout, context)


class VIEW3D_PT_cache_play_stats(bpy.types.Panel):
    """How smoothly the last cached playback ran."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Cache Play"
    bl_label = "Statistics"
    bl_parent_id = "VIEW3D_PT_cache_play"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return is_on(context)

    def draw(self, context):
        _draw_stats(self.layout)


classes = (CachePlaySettings, CachePlayPreferences, VIEW3D_PT_cache_play,
           VIEW3D_PT_cache_play_storage, VIEW3D_PT_cache_play_stats, DOPESHEET_PT_cache_play,
           VIEW3D_PT_cache_play_popover)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.cache_play = PointerProperty(type=CachePlaySettings)
    bpy.types.DOPESHEET_HT_header.append(_draw_timeline_header)
    _wrap_playback_controls()


def unregister():
    _unwrap_playback_controls()
    bpy.types.DOPESHEET_HT_header.remove(_draw_timeline_header)
    del bpy.types.Scene.cache_play
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
