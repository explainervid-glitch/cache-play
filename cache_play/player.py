# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime playback state shared by the operator and the draw callbacks."""

import statistics
import time


class Stats:
    """Frame pacing, measured at the moment each new cached frame is drawn."""

    def __init__(self, fps, count):
        self.fps = fps
        self.count = count
        self.shown = 0
        self.dropped = 0
        self.errors = []   # actual interval minus ideal interval, in seconds
        self.latency = []  # tag_redraw() to end of our draw, in seconds
        self._last = None  # (index, time) of the previous frame drawn

    def restart(self):
        """Forget the previous frame, e.g. after pause or a manual step."""
        self._last = None

    def presented(self, index, now, latency):
        self.shown += 1
        self.latency.append(latency)
        if self._last is not None:
            step = (index - self._last[0]) % self.count
            if step:
                self.errors.append((now - self._last[1]) - step / self.fps)
                self.dropped += step - 1
        self._last = (index, now)

    def summary(self):
        if not self.errors:
            return None
        return {
            "shown": self.shown,
            "dropped": self.dropped,
            "jitter_ms": statistics.pstdev(self.errors) * 1000.0,
            "worst_ms": max(abs(e) for e in self.errors) * 1000.0,
            "latency_ms": statistics.fmean(self.latency) * 1000.0,
            "latency_max_ms": max(self.latency) * 1000.0,
        }


class PlayerState:
    def __init__(self):
        self.last_summary = None
        self.reset()

    def reset(self):
        self.active = False    # playing or paused; the cached picture covers the viewport
        self.filling = False   # the cache is being filled
        self.idle_step = False  # idle caching is rendering a frame right now
        self.capturing = False  # Cache & Play is caching a frame while it plays
        self.cached_at = 0.0    # when Cache & Play last cached a frame while playing
        self.paused = False
        self.cache_mode = False       # reviewing the cache: clicks pass through, Timeline drags scrub
        self.exit_requested = False   # the Cache Mode toggle asks the running operator to stop
        self.area_ptr = 0      # 3D Viewport area we draw over
        self.frames = []       # cached frame numbers, in playback order
        self.index = 0         # position in self.frames
        self.drawn_index = -1  # last index the draw callback showed
        self.dirty = False     # index changed, editors need a redraw
        self.fps = 24.0
        self.t0 = 0.0          # clock time of index 0
        self.tag_time = 0.0
        self.suppress_until = 0.0
        self.stats = None

    @property
    def caching(self):
        """Cache & Play cached a frame in the last half second (status label)."""
        return self.active and time.perf_counter() - self.cached_at < 0.5

    @property
    def frame(self):
        return self.frames[self.index] if self.frames else 0

    def busy(self):
        """True while our own frame changes must not invalidate the cache.

        Playback never changes the scene frame, so it does not count: an edit made
        during Cache Mode must still clear the cache.
        """
        return (self.filling or self.idle_step or self.capturing
                or time.perf_counter() < self.suppress_until)

    def suppress(self, seconds):
        self.suppress_until = time.perf_counter() + seconds

    def start(self, area_ptr, frames, fps, index, now, paused=False, cache_mode=False):
        self.active = True
        self.paused = paused
        self.cache_mode = cache_mode
        self.exit_requested = False
        self.area_ptr = area_ptr
        self.frames = frames
        self.fps = fps
        self.index = index
        self.drawn_index = -1
        self.t0 = now - index / fps
        self.tag_time = now
        self.dirty = True
        self.stats = Stats(fps, len(frames))

    def advance(self, now, loop):
        """Move to the frame the clock asks for. Returns False at the end of a non-looping play."""
        if self.paused:
            return True
        index = int((now - self.t0) * self.fps)
        count = len(self.frames)
        if index >= count:
            if not loop:
                return False
            index %= count
        if index != self.index:
            self.index = index
            self.tag_time = now
            self.dirty = True
        return True

    def set_paused(self, paused, now):
        if paused == self.paused:
            return
        self.paused = paused
        if not paused:
            self.t0 = now - self.index / self.fps
        self.stats.restart()

    def step(self, delta, now):
        self.seek((self.index + delta) % len(self.frames), now)

    def seek(self, index, now):
        """Pause and show the frame at index (stepping, scrubbing)."""
        self.paused = True
        if index != self.index:
            self.index = index
            self.tag_time = now
            self.dirty = True
        self.stats.restart()

    def stop(self):
        if self.stats is not None:
            self.last_summary = self.stats.summary()
        self.active = False
        self.paused = False
        self.cache_mode = False
        self.exit_requested = False
        self.cached_at = 0.0
        self.frames = []
        self.stats = None


state = PlayerState()
