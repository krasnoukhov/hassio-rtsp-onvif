# hassio-rtsp-onvif

A Home Assistant add-on (folder `rtsp_onvif/`, slug `rtsp_onvif`) that puts RTSP cameras into UniFi Protect as third-party ONVIF cameras, with the motion events Protect needs to record on detections. User docs: [README.md](README.md) and [rtsp_onvif/DOCS.md](rtsp_onvif/DOCS.md). This file is for working on the code.

## Layout

- `repository.yaml`: the add-on repository's metadata.
- `rtsp_onvif/config.yaml`: the add-on manifest (version, image, options and schema).
- `rtsp_onvif/Dockerfile`: Debian with Python 3.12, because OpenVINO wheels need glibc. It installs ffmpeg and the DejaVu font, downloads go2rtc for `BUILD_ARCH`, and fetches Scrypted's model from Hugging Face, pinned to a commit.
- `rtsp_onvif/app/`: the add-on, run as `python -m app`.
  - `config.py`: option defaults, validation, the login URL, log redaction
  - `onvif.py`: PullPoint events (`Subscriptions`, `event_reply`) and fixes to go2rtc's replies (`patch`)
  - `motion.py`: event state (`Motion`) and frame differences (`PixelDiff`)
  - `detect.py`: the OpenVINO detector
  - `camera.py`: `ContinuousCamera` and `BatteryCamera`
  - `server.py`: one HTTP server per camera
  - `main.py`: wiring
- `rtsp_onvif/translations/`: setting names and descriptions (`en`, `uk`). Per-camera settings go under `cameras.fields`, and a test checks that every schema key is translated.
- `tests/`: unittest. `tests/__init__.py` puts `rtsp_onvif/` on `sys.path`, so tests import `app`.
- `.github/workflows/ci.yml`: lint, tests, images, release.

## Commands

```bash
uv sync
uv run ruff format . && uv run ruff check .
uv run python -m unittest discover -s tests -t .
```

## Releasing

1. Bump `version` in `rtsp_onvif/config.yaml`.
2. Add a `## <version>` section to `rtsp_onvif/CHANGELOG.md`, written for users.
3. Push to `main`.

CI then:
1. runs ruff and the tests;
2. builds natively on `ubuntu-24.04` (amd64) and `ubuntu-24.04-arm` (aarch64), pushing `ghcr.io/krasnoukhov/{arch}-hassio-rtsp-onvif:<version>` and `:latest` (public);
3. creates the GitHub release `v<version>` from the changelog section, unless it already exists.

Home Assistant offers the update once `version` changes. Every push to `main` rebuilds and re-tags the current version, which is harmless. Pushing changes under `.github/workflows/` needs a token with the `workflow` scope.

## How it works

- **go2rtc:** one instance serves RTSP on `rtsp_port` and its API on `127.0.0.1:api_port`.
  - Modules: api, rtsp, onvif, mjpeg, ffmpeg, exec. The `ffmpeg:` sources run through exec.
  - No webrtc, because Home Assistant's own go2rtc holds 8555.
  - Its output is piped through the redacting logger.
- **Per camera, an HTTP server on `onvif_port`:**
  - proxies go2rtc's ONVIF, adjusting each reply with `onvif.patch`
  - answers the event service itself
  - also serves `/status` (JSON) and `/api/frame.jpeg`, the snapshot
- **Per camera, one ffmpeg decode** (`Camera._decode`) feeds pipes:
  - 160×90 gray for motion
  - 1280-px MJPEG for snapshots
  - 320×320 RGB for the detector, when `motion: objects`
  - for battery cameras, full-size yuv420p on stdout
- **continuous:**
  - go2rtc stream `<name>_src` is the source, with its login. `<name>` is what Protect pulls: the video passed through, plus `ffmpeg:<name>_src#audio=aac/16000` when audio is on.
  - The decode reads `<name>_src` back from go2rtc.
  - `objects` motion only runs on frames that changed by at least `min_area`.
- **battery:**
  - A TCP connect probes the camera every `probe_seconds`. On a wake, the add-on decodes the camera directly.
  - An always-on x264 encoder (`BatteryCamera.output`) publishes `<name>` to go2rtc at a fixed frame rate. While the camera sleeps, it sends the dimmed last frame with a drawtext `ASLEEP <time>` label, read from a text file reloaded every second.
  - The last frame and the sleep time live in `/data/<name>/`.
- **Events:** `Motion.hit` starts an event and keeps the best frame. `Motion.tick` ends it `hold_seconds` after the last hit, or `SLEEP_GRACE` after the video stops. The snapshot is the best frame during the event and for `SNAP_HOLD` after it.
- **save_frames:** saves each wake's best frame (battery) or each event's (continuous) to `/share/rtsp_onvif/<name>/`, keeping the newest `SAVE_KEEP`.

## UniFi Protect behaviour this relies on

- Adoption is Advanced Adoption at `<host IP>:<onvif_port>`, with any login. go2rtc doesn't answer WS-Discovery.
- Protect checks for motion events only on a fresh adoption. A re-adopted camera keeps `motionDetection: unsupported` until it's removed and adopted again.
- It identifies cameras by the MAC in `GetNetworkInterfaces`, which go2rtc leaves empty. Several cameras on one host adopt fine with different MACs: the first reports the host's MAC, the rest get derived, locally administered ones.
- It names a new camera `<Manufacturer> <Model>` from `GetDeviceInformation` and ignores the ONVIF name scope. go2rtc's empty manufacturer gave names a leading space.
- It polls `PullMessages` every 5 s (`Timeout` PT2S, `MessageLimit` 10, `?sub=<id>`) and keeps using its subscription id after the add-on restarts. An unknown id is recreated, starting with the current motion state.
- It takes the event thumbnail about 1 s after the event ends, which is why the best frame is served for a while afterwards.
- It decodes only the first H.264 slice of each frame and shows the rest green. With `-tune zerolatency`, x264 uses sliced threads (4 slices on 4 cores), so the encoder sets `-x264-params sliced-threads=0`.
- It waits about 5 minutes before retrying a dead stream, so battery mode never lets the stream drop.
- Its players are silent on AAC stereo 48 kHz, so audio goes out as AAC 16 kHz mono. It only takes audio that the ONVIF profile describes, and go2rtc doesn't describe audio.
- Adaptive recording needs a second, low-quality stream; with one stream it behaves like Detections. A Protect with no drive only records on motion anyway.
- go2rtc 1.9.9 breaks adoption: its empty `RateControl` makes Protect report "Invalid credentials". Use 1.9.14 or later.

## Camera and ffmpeg quirks

- Battery cameras don't answer anything while asleep, not even ping, so the probe is a TCP connect to the RTSP port.
- They stop sending video about 10 s before they drop the connection. The add-on kills the decode after `STALL` seconds without frames. Waiting for ffmpeg's own timeout would flush a stale frame and open a short ghost event.
- Early in a wake, the newest JPEG can still be from the previous wake, so it's compared against `alive_since`.
- ffmpeg prints input URLs, logins included; everything logged goes through `config.redact`.
- `-use_wallclock_as_timestamps 1`, because cameras' RTP clocks jump; without it the `fps` filter floods duplicate frames. Outputs use `-fps_mode passthrough`.
- On HA OS the Raspberry Pi CM4's hardware H.264 decoder fails (its CMA is too small), so decoding is in software.

## Checking a real install

- `ha apps logs <slug>` shows each wake (`camera awake`, `camera asleep after N s ... max scores`), events and saved frames.
- `http://<host>:<onvif_port>/status` shows the live state.
- The `ha` CLI can't set options. Use the Supervisor API: `POST /addons/<slug>/options` with the full `options` object.
- To check slices, pull the RTSP stream and read `first_mb_in_slice` from each frame's slice NALs. Every frame should have one slice, starting at 0.
- `tests/test_camera.py` simulates a battery wake and stall with a fake ffmpeg.

## Conventions

- Python 3.12. Standard library plus numpy, and OpenVINO in the image. ruff with line length 120.
- Keep comments short, and only for things that aren't obvious.
- Keep secrets, home IPs and real MACs out of the repo; use placeholders like `192.168.1.20`.
- `CHANGELOG.md` is for users; one section per released version.
