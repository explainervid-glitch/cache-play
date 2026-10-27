# SPDX-License-Identifier: GPL-3.0-or-later
"""Disk tier helpers: the session folder and raw frame files.

Each Blender session writes into its own folder, <cache folder>/session_<pid>_<time>, and
keeps a lock on a file inside it. Folders whose lock can be taken belong to a Blender that
has exited (or crashed) and are safe to delete. v2 does not reuse frames across sessions;
that needs the v3 name tags.
"""

import os
import shutil
import tempfile
import time

import numpy as np

_LOCK_NAME = ".lock"
_SESSION_PREFIX = "session_"


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
