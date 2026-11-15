"""Card maker live mode (sim/live_locks.py): judge message -> next live card id -> lock sent
to every node's control port.

Run:  python -m unittest tests.test_live_maker -v
"""
import json
import os
import random
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.beacon import unlock, check                 # noqa: E402
from core.node import Node, LIVE_START                 # noqa: E402
from sim.live_locks import LiveLocks, ctl_targets, send_all   # noqa: E402


class Live(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "live.json")

    def test_ids_count_up_from_100_and_survive_a_restart(self):
        live = LiveLocks(self.path, rounds=3)
        a = live.add("Ana", "help", "easy", random.Random(1))
        b = live.add("Bo", "here", "hard", random.Random(2))
        self.assertEqual((a[0], b[0]), ("100", "101"))
        again = LiveLocks(self.path, rounds=3)
        self.assertEqual(again.add("Cy", "x", "medium", random.Random(3))[0], "102")
        self.assertEqual(sorted(again.locks_json()["beacons"]), ["100", "101", "102"])

    def test_skips_ids_already_in_beacons_json(self):
        live = LiveLocks(self.path, rounds=3, taken={"100", "101"})
        self.assertEqual(live.add("Ana", "help", "easy", random.Random(1))[0], "102")

    def test_message_has_lock_without_ct_and_node_can_crack_it(self):
        live = LiveLocks(self.path, rounds=3)
        cid, b, pin = live.add("Ana", "canteen, send snacks", "easy", random.Random(4))
        msg = live.lock_message(cid)
        self.assertEqual((msg["type"], msg["cmd"], msg["id"]), ("ctl", "lock", cid))
        self.assertNotIn("ct", msg["lock"])
        self.assertEqual(unlock(live.locks_json()["beacons"][cid], pin, 3), "canteen, send snacks")
        node = Node("pi", [], 0.1, beacons={}, rate=50,
                    check=lambda bb, p: check(bb, p, 3))
        node.on_control(json.loads(json.dumps(msg)), 0.0)
        self.assertIn(cid, node.live_locks)
        self.assertGreaterEqual(int(cid), LIVE_START)

    def test_full_after_199(self):
        live = LiveLocks(self.path, rounds=3, last=101)
        live.add("a", "a", "easy", random.Random(1))
        live.add("b", "b", "easy", random.Random(1))
        with self.assertRaises(ValueError):
            live.add("c", "c", "easy", random.Random(1))

    def test_send_reaches_every_node_ctl_port(self):
        socks = []
        cfg = {"nodes": {}}
        for i, name in enumerate(["webcam", "pi"]):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", 0))
            s.settimeout(1.0)
            socks.append(s)
            cfg["nodes"][name] = {"ip": "127.0.0.1", "ctl_port": s.getsockname()[1]}
        targets = ctl_targets(cfg)
        self.assertEqual(len(targets), 2)
        send_all([{"type": "ctl", "cmd": "lock", "id": "100"}], targets, repeat=1)
        for s in socks:
            data, _ = s.recvfrom(2048)
            self.assertEqual(json.loads(data)["id"], "100")
            s.close()


if __name__ == "__main__":
    unittest.main()
