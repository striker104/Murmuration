"""Distress beacons: slow hash, lock/unlock/check (core/beacon.py).

Run:  python -m unittest tests.test_beacon -v
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.beacon import pbkdf2_ref, lock, unlock, check, pin_str, load_beacons  # noqa: E402


class Beacon(unittest.TestCase):
    def test_ref_matches_hashlib(self):
        for pin, salt, rounds in [(b"0420", bytes.fromhex("a1b2c3d4"), 50),
                                  (b"7", b"s", 1),
                                  (b"12345", b"x" * 80, 3)]:
            self.assertEqual(pbkdf2_ref(pin, salt, rounds),
                             hashlib.pbkdf2_hmac("sha256", pin, salt, rounds, 32))

    def test_lock_unlock_roundtrip(self):
        msg = "Trapped under desk, room 3B"
        b = lock("Maria", msg, "0420", 4, "a1b2c3d4", 10)
        self.assertEqual(unlock(b, "0420", 10), msg)
        self.assertTrue(check(b, "0420", 10))
        self.assertFalse(check(b, "0421", 10))
        long_msg = "Maria, 34: trapped under the desk in room 3B, leg injured, can hear you"
        b2 = lock("Maria", long_msg, "0420", 4, "a1b2c3d4", 10)
        self.assertEqual(unlock(b2, "0420", 10), long_msg)
        self.assertEqual(len(b["check"]), 16)

    def test_pin_str(self):
        self.assertEqual(pin_str(7, 4), "0007")
        self.assertEqual(pin_str(99, 2), "99")

    def test_load_beacons(self):
        b = lock("Maria", "hi", "07", 2, "a1b2c3d4", 10)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "beacons.json")
            with open(p, "w") as f:
                json.dump({"rounds": 10, "beacons": {"7": b}}, f)
            self.assertEqual(load_beacons(p), (10, {"7": b}))


if __name__ == "__main__":
    unittest.main()
