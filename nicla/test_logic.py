"""Laptop tests for the pure logic in nicla/main.py (no board needed).

    python -m unittest nicla.test_logic -v      (from the repo root)
"""
import importlib.util
import json
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from core import beacon  # noqa: E402   (laptop reference, to check the Nicla port against)

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
        "pin": rng.choice([None, None, "0420", "0999"]),
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


    def test_pin_is_kept_and_lower_string_wins(self):
        base = rand_job(random.Random(2))
        def pin(p):
            j = dict(base); j["pin"] = p; return j
        self.assertEqual(nm.merge_jobs(pin(None), pin("0420"))["pin"], "0420")
        self.assertEqual(nm.merge_jobs(pin("0999"), pin("0420"))["pin"], "0420")
        self.assertIsNone(nm.merge_jobs(pin(None), pin(None))["pin"])

    def test_pin_survives_the_wire_and_a_missing_pin_is_null(self):
        node = nm.node_from_config(CFG, "nicla", 0)
        job = rand_job(random.Random(4))
        job["pin"] = "0420"
        old = dict(rand_job(random.Random(5), "8"))
        del old["pin"]                                   # a peer that has never heard of PINs
        wire = json.dumps({"type": "gossip", "v": 1, "from": "pi", "seq": 1, "jobs": {"7": job, "8": old}})
        node.on_gossip(json.loads(wire), 100)
        self.assertEqual(node.jobs["7"]["pin"], "0420")
        self.assertIsNone(node.jobs["8"]["pin"])
        self.assertEqual(node.gossip_messages()[0]["jobs"]["7"]["pin"], "0420")


class Team:
    """Four nodes in one process with a fake clock. Gossip goes through json like on the wire."""

    def __init__(self, cfg=None, beacons_text=None):
        self.t = 0
        self.nodes = {n: nm.node_from_config(cfg or CFG, n, 0, beacons_text=beacons_text) for n in NAMES}

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
                n.crack(self.t)

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

    def test_reset_stops_sending_old_jobs(self):
        team = Team()
        team.run(1)
        team.spot([1, 2])
        team.run(1)
        nicla = team.nodes["nicla"]
        team.ctl("nicla", "reset")
        msgs = nicla.gossip_messages()
        self.assertEqual(len(msgs), 1)               # still a heartbeat with zero jobs
        self.assertEqual(msgs[0]["jobs"], {})
        self.assertEqual(nicla.telemetry(team.t)["jobs"], {})

    def test_crash_is_silent_and_deaf_then_revive_restarts_clocks(self):
        team = Team()
        team.run(1)
        team.spot([1, 2])
        team.run(2)
        nicla = team.nodes["nicla"]
        team.ctl("nicla", "crash")
        self.assertEqual(nicla.gossip_targets(), [])  # stops sending
        self.assertIsNone(nicla.current)
        frozen = json.loads(json.dumps(nicla.jobs))
        team.run(8)                                   # others carry on; nicla must not merge
        self.assertEqual(nicla.jobs, frozen)
        self.assertEqual(nicla.telemetry(team.t)["mode"], "crashed")
        team.ctl("nicla", "revive")
        self.assertIsNone(nicla.current)
        self.assertEqual(nicla.isolated_since, team.t)
        self.assertEqual(sorted(nicla.last_change), sorted(nicla.jobs))
        self.assertTrue(all(t == team.t for t in nicla.last_change.values()))
        self.assertEqual(len(nicla.gossip_targets()), 3)

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


# ---------------------------------------------------------------- decode mode

ROUNDS = 3
VECTORS = [   # python -m core.beacon --vectors
    ("0420", "a1b2c3d4", 1, "fa8dcff1b8b7d6e17394ba1f4451379d18fa87fca4dad1bbfcfd8ee31ca4275a", "39d2891d0679d7d1"),
    ("0420", "a1b2c3d4", 2, "8f63d4a96373232bd23a4f2ca89c6dc5bc1f9563d6224f76b3048e46865f8d79", "da4d37d56c63ce67"),
    ("07", "00112233", 500, "2a6018b12acc82e6e28c3c03de533e5ed5e379104d45907bed2eedfa98adf495", "e2c242e6d2d0b4f4"),
    ("12345", "deadbeef", 500, "c5ecda5930575f26f557913b3b0bcf68e8dbd6eab702cbfe06d4eeea11297b89", "31fa65a9e04b3f55"),
]


def beacons_text(pins, rounds=ROUNDS):
    """{card id: pin} -> the text of a beacons.json, locked by the reference code."""
    out = {}
    for jid, pin in pins.items():
        out[str(jid)] = beacon.lock("P%s" % jid, "message %s" % jid, pin, len(pin), "a1b2c3%02x" % int(jid), rounds)
    return json.dumps({"rounds": rounds, "beacons": out})


def decode_cfg(rates, **marks):
    """rates: {node: guesses/s}. marks: nicla="counter" marks a node as not decoding yet."""
    nodes = {}
    for n in NAMES:
        nodes[n] = dict(CFG["nodes"][n], rate=rates[n])
        if n in marks:
            nodes[n]["work"] = marks[n]
    return {"work": "decode", "nodes": nodes}


EVEN = {"webcam": 200, "nicla": 200, "unoq": 200, "pi": 200}


def decoder(pins, rates=EVEN, **marks):
    """One Nicla-logic node in decode mode."""
    return nm.node_from_config(decode_cfg(rates, **marks), "nicla", 0, beacons_text=beacons_text(pins))


class SlowHashTest(unittest.TestCase):
    def test_matches_the_reference_vectors(self):
        for pin, salt, rounds, key, check in VECTORS:
            got = nm.pbkdf2(pin.encode(), bytes.fromhex(salt), rounds)
            self.assertEqual(got.hex(), key)
            self.assertEqual(got, beacon.pbkdf2_ref(pin.encode(), bytes.fromhex(salt), rounds))
            b = {"digits": len(pin), "salt": bytes.fromhex(salt), "check": bytes.fromhex(check)}
            self.assertTrue(nm.pin_ok(b, pin, rounds))
            self.assertFalse(nm.pin_ok(b, "9" * len(pin), rounds))

    def test_selftest_passes_and_beacons_load_small(self):
        nm.hash_selftest()
        rounds, b = nm.load_beacons(beacons_text({7: "0420"}))
        self.assertEqual(rounds, ROUNDS)
        self.assertEqual(sorted(b["7"]), ["check", "digits", "salt"])       # no ct / title kept
        self.assertTrue(nm.pin_ok(b["7"], "0420", rounds))

    def test_real_beacons_file_loads(self):
        with open(os.path.join(os.path.dirname(_path), "..", "beacons.json")) as f:
            rounds, b = nm.load_beacons(f.read())
        self.assertEqual(rounds, 500)
        self.assertEqual(len(b), 12)

    def test_pin_str(self):
        self.assertEqual(nm.pin_str(7, 4), "0007")
        self.assertEqual(nm.pin_str(12345, 5), "12345")


class DecodeTest(unittest.TestCase):
    def test_counter_config_is_unchanged(self):
        node = nm.node_from_config(CFG, "nicla", 0)
        self.assertIsNone(node.beacons)
        tele = node.telemetry(0)
        self.assertEqual((tele["trying"], tele["rate"]), (None, None))

    def test_marked_counter_stays_in_counter_mode(self):
        node = nm.node_from_config(decode_cfg(EVEN, nicla="counter"), "nicla", 0)
        self.assertIsNone(node.beacons)
        self.assertIsNone(node.telemetry(0)["rate"])

    def test_decode_mode_needs_beacons(self):
        with self.assertRaises(ValueError):
            nm.node_from_config(decode_cfg(EVEN), "nicla", 0)

    def test_rate_is_measured_when_the_config_gives_none(self):
        cfg = decode_cfg(EVEN)
        del cfg["nodes"]["nicla"]["rate"]
        node = nm.node_from_config(cfg, "nicla", 0, beacons_text=beacons_text({6: "0420"}))
        self.assertGreater(node.rate, 0)

    def test_swarm_finds_the_pins_and_agrees(self):
        team = Team(decode_cfg(EVEN), beacons_text({1: "0420", 2: "37", 4: "0199"}))
        team.run(1)
        team.spot([1, 2, 4])
        team.run(30)
        team.assert_agree(self)
        jobs = team.nodes["nicla"].jobs
        self.assertEqual({i: (j["pin"], j["done"], j["progress"]) for i, j in jobs.items()},
                         {"1": ("0420", True, 1.0), "2": ("37", True, 1.0), "4": ("0199", True, 1.0)})

    def test_progress_is_exact_and_trying_is_reported(self):
        node = decoder({6: "0420"})
        node.spot(6, 0)
        node.step(100)
        self.assertEqual(node.current, "6")
        node.crack(100, 5)                               # guesses 0000..0004
        self.assertEqual(node.jobs["6"]["progress"], 5 / 10000)
        tele = node.telemetry(100)
        self.assertEqual((tele["trying"], tele["rate"]), ("0004", 200.0))
        node.crack(200, 3)
        self.assertEqual(node.trying, "0007")

    def test_bid_is_seconds_per_guess_with_no_zone_bonus(self):
        node = decoder({6: "0420"})
        node.spot(6, 0)                                  # in my own zone B
        node.spot(9, 0)                                  # no beacon: counter work
        self.assertEqual(node.my_bid(node.jobs["6"]), 0.005)
        self.assertEqual(node.my_bid(node.jobs["9"]), 1 / 0.05 - 0.5)

    def test_card_without_a_beacon_uses_counter_work(self):
        node = decoder({6: "0420"})
        node.spot(9, 0)
        for t in range(100, 25000, 100):
            node.step(t)
            node.crack(t)
        self.assertTrue(node.jobs["9"]["done"])
        self.assertIsNone(node.jobs["9"]["pin"])
        self.assertIsNone(node.trying)

    def test_takeover_resumes_from_the_exact_guess(self):
        team = Team(decode_cfg(EVEN), beacons_text({3: "0900"}))
        team.run(1)
        team.spot([3])
        team.run(2)
        owner = team.nodes["pi"].jobs["3"]["owner"]
        before = team.nodes[owner].jobs["3"]["progress"]
        self.assertGreater(before, 0.01)
        team.ctl(owner, "crash")
        team.run(5)                                      # past LEASE
        other = [n for n in NAMES if n != owner][0]
        job = team.nodes[other].jobs["3"]
        new_owner = job["owner"]
        self.assertNotEqual(new_owner, owner)
        self.assertEqual(job["epoch"], 1)
        # the taker never went back: it carried on from the dead node's next guess
        self.assertGreaterEqual(int(team.nodes[new_owner].trying), int(round(before * 10000)))
        team.run(10)
        self.assertEqual(team.nodes[other].jobs["3"]["pin"], "0900")

    def test_take_back_at_any_progress_only_when_twice_as_fast(self):
        def nicla_takes(my_rate, owner_rate):
            node = decoder({5: "9999"}, dict(EVEN, nicla=my_rate))
            job = {"id": "5", "sev": 5, "zone": "A", "spotted_by": "webcam", "progress": 0.8, "done": False,
                   "owner": "pi", "epoch": 0, "bid": round(1.0 / owner_rate, 6), "workers": ["pi"], "pin": None}
            node.on_gossip({"type": "gossip", "v": 1, "from": "pi", "seq": 1, "jobs": {"5": job}}, 100)
            node.step(200)
            return node.jobs["5"]["owner"] == "nicla", node.jobs["5"]["epoch"]
        self.assertEqual(nicla_takes(300, 100), (True, 0))      # 3x faster: take it, same epoch, at 80%
        self.assertEqual(nicla_takes(150, 100), (False, 0))     # only 1.5x faster: leave it

    def test_all_guesses_wrong_ends_unsolved(self):
        data = json.loads(beacons_text({2: "37"}))
        data["beacons"]["2"]["check"] = "00" * 8                 # no PIN will ever match
        node = nm.node_from_config(decode_cfg(EVEN), "nicla", 0, beacons_text=json.dumps(data))
        node.spot(2, 0)
        for t in range(100, 3000, 100):
            node.step(t)
            node.crack(t)
        job = node.jobs["2"]
        self.assertEqual((job["done"], job["pin"], job["progress"]), (True, None, 1.0))

    def test_ignores_work_from_a_counter_marked_peer_on_beacon_jobs(self):
        node = decoder({5: "0420"}, pi="counter")
        def job(jid):
            return {"id": jid, "sev": int(jid), "zone": "A", "spotted_by": "webcam", "progress": 1.0, "done": True,
                    "owner": "pi", "epoch": 2, "bid": 12.5, "workers": ["pi"], "pin": None}
        node.on_gossip({"type": "gossip", "v": 1, "from": "pi", "seq": 1, "jobs": {"5": job("5"), "9": job("9")}}, 100)
        five = node.jobs["5"]                            # beacon job: only "the card exists" is kept
        self.assertEqual((five["progress"], five["done"], five["owner"], five["epoch"], five["workers"]),
                         (0.0, False, None, 0, []))
        self.assertEqual((five["zone"], five["spotted_by"]), ("A", "webcam"))
        self.assertTrue(node.jobs["9"]["done"])          # no beacon: merges normally

    def test_crash_clears_trying(self):
        node = decoder({6: "0420"})
        node.spot(6, 0)
        node.step(100)
        node.crack(100, 3)
        node.on_control({"type": "ctl", "cmd": "crash"}, 150)
        node.crack(200, 3)
        tele = node.telemetry(200)
        self.assertEqual((tele["trying"], tele["mode"]), (None, "crashed"))
        self.assertEqual(node.jobs["6"]["progress"], 3 / 10000)

    def test_talks_to_the_reference_node(self):
        """A Nicla-logic node and one of the laptop reference nodes crack the same cards and agree."""
        from core import node as ref
        text = beacons_text({1: "0150", 2: "0420"})
        a = ref.Node("pi", ["nicla"], 0.08, None, 0.0, beacons=json.loads(text)["beacons"], rate=200.0,
                     check=lambda b, pin: beacon.check(b, pin, ROUNDS))
        cfg = {"work": "decode", "nodes": {"nicla": dict(CFG["nodes"]["nicla"], rate=200),
                                           "pi": dict(CFG["nodes"]["pi"], rate=200)}}
        n = nm.node_from_config(cfg, "nicla", 0, beacons_text=text)
        n.spot(1, 0)
        n.spot(2, 0)
        for i in range(1, 300):
            t = i * 100
            if i % 3 == 0:
                for msg in n.gossip_messages():
                    a.on_gossip(json.loads(json.dumps(msg)), t / 1000.0)
                for msg in a.gossip_messages():
                    n.on_gossip(json.loads(json.dumps(msg)), t)
            n.step(t)
            n.crack(t)
            a.tick(t / 1000.0)
        self.assertEqual({i: j["pin"] for i, j in n.jobs.items()}, {"1": "0150", "2": "0420"})
        self.assertEqual({i: (j["pin"], j["done"]) for i, j in a.jobs.items()},
                         {i: (j["pin"], j["done"]) for i, j in n.jobs.items()})
        workers = set(n.jobs["1"]["workers"]) | set(n.jobs["2"]["workers"])
        self.assertEqual(workers, {"nicla", "pi"})       # they shared the work


if __name__ == "__main__":
    unittest.main()
