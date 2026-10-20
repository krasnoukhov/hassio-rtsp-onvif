# Changelog

## 1.0.1

- New cameras no longer get a leading space in their Protect name. Protect names a camera "<Manufacturer> <Model>", and go2rtc left the manufacturer empty; the add-on now reports `RTSP to ONVIF` and the camera's `model` (its name by default). Rename the camera in Protect as you like.
- The camera's name also goes into the ONVIF `name` scope.
- Releases are published on GitHub.

## 1.0.0

- Continuous and battery cameras, several per add-on, each with its own ONVIF server.
- Motion by frame differences or by Scrypted's YOLOv9c detector (OpenVINO, CPU); event thumbnails use the best-scoring frame.
- Battery cameras keep a continuous stream for Protect, with the last frame dimmed and an `ASLEEP <time>` label while they sleep.
- Optional audio (AAC 16 kHz mono), saved frames per wake or event, and camera logins kept out of the logs.
