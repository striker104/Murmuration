"""Preset cards (sim/make_beacons.py): 0-11 hand-written, 12-99 generated.

Run:  python -m unittest tests.test_presets -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sim import make_beacons as mb      # noqa: E402
from sim.card_maker import LEVELS        # noqa: E402


class Presets(unittest.TestCase):
    def test_hundred_cards_hand_written_ones_unchanged(self):
        cards = mb.all_cards()
        self.assertEqual(sorted(cards), list(range(100)))
        self.assertEqual(cards[0], mb.CARDS[0])

    def test_generated_cards_are_valid_and_varied(self):
        cards = mb.all_cards()
        levels = set()
        for cid in range(12, 100):
            who, msg, digits, pin = cards[cid]
            self.assertLessEqual(len(msg), 60, msg)
            self.assertEqual(len(pin), digits)
            level = {3: "easy", 4: "medium", 5: "hard"}[digits]
            _, lo, hi = LEVELS[level]
            self.assertTrue(lo <= int(pin) <= hi, (cid, pin))
            levels.add(level)
        self.assertEqual(levels, {"easy", "medium", "hard"})
        self.assertGreater(len(set(c[1] for c in cards.values())), 90)   # messages differ

    def test_same_every_run(self):
        self.assertEqual(mb.all_cards(), mb.all_cards())


if __name__ == "__main__":
    unittest.main()
