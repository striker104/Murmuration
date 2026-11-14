"""Acceptance tests for SPEC §9, run in-process on a fake clock (takes a few seconds).

Run:  python -m unittest discover -s tests -v      (from the repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from harness import Sim, MAX_GOSSIP_BYTES  # noqa: E402

ALL = ["nicla", "pi", "unoq", "webcam"]
A_TAGS = [1, 2, 3, 4, 5]
B_TAGS = [6, 7, 8, 9, 10]
TAGS = A_TAGS + B_TAGS


def fresh(seed=1):
    s = Sim(seed)
    s.spot("webcam", *A_TAGS)
    s.spot("nicla", *B_TAGS)
    return s


def everyone_done(s, tags=TAGS):
    return all(s.all_done(n, tags) for n in s.nodes)


def finished_and_agree(s, tags=TAGS):
    return everyone_done(s, tags) and s.agree()


class Scenarios(unittest.TestCase):

    def test_normal_round_all_rescued_and_agree(self):
        s = fresh()
        self.assertTrue(s.run_until(lambda: finished_and_agree(s), 120))
        self.assertLessEqual(s.max_bytes, MAX_GOSSIP_BYTES)   # chunking works (10 jobs)

    def test_new_card_reaches_everyone_within_1s(self):
        s = Sim()
        s.run(3)
        s.spot("webcam", 3)
        self.assertTrue(s.run_until(lambda: all("3" in n.jobs for n in s.nodes.values()), 1.0))

    def test_urgent_first(self):
        # (if BOTH cameras spot at once, each grabs its own card before hearing the
        #  other's — so here one camera spots everything)
        s = Sim()
        s.run(3)
        s.spot("webcam", 6, 5, 4, 3, 2, 1)
        s.run(1)
        # the 4 most urgent jobs should be the ones being worked first
        working = sorted(int(n.current) for n in s.nodes.values() if n.current)
        self.assertEqual(working, [1, 2, 3, 4])

    def _progress_by_node(self, s):
        return {n: {j: v["progress"] for j, v in s.nodes[n].jobs.items()} for n in s.nodes}

    def _check_no_duplicate_work_while_isolated(self, s, before):
        after = self._progress_by_node(s)
        for jid in before["webcam"]:
            movers = [n for n in s.nodes if after[n].get(jid, 0) > before[n].get(jid, 0)]
            self.assertLessEqual(len(movers), 1, "job %s worked by %s" % (jid, movers))

    def _wifi_down(self, real):
        s = fresh()
        s.run(1.5)
        if real:
            s.wire_down = True      # peers time out one by one (fix #1 case)
        else:
            s.ctl(ALL, "isolate")
        s.run(3)
        before = self._progress_by_node(s)    # in-flight packets have landed by now
        for n in s.nodes.values():
            self.assertEqual(n.mode, "isolated", n.name)
            self.assertEqual(sorted(n.members_snapshot), sorted(set(ALL) - {n.name}), n.name)
        # cameras keep spotting while cut off
        self.assertTrue(s.nodes["webcam"].spot(11, s.t))
        s.run(10)                                     # still < 15 s alone
        self._check_no_duplicate_work_while_isolated(s, before)
        s.run(5)
        for n in s.nodes.values():
            self.assertEqual(n.mode, "alone_long", n.name)
        # heal
        if real:
            s.wire_down = False
        else:
            s.ctl(ALL, "unblock_all")
        self.assertTrue(s.run_until(lambda: finished_and_agree(s, TAGS + [11]), 120))

    def test_wifi_down_button(self):
        self._wifi_down(real=False)

    def test_real_wifi_cut(self):
        self._wifi_down(real=True)

    def test_cut_link_still_converges(self):
        s = fresh()
        s.ctl("webcam", "block", peers=["nicla"])
        s.ctl("nicla", "block", peers=["webcam"])
        s.spot("webcam", 11)
        # news hops through unoq/pi
        self.assertTrue(s.run_until(lambda: "11" in s.nodes["nicla"].jobs, 1.5))
        self.assertTrue(s.run_until(lambda: finished_and_agree(s, TAGS + [11]), 120))

    def test_split_then_heal(self):
        s = fresh()
        s.run(1)
        left, right = ["webcam", "unoq"], ["nicla", "pi"]
        s.ctl(left, "block", peers=right)
        s.ctl(right, "block", peers=left)
        s.spot("webcam", 11)
        s.spot("nicla", 12)
        s.run(5)
        self.assertNotIn("11", s.nodes["pi"].jobs)
        self.assertNotIn("12", s.nodes["unoq"].jobs)
        for n in s.nodes.values():
            self.assertEqual(n.mode, "connected")     # two mini-teams
        s.ctl(ALL, "unblock_all")
        self.assertTrue(s.run_until(lambda: finished_and_agree(s, TAGS + [11, 12]), 120))

    def test_cameras_blind(self):
        s = fresh()
        s.run(1)
        s.ctl(["webcam", "nicla"], "blind")
        self.assertFalse(s.nodes["webcam"].spot(20, s.t))
        self.assertFalse(s.nodes["nicla"].spot(21, s.t))
        self.assertTrue(s.run_until(lambda: finished_and_agree(s), 120))
        self.assertNotIn("20", s.nodes["pi"].jobs)

    def test_one_camera_blind(self):
        s = fresh()
        s.ctl("webcam", "blind")
        self.assertFalse(s.nodes["webcam"].spot(20, s.t))
        self.assertTrue(s.nodes["nicla"].spot(21, s.t))
        self.assertTrue(s.run_until(lambda: finished_and_agree(s, TAGS + [21]), 120))

    def test_crash_takeover_keeps_progress(self):
        s = fresh()
        s.run(3)
        victim = s.nodes["unoq"]
        jid = victim.current
        self.assertIsNotNone(jid)
        epoch = victim.jobs[jid]["epoch"]
        s.ctl("unoq", "crash")
        s.step()                                      # let its last packet land
        others = ["webcam", "nicla", "pi"]
        known = max(s.nodes[n].jobs[jid]["progress"] for n in others)
        t0 = s.t

        def taker():
            for n in others:
                j = s.nodes[n].jobs[jid]
                if j["owner"] == n and j["epoch"] > epoch:
                    return n
            return None

        self.assertTrue(s.run_until(lambda: taker() is not None, 8))
        waited = s.t - t0
        self.assertGreaterEqual(waited, 3.5)
        self.assertLess(waited, 6.0)
        # continued from where the team knew it was, not restarted
        self.assertGreaterEqual(s.nodes[taker()].jobs[jid]["progress"], known)
        self.assertGreater(known, 0.1)
        self.assertTrue(s.run_until(
            lambda: all(s.all_done(n, TAGS) for n in ["webcam", "nicla", "pi"]), 150))

    def test_revive_catches_up_without_stealing(self):
        s = fresh()
        s.run(2)
        s.ctl("unoq", "crash")
        s.run(20)                                     # long enough to look like alone_long
        s.nodes["unoq"].pop_events()
        s.ctl("unoq", "revive")
        s.run(1.0)
        kinds = [e[0] for e in s.nodes["unoq"].pop_events()]
        self.assertNotIn("took_over", kinds)
        self.assertEqual(s.done_ids("unoq"), s.done_ids("pi"))   # caught up
        self.assertTrue(s.run_until(lambda: finished_and_agree(s), 120))

    def test_flaky_30_percent_still_converges(self):
        s = fresh(seed=7)
        s.ctl(ALL, "flaky", loss=0.3)
        self.assertTrue(s.run_until(lambda: finished_and_agree(s), 150))

    def test_heal_all_agrees_quickly(self):
        s = fresh()
        s.run(1)
        s.ctl(ALL, "isolate")
        s.ctl(["webcam", "nicla"], "blind")
        s.run(6)
        for n in ALL:                                 # dashboard's "Heal all"
            s.ctl(n, "unblock_all")
            s.ctl(n, "unblind")
            s.ctl(n, "revive")
            s.ctl(n, "flaky", loss=0.0)
        self.assertTrue(s.run_until(
            lambda: len(set(tuple(s.done_ids(n)) for n in ALL)) == 1, 1.0))

    def test_reset_ignores_stale_gossip(self):
        s = fresh()
        s.run(2)
        stale = s.nodes["webcam"].gossip_messages()   # a packet "still in flight"
        s.ctl(ALL, "reset")
        for m in stale:
            s.nodes["pi"].on_gossip(m, s.t + 0.2)
        s.run(2)
        for n in s.nodes.values():
            self.assertEqual(n.jobs, {}, n.name)


if __name__ == "__main__":
    unittest.main()
