"""Motion event state, and frame-difference motion."""

import threading
import time

from .onvif import utcnow

SLEEP_GRACE = 2  # seconds of frozen video before a running event is closed
SNAP_HOLD = 20  # seconds the best frame keeps serving as the snapshot after an event


class Motion:
    """One camera's event: on with the first hit, off after hold_seconds without hits or once the video stops.
    Keeps the best-scoring frame, because Protect takes the event thumbnail after the event ends."""

    def __init__(self, hold, on_change, log=print):
        self.hold, self.on_change, self.log = hold, on_change, log
        self.lock = threading.Lock()
        self.active = False
        self.since = utcnow()
        self.last_hit = self.ended = 0.0
        self.best_jpeg, self.best_label, self.best_score = None, None, 0.0

    def hit(self, label, score, jpeg):
        with self.lock:
            self.last_hit = time.monotonic()
            if not self.active:
                self.best_jpeg, self.best_label, self.best_score = jpeg, label, score
                self._set(True, f"({label} {score:.2f})")
            elif score > self.best_score:
                self.best_label, self.best_score = label, score
                self.best_jpeg = jpeg or self.best_jpeg
            elif self.best_jpeg is None:
                self.best_jpeg = jpeg

    def tick(self, alive, dead_since):
        """Ends the event; returns the finished event's best frame, if any."""
        with self.lock:
            now = time.monotonic()
            if self.active and (now - self.last_hit > self.hold or (not alive and now - dead_since > SLEEP_GRACE)):
                self._set(False, f"(best {self.best_label} {self.best_score:.2f})")
                return self.best_jpeg, self.best_label, self.best_score
        return None

    def snapshot(self):
        if self.best_jpeg and (self.active or time.monotonic() - self.ended < SNAP_HOLD):
            return self.best_jpeg
        return None

    def _set(self, active, why):
        if not active:
            self.ended = time.monotonic()
        self.active, self.since = active, utcnow()
        self.log("motion", "started" if active else "stopped", why)
        self.on_change(active)


class PixelDiff:
    """Percent of pixels that changed by more than `sensitivity` between consecutive gray frames."""

    def __init__(self, sensitivity):
        self.sensitivity = sensitivity
        self.prev = None

    def changed(self, gray):
        import numpy as np

        cur = np.frombuffer(gray, np.uint8).astype(np.int16)
        prev, self.prev = self.prev, cur
        if prev is None or prev.shape != cur.shape:
            return 0.0
        return 100.0 * np.count_nonzero(np.abs(cur - prev) > self.sensitivity) / cur.size

    def reset(self):
        self.prev = None
