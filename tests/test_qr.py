"""Judge QR codes: the phone shows a QR holding the card number + lock; the webcam scans it.

Run:  python -m unittest tests.test_qr -v
"""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.beacon import qr_text, parse_qr          # noqa: E402

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None

LOCK = {"title": "Judge Ana", "digits": 4, "salt": "9f3a1c07", "check": "e2c242e6d2d0b4f4"}


class Payload(unittest.TestCase):
    def test_roundtrip_compact_no_title(self):
        text = qr_text("103", LOCK)
        self.assertEqual(text, "M1:103:4:9F3A1C07:E2C242E6D2D0B4F4")   # QR alphanumeric mode
        self.assertEqual(parse_qr(text), ("103", dict(LOCK, title="")))  # name comes from locks.json

    def test_garbage_is_none(self):
        for t in ["", "hello", "https://example.com", "M1:X:4:9F3A1C07:E2C242E6D2D0B4F4",
                  "M1:103:4:ZZ:E2C242E6D2D0B4F4", "M1:103:4:9F3A1C07:SHORT",
                  "M9:103:4:9F3A1C07:E2C242E6D2D0B4F4", "M1:103:X:9F3A1C07:E2C242E6D2D0B4F4",
                  "M1:103:4:9F3A1C07:E2C242E6D2D0B4F4:EXTRA"]:
            self.assertIsNone(parse_qr(t), t)


class RunnerSpotLive(unittest.TestCase):
    def test_scanned_qr_creates_the_job_with_its_lock(self):
        from core.net import Runner, load_config
        cfg = {"work": "decode",
               "nodes": {"a": {"ip": "127.0.0.1", "gossip_port": 5331, "ctl_port": 5341,
                               "speed": 0.1, "zone": "A", "rate": 100}},
               "dashboard": {"ip": "127.0.0.1", "telemetry_port": 5398}}
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump(cfg, f)
        with open(os.path.join(d, "beacons.json"), "w") as f:
            json.dump({"rounds": 3, "beacons": {}}, f)
        r = Runner("a", load_config(os.path.join(d, "config.json")))
        self.addCleanup(r.gossip_sock.close)
        self.addCleanup(r.ctl_sock.close)
        self.assertTrue(r.spot_live("103", copy.deepcopy(LOCK)))
        self.assertEqual(r.node.jobs["103"]["lock"]["salt"], "9f3a1c07")
        self.assertIn("103", r.node.live_locks)


@unittest.skipIf(cv2 is None, "needs OpenCV")
class Webcam(unittest.TestCase):
    def test_reader_finds_our_qr_and_ignores_others(self):
        from adapters.webcam.detect import QrReader
        from sim.card_maker import qr_image
        img = np.full((480, 640), 255, np.uint8)
        ours = qr_image(qr_text("103", LOCK), 6)
        img[40:40 + ours.shape[0], 20:20 + ours.shape[1]] = ours
        other = qr_image("https://example.com", 6)
        img[40:40 + other.shape[0], 360:360 + other.shape[1]] = other
        found = [t for t, _ in QrReader().detect(img)]
        self.assertEqual(found, [qr_text("103", LOCK)])

    def test_phone_page_qr_png_decodes(self):
        from sim.card_maker import qr_png
        data = qr_png(qr_text("105", LOCK))
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        text, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        self.assertEqual(parse_qr(text), ("105", dict(LOCK, title="")))

    def test_reader_handles_a_small_tilted_blurry_phone_photo(self):
        from adapters.webcam.detect import QrReader
        from sim.card_maker import qr_image
        q = cv2.resize(qr_image(qr_text("106", LOCK), 4), (100, 100), interpolation=cv2.INTER_AREA)
        screen = (q.astype(np.float32) * 0.75 + 40).astype(np.uint8)          # dim screen
        src = np.float32([[0, 0], [100, 0], [100, 100], [0, 100]])
        dst = np.float32([[250, 150], [345, 159], [339, 256], [244, 247]])     # tilted
        m = cv2.getPerspectiveTransform(src, dst)
        frame = np.full((480, 640), 90, np.uint8)
        mask = cv2.warpPerspective(np.full((100, 100), 255, np.uint8), m, (640, 480))
        frame[mask > 0] = cv2.warpPerspective(screen, m, (640, 480))[mask > 0]
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        found = [t for t, _ in QrReader().detect(frame)]
        self.assertEqual(found, [qr_text("106", LOCK)])


if __name__ == "__main__":
    unittest.main()
