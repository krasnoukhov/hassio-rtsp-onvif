"""Add-on options: defaults, validation, and keeping camera logins out of logs."""

import json
import re
from urllib.parse import quote, urlparse

MODES = ("continuous", "battery")
MOTIONS = ("pixels", "objects", "off")
LABELS = ("person", "vehicle", "animal")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")

DEFAULTS = {"rtsp_port": 8564, "api_port": 1985}
CAMERA_DEFAULTS = {
    "username": "",
    "password": "",
    "mode": "continuous",
    "motion": "pixels",
    "audio": False,
    "labels": "person,animal",
    "min_score": 0.5,
    "sensitivity": 25,
    "min_area": 0.5,
    "max_area": 60.0,
    "hold_seconds": 10,
    "probe_seconds": 2.0,
    "width": 1280,
    "height": 720,
    "fps": 15,
    "save_frames": False,
    "mac": "",
    "model": "",
}


class ConfigError(ValueError):
    pass


def load(path="/data/options.json"):
    with open(path) as f:
        return parse(json.load(f))


def parse(raw):
    opts = dict(DEFAULTS)
    opts.update({k: v for k, v in raw.items() if k != "cameras" and v is not None})
    cams = []
    for i, given in enumerate(raw.get("cameras") or []):
        cam = dict(CAMERA_DEFAULTS)
        cam.update({k: v for k, v in given.items() if v is not None})
        cam["index"] = i
        cam["labels"] = [x.strip() for x in str(cam["labels"]).split(",") if x.strip()]
        cam["mac"] = str(cam["mac"]).lower()
        _check(cam)
        cams.append(cam)
    if not cams:
        raise ConfigError("no cameras configured")
    _unique([c["name"] for c in cams], "camera name")
    _unique(
        [c["onvif_port"] for c in cams] + [opts["rtsp_port"], opts["api_port"]],
        "port",
    )
    opts["cameras"] = cams
    return opts


def _check(cam):
    name = cam.get("name", "")
    if not NAME_RE.match(name):
        raise ConfigError(f"camera name {name!r}: use a-z, 0-9 and _, starting with a letter")
    for key in ("source", "onvif_port"):
        if not cam.get(key):
            raise ConfigError(f"camera {name}: {key} is required")
    if not urlparse(cam["source"]).hostname:
        raise ConfigError(f"camera {name}: source needs a host")
    if cam["mode"] not in MODES:
        raise ConfigError(f"camera {name}: mode must be one of {', '.join(MODES)}")
    if cam["motion"] not in MOTIONS:
        raise ConfigError(f"camera {name}: motion must be one of {', '.join(MOTIONS)}")
    bad = [x for x in cam["labels"] if x not in LABELS]
    if bad:
        raise ConfigError(f"camera {name}: unknown labels {bad}; use {', '.join(LABELS)}")
    if cam["mac"] and not MAC_RE.match(cam["mac"]):
        raise ConfigError(f"camera {name}: mac must look like 02:00:00:00:00:01")


def _unique(values, what):
    seen = set()
    for v in values:
        if v in seen:
            raise ConfigError(f"duplicate {what}: {v}")
        seen.add(v)


def source_url(cam):
    """The camera URL with its login; never log this, log cam['source'] instead."""
    if not cam["username"]:
        return cam["source"]
    u = urlparse(cam["source"])
    auth = quote(cam["username"], safe="") + ":" + quote(cam["password"] or "", safe="")
    return u._replace(netloc=f"{auth}@{u.netloc}").geturl()


def secrets(opts):
    out = set()
    for cam in opts["cameras"]:
        if cam["password"]:
            out.update({cam["password"], quote(cam["password"], safe="")})
    # Longest first, so a password that contains another is masked whole.
    return sorted(out, key=len, reverse=True)


def redact(text, hidden):
    for x in hidden:
        text = text.replace(x, "***")
    return text
