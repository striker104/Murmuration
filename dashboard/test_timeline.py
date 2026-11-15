"""Timeline history: bars, takeovers, outages, colours.

    python -m unittest dashboard.test_timeline -v      (from the repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timeline  # noqa: E402

ALL = ["webcam", "nicla", "unoq", "pi"]


def tele(node, current, jobs, mode="connected"):
    """jobs: {id: progress}; progress 1.0 means done."""
    return {"type": "telemetry", "node": node, "mode": mode, "current": current,
            "jobs": {str(i): {"id": str(i), "sev": i, "progress": p, "done": p >= 1.0} for i, p in jobs.items()}}


class Bars(unittest.TestCase):
    def setUp(self):
        self.tl = timeline.Timeline(ALL)

    def segs(self, t=99.0):
        return self.tl.snapshot(t)["segments"]

    def test_pickup_then_finish(self):
        self.tl.on_telemetry(tele("pi", "3", {3: 0.0}), 1.0)
        self.tl.on_telemetry(tele("pi", "3", {3: 0.5}), 5.0)
        self.assertEqual(self.segs(), [{"node": "pi", "job": "3", "start": 1.0, "end": None,
                                        "p0": 0.0, "p1": 0.5, "outcome": "live", "from": None}])
        self.tl.on_telemetry(tele("pi", None, {3: 1.0}), 9.0)
        self.assertEqual(self.segs(), [{"node": "pi", "job": "3", "start": 1.0, "end": 9.0,
                                        "p0": 0.0, "p1": 1.0, "outcome": "done", "from": None}])

    def test_current_still_set_when_done(self):
        self.tl.on_telemetry(tele("pi", "3", {3: 0.9}), 1.0)
        self.tl.on_telemetry(tele("pi", "3", {3: 1.0}), 2.0)
        self.assertEqual([(s["end"], s["outcome"]) for s in self.segs()], [(2.0, "done")])

    def test_crash_then_takeover(self):
        self.tl.on_telemetry(tele("unoq", "7", {7: 0.1}), 8.0)
        self.tl.on_telemetry(tele("unoq", "7", {7: 0.5}, mode="crashed"), 12.0)
        self.tl.on_telemetry(tele("unoq", "7", {7: 0.5}, mode="crashed"), 12.5)
        self.tl.on_telemetry(tele("webcam", "7", {7: 0.5}), 16.0)
        snap = self.tl.snapshot(17.0)
        old, new = snap["segments"]
        self.assertEqual((old["end"], old["outcome"], old["p1"]), (12.0, "left", 0.5))
        self.assertEqual((new["node"], new["from"], new["p0"], new["outcome"]), ("webcam", "unoq", 0.5, "live"))
        self.assertEqual(snap["outages"], [{"node": "unoq", "kind": "crashed", "start": 12.0, "end": None}])
        self.tl.on_telemetry(tele("unoq", None, {7: 0.5}), 24.0)
        self.assertEqual(self.tl.snapshot(25.0)["outages"][0]["end"], 24.0)

    def test_no_from_after_a_finished_bar(self):
        self.tl.on_telemetry(tele("pi", "3", {3: 0.5}), 1.0)
        self.tl.on_telemetry(tele("pi", None, {3: 1.0}), 2.0)
        self.tl.on_telemetry(tele("webcam", "3", {3: 0.2}), 3.0)      # webcam has a stale view
        self.assertIsNone(self.segs()[1]["from"])

    def test_two_nodes_on_one_job(self):
        self.tl.on_telemetry(tele("pi", "5", {5: 0.3}), 1.0)
        self.tl.on_telemetry(tele("nicla", "5", {5: 0.3}), 2.0)       # nicla carries on from pi's progress
        a, b = self.segs()
        self.assertEqual((a["outcome"], b["outcome"], b["from"]), ("live", "live", "pi"))

    def test_starting_from_scratch_is_not_a_takeover(self):
        self.tl.on_telemetry(tele("webcam", "5", {5: 0.77}), 1.0)
        self.tl.on_telemetry(tele("webcam", None, {5: 0.77}), 2.0)
        self.tl.on_telemetry(tele("nicla", "5", {5: 0.01}), 9.0)      # nicla never heard of webcam's work
        self.assertIsNone(self.segs()[1]["from"])

    def test_takeover_skips_a_finished_bar_to_find_whose_progress_it_kept(self):
        self.tl.on_telemetry(tele("unoq", "2", {2: 0.67}), 1.0)
        self.tl.on_telemetry(tele("unoq", "2", {2: 0.67}, mode="crashed"), 2.0)
        self.tl.on_telemetry(tele("webcam", "2", {2: 0.66}), 3.0)
        self.tl.on_telemetry(tele("webcam", None, {2: 1.0}), 6.0)
        self.tl.on_telemetry(tele("nicla", "2", {2: 0.67}), 8.0)      # cut off from webcam, has unoq's progress
        self.assertEqual([s["from"] for s in self.segs()], [None, "unoq", "unoq"])

    def test_short_silence_does_not_cut_the_bar(self):
        self.tl.on_telemetry(tele("webcam", "6", {6: 0.2}), 1.0)
        self.tl.silent("webcam", 1.0)
        self.tl.on_telemetry(tele("webcam", "6", {6: 0.6}), 4.0)
        self.assertEqual(self.segs(), [{"node": "webcam", "job": "6", "start": 1.0, "end": None,
                                        "p0": 0.2, "p1": 0.6, "outcome": "live", "from": None}])

    def test_job_finished_during_silence_counts_as_done(self):
        self.tl.on_telemetry(tele("webcam", "5", {5: 0.77}), 1.0)
        self.tl.silent("webcam", 1.0)
        self.tl.on_telemetry(tele("webcam", "8", {5: 1.0, 8: 0.1}), 6.0)
        a, b = self.segs()
        self.assertEqual((a["job"], a["outcome"], a["end"], a["p1"]), ("5", "done", 6.0, 1.0))
        self.assertEqual((b["job"], b["start"], b["outcome"]), ("8", 6.0, "live"))

    def test_crashed_node_coming_back_from_silence_shows_crashed(self):
        self.tl.on_telemetry(tele("pi", None, {3: 0.5}, mode="crashed"), 1.0)
        self.tl.silent("pi", 1.0)
        self.tl.on_telemetry(tele("pi", None, {3: 0.5}, mode="crashed"), 6.0)
        self.assertEqual(self.tl.snapshot(7.0)["outages"],
                         [{"node": "pi", "kind": "crashed", "start": 1.0, "end": 1.0},
                          {"node": "pi", "kind": "no_signal", "start": 1.0, "end": 6.0},
                          {"node": "pi", "kind": "crashed", "start": 6.0, "end": None}])

    def test_silent_node(self):
        self.tl.on_telemetry(tele("pi", "3", {3: 0.2}), 1.0)
        self.tl.silent("pi", 1.0)
        self.tl.silent("pi", 1.0)
        snap = self.tl.snapshot(5.0)
        self.assertEqual((snap["segments"][0]["end"], snap["segments"][0]["outcome"]), (1.0, "left"))
        self.assertEqual(snap["outages"], [{"node": "pi", "kind": "no_signal", "start": 1.0, "end": None}])
        self.tl.on_telemetry(tele("pi", "3", {3: 0.2}), 6.0)
        snap = self.tl.snapshot(7.0)
        self.assertEqual(snap["outages"][0]["end"], 6.0)
        self.assertEqual([(g["start"], g["outcome"]) for g in snap["segments"]], [(1.0, "live")])

    def test_colours_follow_first_appearance(self):
        self.tl.on_telemetry(tele("pi", None, {9: 0.0, 4: 0.0}), 1.0)
        self.tl.on_telemetry(tele("unoq", None, {2: 0.0, 9: 0.0}), 2.0)
        self.assertEqual(self.tl.snapshot(3.0)["colors"], {"4": 0, "9": 1, "2": 2})

    def test_keeps_only_newest_segments(self):
        self.tl.MAX_SEGMENTS = 3
        for i in range(1, 6):
            self.tl.on_telemetry(tele("pi", str(i), {i: 0.0}), float(i))
        self.assertEqual([s["job"] for s in self.segs()], ["3", "4", "5"])

    def test_markers_and_reset(self):
        self.tl.on_telemetry(tele("pi", "3", {3: 0.2}, mode="crashed"), 1.0)
        self.tl.mark("Crashed pi", 1.0)
        self.assertEqual(self.tl.snapshot(2.0)["markers"], [{"t": 1.0, "text": "Crashed pi"}])
        self.tl.reset()
        self.assertEqual(self.tl.snapshot(0.0),
                         {"now": 0.0, "segments": [], "outages": [], "markers": [], "colors": {}})

    def test_malformed_telemetry_never_raises(self):
        bad = {"type": "telemetry", "node": "pi", "mode": "connected", "current": "1",
               "jobs": {"1": "junk", "2": {"id": "2"}}}
        self.tl.on_telemetry(bad, 1.0)
        self.assertEqual(self.segs(), [])
        self.tl.on_telemetry(dict(bad, current="99"), 2.0)
        self.assertEqual(self.segs(), [])
        self.tl.on_telemetry(dict(bad, current=2), 3.0)                # int id, no progress
        self.assertEqual((self.segs()[0]["job"], self.segs()[0]["p0"]), ("2", 0.0))


if __name__ == "__main__":
    unittest.main()
