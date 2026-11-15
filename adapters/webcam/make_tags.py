"""Make printable AprilTag 36h11 cards (works for the webcam AND the Nicla).

    python -m adapters.webcam.make_tags                 tags 1-8   -> tags_1-8.png
    python -m adapters.webcam.make_tags 1 12            tags 1-12

Each PNG page is A4 at 150 dpi: 2 x 3 cards, ~8 cm tags with the number printed underneath.
Print at 100% (no "fit to page" shrink is fine too — bigger is easier for the Nicla).
Keep the white border around each tag: the detector needs it.
"""
import sys

import cv2
import numpy as np

A4 = (1754, 1240)            # h, w at 150 dpi
TAG_PX = 470                 # ~8 cm
COLS, ROWS = 2, 3


def page(ids):
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    img = np.full(A4, 255, np.uint8)
    cell_w, cell_h = A4[1] // COLS, A4[0] // ROWS
    for k, tid in enumerate(ids):
        r, c = divmod(k, COLS)
        x = c * cell_w + (cell_w - TAG_PX) // 2
        y = r * cell_h + 40
        img[y:y + TAG_PX, x:x + TAG_PX] = cv2.aruco.generateImageMarker(d, tid, TAG_PX)
        cv2.putText(img, "#%d" % tid, (x + TAG_PX // 2 - 40, y + TAG_PX + 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.8, 0, 4)
    return img


def main(argv):
    lo, hi = (int(argv[0]), int(argv[1])) if len(argv) >= 2 else (1, 8)
    ids = list(range(lo, hi + 1))
    per = COLS * ROWS
    for p in range(0, len(ids), per):
        chunk = ids[p:p + per]
        name = "tags_%d-%d.png" % (chunk[0], chunk[-1])
        cv2.imwrite(name, page(chunk))
        print("wrote", name)


if __name__ == "__main__":
    main(sys.argv[1:])
