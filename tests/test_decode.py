"""Decode mode: nodes brute-force the PIN that locks each card's distress message.

Run:  python -m unittest tests.test_decode -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import beacon                       # noqa: E402
from core.node import Node, new_job, TICK     # noqa: E402
from tests.harness import Sim                 # noqa: E402

ROUNDS = 1   # fast tests; real presets use 500


def check1(b, pin):
    return beacon.check(b, pin, ROUNDS)


def make(pin, digits):
    return beacon.lock("Test", "help at " + pin, pin, digits, "a1b2c3d4", ROUNDS)


def solo(beacons, rate=50, check=check1, speed=0.1):
    return Node("pi", [], speed, None, now=0.0, beacons=beacons, rate=rate, check=check)


def run_ticks(node, n, t0=0.0):
    t = t0
    for _ in range(n):
        t = round(t + TICK, 6)
        node.tick(t)
    return t


class SingleNode(unittest.TestCase):
    def test_single_node_finds_pin(self):
        node = solo({"3": make("17", 2)})
        node.spot("3", 0.0)
        for i in range(100):
            node.tick(0.1 * (i + 1))
            if node.jobs["3"]["done"]:
                break
        job = node.jobs["3"]
        self.assertEqual(job["pin"], "17")
        self.assertEqual(job["progress"], 1.0)
        self.assertIn("rescued", [e[0] for e in node.pop_events()])

    def test_resume_from_exact_guess(self):
        tried = []

        def spy(b, pin):
            tried.append(pin)
            return check1(b, pin)

        node = solo({"3": make("80", 2)}, check=spy)
        node.spot("3", 0.0)
        job = node.jobs["3"]
        job.update(progress=0.42, owner="pi", bid=0.02)
        node.tick(0.1)
        self.assertEqual(tried[0], "42")
        self.assertTrue(all(int(p) >= 42 for p in tried))
        self.assertEqual(node.jobs["3"]["progress"], 0.45)   # 3 guesses per tick at rate 50

    def test_unsolved_beacon_finishes(self):
        node = solo({"3": make("17", 2)}, check=lambda b, p: False)
        node.spot("3", 0.0)
        run_ticks(node, 40)
        job = node.jobs["3"]
        self.assertTrue(job["done"])
        self.assertIsNone(job["pin"])
        self.assertIn("unsolved", [e[0] for e in node.pop_events()])

    def test_no_beacon_card_uses_counter(self):
        node = solo({"3": make("17", 2)}, speed=0.1)
        node.spot("30", 0.0)
        node.tick(0.1)
        self.assertAlmostEqual(node.jobs["30"]["progress"], 0.1 * TICK)
        self.assertIsNone(node.trying)

    def test_counter_mode_bids_unchanged(self):
        self.assertEqual(Node("pi", [], 0.08).my_bid(new_job("3", None, "webcam")), 12.5)

    def test_decode_bid_is_seconds_per_guess_no_zone_bonus(self):
        node = Node("webcam", [], 0.12, "A", beacons={"3": make("17", 2)}, rate=200,
                    check=check1)
        self.assertEqual(node.my_bid(new_job("3", "A", "webcam")), 0.005)

    def test_telemetry_has_trying_and_rate(self):
        node = solo({"3": make("17", 2)})
        node.spot("3", 0.0)
        node.tick(0.1)
        tm = node.telemetry(0.1)
        self.assertEqual(tm["rate"], 50)
        self.assertEqual(tm["trying"], "02")       # guesses 00, 01, 02 this tick
        self.assertIsNone(Node("x", [], 0.1).telemetry(0.0)["trying"])


class Swarm(unittest.TestCase):
    def setUp(self):
        self.beacons = {"5": make("9999", 4)}

    def _pi_owns_job_at_70pc(self, sim):
        sim.run(1.0)                                    # let everyone connect
        for n in sim.nodes.values():
            j = new_job("5", None, "webcam")
            j.update(progress=0.7, owner="pi", bid=0.1)
            n.jobs["5"] = j
            n.last_change["5"] = sim.t

    def test_takeback_any_progress(self):
        sim = Sim(beacons=self.beacons, check=check1,
                  rates={"webcam": 1000, "pi": 10, "unoq": 10, "nicla": 10})
        self._pi_owns_job_at_70pc(sim)
        sim.run(1.0)
        job = sim.nodes["pi"].jobs["5"]
        self.assertEqual(job["owner"], "webcam")
        self.assertEqual(job["epoch"], 0)
        self.assertGreater(job["progress"], 0.7)

    def test_no_takeback_when_not_much_faster(self):
        sim = Sim(beacons=self.beacons, check=check1,
                  rates={"webcam": 15, "pi": 10, "unoq": 10, "nicla": 10})
        self._pi_owns_job_at_70pc(sim)
        sim.run(1.0)
        self.assertEqual(sim.nodes["webcam"].jobs["5"]["owner"], "pi")

    def test_gossip_size_with_pins(self):
        beacons = {str(i): make("%02d" % (90 + i % 10), 2) for i in range(12)}
        sim = Sim(beacons=beacons, check=check1,
                  rates={"webcam": 1000, "pi": 1000, "unoq": 1000, "nicla": 1000})
        sim.run(1.0)
        sim.spot("webcam", *range(12))
        self.assertTrue(sim.run_until(lambda: sim.all_done("pi", range(12)), 30))
        sim.run(1.0)
        self.assertTrue(all(sim.nodes["pi"].jobs[str(i)]["pin"] for i in range(12)))
        self.assertLessEqual(sim.max_bytes, 1400)


if __name__ == "__main__":
    unittest.main()
