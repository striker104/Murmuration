"""Write beacons.json: each card's distress message, locked with a PIN (core/beacon.py).

    python sim/make_beacons.py

Edit CARDS to change messages / PINs. Time to crack ~ PIN value / guesses per second, so the
PIN you pick sets how long the rescue takes (guesses go 0, 1, 2 ... in order on every device).
Same salt every run (derived from the card id) -> regenerating gives the same file.
"""
import hashlib
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.beacon import lock, unlock      # noqa: E402
from core.node import WORK_FRACTION       # noqa: E402
from sim.card_maker import LEVELS         # noqa: E402

ROUNDS = 500
OUT = os.path.join(os.path.dirname(__file__), "..", "beacons.json")

# card: (who, message, digits, pin)
CARDS = {
    0: ("Maria", "Maria, 34: under the desk in room 3B. Leg trapped, can breathe.", 5, "61840"),
    1: ("Tom", "Tom, 8: in the stairwell, level 2. Scared, not hurt.", 5, "54207"),
    2: ("Aisha", "Aisha, 61: kitchen, ground floor. Needs insulin within 2h.", 5, "47116"),
    3: ("Jonas", "Jonas, 27: lift shaft B. Arm broken. Hears tapping.", 5, "38031"),
    4: ("Priya", "Priya, 45: car park P1, under a car. Conscious.", 4, "9877"),
    5: ("Leo", "Leo, 19: library, back room. Two others with him.", 4, "9250"),
    6: ("Sam", "Sam, 52: corridor 4, near fire door. Dust, coughing.", 4, "8712"),
    7: ("Ella", "Ella, 12: classroom 7. Teacher is with her.", 4, "7455"),
    8: ("Omar", "Omar, 38: roof access. Safe but can't get down.", 3, "963"),
    9: ("Nina", "Nina, 70: bathroom, 1st floor. Door jammed.", 3, "861"),
    10: ("Ravi", "Ravi, 23: basement gym. Water rising slowly.", 3, "774"),
    11: ("Zoe", "Zoe, 30: reception. Minor cuts, helping others.", 2, "89"),
}

# guesses/s at rounds=500, measured on the running nodes 2026-11-15 (nicla still an estimate)
RATES = [("laptop", 5210), ("unoq", 1674), ("pi", 139), ("nicla", 30)]


# cards 12-99: generated rescue stories (same every run). Live judge cards start at 100.
NAMES = ["Lena", "Marco", "Ife", "Chen", "Ola", "Grace", "Yusuf", "Mila", "Dev", "Rosa", "Kofi",
         "Hana", "Ben", "Amara", "Luis", "Sara", "Tariq", "Jade", "Niko", "Femi", "Ivy", "Raj",
         "Elif", "Owen", "Noor", "Kai", "Bea", "Sami", "Tess", "Juan"]
PLACES = ["stairwell C", "lift lobby", "car park P2", "room 4A", "kitchen", "server room",
          "library", "gym", "roof", "basement", "corridor 6", "lab 3", "canteen", "garage",
          "atrium", "toilets L1", "office 12", "storeroom", "chapel", "bus stop"]
STATES = ["smoke, can walk", "leg trapped", "door jammed", "no light", "water rising",
          "with a child", "dizzy, sitting", "arm hurt", "calm, waiting", "can hear you",
          "needs inhaler", "glass cuts", "with 2 others", "cold, shaking", "phone at 3%"]
LEVEL_CYCLE = ["easy", "medium", "hard", "medium", "easy", "hard", "medium"]


def generated_card(cid):
    rng = random.Random("murmuration-card-%d" % cid)
    name = rng.choice(NAMES)
    msg = "%s, %d: %s, %s." % (name, rng.randint(6, 85), rng.choice(PLACES), rng.choice(STATES))
    level = LEVEL_CYCLE[cid % len(LEVEL_CYCLE)]
    digits, lo, hi = LEVELS[level]
    return (name, msg[:60], digits, str(rng.randint(lo, hi)).zfill(digits))


def all_cards():
    cards = dict(CARDS)
    for cid in range(12, 100):
        cards[cid] = generated_card(cid)
    return cards


def salt_for(card):
    return hashlib.sha256(("murmuration-%d" % card).encode()).hexdigest()[:8]


def main():
    beacons = {}
    print("card digits  pin    " + "  ".join("%7s" % n for n, _ in RATES) + "   (seconds)")
    for card, (who, msg, digits, pin) in sorted(all_cards().items()):
        assert len(pin) == digits, card
        b = lock(who, msg, pin, digits, salt_for(card), ROUNDS)
        assert unlock(b, pin, ROUNDS) == msg
        beacons[str(card)] = b
        secs = ["%7.1f" % ((int(pin) + 1) / (r * WORK_FRACTION)) for _, r in RATES]
        print("%4d %6d  %-6s %s" % (card, digits, pin, "  ".join(secs)))
    with open(OUT, "w") as f:
        json.dump({"rounds": ROUNDS, "beacons": beacons}, f, indent=1, sort_keys=True)
    print("wrote %s (%d beacons, rounds=%d)" % (os.path.abspath(OUT), len(beacons), ROUNDS))


if __name__ == "__main__":
    main()
