"""Entry point: go2rtc for RTSP and ONVIF basics, plus per-camera motion and ONVIF servers."""

import json
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer

from . import config
from .camera import BatteryCamera, ContinuousCamera
from .server import make_handler

GO2RTC_CONF = "/data/go2rtc.yaml"


def make_log(hidden):
    lock = threading.Lock()

    def log(*args):
        text = config.redact(" ".join(str(a) for a in args), hidden)
        with lock:
            print(time.strftime("%Y-%m-%d %H:%M:%S"), text, flush=True)

    return log


def host_mac():
    try:
        with open("/proc/net/route") as routes:
            for line in routes.read().splitlines()[1:]:
                f = line.split()
                if f[1] == "00000000":
                    with open(f"/sys/class/net/{f[0]}/address") as mac:
                        return mac.read().strip()
    except OSError:
        pass
    return "02:00:00:00:00:00"


def camera_mac(host, index):
    # The first camera reports the host's real MAC; the others get locally administered ones next to it.
    if index == 0:
        return host
    b = bytearray(int(x, 16) for x in host.split(":"))
    b[0] |= 0x02
    b[5] = (b[5] + index) & 0xFF
    return ":".join(f"{x:02x}" for x in b)


def go2rtc_config(opts, cams):
    # No webrtc module: Home Assistant's own go2rtc holds 8555.
    lines = [
        "app:",
        "  modules: [api, rtsp, onvif, mjpeg, ffmpeg, exec]",
        "api:",
        f'  listen: "127.0.0.1:{opts["api_port"]}"',
        "rtsp:",
        f'  listen: ":{opts["rtsp_port"]}"',
        "exec:",
        "  allow_paths: [ffmpeg]",
        "streams:",
    ]
    for cam in cams:
        for name, src in cam.streams().items():
            lines.append(f"  {name}:" + ("" if src is None else " " + json.dumps(src)))
    return "\n".join(lines) + "\n"


def supervise(cmd, log):
    while True:
        log("starting", cmd[0])
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in proc.stdout:  # piped so camera logins get redacted
            log(line.decode("utf-8", "replace").rstrip())
        log(cmd[0], "exited with", proc.wait(), "- restarting in 5 s")
        time.sleep(5)


def load_detector(opts, log):
    if not any(c["motion"] == "objects" for c in opts["cameras"]):
        return None
    try:
        from .detect import Detector

        t = time.monotonic()
        det = Detector()
        log(f"detector loaded in {time.monotonic() - t:.1f} s")
        return det
    except Exception as e:
        log("detector failed to load:", e)
        for cam in opts["cameras"]:
            if cam["motion"] == "objects":
                cam["motion"] = "off" if cam["mode"] == "battery" else "pixels"
                log(f"[{cam['name']}] falling back to motion: {cam['motion']}")
        return None


def main():
    opts = config.load()
    log = make_log(config.secrets(opts))
    detector = load_detector(opts, log)
    host = host_mac()
    cams = []
    for cfg in opts["cameras"]:
        cls = BatteryCamera if cfg["mode"] == "battery" else ContinuousCamera
        cam = cls(cfg, opts, detector, log)
        cam.mac = cfg["mac"] or camera_mac(host, cfg["index"])
        cams.append(cam)
    with open(GO2RTC_CONF, "w") as f:
        f.write(go2rtc_config(opts, cams))
    threading.Thread(target=supervise, args=(["go2rtc", "-c", GO2RTC_CONF], log), daemon=True).start()
    for cam in cams:
        cam.start()
        server = ThreadingHTTPServer(("0.0.0.0", cam.cfg["onvif_port"]), make_handler(cam))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        extra = ", ".join(
            x
            for x in (
                cam.cfg["mode"],
                f"motion {cam.cfg['motion']}",
                "with login" if cam.cfg["username"] else "",
                "audio" if cam.cfg["audio"] else "",
            )
            if x
        )
        log(
            f"[{cam.name}] ONVIF on :{cam.cfg['onvif_port']}, RTSP :{opts['rtsp_port']}/{cam.name}, "
            f"MAC {cam.mac}, source {cam.cfg['source']} ({extra})"
        )
    threading.Event().wait()
