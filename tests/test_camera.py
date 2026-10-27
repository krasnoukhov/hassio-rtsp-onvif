import contextlib
import io
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from app import camera, config, main

QUIET = lambda *a: None  # noqa: E731


def opts(**cam):
    base = {"name": "birds", "source": "rtsp://10.0.0.5/live", "onvif_port": 8000, "mode": "battery", "motion": "off"}
    return config.parse({"cameras": [{**base, "width": 32, "height": 18, **cam}]})


class FakeDecoder:
    """Stands in for ffmpeg: sends a few frames, then goes quiet with the pipe still open, like a battery camera."""

    frames = 10

    def __init__(self, cmd, **kw):
        size = 32 * 18 * 3 // 2
        r, self.w = os.pipe()
        self.stdout = os.fdopen(r, "rb")
        self.stderr = io.BytesIO(b"")

        def feed():
            for _ in range(self.frames):
                os.write(self.w, bytes([100]) * size)
                time.sleep(0.01)

        threading.Thread(target=feed, daemon=True).start()

    def kill(self):
        with contextlib.suppress(OSError):
            os.close(self.w)

    def wait(self):
        return 0


class CameraTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for name, value in (("data_root", "data"), ("share_root", "share")):
            p = mock.patch.object(camera.Camera, name, os.path.join(self.tmp.name, value))
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()


class BatteryTest(CameraTestCase):
    def test_wake_then_stall_then_sleep(self):
        o = opts(save_frames=True)
        cam = camera.BatteryCamera(o["cameras"][0], o, None, QUIET)
        cam.jpeg, cam.jpeg_t = b"\xff\xd8x\xff\xd9", time.monotonic() + 60  # a frame from this wake
        threading.Thread(target=cam._tick, daemon=True).start()
        with mock.patch.object(camera, "STALL", 0.3), mock.patch.object(camera.subprocess, "Popen", FakeDecoder):
            t = time.monotonic()
            cam.stream(t)
        self.assertLess(time.monotonic() - t, 3)  # closed by the stall check, not ffmpeg's 5 s timeout
        self.assertFalse(cam.alive)
        self.assertEqual(cam.wakes, 1)
        self.assertEqual(cam.frame[0], camera.DIM[100])  # dimmed last frame
        self.assertTrue(os.path.exists(cam.last_yuv) and os.path.exists(cam.last_seen))
        saved = os.listdir(cam.save_dir)
        self.assertEqual(len(saved), 1)
        self.assertIn("_none_0.00.jpg", saved[0])
        cam._overlay()
        self.assertEqual(cam.overlay_text, "ASLEEP 0m")

        again = camera.BatteryCamera(o["cameras"][0], o, None, QUIET)
        self.assertEqual(again.frame, cam.frame)  # restart keeps the frozen frame and the sleep time
        self.assertEqual(again.overlay_text, "ASLEEP 0m")

    def test_overlay_concurrent_writes(self):
        # The tick thread and a direct call can write at once.
        o = opts()
        cam = camera.BatteryCamera(o["cameras"][0], o, None, QUIET)
        errors = []

        def write():
            for _ in range(500):
                cam.overlay_text = None  # force a write
                try:
                    cam._overlay()
                except OSError as e:
                    errors.append(e)

        threads = [threading.Thread(target=write) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_save_keeps_newest(self):
        o = opts(save_frames=True)
        cam = camera.BatteryCamera(o["cameras"][0], o, None, QUIET)
        with mock.patch.object(camera, "SAVE_KEEP", 3):
            for i in range(5):
                cam._save(b"jpeg", "animal", i / 10, 1_700_000_000 + i)
        saved = sorted(os.listdir(cam.save_dir))
        self.assertEqual(len(saved), 3)
        self.assertTrue(saved[-1].endswith("_animal_0.40.jpg"))

    def test_save_is_off_by_default(self):
        o = opts()
        cam = camera.BatteryCamera(o["cameras"][0], o, None, QUIET)
        cam._save(b"jpeg", "animal", 0.9, time.time())
        self.assertFalse(os.path.exists(cam.save_dir))

    def test_ago(self):
        self.assertEqual(
            [camera.ago(s) for s in (59, 61, 3600, 49200, 90000)],
            ["0m", "1m", "1h 00m", "13h 40m", "1d 1h"],
        )


class Go2rtcConfigTest(CameraTestCase):
    def test_streams(self):
        o = config.parse(
            {
                "cameras": [
                    {"name": "ipad", "source": "rtsp://10.0.0.3:8554/live", "onvif_port": 8000, "audio": True,
                     "username": "admin", "password": "s3cret"},
                    {"name": "birds", "source": "rtsp://10.0.0.5/live", "onvif_port": 8001, "mode": "battery"},
                ]
            }
        )  # fmt: skip
        cams = [
            camera.ContinuousCamera(o["cameras"][0], o, None, QUIET),
            camera.BatteryCamera(o["cameras"][1], o, None, QUIET),
        ]
        conf = main.go2rtc_config(o, cams)
        self.assertIn('  ipad_src: "rtsp://admin:s3cret@10.0.0.3:8554/live"', conf)
        self.assertIn('  ipad: ["rtsp://127.0.0.1:8564/ipad_src#media=video", "ffmpeg:ipad_src#audio=aac/16000"]', conf)
        self.assertIn("  birds:\n", conf)
        self.assertNotIn("webrtc", conf)

    def test_camera_mac(self):
        self.assertEqual(main.camera_mac("dc:a6:32:00:00:01", 0), "dc:a6:32:00:00:01")
        self.assertEqual(main.camera_mac("dc:a6:32:00:00:01", 1), "de:a6:32:00:00:02")
        self.assertEqual(main.camera_mac("02:00:00:00:00:ff", 1), "02:00:00:00:00:00")


if __name__ == "__main__":
    unittest.main()
