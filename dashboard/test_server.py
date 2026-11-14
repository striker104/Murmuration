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


if __name__ == "__main__":
    unittest.main()
