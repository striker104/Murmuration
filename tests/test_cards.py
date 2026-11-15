"""Printable card pages (adapters/webcam/make_tags.py) at different grid sizes still detect.

Run:  python -m unittest tests.test_cards -v      (needs OpenCV; skipped without it)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    import cv2
except ImportError:          # Pi / UNO Q: no OpenCV
    cv2 = None
if cv2 is not None:
    from adapters.webcam.make_tags import page, parse_grid, label_for   # noqa: E402


@unittest.skipIf(cv2 is None, "needs OpenCV")
class Cards(unittest.TestCase):
    def detect(self, img):
        d = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11))
        _, ids, _ = d.detectMarkers(img)
        return sorted(int(i) for i in ids.flatten()) if ids is not None else []

    def test_every_grid_detects_all_tags(self):
        for cols, rows in [(2, 3), (3, 4), (4, 5), (5, 7)]:
            ids = list(range(100, 100 + cols * rows))
            self.assertEqual(self.detect(page(ids, cols, rows)), ids, (cols, rows))

    def test_labels(self):
        names = {3: "Jonas  5-digit PIN"}
        self.assertEqual(label_for(3, names), "#3  Jonas  5-digit PIN")
        self.assertEqual(label_for(104, names), "#104  LIVE judge card")
        self.assertEqual(label_for(50, {}), "#50")

    def test_parse_grid(self):
        self.assertEqual(parse_grid("4x5"), (4, 5))
        self.assertEqual(parse_grid(None), (2, 3))


if __name__ == "__main__":
    unittest.main()
