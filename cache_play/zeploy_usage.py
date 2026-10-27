"""zeploy usage helper for Blender add-ons and extensions.

Copy this file into the add-on unchanged. Contract: docs/plugin-usage.md in
the zeploy repository.

    from . import zeploy_usage
    usage = zeploy_usage.Usage("remaper", "1.3.2")   # module or extension id, version

    class REMAPER_OT_remap(bpy.types.Operator):
        ...
        @usage.track("remap")          # counts each run that is not cancelled
        def execute(self, context):
            ...

    usage.action("remap")              # or count a use directly
    usage.error("remap")               # a feature failed

Do not count anything in register(): Blender registers enabled add-ons at
every start, used or not.

It appends one line to %LOCALAPPDATA%\\zeploy\\usage\\<plugin>.jsonl, and only
on PCs where zeploy is installed. It never raises, never shows anything and
never uses the network.
"""

import functools
import json
import os
import re
import time
from datetime import datetime, timezone

_MAX_BYTES = 512 * 1024


class Usage:
    def __init__(self, plugin, version=""):
        self.plugin = str(plugin)
        self.version = str(version) if version else ""

    def _write(self, ev, name=None, ms=None):
        try:
            base = os.environ.get("LOCALAPPDATA")
            if not base:
                return
            home = os.path.join(base, "zeploy")
            if not os.path.isdir(home):
                return
            folder = os.path.join(home, "usage")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, re.sub(r"[^A-Za-z0-9._-]", "_", self.plugin) + ".jsonl")
            if os.path.exists(path) and os.path.getsize(path) > _MAX_BYTES:
                return
            now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            event = {"v": 1, "t": now, "plugin": self.plugin}
            if self.version:
                event["ver"] = self.version
            event["app"] = "blender"
            event["ev"] = ev
            if name:
                event["name"] = str(name)
            if ms is not None and ms >= 0:
                event["ms"] = int(round(ms))
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
        except Exception:
            # Usage must never get in the way of the add-on.
            pass

    def open(self):
        self._write("open")

    def action(self, name, ms=None):
        self._write("action", name, ms)

    def error(self, name):
        self._write("error", name)

    def track(self, name):
        """Decorator: counts each call as the feature `name`, with its duration.

        An operator that returns {'CANCELLED'} is not counted; an exception
        counts an error and is raised on.
        """

        def decorate(fn):
            def run(*args, **kwargs):
                start = time.perf_counter()
                try:
                    result = fn(*args, **kwargs)
                except Exception:
                    self.error(name)
                    raise
                if not (isinstance(result, set) and "CANCELLED" in result):
                    self.action(name, (time.perf_counter() - start) * 1000)
                return result

            # Blender registers an operator only if execute/invoke/modal take
            # exactly (self, context) or (self, context, event): the wrapper
            # has as many arguments as the function it wraps.
            count = getattr(getattr(fn, "__code__", None), "co_argcount", None)
            if count == 2:
                def wrapper(a, b):
                    return run(a, b)
            elif count == 3:
                def wrapper(a, b, c):
                    return run(a, b, c)
            else:
                def wrapper(*args, **kwargs):
                    return run(*args, **kwargs)
            return functools.wraps(fn)(wrapper)

        return decorate
