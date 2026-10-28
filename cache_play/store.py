# SPDX-License-Identifier: GPL-3.0-or-later
"""Frame storage: a RAM tier in front of a disk tier, and cache invalidation.

Disk holds every cached frame, one raw file each, written by a background thread.
RAM holds the frames ready to show. During playback a reader thread loads the frames just
ahead of the playhead from disk into RAM and lets older ones go, like After Effects'
"Preview from Disk Cache". With the disk cache off, RAM holds everything, as in v1.
"""

import hashlib
import json
import os
import queue
import shutil
import threading
import time
import uuid

import bpy
import numpy as np
from bpy.app.handlers import persistent

from . import disk, player

PRESETS = (
    ('FULL', "Full", "Render resolution, capped to fit inside 1920×1080 (1080×1920 for vertical)"),
    ('HALF', "Half", "Half of Full"),
    ('THIRD', "Third", "A third of Full"),
    ('QUARTER', "Quarter", "A quarter of Full"),
)
_DIVISORS = {'FULL': 1, 'HALF': 2, 'THIRD': 3, 'QUARTER': 4}


def scene_range(scene):
    """(first, last) frame to cache: the preview range when one is set."""
    if scene.use_preview_range:
        return scene.frame_preview_start, scene.frame_preview_end
    return scene.frame_start, scene.frame_end


def full_size(width, height):
    """Fit (width, height) inside 1920×1080, or 1080×1920 when taller than wide. Never upscale."""
    box_w, box_h = (1920, 1080) if width >= height else (1080, 1920)
    scale = min(1.0, box_w / width, box_h / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def preview_size(width, height, preset):
    full_w, full_h = full_size(width, height)
    divisor = _DIVISORS[preset]
    return max(16, round(full_w / divisor)), max(16, round(full_h / divisor))


_PACK_BASE = np.uint32(0x40000000)
_RGB_MASK = np.uint32(0x00FFFFFF)


def pack_pixels(pixels):
    """RGBA8 bytes (a UBYTE gpu.types.Buffer) -> one float32 per pixel.

    gpu.types.GPUTexture only accepts FLOAT data, and real float colour would take 4x the
    memory. R, G and B go in the low 24 bits of a float32 whose top bits are 0x40000000,
    so every value is a normal float (2.0 to 8.0) that uploads bit for bit. Alpha is
    dropped because viewport captures are opaque. display.py unpacks it on the GPU.

    Captures skip this: capture._stamp_alpha() writes the 0x40 top byte on the graphics
    card, so the bytes read back are already packed. This CPU version is the reference.
    """
    words = np.frombuffer(pixels, dtype=np.uint32)
    return ((words & _RGB_MASK) | _PACK_BASE).view(np.float32)


class FrameCache:
    """Cached frames for one view of one scene.

    Everything below `lock` is shared with the writer and reader threads.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.key = None
        self.scene_name = ""
        self.mode = 'CAMERA'
        self.width = 0
        self.height = 0
        self.area_ptr = 0          # 3D Viewport the cache was made from
        self.generation = 0        # goes up on every clear
        self.added = 0             # goes up on every stored frame
        self.ram = {}              # frame -> packed array, least recently used first
        self.disk = set()          # frames with a complete file
        self.pending = set()       # frames queued for writing; they stay in RAM until written
        self.folder = None         # folder of the frame files: kept (saved .blend) or session
        self.disk_error = ""
        self.full_reason = ""      # why the last put() was refused
        self.sync_reads = 0        # frames the main thread had to read itself (reader was late)
        self.read_mb_s = 0.0
        self.use_disk = True
        self.ram_limit = 4 * 2 ** 30
        self.disk_limit = 20 * 2 ** 30
        self.base_dir = ""
        self._session = None
        self._kept = None          # lock on the kept folder in use (saved .blend), else None
        self._others = []          # (last used, folder, bytes) of the other kept caches
        self._others_bytes = 0
        self._over_disk_limit = False
        self._want = ()            # frames to keep in RAM, nearest first (set during playback)
        self._want_set = frozenset()
        self._jobs = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads = []

    # Sizes ------------------------------------------------------------------

    @property
    def frame_bytes(self):
        return self.width * self.height * 4

    @property
    def capacity(self):
        """How many frames fit in the RAM limit."""
        return max(2, self.ram_limit // max(1, self.frame_bytes))

    @property
    def read_ahead(self):
        return max(1, self.capacity - 2)

    def stats(self):
        with self.lock:
            stored = len(self.ram.keys() | self.disk | self.pending)
            return {
                "frames": stored,
                "ram_frames": len(self.ram),
                "ram_bytes": len(self.ram) * self.frame_bytes,
                "disk_frames": len(self.disk),
                "disk_bytes": (len(self.disk) + len(self.pending)) * self.frame_bytes,
            }

    # Setup ------------------------------------------------------------------

    def configure(self, ram_limit, disk_limit, use_disk, base_dir):
        if (use_disk, base_dir) != (self.use_disk, self.base_dir) and self.key is not None:
            self.clear(delete=False)
        self.ram_limit = ram_limit
        self.disk_limit = disk_limit
        self.use_disk = use_disk
        self.base_dir = base_dir

    def prepare(self, key, scene_name, width, height, mode, area_ptr):
        """Make the cache match key. Returns True when old frames were set aside.

        For a saved .blend the frames go into a kept folder named by persist_tag(); if one
        from an earlier session still matches, its frames are cached already.
        """
        self.area_ptr = area_ptr
        if key == self.key:
            return False
        had_frames = not self.is_empty()
        self.clear(delete=False)
        folder, kept, frames = None, None, set()
        if self.use_disk:
            try:
                tag = persist_tag(scene_name, key)
                if tag is not None:
                    kept, frames = self._open_kept(tag, width, height, mode)
                    folder = kept.path if kept is not None else None
                if folder is None:
                    folder = os.path.join(self._ensure_session().path, f"{self.generation:05d}")
                    os.makedirs(folder, exist_ok=True)
                self._start_threads()
            except OSError as ex:
                folder, kept, frames = None, None, set()
                self.disk_error = f"Disk cache unavailable: {ex}"
        with self.lock:
            self.key = key
            self.scene_name = scene_name
            self.width = width
            self.height = height
            self.mode = mode
            self.folder = folder
            self._kept = kept
            self.disk = frames
        if kept is not None:
            self._measure_others()
        return had_frames

    def _open_kept(self, tag, width, height, mode):
        """Lock the kept folder for tag. Returns (lock, frames reused), or (None, set()) when
        another Blender is using it."""
        folder = os.path.join(disk.caches_dir(self.base_dir), tag["id"])
        os.makedirs(folder, exist_ok=True)
        try:
            kept = disk.FolderLock(folder)
        except OSError:
            return None, set()
        info = disk.read_info(folder)
        reusable = (info is not None and info.get("width") == width and info.get("height") == height
                    and (info.get("session") == disk.SESSION_ID
                         or (info.get("blend_mtime") is not None
                             and info.get("blend_mtime") == tag["disk_mtime"])))
        if reusable:
            frames = disk.frames_in(folder, width * height * 4)
        else:
            disk.remove_frames(folder)  # pictures of another state of the file
            frames = set()
        fields = {k: v for k, v in tag.items() if k not in ("id", "disk_mtime", "at_disk_state")}
        if tag["at_disk_state"]:
            blend_mtime = tag["disk_mtime"]  # the saved .blend shows exactly this state
        else:
            blend_mtime = info.get("blend_mtime") if reusable else None
        disk.write_info(folder, {
            **fields, "width": width, "height": height, "mode": mode,
            "session": disk.SESSION_ID, "last_used": time.time(), "blend_mtime": blend_mtime,
        })
        return kept, frames

    def adopt(self, scene_name, info, folder):
        """Use a kept cache found when a file is opened (see find_kept_cache)."""
        try:
            kept = disk.FolderLock(folder)
        except OSError:
            return False
        frames = disk.frames_in(folder, info["width"] * info["height"] * 4)
        if not frames:
            kept.release()
            return False
        self.clear(delete=False)
        try:
            self._start_threads()
            info["session"] = disk.SESSION_ID
            info["last_used"] = time.time()
            disk.write_info(folder, info)
        except OSError:
            pass
        with self.lock:
            self.key = _tupled(info["key"])
            self.scene_name = scene_name
            self.width = info["width"]
            self.height = info["height"]
            self.mode = info.get("mode", 'CAMERA')
            self.folder = folder
            self._kept = kept
            self.disk = frames
        self._measure_others()
        return True

    def saved(self, scene_name, tag_fields, mtime):
        """The .blend was saved: if this cache shows the saved state, note the file's new date
        (and its new path after Save As) so the next session finds it."""
        with self.lock:
            kept = self._kept
            folder = self.folder
        if kept is None or self.scene_name != scene_name:
            return
        info = disk.read_info(folder)
        if info is None or info.get("edit_id") != tag_fields["edit_id"]:
            return
        info.update(tag_fields, blend_mtime=mtime, last_used=time.time())
        try:
            disk.write_info(folder, info)
        except OSError:
            pass

    def _ensure_session(self):
        session = self._session
        if session is None or session.base_dir != self.base_dir:
            if session is not None:
                self._jobs.put(("close", session))
            session = self._session = disk.Session(self.base_dir)
            for path in session.abandoned() + disk.leftover_trash(self.base_dir):
                self._jobs.put(("delete", path))
        self._start_threads()
        return session

    def clear(self, delete=True):
        """Forget the frames. delete=False leaves a kept folder on disk (an edit made it out of
        date, but reopening the saved file may want it again); session folders always go."""
        with self.lock:
            old_folder = self.folder
            kept, self._kept = self._kept, None
            self.key = None
            self.folder = None
            self.ram.clear()
            self.disk.clear()
            self.pending.clear()
            self._want = ()
            self._want_set = frozenset()
            self.generation += 1
            self.disk_error = ""
            self.full_reason = ""
        if kept is not None:
            kept.release()
            if delete:
                self._trash(old_folder)
        elif old_folder:
            self._jobs.put(("delete", old_folder))

    def _trash(self, folder):
        """Delete a kept folder: renamed at once so nothing finds it, removed by the writer."""
        try:
            trash = disk.to_trash(folder)
        except OSError:
            return
        self._jobs.put(("delete", trash))
        self._start_threads()

    def clear_all(self):
        """Delete this cache and every kept cache not in use by another Blender."""
        self.clear(delete=True)
        if not self.base_dir:
            return
        for folder, _info in disk.kept_caches(self.base_dir):
            if not disk.in_use(folder):
                self._trash(folder)

    def kept_usage(self):
        """(number of kept caches, bytes) in the cache folder, this one included."""
        if not self.base_dir:
            return 0, 0
        caches = disk.kept_caches(self.base_dir)
        return len(caches), sum(disk.folder_bytes(folder) for folder, _info in caches)

    def _measure_others(self):
        """Bytes used by the other kept caches, for the disk limit."""
        own = self.folder
        self._others = [(info.get("last_used", 0), folder, disk.folder_bytes(folder))
                        for folder, info in disk.kept_caches(self.base_dir) if folder != own]
        self._others_bytes = sum(size for _used, _folder, size in self._others)

    def _evict_others(self, needed):
        """Delete the least recently used other kept caches until needed more bytes fit.
        Returns True when something was deleted."""
        own = (len(self.disk) + len(self.pending)) * self.frame_bytes
        freed = False
        for entry in sorted(self._others):
            if own + self._others_bytes + needed <= self.disk_limit:
                break
            _used, folder, size = entry
            if disk.in_use(folder):
                continue
            self._trash(folder)
            self._others.remove(entry)
            self._others_bytes -= size
            freed = True
        return freed

    def shutdown(self):
        """Stop the threads and delete this session's files; kept caches stay on disk."""
        self.clear(delete=False)
        if self._session is not None:
            self._jobs.put(("close", self._session))
            self._session = None
        self._stop.set()
        self._wake.set()
        if self._threads:
            self._jobs.put(None)
            for thread in self._threads:
                thread.join(timeout=5.0)
        self._threads = []
        self._jobs = queue.Queue()

    # Frames -----------------------------------------------------------------

    def is_empty(self):
        with self.lock:
            return not (self.ram or self.disk or self.pending)

    def has(self, frame):
        with self.lock:
            return frame in self.ram or frame in self.disk or frame in self.pending

    def snapshot(self):
        """(frames in RAM, all cached frames) for drawing the timeline bar."""
        with self.lock:
            ram = set(self.ram)
            return ram, ram | self.disk | self.pending

    def has_room(self, frame=None):
        with self.lock:
            if self._room_locked(frame):
                return True
            if not self._over_disk_limit:
                return False
        # Make room by deleting the least recently used other kept caches, then check again.
        if not self._evict_others(self.frame_bytes):
            return False
        with self.lock:
            return self._room_locked(frame)

    def _room_locked(self, frame):
        self._over_disk_limit = False
        if self.folder is not None:
            if self.disk_error:
                self.full_reason = self.disk_error
                return False
            if frame in self.disk or frame in self.pending:
                return True
            own = (len(self.disk) + len(self.pending) + 1) * self.frame_bytes
            if own + self._others_bytes > self.disk_limit:
                self._over_disk_limit = self._others_bytes > 0
                self.full_reason = "Disk limit reached"
                return False
            return True
        if frame in self.ram or len(self.ram) < self.capacity:
            return True
        self.full_reason = self.disk_error or "RAM limit reached (disk cache is off)"
        return False

    def put(self, frame, array):
        """Store packed pixels (see pack_pixels) for frame. Returns False when there is no room."""
        if not self.has_room(frame):
            return False
        with self.lock:
            self.ram.pop(frame, None)
            self.ram[frame] = array
            if self.folder is not None and frame not in self.disk:
                self.pending.add(frame)
                self._jobs.put(("write", self.folder, frame, array))
            self._evict_locked(0)
            self.added += 1
        return True

    def get(self, frame):
        """Packed pixels for frame, read from disk right now if the reader has not got it yet."""
        with self.lock:
            array = self.ram.pop(frame, None)
            if array is not None:
                self.ram[frame] = array  # now the most recently used
                return array
            folder = self.folder if frame in self.disk else None
            count = self.width * self.height
        if folder is None:
            return None
        array = disk.read_frame(disk.frame_path(folder, frame), count)
        if array is None:
            return None
        with self.lock:
            self.sync_reads += 1
            if folder == self.folder:
                self.ram[frame] = array
                self._evict_locked(0)
        return array

    def set_want(self, frames):
        """Frames the reader should have in RAM, nearest to the playhead first."""
        with self.lock:
            self._want = tuple(frames)
            self._want_set = frozenset(self._want)
        self._wake.set()

    def _evict_locked(self, room):
        """Drop least recently used frames that are safe on disk and not wanted soon."""
        limit = self.capacity - room
        if len(self.ram) <= limit:
            return
        for frame in list(self.ram):
            if len(self.ram) <= limit:
                break
            if frame in self.disk and frame not in self._want_set:
                del self.ram[frame]

    # Threads ----------------------------------------------------------------

    def _start_threads(self):
        if self._threads:
            return
        self._stop.clear()
        for target, name in ((self._write_loop, "CachePlayWriter"), (self._read_loop, "CachePlayReader")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def _write_loop(self):
        jobs = self._jobs
        while True:
            job = jobs.get()
            if job is None:
                return
            kind = job[0]
            try:
                if kind == "write":
                    self._write(*job[1:])
                elif kind == "delete":
                    shutil.rmtree(job[1], ignore_errors=True)
                elif kind == "close":
                    job[1].close()
            except Exception as ex:
                print(f"Cache Play: {kind} failed: {ex}")

    def _write(self, folder, frame, array):
        with self.lock:
            if folder != self.folder:
                return  # cleared meanwhile; the folder is deleted by a later job
        error = None
        try:
            disk.write_frame(disk.frame_path(folder, frame), array)
        except OSError as ex:
            error = ex
        with self.lock:
            if folder != self.folder:
                return
            self.pending.discard(frame)
            if error is None:
                self.disk.add(frame)
                self._evict_locked(0)
            else:
                self.disk_error = f"Disk write failed: {error}"

    def _read_loop(self):
        while not self._stop.is_set():
            self._wake.wait(0.1)
            self._wake.clear()
            while not self._stop.is_set():
                with self.lock:
                    target = next((f for f in self._want if f not in self.ram and f in self.disk), None)
                    if target is None:
                        break
                    self._evict_locked(1)
                    if len(self.ram) >= self.capacity:
                        break  # RAM holds only wanted or unwritten frames; wait for the playhead
                    folder = self.folder
                    count = self.width * self.height
                start = time.perf_counter()
                array = disk.read_frame(disk.frame_path(folder, target), count)
                elapsed = time.perf_counter() - start
                with self.lock:
                    if folder != self.folder:
                        continue
                    if array is None:
                        self.disk.discard(target)  # broken file: render this frame again
                        continue
                    if target in self._want_set and target not in self.ram:
                        self.ram[target] = array
                    if elapsed > 0:
                        speed = array.nbytes / elapsed / 2 ** 20
                        self.read_mb_s = speed if not self.read_mb_s else 0.8 * self.read_mb_s + 0.2 * speed


frame_cache = FrameCache()


# Kept caches ("name tags")
# -------------------------
# A cache of a saved .blend is kept on disk under a tag made of everything that decides the
# pictures: the file, scene, view settings (the capture key), an edit id saved in the file
# (a new random id on every edit, so two different edit histories never share an id), the
# dates of linked files and images, and the Blender version. Reopening the file finds the
# folder again only if the .blend on disk is the one those pictures were made from: its
# date must match the date noted when it was loaded or saved in that state. A file changed
# elsewhere (for example in a Blender without Cache Play) gets a new date, so it re-caches.

# The .blend as it is on disk: its path, date, and each scene's edit id at load / save.
_disk_state = {"path": "", "mtime": None, "edit_ids": {}}


def _blend_path():
    path = bpy.data.filepath
    return os.path.normcase(os.path.abspath(path)) if path else ""


def _tupled(value):
    """JSON lists back to the tuples a capture key is made of."""
    return tuple(_tupled(v) for v in value) if isinstance(value, list) else value


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _deps_signature():
    """[path, date] of every file the pictures may show: linked libraries, images, caches."""
    files = set()
    for lib in bpy.data.libraries:
        files.add(bpy.path.abspath(lib.filepath, library=lib.library))
    for image in bpy.data.images:
        if image.source in {'FILE', 'SEQUENCE', 'MOVIE', 'TILED'} and image.filepath and not image.packed_file:
            files.add(bpy.path.abspath(image.filepath, library=image.library))
    for group in (bpy.data.cache_files, bpy.data.volumes):
        for item in group:
            if item.filepath and not getattr(item, "packed_file", None):
                files.add(bpy.path.abspath(item.filepath, library=item.library))
    return [[os.path.normcase(os.path.abspath(path)), _mtime(path)] for path in sorted(files)]


def _tag_fields(scene_name, key=None):
    scene = bpy.data.scenes.get(scene_name)
    fields = {
        "format": disk.FORMAT, "path": _blend_path(), "scene": scene_name,
        "edit_id": scene.cache_play.edit_id if scene else "",
        "deps": _deps_signature(), "blender": bpy.app.version_string,
    }
    if key is not None:
        fields["key"] = key
    return json.loads(json.dumps(fields, default=repr))  # exactly as info.json reads back


def persist_tag(scene_name, key):
    """The name tag of a cache, or None for an unsaved file (it gets a session folder)."""
    path = _blend_path()
    if not path or bpy.data.scenes.get(scene_name) is None:
        return None
    fields = _tag_fields(scene_name, key)
    tag_id = hashlib.sha1(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:20]
    disk_mtime = _disk_state["mtime"] if _disk_state["path"] == path else None
    at_disk = disk_mtime is not None and _disk_state["edit_ids"].get(scene_name) == fields["edit_id"]
    return {"id": tag_id, **fields, "disk_mtime": disk_mtime, "at_disk_state": at_disk}


def _note_disk_state():
    """Remember the .blend as it is on disk right now (just loaded or saved)."""
    path = _blend_path()
    _disk_state["path"] = path
    _disk_state["mtime"] = _mtime(bpy.data.filepath) if path else None
    _disk_state["edit_ids"] = {s.name: s.cache_play.edit_id for s in bpy.data.scenes}


def adopt_kept_cache():
    """Just after a file opens: pick up the kept cache made from this exact saved state."""
    from .ui import configure_cache  # ui imports this module
    # Without a previous frame on record, the first edit would look like a frame change.
    for scene in bpy.data.scenes:
        _last_frame.setdefault(scene.name, _frame_of(scene))
    configure_cache()
    cache = frame_cache
    if not (cache.use_disk and cache.base_dir and _disk_state["mtime"]) or cache.key is not None:
        return None
    windows = bpy.context.window_manager.windows
    scene = windows[0].scene if windows else bpy.context.scene
    fields = _tag_fields(scene.name)
    best = None
    for folder, info in disk.kept_caches(cache.base_dir):
        if (all(info.get(name) == value for name, value in fields.items())
                and info.get("blend_mtime") == _disk_state["mtime"] and not disk.in_use(folder)
                and (best is None or info.get("last_used", 0) > best[1].get("last_used", 0))):
            best = (folder, info)
    if best is not None and cache.adopt(scene.name, best[1], best[0]):
        tag_time_editors()
    return None


@persistent
def _on_load_post(*_args):
    _note_disk_state()
    # Once the file's windows are up.
    bpy.app.timers.register(adopt_kept_cache, first_interval=0.1)


@persistent
def _on_save_post(*_args):
    _note_disk_state()
    scene_name = frame_cache.scene_name
    if scene_name and bpy.data.scenes.get(scene_name) is not None:
        fields = _tag_fields(scene_name)
        frame_cache.saved(scene_name, {k: fields[k] for k in ("path", "edit_id", "deps")},
                          _disk_state["mtime"])


def _startup():
    """When the add-on is turned on with a file already open."""
    if bpy.data.filepath and not bpy.data.is_dirty:
        _note_disk_state()
    adopt_kept_cache()
    return None


TIME_EDITORS = {'DOPESHEET_EDITOR', 'GRAPH_EDITOR', 'NLA_EDITOR'}  # Timeline is a Dope Sheet mode


def is_frame_editor(area):
    """True for editors whose horizontal axis is scene frames (not the Drivers editor)."""
    return area is not None and area.type in TIME_EDITORS and area.ui_type != 'DRIVERS'


def tag_time_editors():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if is_frame_editor(area):
                area.tag_redraw()


# Simple invalidation mode: any real edit clears everything.
# Scene updates come from settings (including ours) and never clear; view, size and
# shading changes are caught by the cache key instead. Object, Collection and Action
# updates without any of the three change flags are selection changes.
_SKIP_TYPES = (bpy.types.Scene, bpy.types.WindowManager, bpy.types.Screen, bpy.types.WorkSpace)
_FLAG_ONLY_TYPES = (bpy.types.Object, bpy.types.Collection, bpy.types.Action)

# Changing frames (scrubbing, arrow keys, normal playback) also reports every animated
# object as updated, and in Blender 5.x that update can arrive before frame_change_pre.
# So an update counts as an edit only when the frame has not moved since the previous
# handler call, and not just after a frame change finished.
_FRAME_QUIET = 0.05  # seconds after frame_change_post during which updates are ignored
_last_frame = {}     # scene name -> (frame, subframe) at the previous handler call
_quiet_until = 0.0


def _frame_of(scene):
    return scene.frame_current, scene.frame_subframe


@persistent
def _on_frame_change(scene, *_args):
    global _quiet_until
    _last_frame[scene.name] = _frame_of(scene)
    _quiet_until = time.perf_counter() + _FRAME_QUIET


# Opening or closing a window (e.g. the Export file browser) makes Blender re-sync the
# scene: every visible object is reported with only its shading flag. Real edits touch a
# material, mesh or transform, or only some objects, so that exact pattern is ignored, as
# is any update right after the set of windows changed.
_last_windows = {"set": None}


def _windows_changed():
    current = frozenset(w.as_pointer() for w in bpy.context.window_manager.windows)
    changed = _last_windows["set"] is not None and current != _last_windows["set"]
    _last_windows["set"] = current
    return changed


def _is_visibility_resync(depsgraph):
    flagged = set()
    for update in depsgraph.updates:
        id_data = update.id
        if isinstance(id_data, _SKIP_TYPES):
            continue
        if (not isinstance(id_data, bpy.types.Object) or update.is_updated_transform
                or update.is_updated_geometry or not update.is_updated_shading):
            return False
        flagged.add(id_data.name)
    visible = {instance.object.name for instance in depsgraph.object_instances if not instance.is_instance}
    return len(flagged) > 1 and visible <= flagged


def _is_edit(depsgraph):
    for update in depsgraph.updates:
        id_data = update.id
        if isinstance(id_data, _SKIP_TYPES):
            continue
        changed = update.is_updated_geometry or update.is_updated_transform or update.is_updated_shading
        if changed or not isinstance(id_data, _FLAG_ONLY_TYPES):
            return True
    return False


@persistent
def _on_depsgraph_update(scene, depsgraph):
    frame = _frame_of(scene)
    frame_moved = _last_frame.get(scene.name) != frame
    _last_frame[scene.name] = frame
    windows_changed = _windows_changed()
    if frame_moved or windows_changed or time.perf_counter() < _quiet_until or player.state.busy():
        return
    if not _is_edit(depsgraph) or _is_visibility_resync(depsgraph):
        return
    # Every edit gets a new id, saved with the file, even with nothing cached: kept caches
    # of the old state then never match this one.
    scene.cache_play.edit_id = uuid.uuid4().hex
    if frame_cache.key is not None and scene.name == frame_cache.scene_name:
        frame_cache.clear(delete=False)
        tag_time_editors()


@persistent
def _on_load_pre(*_args):
    frame_cache.clear(delete=False)
    player.state.reset()


_HANDLERS = (
    ("depsgraph_update_post", _on_depsgraph_update),
    ("frame_change_post", _on_frame_change),
    ("load_pre", _on_load_pre),
    ("load_post", _on_load_post),
    ("save_post", _on_save_post),
)


def register():
    for name, func in _HANDLERS:
        getattr(bpy.app.handlers, name).append(func)
    bpy.app.timers.register(_startup, first_interval=0.1)


def unregister():
    for func in (_startup, adopt_kept_cache):
        if bpy.app.timers.is_registered(func):
            bpy.app.timers.unregister(func)
    for name, func in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if func in handlers:
            handlers.remove(func)
