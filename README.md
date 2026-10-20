# RTSP to ONVIF

A Home Assistant add-on that puts RTSP cameras into **UniFi Protect** as third-party ONVIF cameras, with the motion events Protect needs to record on detections.

It handles two kinds of camera:

- **Continuous**: an always-on stream, such as an IP camera or an old phone or tablet running an RTSP app. Video goes to Protect untouched (optionally with audio). The add-on looks for motion on the side, either by frame differences or with an object detector.
- **Battery**: a camera that sleeps until its own motion sensor wakes it, and only then serves RTSP for a few seconds. Protect drops dead streams and waits minutes before retrying, so it would miss every wake. The add-on keeps one continuous stream going for Protect: live video while the camera is awake, and its last frame (dimmed, labelled `ASLEEP 2h 14m`) while it sleeps. Each wake becomes an event only when the detector sees a person or an animal, so sun and wind don't fill your recordings.

Object detection uses [Scrypted](https://github.com/koush/scrypted)'s YOLOv9c model on the CPU through OpenVINO, with the same preprocessing as Scrypted, so it filters the same way. Event thumbnails show the best-scoring frame of the event rather than whatever is in view when it ends.

## Install

1. In Home Assistant: **Settings → Add-ons → Add-on store → ⋮ → Repositories**, and add `https://github.com/krasnoukhov/hassio-rtsp-onvif`.
2. Install **RTSP to ONVIF**, set up your cameras on the **Configuration** tab (see the **Documentation** tab), and start it.
3. In Protect: **Settings → System → Discover 3rd-Party Cameras** on, then **Add device → Advanced adoption** with `<Home Assistant IP>:<onvif_port>`. Any username and password work.

Images are built for `amd64` and `aarch64` (Raspberry Pi 4/CM4, Home Assistant Yellow and Green).

## How it works

- [go2rtc](https://github.com/AlexxIT/go2rtc) serves the RTSP streams and the ONVIF basics.
- The add-on runs one small ONVIF server per camera in front of go2rtc. It adds PullPoint motion events (`tns1:VideoSource/MotionAlarm`, in the format [scrypted-onvif-rebroadcast](https://github.com/apocaliss92/scrypted-onvif-rebroadcast) uses), the MAC address Protect identifies cameras by, and audio descriptions.
- One ffmpeg decode per camera feeds motion analysis, the detector and JPEG snapshots. For battery cameras it also feeds an always-on H.264 encoder.

## Credits

- [go2rtc](https://github.com/AlexxIT/go2rtc) (MIT) for RTSP and ONVIF.
- Scrypted's detection model from [scrypted/plugin-models](https://huggingface.co/scrypted/plugin-models) (MIT), downloaded at build time.
- [scrypted-onvif-rebroadcast](https://github.com/apocaliss92/scrypted-onvif-rebroadcast), whose event format UniFi Protect accepts.

## Development

```bash
uv sync
uv run ruff format . && uv run ruff check .
uv run python -m unittest discover -s tests -t .
```

Pushing to `main` builds and publishes the images to `ghcr.io/krasnoukhov/{arch}-hassio-rtsp-onvif`. Bump `version` in `rtsp_onvif/config.yaml` to release; Home Assistant offers the update when the version changes.

## License

MIT
