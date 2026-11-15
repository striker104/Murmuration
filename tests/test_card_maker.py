"""Card maker: messages from people -> locked beacons with random secret PINs + printable cards.

Run:  python -m unittest tests.test_card_maker -v
"""
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.beacon import load_beacons, unlock                       # noqa: E402
from sim import card_maker as cm                                    # noqa: E402


class Parse(unittest.TestCase):
    def test_pipe_lines(self):
        text = "Maria | Trapped under desk, room 3B | hard\n\n# comment\nTom|Stairwell|easy\n"
        self.assertEqual(cm.parse(text), [("Maria", "Trapped under desk, room 3B", "hard"),
                                          ("Tom", "Stairwell", "easy")])

    def test_forms_csv_export(self):
        text = ('ID,Start time,Your name,Your distress message,Difficulty\n'
                '1,x,Maria,"Under desk, room 3B",Hard\n'
                '2,x,Tom,Stairwell,Easy\n')
        self.assertEqual(cm.parse(text), [("Maria", "Under desk, room 3B", "hard"),
                                          ("Tom", "Stairwell", "easy")])

    def test_unknown_level_becomes_medium_and_long_text_is_cut(self):
        [(name, msg, level)] = cm.parse("A very long name indeed | " + "x" * 200 + " | ultra")
        self.assertEqual(level, "medium")
        self.assertLessEqual(len(msg), cm.MAX_MESSAGE)
        self.assertLessEqual(len(name), cm.MAX_NAME)

    def test_pipe_in_message_is_kept(self):
        self.assertEqual(cm.parse("Ana | help | room 2 | easy"), [("Ana", "help | room 2", "easy")])


class Build(unittest.TestCase):
    def test_pins_are_secret_random_and_in_the_level_band(self):
        rng = random.Random(1)
        for level, (digits, lo, hi) in cm.LEVELS.items():
            for _ in range(50):
                pin = cm.pick_pin(level, rng)
                self.assertEqual(len(pin), digits)
                self.assertTrue(lo <= int(pin) <= hi, (level, pin))

    def test_build_locks_every_message(self):
        entries = [("Maria", "Under desk", "hard"), ("Tom", "Stairwell", "easy")]
        beacons, pins = cm.build(entries, start_id=20, rounds=3, rng=random.Random(2))
        self.assertEqual(sorted(beacons), ["20", "21"])
        self.assertEqual(unlock(beacons["20"], pins["20"], 3), "Under desk")
        self.assertEqual(beacons["21"]["title"], "Tom")
        self.assertEqual(beacons["21"]["digits"], 3)

    def test_write_keeps_a_backup_and_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "beacons.json")
            with open(path, "w") as f:
                f.write('{"rounds": 500, "beacons": {}}')
            beacons, pins = cm.build([("A", "hi", "easy")], 0, 3, random.Random(3))
            cm.write_beacons(path, beacons, 3)
            self.assertTrue(os.path.exists(os.path.join(d, "beacons.prev.json")))
            self.assertEqual(load_beacons(path), (3, beacons))


class Web(unittest.TestCase):
    def test_form_entry_cleans_and_rejects_empty(self):
        self.assertEqual(cm.form_entry({"name": ["<b>Bob</b>"], "message": ["help|me"],
                                        "level": ["hard"]}), ("<b>Bob</b>", "help/me", "hard"))
        self.assertIsNone(cm.form_entry({"name": ["x"], "message": [" "]}))

    def test_page_shows_your_card_number_and_escapes_html(self):
        cards = [("103", "<b>Bob</b>", "hard")]
        page = cm.render_page(cards, card="103")
        self.assertIn("#103", page)
        self.assertIn("&lt;b&gt;Bob&lt;/b&gt;", page)
        self.assertNotIn("<b>Bob</b>", page)
        self.assertIn("in the swarm", page)
        self.assertNotIn("<img", page)                       # laptop version: nothing to scan
        self.assertNotIn("in the swarm", cm.render_page(cards))


class Fresh(unittest.TestCase):
    def test_fresh_deletes_the_live_card_state(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "live.json")
        with open(p, "w") as f:
            f.write('{"next": 105, "beacons": {}}')
        self.assertTrue(cm.fresh(p))
        self.assertFalse(os.path.exists(p))
        self.assertFalse(cm.fresh(p))            # nothing to delete the second time


if __name__ == "__main__":
    unittest.main()
