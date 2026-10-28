# SPDX-License-Identifier: GPL-3.0-or-later
"""Cache Play: After Effects style cached preview for the 3D Viewport.

Each frame of the range is drawn once into an offscreen buffer and stored (RAM in front
of a disk cache). The stored pictures are played back on our own clock without touching
scene.frame_current, so Blender never re-evaluates the scene during playback.
"""

bl_info = {
    "name": "Cache Play",
    "author": "Zeus Animation Studio",
    "version": (0, 3, 1),
    "blender": (5, 1, 0),
    "location": "3D Viewport > Sidebar > Cache Play",
    "description": "After Effects style cached preview for the 3D Viewport",
    "category": "Animation",
}

import atexit
import os
import tomllib

from . import zeploy_usage


def _version():
    """The version in blender_manifest.toml (Blender ignores bl_info for an extension)."""
    try:
        with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"), "rb") as f:
            return tomllib.load(f)["version"]
    except Exception:
        return ".".join(map(str, bl_info["version"]))


# zeploy, the studio's deployment tool, counts which features get used (Cache & Play,
# Cache Only, Cache Mode, Export, Clear Cache). zeploy_usage.py appends the counts to a
# local file that zeploy's agent collects; it records no names, paths or scene data.
usage = zeploy_usage.Usage("cache_play", _version())

from . import display, draw, export, idle, ops, player, store, toolbar, ui  # noqa: E402


def register():
    ui.register()
    ops.register()
    export.register()
    draw.register()
    store.register()
    idle.register()
    toolbar.register()
    atexit.register(store.frame_cache.shutdown)


def unregister():
    atexit.unregister(store.frame_cache.shutdown)
    toolbar.unregister()
    idle.unregister()
    store.unregister()
    draw.unregister()
    export.unregister()
    ops.unregister()
    ui.unregister()
    display.unregister()
    store.frame_cache.shutdown()
    player.state.reset()
