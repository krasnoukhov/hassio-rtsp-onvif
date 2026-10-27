"""Cameras: decode, look for motion, and (battery mode) keep a continuous stream going for Protect."""

import os
import socket
import subprocess
import threading
import time
from urllib.parse import urlparse

from . import detect
from .config import source_url
from .motion import Motion, PixelDiff
from .onvif import Subscriptions, iso

GRAY_W, GRAY_H = 160, 90
ANALYSIS_FPS = 3
SNAP_W = 1280
STALL = 4  # seconds without frames before the stream is closed; battery cameras go quiet before disconnecting
SAVE_KEEP = 500
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DIM = bytes(16 + (y - 16) * 55 // 100 if y > 16 else y for y in range(256))


def ago(seconds):
    m = int(seconds // 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m:02d}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"


def _load(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


class Camera:
    data_root, share_root = "/data", "/share/rtsp_onvif"

    def __init__(self, cfg, opts, detector, log):
        self.cfg, self.name = cfg, cfg["name"]
        self.rtsp_port, self.api_port = opts["rtsp_port"], opts["api_port"]
        self.log = lambda *a: log(f"[{self.name}]", *a)
        self.detector = detector if cfg["motion"] == "objects" else None
        self.subs = Subscriptions(lambda: self.motion.active, self.log)
        self.motion = Motion(cfg["hold_seconds"], self.subs.push, self.log)
        self.diff = PixelDiff(cfg["sensitivity"])
        self.mac = cfg["mac"]
        self.data_dir = os.path.join(self.data_root, self.name)
        self.save_dir = os.path.join(self.share_root, self.name)
        os.makedirs(self.data_dir, exist_ok=True)
        self.gray = self.rgb = None
        self.jpeg, self.jpeg_t = None, 0.0
        self.frame_ready = threading.Event()
        self.proc = None
        now = time.monotonic()
        self.alive, self.alive_since, self.dead_since, self.frame_t = False, now, now, 0.0
        self.frames, self.fps, self.hits = 0, 0.0, 0
        self.changed, self.scores, self.peak, self.detect_ms = 0.0, {}, {}, 0.0
        self.best_frame = (-1.0, None, None)  # (score, label, jpeg) for save_frames

    # --- go2rtc and Protect-facing bits

    def streams(self):
        raise NotImplementedError

    def snapshot(self):
        return self.motion.snapshot() or self.jpeg

    def status(self):
        now = time.monotonic()
        return {
            "name": self.name,
            "mode": self.cfg["mode"],
            "motion_mode": self.cfg["motion"],
            "alive": self.alive,
            "state_seconds": round(now - (self.alive_since if self.alive else self.dead_since)),
            "motion": self.motion.active,
            "since": iso(self.motion.since),
            "best": [self.motion.best_label, round(self.motion.best_score, 2)],
            "changed_pct": round(self.changed, 2),
            "scores": self.scores,
            "peak": self.peak,
            "detect_ms": round(self.detect_ms),
            "fps": round(self.fps, 1),
            "frames": self.frames,
            "subscriptions": len(self.subs.subs),
        }

    def start(self):
        for target in (self.run, self._analyse, self._tick):
            threading.Thread(target=target, daemon=True, name=f"{self.name}-{target.__name__}").start()

    # --- decoding

    def _decode(self, url, out=None):
        """One ffmpeg decode: small gray frames (motion), JPEGs (snapshots), RGB 320x320 (objects, if used),
        and with out=(w, h, fps) full-size yuv420p on stdout for re-encoding."""
        n = 2 + bool(self.detector) + bool(out)
        chains = [f"[0:v]split={n}" + "".join(f"[v{i}]" for i in range(n))]
        maps, fds, readers, i = [], [], [], 0
        if out:
            w, h, fps = out
            chains.append(f"[v{i}]fps={fps},scale={w}:{h},format=yuv420p[out]")
            maps += ["-map", "[out]", "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
            i += 1

        def pipe(label, fmt, reader, *args):
            nonlocal i
            r, w = os.pipe()
            fds.append(w)
            chains.append(f"[v{i}]{fmt}[{label}]")
            maps.extend(["-map", f"[{label}]", "-fps_mode", "passthrough", *args, f"pipe:{w}"])
            readers.append((reader, r))
            i += 1

        pipe("g", f"fps={ANALYSIS_FPS},scale={GRAY_W}:{GRAY_H},format=gray", self._read_gray, "-f", "rawvideo")
        pipe(
            "j",
            f"fps={ANALYSIS_FPS},scale={SNAP_W}:-2",
            self._read_jpegs,
            "-c:v",
            "mjpeg",
            "-q:v",
            "4",
            "-f",
            "image2pipe",
        )
        if self.detector:
            size = detect.SIZE
            pipe("r", f"fps={ANALYSIS_FPS},scale={size}:{size},format=rgb24", self._read_rgb, "-f", "rawvideo")
        # -timeout ends ffmpeg once the source goes quiet; wallclock timestamps because cameras' RTP clocks jump.
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp", "-timeout", "5000000",
            "-flags2", "fast", "-use_wallclock_as_timestamps", "1", "-i", url,
            "-filter_complex", ";".join(chains), *maps,
        ]  # fmt: skip
        self.gray = self.rgb = None
        self.peak = {}
        self.diff.reset()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE if out else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            pass_fds=fds,
        )
        for w in fds:
            os.close(w)
        for reader, r in readers:
            threading.Thread(target=reader, args=(os.fdopen(r, "rb"),), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(proc.stderr,), daemon=True).start()
        self.proc = proc
        return proc

    def _read_stderr(self, f):
        # ffmpeg echoes the input URL, login included; the logger redacts it.
        for line in f:
            self.log(line.decode("utf-8", "replace").rstrip())
        f.close()

    def _read_gray(self, f):
        n = GRAY_W * GRAY_H
        while len(buf := f.read(n)) == n:
            now = time.monotonic()
            self.fps = 0.9 * self.fps + 0.1 / max(now - getattr(self, "_gray_t", 0.0), 1e-3)
            self._gray_t = now
            self.frames += 1
            self.gray = buf
            self._on_gray()
            self.frame_ready.set()
        f.close()

    def _on_gray(self):
        pass

    def _read_rgb(self, f):
        n = detect.SIZE * detect.SIZE * 3
        while len(buf := f.read(n)) == n:
            self.rgb = buf
        f.close()

    def _read_jpegs(self, f):
        buf = b""
        while chunk := f.read1(1 << 16):
            buf += chunk
            while (end := buf.find(b"\xff\xd9")) != -1:
                if (start := buf.find(b"\xff\xd8")) != -1 and start < end:
                    self.jpeg, self.jpeg_t = buf[start : end + 2], time.monotonic()
                buf = buf[end + 2 :]
        f.close()

    # --- analysis

    def _analyse(self):
        # Works on the newest frames only, so slow detection never backs up the decode.
        cfg = self.cfg
        while True:
            self.frame_ready.wait()
            self.frame_ready.clear()
            if not self.alive or self.gray is None:
                continue
            gray, rgb = self.gray, self.rgb
            # Early on, the newest JPEG can still be from before this stream started.
            jpeg = self.jpeg if self.jpeg_t >= self.alive_since else None
            self.changed = self.diff.changed(gray)
            if cfg["motion"] == "pixels":
                # Whole-frame changes are lighting, not motion; two frames in a row filter out noise.
                if cfg["min_area"] <= self.changed <= cfg["max_area"]:
                    self.hits += 1
                    self._consider(self.changed / 100, "motion", jpeg)
                    if self.hits >= 2:
                        self.motion.hit("motion", self.changed / 100, jpeg)
                else:
                    self.hits = 0
            elif cfg["motion"] == "objects":
                if rgb is None or not self._worth_detecting():
                    continue
                label, score = self._detect(rgb)
                self._consider(score, label, jpeg)
                if score >= cfg["min_score"]:
                    self.motion.hit(label, score, jpeg)
            elif cfg["mode"] == "battery":
                # Detection off: the camera's own wake is the event.
                self._consider(0.0, "wake", jpeg)
                self.motion.hit("wake", 1.0, jpeg)

    def _worth_detecting(self):
        return True

    def _detect(self, rgb):
        t = time.monotonic()
        try:
            found = self.detector(rgb)
        except Exception as e:
            self.log("detection failed:", e)
            return "none", 0.0
        ms = (time.monotonic() - t) * 1000
        self.detect_ms = ms if not self.detect_ms else 0.8 * self.detect_ms + 0.2 * ms
        self.scores = {k: round(s, 2) for k, (s, _) in found.items()}
        for k, s in self.scores.items():
            self.peak[k] = max(self.peak.get(k, 0.0), s)
        score, label = max(((s, k) for k, (s, _) in found.items() if k in self.cfg["labels"]), default=(0.0, "none"))
        return label, score

    def _consider(self, score, label, jpeg):
        if jpeg and score > self.best_frame[0]:
            self.best_frame = (score, label, jpeg)

    def _save(self, jpeg, label, score, when):
        if not (self.cfg["save_frames"] and jpeg):
            return
        name = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(when))}_{label}_{score:.2f}.jpg"
        try:
            os.makedirs(self.save_dir, exist_ok=True)
            with open(os.path.join(self.save_dir, name), "wb") as f:
                f.write(jpeg)
            # Names start with the time, so sorting keeps the newest.
            for old in sorted(x for x in os.listdir(self.save_dir) if x.endswith(".jpg"))[:-SAVE_KEEP]:
                os.remove(os.path.join(self.save_dir, old))
            self.log("saved", name)
        except OSError as e:
            self.log("saving a frame failed:", e)

    def _tick(self):
        while True:
            # Killing (rather than waiting for ffmpeg's timeout) also skips its flush of a stale last frame.
            if self.alive and self.proc and time.monotonic() - self.frame_t > STALL:
                self.log(f"no video for {STALL} s, closing the stream")
                self.proc.kill()
            ended = self.motion.tick(self.alive, self.dead_since)
            self._on_tick(ended)
            time.sleep(0.5)

    def _on_tick(self, ended):
        pass


class ContinuousCamera(Camera):
    """An always-on camera: go2rtc passes its video to Protect untouched; a side decode looks for motion."""

    def streams(self):
        video = f"rtsp://127.0.0.1:{self.rtsp_port}/{self.name}_src#media=video"
        # Protect's players stay silent on stereo 48 kHz AAC (its own cameras record mono), so audio is re-encoded.
        protect = [video, f"ffmpeg:{self.name}_src#audio=aac/16000"] if self.cfg["audio"] else [video]
        return {f"{self.name}_src": source_url(self.cfg), self.name: protect}

    def run(self):
        time.sleep(3)  # let go2rtc open its RTSP port
        url = f"rtsp://127.0.0.1:{self.rtsp_port}/{self.name}_src?video"
        while True:
            self._decode(url).wait()
            if self.alive:
                self.alive, self.dead_since = False, time.monotonic()
            self.log("stream ended, retrying in 5 s")
            time.sleep(5)

    def _on_gray(self):
        self.frame_t = time.monotonic()
        if not self.alive:
            self.alive, self.alive_since = True, self.frame_t

    def _worth_detecting(self):
        # Only frames that changed, so a still scene costs no detection.
        return self.changed >= self.cfg["min_area"]

    def _on_tick(self, ended):
        if ended:
            jpeg, label, score = ended
            self._save(jpeg, label, score, time.time())


class BatteryCamera(Camera):
    """A camera that sleeps until its own motion sensor wakes it, and only then serves RTSP.

    Protect gets one continuous stream: live video while the camera is awake, the last frame dimmed
    (with "ASLEEP <time>") while it sleeps. Protect waits ~5 min before retrying a dead stream,
    so it would miss every wake otherwise.
    """

    def __init__(self, cfg, opts, detector, log):
        super().__init__(cfg, opts, detector, log)
        self.w, self.h, self.out_fps = cfg["width"], cfg["height"], cfg["fps"]
        self.frame_size = self.w * self.h * 3 // 2
        url = urlparse(cfg["source"])
        self.addr = (url.hostname, url.port or 554)
        self.last_yuv = os.path.join(self.data_dir, "last.yuv")
        self.last_jpeg = os.path.join(self.data_dir, "last.jpg")
        self.last_seen = os.path.join(self.data_dir, "last_seen")
        self.overlay_file = f"/tmp/overlay_{self.name}.txt"
        self.wakes, self.out_frames = 0, 0
        self.live = _load(self.last_yuv)
        if self.live and len(self.live) != self.frame_size:
            self.live = None
        # Until the first wake: the frame saved at the last sleep, or dark gray.
        self.frame = (
            self.dim(self.live)
            if self.live
            else bytes([48]) * (self.w * self.h) + bytes([128]) * (self.w * self.h // 2)
        )
        self.jpeg = _load(self.last_jpeg)
        seen = _load(self.last_seen)
        self.asleep_wall = float(seen) if seen else None  # when the camera last went to sleep
        self.awake_wall = time.time()
        self.overlay_text = None
        self._overlay()

    def dim(self, yuv):
        return yuv[: self.w * self.h].translate(DIM) + yuv[self.w * self.h :]

    def streams(self):
        return {self.name: None}  # no source: the encoder publishes into it

    def start(self):
        super().start()
        threading.Thread(target=self.output, daemon=True, name=f"{self.name}-output").start()

    def status(self):
        return {**super().status(), "wakes": self.wakes, "out_frames": self.out_frames}

    def reachable(self):
        # A sleeping camera doesn't answer at all; a TCP connect is the cheapest check.
        try:
            socket.create_connection(self.addr, timeout=1.5).close()
            return True
        except OSError:
            return False

    def run(self):
        while True:
            t = time.monotonic()
            try:
                if self.reachable():
                    self.stream(t)
            except Exception as e:
                self.log("stream failed:", repr(e))
                self.alive = False
            time.sleep(max(0.0, self.cfg["probe_seconds"] - (time.monotonic() - t)))

    def stream(self, probed):
        proc = self._decode(source_url(self.cfg), out=(self.w, self.h, self.out_fps))
        frames = 0
        while len(buf := proc.stdout.read(self.frame_size)) == self.frame_size:
            if not frames:
                self.best_frame, self.awake_wall = (-1.0, None, None), time.time()
                self.alive, self.alive_since = True, time.monotonic()
                self.wakes += 1
                self.log(f"camera awake, first frame {self.alive_since - probed:.1f} s after the probe")
            frames += 1
            self.frame = self.live = buf
            self.frame_t = time.monotonic()
        proc.kill()
        proc.wait()
        if not frames:
            return
        self.alive, self.dead_since, self.asleep_wall = False, time.monotonic(), time.time()
        self.frame = self.dim(self.live)
        top = " ".join(f"{k} {v:.2f}" for k, v in self.peak.items())
        self.log(
            f"camera asleep after {self.dead_since - self.alive_since:.0f} s, {frames} frames, max scores: {top or '-'}"
        )
        try:
            with open(self.last_yuv, "wb") as f:
                f.write(self.live)
            with open(self.last_seen, "w") as f:
                f.write(str(self.asleep_wall))
            if self.jpeg:
                with open(self.last_jpeg, "wb") as f:
                    f.write(self.jpeg)
        except OSError as e:
            self.log("saving the last frame failed:", e)
        score, label, jpeg = self.best_frame
        if jpeg is None and self.jpeg_t >= self.alive_since:
            score, label, jpeg = 0.0, "none", self.jpeg
        self._save(jpeg, label, score, self.awake_wall)

    def _overlay(self):
        # A space while awake: drawtext fails on an empty file.
        if self.alive:
            text = " "
        else:
            text = ("ASLEEP " + ago(time.time() - self.asleep_wall)) if self.asleep_wall else "ASLEEP"
        if text != self.overlay_text:
            # A temp file per thread: two writers sharing one would replace it from under each other.
            tmp = f"{self.overlay_file}.{threading.get_ident()}.tmp"
            with open(tmp, "w") as f:
                f.write(text)
            os.replace(tmp, self.overlay_file)
            self.overlay_text = text

    def _on_tick(self, ended):
        self._overlay()

    def _overlay_filter(self):
        # Without the font drawtext would kill the encoder, so the label is optional.
        if not os.path.exists(FONT):
            self.log("no font, asleep label off")
            return []
        m = self.h // 30
        return [
            "-vf",
            f"drawtext=fontfile={FONT}:textfile={self.overlay_file}:reload={self.out_fps}:x=w-tw-{m}:y={m}:"
            f"fontsize={self.h // 20}:fontcolor=white:borderw=2:bordercolor=black@0.7",
        ]

    def output(self):
        # Always-on encoder publishing into go2rtc; frames are paced here so the stream never stalls.
        # One slice per frame: Protect decodes only the first slice (zerolatency's sliced threads made 4).
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{self.w}x{self.h}", "-r", str(self.out_fps),
            "-i", "pipe:0",
            *self._overlay_filter(),
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-x264-params", "sliced-threads=0",
            "-profile:v", "main", "-g", str(self.out_fps * 2), "-crf", "23", "-maxrate", "4M", "-bufsize", "4M",
            "-f", "rtsp", "-rtsp_transport", "tcp", f"rtsp://127.0.0.1:{self.rtsp_port}/{self.name}",
        ]  # fmt: skip
        time.sleep(3)  # let go2rtc open its RTSP port
        while True:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            start, n = time.monotonic(), 0
            try:
                while proc.poll() is None:
                    proc.stdin.write(self.frame)
                    n += 1
                    self.out_frames += 1
                    delay = start + n / self.out_fps - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    elif delay < -2:
                        start, n = time.monotonic(), 0
            except OSError:
                pass
            proc.kill()
            proc.wait()
            self.log("encoder exited, restarting in 2 s")
            time.sleep(2)
