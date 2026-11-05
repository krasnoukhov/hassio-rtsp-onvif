# Changelog

## 1.1.0

- New battery-mode setting `wake_entity`: a Home Assistant entity that wakes the camera while it's `on`, instead of a connection check. Use it for sources that never go down by themselves, such as a robot vacuum's camera from another go2rtc, woken while the robot is off its dock. The add-on now has access to Home Assistant's API to read that entity.
- Fixed: two updates of a battery camera's `ASLEEP` label at the same moment could fail one of them.

## 1.0.2

- Names and descriptions for every setting, in English and Ukrainian.

## 1.0.1

- New cameras no longer get a leading space in their Protect name. Protect names a camera "<Manufacturer> <Model>", and go2rtc left the manufacturer empty; the add-on now reports `RTSP to ONVIF` and the camera's `model` (its name by default). Rename the camera in Protect as you like.
- The camera's name also goes into the ONVIF `name` scope.
- Releases are published on GitHub.

## 1.0.0

- Continuous and battery cameras, several per add-on, each with its own ONVIF server.
- Motion by frame differences or by Scrypted's YOLOv9c detector (OpenVINO, CPU); event thumbnails use the best-scoring frame.
- Battery cameras keep a continuous stream for Protect, with the last frame dimmed and an `ASLEEP <time>` label while they sleep.
- Optional audio (AAC 16 kHz mono), saved frames per wake or event, and camera logins kept out of the logs.
