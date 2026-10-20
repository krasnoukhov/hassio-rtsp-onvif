import time
import unittest
from unittest import mock

from app import motion


class MotionTest(unittest.TestCase):
    def setUp(self):
        self.changes = []
        self.m = motion.Motion(10, self.changes.append, log=lambda *a: None)

    def test_best_frame(self):
        m = self.m
        m.hit("animal", 0.55, None)
        self.assertEqual((m.best_jpeg, m.best_score), (None, 0.55))
        m.hit("animal", 0.52, b"A")  # fills a missing frame
        m.hit("animal", 0.70, None)  # better score, no frame: keeps A
        self.assertEqual((m.best_jpeg, m.best_score), (b"A", 0.70))
        m.hit("animal", 0.80, b"B")
        m.hit("animal", 0.60, b"C")
        self.assertEqual((m.best_jpeg, m.best_score), (b"B", 0.80))
        self.assertEqual(self.changes, [True])

    def test_ends_after_hold(self):
        m = self.m
        m.hit("person", 0.9, b"J")
        self.assertIsNone(m.tick(True, 0))
        with mock.patch.object(motion.time, "monotonic", return_value=time.monotonic() + 11):
            self.assertEqual(m.tick(True, 0), (b"J", "person", 0.9))
        self.assertEqual(self.changes, [True, False])

    def test_ends_soon_after_the_video_stops(self):
        m = self.m
        m.hit("person", 0.9, b"J")
        dead = time.monotonic()
        self.assertIsNone(m.tick(False, dead))
        with mock.patch.object(motion.time, "monotonic", return_value=dead + motion.SLEEP_GRACE + 0.1):
            self.assertIsNotNone(m.tick(False, dead))

    def test_snapshot_serves_best_frame_for_a_while(self):
        m = self.m
        self.assertIsNone(m.snapshot())
        m.hit("person", 0.9, b"J")
        with mock.patch.object(motion.time, "monotonic", return_value=time.monotonic() + 11):
            m.tick(True, 0)
        self.assertEqual(m.snapshot(), b"J")
        with mock.patch.object(motion.time, "monotonic", return_value=time.monotonic() + 60):
            self.assertIsNone(m.snapshot())


class PixelDiffTest(unittest.TestCase):
    def test_changed_percent(self):
        d = motion.PixelDiff(25)
        self.assertEqual(d.changed(bytes(100)), 0.0)
        self.assertEqual(d.changed(bytes([200] * 10 + [10] * 90)), 10.0)  # 10 changed a lot, 90 within 25
        d.reset()
        self.assertEqual(d.changed(bytes([255] * 100)), 0.0)


if __name__ == "__main__":
    unittest.main()
