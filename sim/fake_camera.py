"""Fake camera: spawn cards without a webcam.

    python sim/fake_camera.py            then type:  7 A     (card 7 in zone A)
                                                    3 B     (card 3 in zone B)
    python sim/fake_camera.py 7 A 3 B    one-shot, no prompt

Zone A -> the webcam node spots it, zone B -> the nicla node spots it.
Uses the TEST-ONLY control command {"cmd":"spot","tag":N} (respects 'blind').
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from ctl import send, ROOT  # noqa: E402

CAMERA_FOR_ZONE = {"A": "webcam", "B": "nicla"}


def spawn(cfg, tag, zone):
    cam = CAMERA_FOR_ZONE.get(zone.upper())
    if cam is None:
        print("zone must be A or B")
        return
    send(cfg, [cam], {"type": "ctl", "cmd": "spot", "tag": int(tag)})
    print("card %s -> %s (zone %s)" % (tag, cam, zone.upper()))


def main(argv):
    config = os.environ.get("MURM_CONFIG", os.path.join(ROOT, "config.local.json"))
    with open(config) as f:
        cfg = json.load(f)
    if argv:
        for i in range(0, len(argv) - 1, 2):
            spawn(cfg, argv[i], argv[i + 1])
        return
    print("type '<tag> <zone>' e.g. '7 A'; Ctrl-C to quit")
    while True:
        try:
            parts = input("> ").split()
        except (EOFError, KeyboardInterrupt):
            return
        if len(parts) == 2 and parts[0].isdigit():
            spawn(cfg, parts[0], parts[1])
        elif parts:
            print("?? use: 7 A")


if __name__ == "__main__":
    main(sys.argv[1:])
