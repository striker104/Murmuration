"""Live locks (SPEC §12): a judge's card gets its lock over the network while the swarm runs.

Run:  python -m unittest tests.test_live -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import beacon                                   # noqa: E402
from core.node import Node, new_job, TICK, LIVE_START     # noqa: E402
from tests.harness import Sim                             # noqa: E402

ROUNDS = 1


def check1(b, pin):
    return beacon.check(b, pin, ROUNDS)


def live_lock(pin, digits=3, title="Ana"):
    b = beacon.lock(title, "judge says hi", pin, digits, "a1b2c3d4", ROUNDS)
    return {k: b[k] for k in ("title", "digits", "salt", "check")}


def lock_cmd(jid, lock):
    return {"type": "ctl", "cmd": "lock", "id": str(jid), "lock": lock}


def solo(**kw):
    return Node("pi", ["webcam"], 0.08, None, beacons={}, rate=200, check=check1, **kw)


def ticks(node, n, t=0.0):
    for _ in range(n):
        t = round(t + TICK, 6)
        node.tick(t)
    return t


class LiveNode(unittest.TestCase):
    def test_live_card_without_lock_is_not_worked(self):
        node = solo()
        node.spot("103", 0.0)
        ticks(node, 30)
        job = node.jobs["103"]
        self.assertEqual((job["owner"], job["progress"], job["done"]), (None, 0.0, False))

    def test_lock_command_then_it_cracks(self):
        node = solo()
        node.spot("103", 0.0)
        node.on_control(lock_cmd(103, live_lock("517")), 0.0)
        self.assertEqual(node.jobs["103"]["lock"]["digits"], 3)
        ticks(node, 100)
        self.assertEqual(node.jobs["103"]["pin"], "517")

    def test_lock_before_the_card_is_spotted(self):
        node = solo()
        node.on_control(lock_cmd(104, live_lock("042")), 0.0)
        self.assertNotIn("104", node.jobs)                   # the command never creates a job
        node.spot("104", 0.1)
        self.assertEqual(node.jobs["104"]["lock"]["salt"], "a1b2c3d4")

    def test_bad_lock_command_is_ignored(self):
        node = solo()
        for bad in [None, {"digits": 3}, {"digits": 9, "salt": "aa", "check": "0" * 16},
                    {"digits": 3, "salt": "zz", "check": "0" * 16}]:
            node.on_control(lock_cmd(105, bad), 0.0)
        self.assertNotIn("105", node.live_locks)

    def test_copy_without_lock_is_ignored_for_live_cards(self):
        node = solo()
        node.on_control(lock_cmd(103, live_lock("517")), 0.0)
        j = new_job("103", None, "webcam")
        j.update(progress=0.9, done=True, owner="webcam", bid=0.001)    # no 'lock': can't be real
        node.on_gossip({"type": "gossip", "v": 1, "from": "webcam", "seq": 1,
                        "jobs": {"103": j}}, 0.0)
        job = node.jobs["103"]
        self.assertEqual((job["progress"], job["done"], job["owner"]), (0.0, False, None))
        self.assertEqual(job["spotted_by"], "webcam")       # the spot itself still counts

    def test_lock_learned_from_gossip(self):
        node = solo()
        j = new_job("103", None, "webcam")
        j["lock"] = live_lock("517")
        node.on_gossip({"type": "gossip", "v": 1, "from": "webcam", "seq": 1,
                        "jobs": {"103": j}}, 0.0)
        self.assertIn("103", node.live_locks)
        ticks(node, 100)
        self.assertEqual(node.jobs["103"]["pin"], "517")

    def test_reset_keeps_live_locks(self):
        node = solo()
        node.on_control(lock_cmd(103, live_lock("517")), 0.0)
        node.on_control({"type": "ctl", "cmd": "reset"}, 0.0)
        self.assertEqual(node.jobs, {})
        self.assertIn("103", node.live_locks)

    def test_counter_mode_ignores_locks_and_cards_below_100_unchanged(self):
        node = Node("pi", [], 0.1)
        node.on_control(lock_cmd(103, live_lock("517")), 0.0)
        self.assertEqual(node.live_locks, {})
        dec = solo()
        dec.spot("30", 0.0)
        ticks(dec, 1)
        self.assertGreater(dec.jobs["30"]["progress"], 0.0)   # <100, no beacon: counter work
        self.assertEqual(LIVE_START, 100)

    def test_gossip_messages_stay_under_1200_bytes(self):
        node = solo()
        for i in range(100, 113):
            node.on_control(lock_cmd(i, live_lock("517", title="Abcdefghijkl")), 0.0)
            node.spot(str(i), 0.0)
            node.jobs[str(i)].update(workers=["nicla", "pi", "unoq", "webcam"], epoch=12,
                                     owner="webcam", bid=0.000192, pin="99999")
        msgs = node.gossip_messages()
        sizes = [len(json.dumps(m, separators=(",", ":"))) for m in msgs]
        self.assertTrue(all(s <= 1200 for s in sizes), sizes)
        self.assertEqual(sum(len(m["jobs"]) for m in msgs), 13)
        self.assertTrue(all(len(m["jobs"]) <= 6 for m in msgs))

    def test_card_from_beacons_json_above_100_is_a_normal_card(self):
        b = beacon.lock("F", "file card", "250", 3, "a1b2c3d4", ROUNDS)
        node = Node("pi", ["webcam"], 0.08, None, beacons={"200": b}, rate=200, check=check1)
        j = new_job("200", None, "webcam")
        j.update(progress=0.4, owner="webcam", bid=0.001)          # no lock field: still real
        node.on_gossip({"type": "gossip", "v": 1, "from": "webcam", "seq": 1,
                        "jobs": {"200": j}}, 0.0)
        self.assertEqual(node.jobs["200"]["progress"], 0.4)


class LiveSwarm(unittest.TestCase):
    def test_lock_sent_to_one_node_spreads_and_everyone_agrees(self):
        sim = Sim(beacons={}, check=check1,
                  rates={"webcam": 300, "pi": 100, "unoq": 200, "nicla": 50})
        sim.run(1.0)
        sim.ctl("pi", "lock", id="103", lock=live_lock("733"))   # only the Pi heard the judge
        sim.spot("webcam", 103)
        self.assertTrue(sim.run_until(lambda: sim.all_done("nicla", [103]), 30))
        sim.run(1.0)
        self.assertTrue(sim.agree())
        self.assertEqual(sim.nodes["webcam"].jobs["103"]["pin"], "733")
        self.assertTrue(all("103" in n.live_locks for n in sim.nodes.values()))


if __name__ == "__main__":
    unittest.main()
