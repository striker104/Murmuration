"""Decoded distress messages (decode mode, SPEC "Person B — to do for decode mode").

    python -m unittest dashboard.test_decoded -v      (from the repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402
from decoded import Decoded, beacon  # noqa: E402

ROUNDS = 5
BEACONS = {
    "7": beacon.lock("Maria", "Trapped under the stairs", "0420", 4, "a1b2c3d4", ROUNDS),
    "3": beacon.lock("Sam", "Second floor, two of us", "17", 2, "00112233", ROUNDS),
}
CFG = server.load_config("config.local.json")


def tele(node, jobs, current=None):
    """jobs: {id: pin or None or progress float}. A string pin means done."""
    out = {}
    for jid, v in jobs.items():
        done = isinstance(v, str) or v is True
        out[str(jid)] = {"id": str(jid), "sev": int(jid), "done": done,
                         "progress": 1.0 if done else (v or 0.0),
                         "pin": v if isinstance(v, str) else None, "workers": []}
    return {"type": "telemetry", "node": node, "mode": "connected", "current": current, "jobs": out}


class DecodedMessages(unittest.TestCase):
    def setUp(self):
        self.d = Decoded(ROUNDS, BEACONS)

    def test_nothing_until_a_pin_is_found(self):
        self.d.on_telemetry(tele("pi", {7: 0.4}), 1.0)
        self.assertEqual(self.d.snapshot(), [])

    def test_done_with_pin_unlocks_the_message(self):
        self.d.on_telemetry(tele("pi", {7: "0420"}), 12.5)
        self.assertEqual(self.d.snapshot(), [
            {"job": "7", "title": "Maria", "pin": "0420", "ok": True,
             "message": "Trapped under the stairs", "t": 12.5, "judge": False}])

    def test_newest_first_and_each_job_once(self):
        self.d.on_telemetry(tele("pi", {7: "0420"}), 5.0)
        self.d.on_telemetry(tele("unoq", {7: "0420", 3: "17"}), 9.0)
        self.d.on_telemetry(tele("pi", {7: "0420", 3: "17"}), 9.5)
        self.assertEqual([(e["job"], e["t"]) for e in self.d.snapshot()], [("3", 9.0), ("7", 5.0)])

    def test_wrong_pin_is_not_shown_as_a_message(self):
        self.d.on_telemetry(tele("pi", {7: "0001"}), 3.0)
        e = self.d.snapshot()[0]
        self.assertEqual((e["ok"], e["message"]), (False, None))

    def test_lower_pin_wins_like_the_merge_rule(self):
        self.d.on_telemetry(tele("pi", {7: "0999"}), 3.0)
        self.d.on_telemetry(tele("unoq", {7: "0420"}), 4.0)
        self.d.on_telemetry(tele("pi", {7: "0999"}), 5.0)
        e = self.d.snapshot()[0]
        self.assertEqual((e["pin"], e["ok"], e["t"]), ("0420", True, 3.0))

    def test_ignores_cards_without_a_beacon_and_bad_input(self):
        self.d.on_telemetry(tele("pi", {9: "1234"}), 1.0)
        self.d.on_telemetry({"node": "pi", "jobs": {"7": "junk", "3": {"done": True, "pin": 17}}}, 1.0)
        self.d.on_telemetry({"node": "pi", "jobs": None}, 1.0)
        self.assertEqual(self.d.snapshot(), [])

    def test_reset_forgets(self):
        self.d.on_telemetry(tele("pi", {7: "0420"}), 5.0)
        self.d.reset()
        self.assertEqual(self.d.snapshot(), [])

    def test_titles(self):
        self.assertEqual(self.d.titles(), {"7": {"title": "Maria", "digits": 4, "judge": False},
                                           "3": {"title": "Sam", "digits": 2, "judge": False}})


JUDGE = beacon.lock("Ana", "Room 4, under the table", "0731", 4, "9f3a1c07", 7)


def locks_json(**cards):
    return {"rounds": 7, "beacons": dict(cards)}


class LiveJudgeCards(unittest.TestCase):
    def setUp(self):
        self.d = Decoded(ROUNDS, BEACONS)

    def test_unlocks_a_judge_card_with_its_own_rounds(self):
        self.d.set_live(locks_json(**{"103": JUDGE}))
        self.d.on_telemetry(tele("pi", {103: "0731"}), 4.0)
        e = self.d.snapshot()[0]
        self.assertEqual((e["job"], e["title"], e["ok"], e["message"], e["judge"]),
                         ("103", "Ana", True, "Room 4, under the table", True))

    def test_done_before_the_lock_arrives_shows_up_once_it_does(self):
        self.d.on_telemetry(tele("pi", {103: "0731"}), 4.0)
        self.assertEqual(self.d.snapshot(), [])
        self.d.set_live(locks_json(**{"103": JUDGE}))
        self.d.on_telemetry(tele("pi", {103: "0731"}), 4.5)            # telemetry repeats every 0.5 s
        self.assertEqual(self.d.snapshot()[0]["message"], "Room 4, under the table")

    def test_a_card_in_beacons_json_is_never_live(self):
        b = dict(BEACONS, **{"150": BEACONS["7"]})
        d = Decoded(ROUNDS, b)
        d.set_live(locks_json(**{"150": JUDGE, "42": JUDGE, "105": JUDGE}))
        self.assertEqual(sorted(d.live), ["105"])                      # 150 is preset, 42 is too low
        self.assertFalse(d.titles()["150"]["judge"])
        self.assertTrue(d.titles()["105"]["judge"])

    def test_reset_keeps_live_locks_and_junk_is_refused(self):
        self.d.set_live(locks_json(**{"103": JUDGE}))
        self.d.reset()
        self.assertIn("103", self.d.live)
        for junk in ({}, {"rounds": "x", "beacons": {}}, {"rounds": 5}):
            with self.assertRaises((KeyError, ValueError, TypeError, AttributeError)):
                self.d.set_live(junk)
        self.assertIn("103", self.d.live)                              # a bad poll keeps what we had

    def test_live_card_needs_its_pin_to_count_as_rescued(self):
        self.assertTrue(self.d.needs_pin("103"))
        self.assertTrue(self.d.needs_pin("7"))
        self.assertFalse(self.d.needs_pin("42"))                       # no beacon, under 100: counter card


class DecodedInState(unittest.TestCase):
    def dash(self, cfg, beacons=None):
        d = server.Dashboard(cfg, beacons=beacons)
        self.addCleanup(d.ctl_sock.close)
        return d

    def test_counter_mode_state_has_empty_decode_keys(self):
        st = self.dash(CFG).state()
        self.assertEqual((st["work"], st["beacons"], st["decoded"]), ("counter", {}, []))

    def test_decode_mode_serves_messages_and_reset_clears_them(self):
        dash = self.dash(dict(CFG, work="decode"), beacons=(ROUNDS, BEACONS))
        dash.on_telemetry(tele("pi", {7: "0420"}))
        st = dash.state()
        self.assertEqual(st["work"], "decode")
        self.assertEqual(st["beacons"]["7"], {"title": "Maria", "digits": 4, "judge": False})
        self.assertEqual([(e["job"], e["message"]) for e in st["decoded"]], [("7", "Trapped under the stairs")])
        dash.run_scenario("reset", {})
        self.assertEqual(dash.state()["decoded"], [])

    def test_a_locked_card_counts_as_rescued_only_with_its_pin(self):
        dash = self.dash(dict(CFG, work="decode"), beacons=(ROUNDS, BEACONS))
        dash.on_telemetry(tele("nicla", {7: True, 9: True}))         # counter-mode node: done, no PIN
        s = dash.state()["score"]
        self.assertEqual((s["rescued"], s["total"]), (1, 2))         # only #9 (no beacon) counts
        dash.on_telemetry(tele("pi", {7: "0420", 9: True}))
        self.assertEqual(dash.state()["score"]["rescued"], 2)

    def test_real_beacons_file_loads_next_to_the_config(self):
        cfg = server.load_config("config.decode.local.json")
        rounds, beacons = server.load_beacons(cfg)
        self.assertEqual(rounds, 500)
        self.assertIn("0", beacons)
        self.assertEqual(server.load_beacons(CFG), (0, {}))          # counter mode: none needed

    def test_polls_locks_json_from_the_card_maker(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        body = json.dumps(locks_json(**{"103": JUDGE})).encode()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == "/locks.json" else 404)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        httpd = HTTPServer(("127.0.0.1", 0), H)
        self.addCleanup(httpd.server_close)
        threading.Thread(target=httpd.handle_request, daemon=True).start()
        dash = self.dash(dict(CFG, work="decode"), beacons=(ROUNDS, BEACONS))
        self.assertTrue(dash.fetch_locks("http://127.0.0.1:%d/locks.json" % httpd.server_port))
        self.assertTrue(dash.state()["beacons"]["103"]["judge"])
        dash.on_telemetry(tele("pi", {103: "0731"}))
        st = dash.state()
        self.assertEqual(st["decoded"][0]["message"], "Room 4, under the table")
        self.assertEqual(st["score"]["rescued"], 1)
        self.assertFalse(dash.fetch_locks("http://127.0.0.1:1/locks.json", timeout=0.3))   # nobody there
        self.assertIn("103", dash.decoded.live)

    def test_locks_url_defaults_to_the_webcam_laptop(self):
        cfg = server.load_config("config.json")
        self.assertEqual(server.default_locks_url(cfg), "http://192.168.137.1:8080/locks.json")
        self.assertIsNone(server.default_locks_url(CFG))                  # counter mode: no judge cards
        self.assertIsNone(server.default_locks_url(dict(cfg, locks_url="")))

    def test_decode_failure_does_not_break_the_dashboard(self):
        dash = self.dash(dict(CFG, work="decode"), beacons=(ROUNDS, BEACONS))
        def boom(*args):
            raise RuntimeError("bug")
        dash.decoded.on_telemetry = boom
        dash.decoded.snapshot = boom
        dash.on_telemetry(tele("pi", {7: "0420"}, current=None))
        st = dash.state()
        self.assertEqual(st["decoded"], [])
        self.assertIn("7", st["nodes"]["pi"]["telemetry"]["jobs"])


if __name__ == "__main__":
    unittest.main()
