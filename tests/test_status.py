"""One-line live status for a node's terminal (core/status.py).

Run:  python -m unittest tests.test_status -v
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.status import status_line, StatusLine, SPINNER, WIDTH  # noqa: E402


def snap(current=None, progress=0.0, mode="connected", trying=None, done=()):
    jobs = {}
    if current:
        jobs[current] = {"id": current, "progress": progress, "done": False}
    for d in done:
        jobs[d] = {"id": d, "progress": 1.0, "done": True}
    return {"jobs": jobs, "current": current, "mode": mode, "trying": trying}


class Line(unittest.TestCase):
    def test_working_decode(self):
        s = status_line("webcam", snap("3", 0.48, trying="04812"), 0, titles={"3": "Jonas"},
                        rate=5210)
        self.assertIn("#3 Jonas", s)
        self.assertIn("try 04812", s)
        self.assertIn(" 48%", s)
        self.assertIn("[" + "#" * 10 + "." * 10 + "]", s)
        self.assertIn("5210/s", s)
        self.assertEqual(s[s.index("webcam") + 7], SPINNER[0])

    def test_spinner_turns(self):
        a = status_line("pi", snap("3", 0.1), 0)
        b = status_line("pi", snap("3", 0.1), 1)
        self.assertNotEqual(a, b)

    def test_idle_and_crashed_and_mode(self):
        self.assertIn("idle", status_line("pi", snap(done=["1", "2"]), 0))
        self.assertIn("2 rescued", status_line("pi", snap(done=["1", "2"]), 0))
        self.assertIn("CRASHED", status_line("pi", snap(mode="crashed"), 0))
        self.assertIn("isolated", status_line("pi", snap("3", 0.2, mode="isolated"), 0))

    def test_counter_job_has_no_try(self):
        self.assertNotIn("try", status_line("pi", snap("30", 0.5), 0))

    def test_fixed_width_so_it_overwrites_cleanly(self):
        for s in [status_line("webcam", snap("3", 0.48, trying="04812"), 0, {"3": "Jonas"}, 5210),
                  status_line("pi", snap(), 0)]:
            self.assertEqual(len(s), WIDTH)


class Writer(unittest.TestCase):
    def test_redraws_same_line_and_clears_before_events(self):
        out = io.StringIO()
        st = StatusLine(out)
        st.draw("hello")
        st.draw("world")
        self.assertEqual(out.getvalue(), "\rhello\rworld")
        self.assertNotIn("\n", out.getvalue())
        st.clear()
        self.assertTrue(out.getvalue().endswith("\r" + " " * 5 + "\r"))


if __name__ == "__main__":
    unittest.main()
