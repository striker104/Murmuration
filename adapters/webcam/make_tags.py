"""Make printable AprilTag 36h11 cards (works for the webcam AND the Nicla).

    python -m adapters.webcam.make_tags                 tags 1-8   -> tags_1-8.png
    python -m adapters.webcam.make_tags 1 12            tags 1-12
    python -m adapters.webcam.make_tags 0 99 4x5        20 per page (smaller tags)

Each PNG page is A4 at 150 dpi. Default 2 x 3 cards, ~8 cm tags (good for the Nicla).
More per page = smaller tags: 3x4 ~5.5 cm, 4x5 ~4 cm (webcam fine, Nicla only very close).
Print at 100% (no "fit to page" shrink is fine too — bigger is easier for the Nicla).
Keep the white border around each tag: the detector needs it.
"""
import json
import os
import sys

import cv2
import numpy as np

A4 = (1754, 1240)            # h, w at 150 dpi
COLS, ROWS = 2, 3            # default grid: ~8 cm tags
BEACONS = os.path.join(os.path.dirname(__file__), "..", "..", "beacons.json")


def labels():
    """card id -> 'Maria  5-digit PIN' from beacons.json (if there is one)."""
    try:
        with open(BEACONS) as f:
            b = json.load(f)["beacons"]
        return {int(k): "%s  %d-digit PIN" % (v["title"], v["digits"]) for k, v in b.items()}
    except (OSError, ValueError, KeyError):
        return {}


def label_for(tid, names):
    """Text under a tag: name + PIN length, 'LIVE judge card' for 100-199, else the number."""
    if tid in names:
        return "#%d  %s" % (tid, names[tid])
    if 100 <= tid <= 199:
        return "#%d  LIVE judge card" % tid
    return "#%d" % tid


def parse_grid(text):
    """'4x5' -> (4, 5); None -> default (2, 3)."""
    if not text:
        return COLS, ROWS
    c, r = text.lower().split("x")
    return int(c), int(r)


def page(ids, cols=COLS, rows=ROWS):
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    img = np.full(A4, 255, np.uint8)
    names = labels()
    cell_w, cell_h = A4[1] // cols, A4[0] // rows
    scale = min(1.0, cell_w / 600.0)                 # label text shrinks with the cell
    label_h = int(60 * scale) + 20
    tag = int(min(cell_w * 0.8, (cell_h - label_h) * 0.93))   # leaves a white border
    for k, tid in enumerate(ids):
        r, c = divmod(k, cols)
        x = c * cell_w + (cell_w - tag) // 2
        y = r * cell_h + (cell_h - tag - label_h) // 2
        img[y:y + tag, x:x + tag] = cv2.aruco.generateImageMarker(d, tid, tag)
        text = label_for(tid, names)
        s = scale
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, s, 2)
        while tw > cell_w - 10 and s > 0.3:          # long name: shrink to fit the cell
            s -= 0.05
            (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, s, 2)
        cv2.putText(img, text, (c * cell_w + (cell_w - tw) // 2, y + tag + int(45 * s) + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, s, 0, 2)
    return img


def main(argv):
    lo, hi = (int(argv[0]), int(argv[1])) if len(argv) >= 2 else (1, 8)
    cols, rows = parse_grid(argv[2] if len(argv) >= 3 else None)
    ids = list(range(lo, hi + 1))
    per = cols * rows
    for p in range(0, len(ids), per):
        chunk = ids[p:p + per]
        name = "tags_%d-%d.png" % (chunk[0], chunk[-1])
        cv2.imwrite(name, page(chunk, cols, rows))
        print("wrote", name)


if __name__ == "__main__":
    main(sys.argv[1:])
