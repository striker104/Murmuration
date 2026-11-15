"""Pi LED + UNO Q matrix logic (no hardware needed).

Run:  python -m unittest tests.test_adapters -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from adapters.pi.led import blink_on, led_state, RESCUE_FLASH      # noqa: E402
from adapters.unoq.matrix import pack, rpc_request, matrix_args     # noqa: E402

try:
    import msgpack
except ImportError:
    msgpack = None


def snap(current=None, progress=0.0, mode="connected"):
    jobs = {}
    if current:
        jobs[current] = {"id": current, "progress": progress, "done": False}
    return {"jobs": jobs, "current": current, "mode": mode}


class Msgpack(unittest.TestCase):
    @unittest.skipIf(msgpack is None, "pip install msgpack to cross-check")
    def test_matches_real_msgpack(self):
        for obj in [0, 1, 127, 128, 255, 256, 65535, 70000, -1, -32, -33, -100000,
                    "show", "x" * 40, [], [1, [2, "a"]], True, False, None,
                    [0, 1, "show", [7, 45, 120]], [0, 99999, "show", [-1, 0, 0]]]:
            self.assertEqual(pack(obj), msgpack.packb(obj), repr(obj))

    def test_request_shape(self):
        req = rpc_request(5, "show", [7, 45, 120])
        if msgpack:
            self.assertEqual(msgpack.unpackb(req), [0, 5, "show", [7, 45, 120]])


class MatrixArgs(unittest.TestCase):
    def test_idle_working_crashed(self):
        self.assertEqual(matrix_args(snap(), 0.12), (-1, 0, 0))
        self.assertEqual(matrix_args(snap("7", 0.456), 0.12), (7, 45, 120))
        self.assertEqual(matrix_args(snap("7", 0.4, mode="crashed"), 0.12), (-1, 0, 0))


class PiLed(unittest.TestCase):
    def test_states(self):
        self.assertEqual(led_state(snap(), 100.0, -99), ("off", 0.0))
        self.assertEqual(led_state(snap("3", 0.5), 100.0, -99), ("blink", 0.5))
        self.assertEqual(led_state(snap("3", 0.5), 100.0, 100.0 - RESCUE_FLASH / 2)[0], "on")
        self.assertEqual(led_state(snap("3", 0.5, mode="crashed"), 100.0, -99)[0], "off")

    def test_blinks_faster_with_progress(self):
        def toggles(p):
            ts = [i * 0.01 for i in range(300)]          # 3 s sampled every 10 ms
            states = [blink_on(p, t) for t in ts]
            return sum(1 for a, b in zip(states, states[1:]) if a != b)
        self.assertGreater(toggles(0.9), toggles(0.1) * 3)
        self.assertGreater(toggles(0.0), 0)


if __name__ == "__main__":
    unittest.main()
