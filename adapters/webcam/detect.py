"""Webcam node: AprilTag 36h11 detection -> spots jobs, runs the core node in this process.

    python -m adapters.webcam.detect --config config.devtest.json
    python -m adapters.webcam.detect --config config.json --camera 1
    python -m adapters.webcam.detect --selftest          (no camera, checks detection works)

Window keys:  q = quit   b = toggle blind   c = toggle crash   i = toggle isolate

Needs: pip install opencv-python   (OpenCV >= 4.7 has AprilTag 36h11 built in)
"""
import argparse
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from core.net import Runner, load_config        # noqa: E402
from core.node import TagStabilizer            # noqa: E402
from core.run_node import make_logger          # noqa: E402
from core.status import StatusLine, status_line  # noqa: E402
from core.beacon import parse_qr                 # noqa: E402

STATUS_EVERY = 0.2      # seconds between redraws of the terminal status line

MODE_COLOURS = {                                # BGR
    "connected": (80, 200, 80),
    "isolated": (0, 140, 255),
    "alone_long": (0, 0, 230),
    "crashed": (120, 120, 120),
}


# ---- detection ---------------------------------------------------------------

class TagDetector:
    def __init__(self):
        d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        params = cv2.aruco.DetectorParameters()
        self.detector = cv2.aruco.ArucoDetector(d, params)

    def detect(self, frame):
        """Returns [(tag_id, corners 4x2)]."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        corners, ids, _ = self.detector.detectMarkers(gray)
        if ids is None:
            return []
        return [(int(i), c.reshape(4, 2)) for i, c in zip(ids.flatten(), corners)]


class QrReader:
    """Judge QR codes on a phone screen (SPEC §12). Ignores QR codes that aren't ours."""

    def __init__(self):
        # the Aruco-based QR detector reads small / tilted / blurry phone screens far better
        self.detector = (cv2.QRCodeDetectorAruco() if hasattr(cv2, "QRCodeDetectorAruco")
                         else cv2.QRCodeDetector())

    def detect(self, frame):
        """Returns [(qr text, corners 4x2)] for our 'MURM1|...' codes only."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        try:
            ok, texts, pts, _ = self.detector.detectAndDecodeMulti(gray)
        except cv2.error:
            return []
        if not ok or pts is None:
            return []
        return [(t, p.reshape(4, 2)) for t, p in zip(texts, pts) if parse_qr(t)]


# ---- overlay -----------------------------------------------------------------

def draw_tags(frame, tags, jobs):
    for tid, pts in tags:
        job = jobs.get(str(tid))
        colour = (0, 200, 0) if job and job["done"] else (0, 220, 255)
        cv2.polylines(frame, [pts.astype(np.int32)], True, colour, 3)
        x, y = pts[0].astype(int)
        label = "#%d" % tid
        if job:
            label += " done" if job["done"] else " %d%%" % int(job["progress"] * 100)
        cv2.putText(frame, label, (x, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)


def draw_panel(frame, name, snap, fps):
    """Side panel: mode, links, and every job this node believes in."""
    h = frame.shape[0]
    panel = np.full((h, 300, 3), 30, np.uint8)
    mode = snap["mode"]
    cv2.rectangle(panel, (0, 0), (300, 40), MODE_COLOURS.get(mode, (200, 200, 200)), -1)
    cv2.putText(panel, "%s  %s" % (name, mode.upper()), (10, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    y = 62
    info = "peers: %s" % (", ".join(snap["reachable"]) or "none")
    cv2.putText(panel, info, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1)
    y += 20
    flags = "BLIND  " if snap["blind"] else ""
    cv2.putText(panel, "%s%.0f fps" % (flags, fps), (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (0, 0, 255) if snap["blind"] else (160, 160, 160), 1)
    y += 18
    jobs = sorted(snap["jobs"].values(), key=lambda j: (j["done"], j["sev"]))
    for j in jobs:
        if y > h - 20:
            break
        y += 26
        mine = j["id"] == snap["current"]
        txt = "#%s %s" % (j["id"], "done" if j["done"] else (j["owner"] or "-"))
        cv2.putText(panel, txt, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 255) if mine else (230, 230, 230), 1)
        # progress bar
        x0, x1 = 150, 290
        cv2.rectangle(panel, (x0, y - 11), (x1, y + 1), (80, 80, 80), 1)
        fill = x0 + int((x1 - x0) * j["progress"])
        cv2.rectangle(panel, (x0, y - 11), (fill, y + 1),
                      (0, 180, 0) if j["done"] else (0, 200, 255), -1)
    return np.hstack([frame, panel])


# ---- main ----------------------------------------------------------------------

def open_camera(index):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(index)
    if not cap.isOpened():
        raise SystemExit("can't open camera %d (try --camera 1)" % index)
    return cap


def run(args):
    status = StatusLine()
    log = make_logger(args.name, args.log)

    def on_event(kind, job_id, mode):
        status.clear()              # log lines scroll above the live status line
        log(kind, job_id, mode)

    runner = Runner(args.name, load_config(args.config), on_event)
    runner.start_thread()
    titles = {k: b.get("title", "") for k, b in (runner.node.beacons or {}).items()}
    spin, next_status = 0, 0.0
    print("[%s] node up; opening camera %d" % (args.name, args.camera), flush=True)

    cap = open_camera(args.camera)
    detector = TagDetector()
    stable = TagStabilizer()
    qr_reader, qr_stable = QrReader(), TagStabilizer()
    qrs, frame_no = [], 0
    fps, last = 0.0, time.monotonic()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("camera read failed; retrying", flush=True)
                time.sleep(0.2)
                continue
            tags = detector.detect(frame)
            for tid in stable.feed([t for t, _ in tags]):
                runner.spot(tid)        # node ignores it if blind / already known

            # judge QR codes on a phone (SPEC §12): slower to find, so every 2nd frame
            frame_no += 1
            if frame_no % 2 == 0:
                qrs = qr_reader.detect(frame)
                for text in qr_stable.feed([t for t, _ in qrs]):
                    cid, lock = parse_qr(text)
                    if runner.spot_live(cid, lock):
                        status.clear()
                        print("[%s] judge QR: card #%s (%s, %d-digit PIN)"
                              % (args.name, cid, lock["title"], lock["digits"]), flush=True)

            now = time.monotonic()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - last, 1e-3))
            last = now
            if now >= next_status:
                next_status = now + STATUS_EVERY
                spin += 1
                status.draw(status_line(args.name, runner.snapshot(), spin, titles,
                                        runner.node.rate))

            if args.no_window:
                continue
            snap = runner.snapshot()
            draw_tags(frame, tags, snap["jobs"])
            for text, pts in qrs:
                cid = parse_qr(text)[0]
                cv2.polylines(frame, [pts.astype(np.int32)], True, (255, 120, 0), 3)
                x, y = pts[0].astype(int)
                cv2.putText(frame, "QR #%s" % cid, (x, max(20, y - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 120, 0), 2)
            cv2.imshow("Murmuration - %s" % args.name, draw_panel(frame, args.name, snap, fps))
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            elif key == ord("b"):
                runner.control("unblind" if snap["blind"] else "blind")
            elif key == ord("c"):
                runner.control("revive" if snap["mode"] == "crashed" else "crash")
            elif key == ord("i"):
                runner.control("unblock_all" if runner.node.blocked else "isolate")
    except KeyboardInterrupt:
        pass
    finally:
        status.clear()
        runner.stop()
        cap.release()
        cv2.destroyAllWindows()


def selftest():
    """Draw tags 3 and 17 on a white image, check the detector + stabilizer find them."""
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    img = np.full((480, 640), 255, np.uint8)
    for tid, (x, y) in [(3, (60, 120)), (17, (360, 120))]:
        img[y:y + 200, x:x + 200] = cv2.aruco.generateImageMarker(d, tid, 200)
    det, stab = TagDetector(), TagStabilizer()
    found = [t for t, _ in det.detect(img)]
    print("detected:", sorted(found))
    became = []
    for _ in range(3):
        became += stab.feed(found)
    print("stable after 3 frames:", sorted(became, key=int))
    ok = sorted(found) == [3, 17] and sorted(became, key=int) == ["3", "17"]
    print("SELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="webcam")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--log", default=None)
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    run(args)


if __name__ == "__main__":
    main()
