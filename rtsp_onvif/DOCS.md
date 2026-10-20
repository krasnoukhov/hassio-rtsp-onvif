# RTSP to ONVIF

Puts RTSP cameras into UniFi Protect as ONVIF cameras, with motion events, including battery cameras that sleep between wakes.

## Example

```yaml
rtsp_port: 8564
cameras:
  - name: tablet
    source: rtsp://192.168.1.20:8554/live
    onvif_port: 8000
    mode: continuous
    motion: pixels
    audio: true
  - name: garden
    source: rtsp://192.168.1.30/live/ch00_0
    username: admin
    password: "..."
    onvif_port: 8001
    mode: battery
    motion: objects
    save_frames: true
```

Then in Protect, use **Add device → Advanced adoption** with `<Home Assistant IP>:<onvif_port>` for each camera. Any username and password work.

## Camera settings

| Setting | Default | What it does |
| --- | --- | --- |
| `name` | (required) | Lowercase a-z, 0-9 and `_`. Also the stream path: `rtsp://<host>:<rtsp_port>/<name>`. |
| `source` | (required) | The camera's RTSP URL. Put the login in `username` and `password`, which keeps it out of the logs. |
| `onvif_port` | (required) | The port you adopt in Protect. Each camera needs its own. |
| `mode` | `continuous` | `continuous` for always-on streams; `battery` for cameras that sleep until their own motion sensor wakes them. |
| `motion` | `pixels` | `pixels` (frame differences), `objects` (person/vehicle/animal detector) or `off`. With `off`, a battery camera's every wake is an event, and a continuous camera sends no events. |
| `username`, `password` | | The camera's RTSP login (Basic or Digest). |
| `audio` | `false` | Continuous mode: pass audio to Protect, re-encoded to AAC 16 kHz mono (Protect's players are silent on stereo 48 kHz). |
| `labels` | `person,animal` | `objects` motion: which of `person`, `vehicle`, `animal` count. Birds count as `animal`. |
| `min_score` | `0.5` | `objects` motion: detector confidence for an event (Scrypted's ONVIF plugin uses 0.5 too). |
| `sensitivity` | `25` | Brightness change (0-255) that counts as a changed pixel. |
| `min_area`, `max_area` | `0.5`, `60` | Percent of the frame that has to change. Above `max_area` it's a lighting change, not motion. Continuous `objects` cameras only run the detector on frames above `min_area`. |
| `hold_seconds` | `10` | How long an event lasts after the last motion or detection. |
| `probe_seconds` | `2` | Battery mode: how often to check whether the camera woke up (a TCP connect). |
| `width`, `height`, `fps` | `1280`, `720`, `15` | Battery mode: the stream sent to Protect. |
| `save_frames` | `false` | Save one JPEG per wake (battery) or per event (continuous) to `/share/rtsp_onvif/<name>/`, named by time, label and score, keeping the newest 500. Good for checking what the detector misses. |
| `mac` | | The MAC address reported to Protect. The first camera reports the host's; the others get locally administered addresses next to it. |
| `model` | `name` | The model name shown in Protect. |

## Notes

- **Recording mode:** use Detections in Protect. Adaptive needs a second, low-quality stream, which these cameras don't have, so it behaves like Detections. A Protect without a drive (for example a UDR with no microSD card) only records on motion anyway.
- **Battery cameras:** each wake shows up in the log as `camera awake ... camera asleep after N s ... max scores`. The camera typically stops sending video several seconds before it drops the connection; the add-on closes the stream after 4 s without frames.
- **Several cameras on one host:** Protect identifies cameras by MAC. The add-on reports a different MAC for each camera, so Protect adopts each `<host IP>:<onvif_port>` as its own camera (tested with two cameras on one Home Assistant Yellow).
- **Status:** `http://<host>:<onvif_port>/status` returns JSON: whether video is flowing, motion, scores, frame rate, and Protect's subscriptions.
- The go2rtc API listens on `127.0.0.1:1985` (`api_port`). go2rtc's WebRTC module is off, because Home Assistant's own go2rtc uses 8555.
