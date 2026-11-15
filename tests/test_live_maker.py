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

    def test_submit_sends_lock_to_all_and_spot_to_the_webcam(self):
        from sim.live_locks import submit
        socks, cfg = {}, {"nodes": {}}
        for name in ["webcam", "pi"]:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", 0))
            s.settimeout(1.0)
            socks[name] = s
            cfg["nodes"][name] = {"ip": "127.0.0.1", "ctl_port": s.getsockname()[1]}
        live = LiveLocks(self.path, rounds=3)
        cid = submit(live, ("Ana", "canteen", "easy"), cfg, spot_node="webcam", repeat=1)
        self.assertEqual(cid, "100")

        def got(name):
            out = []
            socks[name].settimeout(0.3)
            try:
                while True:
                    out.append(json.loads(socks[name].recvfrom(2048)[0]))
            except socket.timeout:
                return out
        web, pi = got("webcam"), got("pi")
        self.assertEqual([m["cmd"] for m in web], ["lock", "spot"])     # lock FIRST, then spot
        self.assertEqual((web[1]["tag"], web[0]["id"]), ("100", "100"))
        self.assertEqual([m["cmd"] for m in pi], ["lock"])
        for s in socks.values():
            s.close()

    def test_typed_message_ends_up_cracked_by_a_node(self):
        from sim.live_locks import spot_message
        live = LiveLocks(self.path, rounds=3)
        cid, _, pin = live.add("Ana", "canteen", "easy", random.Random(5))
        node = Node("webcam", [], 0.1, "A", beacons={}, rate=500,
                    check=lambda bb, p: check(bb, p, 3))
        node.on_control(json.loads(json.dumps(live.lock_message(cid))), 0.0)
        node.on_control(json.loads(json.dumps(spot_message(cid))), 0.0)
        t = 0.0
        while not node.jobs[cid]["done"] and t < 60:
            t += 0.1
            node.tick(t)
        self.assertEqual(node.jobs[cid]["pin"], pin)


if __name__ == "__main__":
    unittest.main()
