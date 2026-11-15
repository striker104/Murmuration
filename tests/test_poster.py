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

    def test_finds_the_localhost_run_link(self):
        line = "a1b2c3d4e5f6.lhr.life tunneled with tls termination, https://a1b2c3d4e5f6.lhr.life"
        self.assertEqual(find_url(line), "https://a1b2c3d4e5f6.lhr.life")
        self.assertIsNone(find_url("visit https://admin.localhost.run/ to set up a custom domain"))

    def test_finds_the_real_localhost_run_link_with_app_subdomain(self):
        line = ("a60b033218d719.app.lhr.life tunneled with tls termination, "
                "https://a60b033218d719.app.lhr.life")
        self.assertEqual(find_url(line), "https://a60b033218d719.app.lhr.life")

    def test_ignores_other_lines(self):
        for line in ["INF Requesting new quick Tunnel on trycloudflare.com...",
                     "https://api.trycloudflare.com/tunnel", "", "ERR something"]:
            self.assertIsNone(find_url(line), line)


class Commands(unittest.TestCase):
    def test_tunnels_point_at_ipv4_not_localhost(self):
        # the card maker listens on IPv4 only; 'localhost' can resolve to ::1 on Windows
        from sim.public_link import tunnel_commands
        for name, cmd in tunnel_commands("8080", cloudflared="cloudflared.exe"):
            joined = " ".join(cmd)
            self.assertIn("127.0.0.1:8080", joined, name)
            self.assertNotIn("localhost:8080", joined, name)


class RotatingLink(unittest.TestCase):
    def test_new_link_from_the_tunnel_is_noticed(self):
        from sim.public_link import start_tunnel
        code = ("import time;print('https://aaa.app.lhr.life', flush=True);time.sleep(0.5);"
                "print('https://bbb.app.lhr.life', flush=True);time.sleep(30)")
        proc, url, updates = start_tunnel([sys.executable, "-c", code], 5, with_updates=True)
        try:
            self.assertEqual(url, "https://aaa.app.lhr.life")
            self.assertEqual(updates.get(timeout=5), "https://bbb.app.lhr.life")
        finally:
            proc.terminate()


class StartTunnel(unittest.TestCase):
    """start_tunnel with fake 'tunnel' programs (no network)."""

    def run_fake(self, code, timeout=5):
        from sim.public_link import start_tunnel
        proc, url = start_tunnel([sys.executable, "-c", code], timeout)
        if proc:
            proc.terminate()
        return url

    def test_gets_the_link_and_keeps_running(self):
        code = ("import time;print('INF starting');"
                "print('x.lhr.life tunneled, https://abc123.lhr.life', flush=True);time.sleep(30)")
        self.assertEqual(self.run_fake(code), "https://abc123.lhr.life")

    def test_program_that_exits_without_a_link(self):
        self.assertIsNone(self.run_fake("print('failed to request quick Tunnel')"))

    def test_program_that_hangs_times_out(self):
        self.assertIsNone(self.run_fake("import time;time.sleep(30)", timeout=1))


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
