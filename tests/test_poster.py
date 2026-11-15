"""Public link for judges (sim/public_link.py): Cloudflare quick tunnel -> QR poster on screen.

Run:  python -m unittest tests.test_poster -v     (poster tests need OpenCV)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sim.public_link import find_url        # noqa: E402
from sim import card_maker as cm             # noqa: E402

try:
    import cv2
except ImportError:
    cv2 = None


class TunnelOutput(unittest.TestCase):
    def test_finds_the_trycloudflare_link_in_its_log(self):
        line = ("2026-11-15T10:00:00Z INF |  https://brave-lion-tiger-moon.trycloudflare.com"
                "                         |")
        self.assertEqual(find_url(line), "https://brave-lion-tiger-moon.trycloudflare.com")

    def test_ignores_other_lines(self):
        for line in ["INF Requesting new quick Tunnel on trycloudflare.com...",
                     "https://api.trycloudflare.com/tunnel", "", "ERR something"]:
            self.assertIsNone(find_url(line), line)


@unittest.skipIf(cv2 is None, "needs OpenCV")
class Poster(unittest.TestCase):
    def test_poster_qr_opens_the_link(self):
        from sim.public_link import poster
        url = "https://brave-lion-tiger-moon.trycloudflare.com"
        ok, texts, _, _ = cv2.QRCodeDetectorAruco().detectAndDecodeMulti(poster(url))
        self.assertTrue(ok)
        self.assertEqual(list(texts), [url])


class Fresh(unittest.TestCase):
    def test_fresh_deletes_the_live_card_state(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "live.json")
        with open(p, "w") as f:
            f.write('{"next": 105, "beacons": {}}')
        self.assertTrue(cm.fresh(p))
        self.assertFalse(os.path.exists(p))
        self.assertFalse(cm.fresh(p))            # nothing to delete the second time


if __name__ == "__main__":
    unittest.main()
