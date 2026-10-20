"""Object detection with Scrypted's YOLOv9c model (OpenVINO, CPU).

Same model and pre/post-processing as Scrypted's OpenVINO plugin: the whole frame stretched to
320x320 RGB, float NCHW / 255, output rows x, y, w, h, then one score per label.
"""

import threading

LABELS = ("person", "vehicle", "animal")
SIZE = 320
MODEL = "/model/model.xml"


class Detector:
    def __init__(self, xml=MODEL):
        import openvino as ov

        self.model = ov.Core().compile_model(xml, "CPU")
        self.req = self.model.create_infer_request()
        self.lock = threading.Lock()  # one infer request, shared by all cameras

    def __call__(self, rgb):
        """rgb: SIZE x SIZE x 3 bytes. Returns {label: (score, (x1, y1, x2, y2))}, each label's best box."""
        import numpy as np

        a = np.frombuffer(rgb, np.uint8).reshape(1, SIZE, SIZE, 3).transpose(0, 3, 1, 2)
        a = np.ascontiguousarray(a, np.float32) / 255.0
        with self.lock:
            out = self.req.infer([a])[self.model.output(0)][0]
        best = {}
        for i, label in enumerate(LABELS):
            j = int(out[4 + i].argmax())
            x, y, w, h = (float(v) for v in out[:4, j])
            best[label] = (float(out[4 + i, j]), (x - w / 2, y - h / 2, x + w / 2, y + h / 2))
        return best
