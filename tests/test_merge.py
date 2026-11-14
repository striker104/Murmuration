"""Merge must be commutative, associative, idempotent (SPEC §6).

Run:  python -m unittest discover -s tests -v      (from the repo root)
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.node import merge_job, new_job  # noqa: E402

NAMES = [None, "nicla", "pi", "unoq", "webcam"]


def random_copy(rng):
    """A random version of job "7", as different nodes might believe it."""
    owner = rng.choice(NAMES)
    j = new_job("7", rng.choice(["A", "B"]), rng.choice(["webcam", "nicla"]))
    j["progress"] = rng.choice([0.0, 0.2, 0.4, 0.6, 1.0])
    j["done"] = rng.random() < 0.2
    j["owner"] = owner
    j["epoch"] = rng.randint(0, 2)
    j["bid"] = None if owner is None else rng.choice([7.833, 8.333, 12.5, 19.5, 20.0])
    j["workers"] = sorted(rng.sample(["nicla", "pi", "unoq", "webcam"], rng.randint(0, 3)))
    return j


class TestMergeLaws(unittest.TestCase):
    N = 3000

    def setUp(self):
        self.rng = random.Random(42)

    def test_commutative(self):
        for _ in range(self.N):
            a, b = random_copy(self.rng), random_copy(self.rng)
            self.assertEqual(merge_job(a, b), merge_job(b, a))

    def test_associative(self):
        for _ in range(self.N):
            a, b, c = (random_copy(self.rng) for _ in range(3))
            self.assertEqual(merge_job(merge_job(a, b), c), merge_job(a, merge_job(b, c)))

    def test_idempotent(self):
        for _ in range(self.N):
            a, b = random_copy(self.rng), random_copy(self.rng)
            self.assertEqual(merge_job(a, a), a)
            ab = merge_job(a, b)
            self.assertEqual(merge_job(ab, b), ab)

    def test_does_not_mutate_inputs(self):
        a, b = random_copy(self.rng), random_copy(self.rng)
        a0, b0 = dict(a), dict(b)
        merge_job(a, b)
        self.assertEqual((a, b), (a0, b0))


class TestMergeRules(unittest.TestCase):
    def job(self, **kw):
        j = new_job("7", "A", "webcam")
        j.update(kw)
        return j

    def test_higher_epoch_wins_even_with_worse_bid(self):
        a = self.job(owner="unoq", epoch=0, bid=8.333)
        b = self.job(owner="nicla", epoch=1, bid=20.0)
        self.assertEqual(merge_job(a, b)["owner"], "nicla")

    def test_same_epoch_lower_bid_wins(self):
        a = self.job(owner="pi", epoch=0, bid=12.5)
        b = self.job(owner="unoq", epoch=0, bid=8.333)
        self.assertEqual(merge_job(a, b)["owner"], "unoq")

    def test_same_bid_lower_name_wins(self):
        a = self.job(owner="webcam", epoch=0, bid=8.333)
        b = self.job(owner="unoq", epoch=0, bid=8.333)
        self.assertEqual(merge_job(a, b)["owner"], "unoq")

    def test_null_owner_loses_at_same_epoch(self):
        a = self.job(owner=None, epoch=0, bid=None)
        b = self.job(owner="pi", epoch=0, bid=12.5)
        self.assertEqual(merge_job(a, b)["owner"], "pi")
        self.assertEqual(merge_job(b, a)["owner"], "pi")

    def test_progress_max_done_or_workers_union(self):
        a = self.job(progress=0.4, done=False, workers=["unoq"])
        b = self.job(progress=0.7, done=True, workers=["pi", "unoq"])
        m = merge_job(a, b)
        self.assertEqual(m["progress"], 0.7)
        self.assertTrue(m["done"])
        self.assertEqual(m["workers"], ["pi", "unoq"])

    def test_zone_from_lower_spotted_by(self):
        a = self.job(zone="A", spotted_by="webcam")
        b = self.job(zone="B", spotted_by="nicla")
        self.assertEqual(merge_job(a, b)["zone"], "B")
        self.assertEqual(merge_job(b, a)["spotted_by"], "nicla")


if __name__ == "__main__":
    unittest.main()
