# SPDX-License-Identifier: GPL-3.0-or-later
"""Disk tier helpers: cache folders and raw frame files.

Saved .blend files get kept caches: <cache folder>/caches/<tag>/, one folder per "name tag"
(file, scene, view settings, edit id, ...; see store.persist_tag). Each holds the frame
files and an info.json describing them, and survives closing Blender, so reopening the
file finds its frames again. The oldest kept caches are deleted first when the disk
limit is reached.

Unsaved files use a session folder, <cache folder>/session_<pid>_<time>, deleted when
Blender closes. Folders in use hold a lock on a .lock file inside them; a folder whose lock
can be taken is not in use by any running Blender.
"""

import json
import os
import shutil
import tempfile
import time

import numpy as np

_LOCK_NAME = ".lock"
_SESSION_PREFIX = "session_"
_CACHES = "caches"
_INFO = "info.json"
_TRASH = ".trash_"
FORMAT = 1  # bump when the frame files or info.json change meaning

# This Blender process: kept caches it made may be reused even with unsaved edits.
SESSION_ID = f"{os.getpid()}_{int(time.time())}"


def default_base_dir(preferences):
    temp = preferences.filepaths.temporary_directory
    return os.path.join(temp if temp and os.path.isdir(temp) else tempfile.gettempdir(), "CachePlay")


def _try_lock(handle):
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle):
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


class Session:
    """This Blender's folder inside the cache folder, locked while Blender runs."""

    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.path = os.path.join(base_dir, f"{_SESSION_PREFIX}{os.getpid()}_{int(time.time())}")
        os.makedirs(self.path, exist_ok=True)
        self._lock = open(os.path.join(self.path, _LOCK_NAME), "a+b")
        if not _try_lock(self._lock):
            raise OSError(f"Could not lock {self.path}")

    def abandoned(self):
        """Session folders of Blenders that are no longer running."""
        found = []
        try:
            names = os.listdir(self.base_dir)
        except OSError:
            return found
        for name in names:
            path = os.path.join(self.base_dir, name)
            if not name.startswith(_SESSION_PREFIX) or path == self.path or not os.path.isdir(path):
                continue
            try:
                with open(os.path.join(path, _LOCK_NAME), "a+b") as handle:
                    if not _try_lock(handle):
                        continue  # still in use by another Blender
                    _unlock(handle)
            except OSError:
                continue
            found.append(path)
        return found

    def close(self):
        if self._lock is not None:
            _unlock(self._lock)
            self._lock.close()
            self._lock = None
        shutil.rmtree(self.path, ignore_errors=True)


class FolderLock:
    """A lock on a kept cache folder while this Blender uses it."""

    def __init__(self, path):
        self.path = path
        self._handle = open(os.path.join(path, _LOCK_NAME), "a+b")
        if not _try_lock(self._handle):
            self._handle.close()
            raise OSError(f"{path} is in use by another Blender")

    def release(self):
        if self._handle is not None:
            _unlock(self._handle)
            self._handle.close()
            self._handle = None


def in_use(path):
    """True when another running Blender holds the folder's lock."""
    try:
        with open(os.path.join(path, _LOCK_NAME), "a+b") as handle:
            if not _try_lock(handle):
                return True
            _unlock(handle)
    except OSError:
        return True
    return False


def caches_dir(base_dir):
    return os.path.join(base_dir, _CACHES)


def read_info(folder):
    try:
        with open(os.path.join(folder, _INFO), encoding="utf-8") as handle:
            info = json.load(handle)
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and info.get("format") == FORMAT else None


def write_info(folder, info):
    """Write info.json atomically."""
    path = os.path.join(folder, _INFO)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(info, handle, indent=1)
    os.replace(temp, path)


def kept_caches(base_dir):
    """[(folder, info)] for every kept cache folder with a readable info.json."""
    root = caches_dir(base_dir)
    found = []
    try:
        names = os.listdir(root)
    except OSError:
        return found
    for name in names:
        folder = os.path.join(root, name)
        if name.startswith(_TRASH) or not os.path.isdir(folder):
            continue
        info = read_info(folder)
        if info is not None:
            found.append((folder, info))
    return found


def frames_in(folder, nbytes):
    """Frame numbers with a complete file in folder."""
    frames = set()
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return frames
    for entry in entries:
        name = entry.name
        if name.endswith(".raw") and name[:-4].lstrip("-").isdigit():
            try:
                if entry.stat().st_size == nbytes:
                    frames.add(int(name[:-4]))
            except OSError:
                pass
    return frames


def remove_frames(folder):
    """Delete every frame file (and info.json) in folder, keeping the folder and its lock."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for entry in entries:
        if entry.name != _LOCK_NAME:
            try:
                os.remove(entry.path)
            except OSError:
                pass


def folder_bytes(folder):
    total = 0
    try:
        for entry in os.scandir(folder):
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def to_trash(folder):
    """Rename folder so nothing finds it any more; returns the new path to delete later."""
    parent, name = os.path.split(folder)
    trash = os.path.join(parent, f"{_TRASH}{name}_{int(time.time() * 1000)}")
    os.replace(folder, trash)
    return trash


def leftover_trash(base_dir):
    root = caches_dir(base_dir)
    try:
        return [os.path.join(root, n) for n in os.listdir(root) if n.startswith(_TRASH)]
    except OSError:
        return []


def frame_path(folder, frame):
    return os.path.join(folder, f"{frame:06d}.raw")


def write_frame(path, array):
    """Write atomically, so a reader never sees half a frame. File I/O releases the GIL."""
    temp = path + ".tmp"
    with open(temp, "wb", buffering=0) as handle:
        handle.write(memoryview(array))
    os.replace(temp, path)


def read_frame(path, count):
    """Read count packed pixels, or None when the file is missing or the wrong size."""
    array = np.empty(count, dtype=np.float32)
    try:
        with open(path, "rb", buffering=0) as handle:
            if os.fstat(handle.fileno()).st_size != array.nbytes:
                return None
            got = handle.readinto(memoryview(array))
    except OSError:
        return None
    return array if got == array.nbytes else None
