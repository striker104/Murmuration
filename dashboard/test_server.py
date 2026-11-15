"""Checks that each dashboard button sends the control commands SPEC 9 says it should.

    python -m unittest dashboard.test_server -v      (from the repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

CFG = server.load_config("config.local.json")
ALL = ["webcam", "nicla", "unoq", "pi"]


def sent(name, **params):
    """{node: [(cmd, extra fields)...]}"""
    out = {}
    for node, msg in server.build_commands(name, params, CFG):
        assert msg["type"] == "ctl"
        extra = {k: v for k, v in msg.items() if k not in ("type", "cmd")}
        out.setdefault(node, []).append((msg["cmd"], extra))
    return out


class ScenarioCommands(unittest.TestCase):
    def test_heal(self):
        got = sent("heal")
        self.assertEqual(sorted(got), sorted(ALL))
        for cmds in got.values():
            self.assertEqual(cmds, [("unblock_all", {}), ("unblind", {}), ("revive", {}), ("flaky", {"loss": 0.0})])

    def test_everyone_scenarios(self):
        self.assertEqual(sent("wifi_down"), {n: [("isolate", {})] for n in ALL})
        self.assertEqual(sent("reset"), {n: [("reset", {})] for n in ALL})
        self.assertEqual(sent("flaky"), {n: [("flaky", {"loss": 0.3})] for n in ALL})

    def test_cut(self):
        self.assertEqual(sent("cut", x="pi", y="unoq"),
                         {"pi": [("block", {"peers": ["unoq"]})], "unoq": [("block", {"peers": ["pi"]})]})

    def test_split(self):
        got = sent("split")
        for n in ("webcam", "unoq"):
            self.assertEqual(got[n], [("block", {"peers": ["nicla", "pi"]})])
        for n in ("nicla", "pi"):
            self.assertEqual(got[n], [("block", {"peers": ["webcam", "unoq"]})])

    def test_cameras_and_single_node(self):
        self.assertEqual(sent("cameras_blind"), {"webcam": [("blind", {})], "nicla": [("blind", {})]})
        self.assertEqual(sent("blind", x="nicla"), {"nicla": [("blind", {})]})
        self.assertEqual(sent("crash", x="pi"), {"pi": [("crash", {})]})
        self.assertEqual(sent("revive", x="pi"), {"pi": [("revive", {})]})

    def test_bad_input(self):
        for name, params in (("nope", {}), ("crash", {}), ("crash", {"x": "bob"}),
                             ("cut", {"x": "pi", "y": "pi"}), ("flaky", {"loss": 2})):
            with self.assertRaises(ValueError):
                server.build_commands(name, params, CFG)


class Scoreboard(unittest.TestCase):
    def test_score_from_telemetry(self):
        dash = server.Dashboard(CFG)
        def job(i, done, workers):
            return {"id": str(i), "sev": i, "done": done, "progress": 1.0 if done else 0.2, "workers": workers}
        def tele(node, jobs):
            dash.on_telemetry({"type": "telemetry", "node": node, "mode": "connected", "jobs": {j["id"]: j for j in jobs}})
        tele("pi", [job(5, True, ["pi"]), job(1, False, ["unoq"]), job(2, False, []), job(9, False, [])])
        tele("unoq", [job(5, True, ["pi"]), job(1, True, ["unoq", "webcam"]), job(2, False, [])])
        s = dash.state()["score"]
        self.assertEqual((s["rescued"], s["total"], s["duplicates"]), (2, 4, 1))
        self.assertEqual(s["urgent_ids"], ["1", "2", "5"])
        self.assertEqual(s["urgent_avg_order"], 1.5)       # #5 finished 1st, #1 finished 2nd
        self.assertTrue(dash.state()["nodes"]["webcam"]["stale"])
        dash.ctl_sock.close()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def tele(node, current, jobs, mode="connected"):
    return {"type": "telemetry", "node": node, "mode": mode, "current": current,
            "jobs": {str(i): {"id": str(i), "sev": i, "progress": p, "done": p >= 1.0, "workers": []}
                     for i, p in jobs.items()}}


class TimelineInState(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.dash = server.Dashboard(CFG, clock=self.clock)
        self.addCleanup(self.dash.ctl_sock.close)

    def test_bars_use_round_time(self):
        self.clock.t = 103.0
        self.dash.on_telemetry(tele("pi", "3", {3: 0.2}))
        self.clock.t = 104.0
        tl = self.dash.state()["timeline"]
        self.assertEqual(tl["now"], 4.0)
        self.assertEqual([(s["node"], s["job"], s["start"], s["outcome"]) for s in tl["segments"]],
                         [("pi", "3", 3.0, "live")])
        self.assertEqual(tl["colors"], {"3": 0})

    def test_silence_becomes_no_signal(self):
        self.clock.t = 101.0
        self.dash.on_telemetry(tele("pi", "3", {3: 0.2}))
        self.clock.t = 101.0 + server.STALE_AFTER + 0.5
        tl = self.dash.state()["timeline"]
        self.assertEqual(tl["segments"][0]["outcome"], "left")
        self.assertEqual(tl["outages"], [{"node": "pi", "kind": "no_signal", "start": 1.0, "end": None}])

    def test_scenario_adds_marker_with_new_wording(self):
        self.clock.t = 112.0
        self.dash.run_scenario("crash", {"x": "unoq"})
        st = self.dash.state()
        self.assertEqual(st["timeline"]["markers"], [{"t": 12.0, "text": "Crashed unoq"}])
        self.assertEqual(st["events"][-1]["text"], "Crashed unoq")

    def test_reset_starts_a_new_round(self):
        self.clock.t = 105.0
        self.dash.on_telemetry(tele("pi", "3", {3: 0.2}))
        self.clock.t = 130.0
        self.dash.run_scenario("reset", {})
        self.clock.t = 130.5
        self.dash.on_telemetry(tele("pi", "3", {3: 0.3}))          # stale packet in the quiet period
        self.clock.t = 134.0                                        # pi has been silent since before reset
        tl = self.dash.state()["timeline"]
        self.assertEqual(tl["now"], 4.0)
        self.assertEqual((tl["segments"], tl["markers"], tl["colors"]), ([], [], {}))
        self.assertTrue(all(o["start"] >= 0 for o in tl["outages"]))

    def test_timeline_failure_does_not_break_the_dashboard(self):
        def boom(*args):
            raise RuntimeError("bug")
        self.dash.timeline.on_telemetry = boom
        self.dash.timeline.snapshot = boom
        self.dash.on_telemetry(tele("pi", "3", {3: 0.2}))
        st = self.dash.state()
        self.assertIsNone(st["timeline"])
        self.assertEqual(st["nodes"]["pi"]["telemetry"]["current"], "3")


class EventWording(unittest.TestCase):
    def test_describe(self):
        cases = [("heal", {}, "Healed everything"), ("wifi_down", {}, "Took Wi-Fi down"),
                 ("split", {}, "Split the team"), ("cameras_blind", {}, "Blinded both cameras"),
                 ("reset", {}, "Reset the round"),
                 ("cut", {"x": "webcam", "y": "nicla"}, "Cut the link between webcam and nicla"),
                 ("blind", {"x": "nicla"}, "Blinded nicla's camera"),
                 ("unblind", {"x": "nicla"}, "Restored nicla's camera"),
                 ("crash", {"x": "pi"}, "Crashed pi"), ("revive", {"x": "pi"}, "Revived pi"),
                 ("flaky", {}, "Started dropping 30% of messages")]
        for name, params, text in cases:
            self.assertEqual(server.describe(name, params), text)


if __name__ == "__main__":
    unittest.main()
