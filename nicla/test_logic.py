"""Laptop tests for the pure logic in nicla/main.py (no board needed).

    python -m unittest nicla.test_logic -v      (from the repo root)
"""
import importlib.util
import json
import os
import random
import unittest

_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
_spec = importlib.util.spec_from_file_location("nicla_main", _path)
nm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nm)

NAMES = ["webcam", "nicla", "unoq", "pi"]
CFG = {"nodes": {
    "webcam": {"speed": 0.12, "zone": "A"},
    "nicla": {"speed": 0.05, "zone": "B"},
    "unoq": {"speed": 0.12, "zone": None},
    "pi": {"speed": 0.08, "zone": None},
}}


def rand_job(rng, jid="7"):
    owner = rng.choice([None] + NAMES)
    spotter = rng.choice(["webcam", "nicla"])
    return {
        "id": jid, "sev": int(jid), "zone": "A" if spotter == "webcam" else "B",
        "spotted_by": spotter, "progress": rng.choice([0.0, 0.25, 0.5, 1.0]),
        "done": rng.random() < 0.3, "owner": owner, "epoch": rng.randint(0, 2),
        "bid": None if owner is None else rng.choice([7.83, 8.33, 12.5, 19.5]),
        "workers": sorted(rng.sample(NAMES, rng.randint(0, 3))),
    }


class MergeTest(unittest.TestCase):
    def test_commutative_associative_idempotent(self):
        rng = random.Random(1)
        for _ in range(3000):
            a, b, c = rand_job(rng), rand_job(rng), rand_job(rng)
            self.assertEqual(nm.merge_jobs(a, b), nm.merge_jobs(b, a))
            self.assertEqual(nm.merge_jobs(nm.merge_jobs(a, b), c), nm.merge_jobs(a, nm.merge_jobs(b, c)))
            self.assertEqual(nm.merge_jobs(a, a), a)

    def test_claim_rules(self):
        base = rand_job(random.Random(2))
        def claim(owner, epoch, bid):
            j = dict(base); j.update(owner=owner, epoch=epoch, bid=bid); return j
        self.assertEqual(nm.merge_jobs(claim("pi", 1, 12.5), claim("unoq", 0, 8.33))["owner"], "pi")    # epoch
        self.assertEqual(nm.merge_jobs(claim("pi", 0, 12.5), claim("unoq", 0, 8.33))["owner"], "unoq")  # bid
        self.assertEqual(nm.merge_jobs(claim("webcam", 0, 8.33), claim("unoq", 0, 8.33))["owner"], "unoq")  # name
        self.assertEqual(nm.merge_jobs(claim(None, 0, None), claim("pi", 0, 12.5))["owner"], "pi")      # null loses


class Team:
    """Four nodes in one process with a fake clock. Gossip goes through json like on the wire."""

    def __init__(self):
        self.t = 0
        self.nodes = {n: nm.node_from_config(CFG, n, 0) for n in NAMES}

    def ctl(self, name, cmd, **extra):
        msg = {"type": "ctl", "cmd": cmd}
        msg.update(extra)
        self.nodes[name].on_control(msg, self.t)

    def run(self, seconds):
        for _ in range(int(round(seconds * 10))):
            self.t += 100
            if self.t % 300 == 0:
                for n in self.nodes.values():
                    targets = n.gossip_targets()
                    if targets:
                        for msg in n.gossip_messages():
                            wire = json.dumps(msg)
                            assert len(wire) < 1470, "gossip packet too big: %d" % len(wire)
                            for p in targets:
                                if not n.drop_outgoing():
                                    self.nodes[p].on_gossip(json.loads(wire), self.t)
            for n in self.nodes.values():
                n.step(self.t)

    def spot(self, ids):
        for i in ids:
            self.nodes["webcam" if i % 2 else "nicla"].spot(i, self.t)

    def all_done(self, name):
        jobs = self.nodes[name].jobs
        return len(jobs) > 0 and all(j["done"] for j in jobs.values())

    def assert_agree(self, test, names=NAMES, exact=True):
        """exact=False: ignore progress, which lags by one gossip round while a job is being worked."""
        def view(name):
            jobs = self.nodes[name].jobs
            if exact:
                return jobs
            return {i: (j["owner"], j["epoch"], j["done"]) for i, j in jobs.items()}
        first = view(names[0])
        for n in names[1:]:
            test.assertEqual(view(n), first, "%s disagrees with %s" % (n, names[0]))


class ScenarioTest(unittest.TestCase):
    def test_normal_run_finishes_everything_and_agrees(self):
        team = Team()
        team.run(1)
        team.spot(range(1, 9))
        team.run(60)
        team.assert_agree(self)
        self.assertEqual(len(team.nodes["pi"].jobs), 8)
        self.assertTrue(team.all_done("pi"))
        self.assertTrue(all(n.mode == "connected" for n in team.nodes.values()))

    def test_crash_then_takeover_keeps_progress(self):
        team = Team()
        team.run(1)
        team.spot([3])
        team.run(3)
        owner = team.nodes["pi"].jobs["3"]["owner"]
        before = team.nodes[owner].jobs["3"]["progress"]
        self.assertGreater(before, 0.1)
        team.ctl(owner, "crash")
        team.run(3.5)                    # under LEASE: nobody has touched it yet
        other = [n for n in NAMES if n != owner][0]
        self.assertEqual(team.nodes[other].jobs["3"]["owner"], owner)
        team.run(2)                      # past LEASE: taken over, epoch bumped, progress kept
        job = team.nodes[other].jobs["3"]
        self.assertNotEqual(job["owner"], owner)
        self.assertEqual(job["epoch"], 1)
        self.assertGreaterEqual(job["progress"], before)
        self.assertEqual(team.nodes[owner].telemetry(team.t)["mode"], "crashed")
        team.ctl(owner, "revive")
        team.run(20)
        team.assert_agree(self)          # the revived node caught up
        self.assertTrue(team.all_done(owner))

    def test_revive_is_a_noop_on_a_healthy_node(self):
        team = Team()
        team.run(1)
        team.spot([2])
        team.run(2)
        owner = team.nodes["pi"].jobs["2"]["owner"]
        team.ctl(owner, "revive")
        self.assertEqual(team.nodes[owner].current, "2")

    def test_wifi_down_splits_work_then_goes_alone(self):
        team = Team()
        team.run(1)
        team.spot(range(10, 18))         # 8 jobs, more than the team can start at once
        team.run(1)
        # jobs nobody had claimed when the WiFi went down, and who had worked on what so far
        free = [i for i in team.nodes["pi"].jobs
                if all(n.jobs[i]["owner"] is None for n in team.nodes.values())]
        self.assertGreaterEqual(len(free), 3)
        before = {n: {i: list(j["workers"]) for i, j in node.jobs.items()} for n, node in team.nodes.items()}
        for n in NAMES:
            team.ctl(n, "isolate")
        team.run(12)                     # inside ISOLATION_GRACE
        took = {}
        for name, node in team.nodes.items():
            self.assertEqual(node.mode, "isolated")
            for jid in free:
                if name in node.jobs[jid]["workers"] and name not in before[name][jid]:
                    took.setdefault(jid, []).append(name)
        self.assertTrue(took)
        self.assertTrue(all(len(v) == 1 for v in took.values()), took)   # shares don't overlap
        team.nodes["webcam"].spot(1, team.t)          # cameras keep spotting while cut off
        self.assertIn("1", team.nodes["webcam"].jobs)
        team.run(5)
        self.assertTrue(all(n.mode == "alone_long" for n in team.nodes.values()))
        team.run(200)
        for n in NAMES:
            self.assertTrue(team.all_done(n), n)      # everyone finished everything it knew, alone
        for n in NAMES:
            team.ctl(n, "unblock_all")
        team.run(2)
        team.assert_agree(self)

    def test_split_then_heal_merges_and_loses_nothing(self):
        team = Team()
        team.run(1)
        left, right = ["webcam", "unoq"], ["nicla", "pi"]
        for a in left:
            team.ctl(a, "block", peers=right)
        for b in right:
            team.ctl(b, "block", peers=left)
        team.run(3)
        team.nodes["webcam"].spot(5, team.t)
        team.nodes["nicla"].spot(6, team.t)
        team.run(3)
        self.assertNotIn("6", team.nodes["unoq"].jobs)
        self.assertNotIn("5", team.nodes["pi"].jobs)
        team.assert_agree(self, left, exact=False)
        team.assert_agree(self, right, exact=False)
        for n in NAMES:
            team.ctl(n, "unblock_all")
        team.run(1)
        team.assert_agree(self, exact=False)
        self.assertEqual(sorted(team.nodes["pi"].jobs), ["5", "6"])
        team.run(30)
        team.assert_agree(self)
        self.assertTrue(team.all_done("pi"))

    def test_cut_link_still_converges(self):
        team = Team()
        team.ctl("webcam", "block", peers=["pi"])
        team.ctl("pi", "block", peers=["webcam"])
        team.run(1)
        team.nodes["webcam"].spot(4, team.t)
        team.run(1)
        self.assertIn("4", team.nodes["pi"].jobs)     # news hopped through the others

    def test_flaky_still_converges(self):
        random.seed(3)
        team = Team()
        for n in NAMES:
            team.ctl(n, "flaky", loss=0.3)
        team.run(1)
        team.spot(range(1, 6))
        team.run(60)
        for n in NAMES:
            self.assertTrue(team.all_done(n), n)
        team.assert_agree(self)

    def test_blind_camera_makes_no_jobs(self):
        team = Team()
        team.ctl("nicla", "blind")
        self.assertFalse(team.nodes["nicla"].spot(9, 0))
        team.ctl("nicla", "unblind")
        self.assertTrue(team.nodes["nicla"].spot(9, 0))

    def test_reset_ignores_stale_gossip_for_one_second(self):
        team = Team()
        team.run(1)
        team.spot([1, 2])
        team.run(1)
        stale = team.nodes["webcam"].gossip_messages()[0]
        team.ctl("pi", "reset")
        self.assertEqual(team.nodes["pi"].jobs, {})
        team.nodes["pi"].on_gossip(stale, team.t + 500)
        self.assertEqual(team.nodes["pi"].jobs, {})
        team.nodes["pi"].on_gossip(stale, team.t + 1100)
        self.assertEqual(len(team.nodes["pi"].jobs), 2)

    def test_gossip_is_chunked_to_six_jobs(self):
        node = nm.node_from_config(CFG, "nicla", 0)
        self.assertEqual(len(node.gossip_messages()), 1)          # empty heartbeat
        for i in range(14):
            node.spot(i, 0)
        msgs = node.gossip_messages()
        self.assertEqual([len(m["jobs"]) for m in msgs], [6, 6, 2])

    def test_spotter_only_never_claims(self):
        node = nm.node_from_config(CFG, "nicla", 0, spotter_only=True)
        node.spot(1, 0)
        for t in range(100, 30000, 100):
            node.step(t)
        self.assertIsNone(node.jobs["1"]["owner"])


if __name__ == "__main__":
    unittest.main()
