"""Real-socket test (localhost, ~2 s): a node whose config has a WRONG IP for a peer
learns the right one from that peer's gossip, and both end up connected.

Run:  python -m unittest tests.test_net -v
"""
import copy
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.net import Runner  # noqa: E402

BASE = {
    "nodes": {
        "a": {"ip": "127.0.0.1", "gossip_port": 5311, "ctl_port": 5321, "speed": 0.1, "zone": None},
        "b": {"ip": "127.0.0.1", "gossip_port": 5312, "ctl_port": 5322, "speed": 0.1, "zone": None},
    },
    "dashboard": {"ip": "127.0.0.1", "telemetry_port": 5399},
}


def run_both(ra, rb, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ra.step()
        rb.step()
        time.sleep(0.02)


class LearnAddress(unittest.TestCase):
    def setUp(self):
        self.runners = []

    def tearDown(self):
        for r in self.runners:
            r.gossip_sock.close()
            r.ctl_sock.close()

    def make(self, name, cfg):
        r = Runner(name, cfg)
        self.runners.append(r)
        return r

    def test_wrong_ip_is_learned_from_gossip(self):
        cfg_a = copy.deepcopy(BASE)
        cfg_a["nodes"]["b"]["ip"] = "10.255.255.1"   # a thinks b is somewhere unreachable
        a = self.make("a", cfg_a)
        b = self.make("b", copy.deepcopy(BASE))
        self.assertEqual(a.addr["b"][0], "10.255.255.1")
        run_both(a, b, 1.5)
        self.assertEqual(a.addr["b"], ("127.0.0.1", 5312))       # learned, port kept
        now = time.monotonic()
        self.assertEqual(a.node.reachable(now), ["b"])
        self.assertEqual(b.node.reachable(now), ["a"])            # b hears a again

    def test_blocked_peer_does_not_change_address(self):
        cfg_a = copy.deepcopy(BASE)
        cfg_a["nodes"]["b"]["ip"] = "10.255.255.1"
        a = self.make("a", cfg_a)
        b = self.make("b", copy.deepcopy(BASE))
        a.control("block", peers=["b"])
        run_both(a, b, 1.0)
        self.assertEqual(a.addr["b"][0], "10.255.255.1")          # ignored gossip -> nothing learned


if __name__ == "__main__":
    unittest.main()
