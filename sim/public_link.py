"""Public link for judges: their phones (any WiFi / 4G) open the card maker page.

    python sim/card_maker.py serve        (first, in another window)
    python sim/public_link.py             starts a Cloudflare quick tunnel, shows a big QR

Tries a Cloudflare quick tunnel first (needs `winget install Cloudflare.cloudflared` once);
if this network blocks it, falls back to localhost.run over SSH (built into Windows).
The link is new every run (so show the QR on screen; it's also saved as cards/join_qr.png).
Needs internet on this laptop. Ctrl-C stops the public link (the card maker keeps running for
people on the hotspot / this laptop). Anyone with the link can add live cards (100-199 only).
"""
import os
import queue
import re
import shutil
import subprocess
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "cards", "join_qr.png")
URL_RE = re.compile(r"https://[a-z0-9-]+\.(?:trycloudflare\.com|lhr\.life)")


def find_url(line):
    """The quick-tunnel link in one line of cloudflared's log, or None."""
    m = URL_RE.search(line or "")
    if not m or m.group(0) == "https://api.trycloudflare.com":
        return None
    return m.group(0)


def poster(url):
    """White A4-ish image: big QR of the link + short instructions (OpenCV only)."""
    import cv2
    import numpy as np
    from sim.card_maker import qr_image
    q = qr_image(url, 1)
    scale = 900 // q.shape[0]
    q = cv2.resize(q, (q.shape[1] * scale, q.shape[0] * scale), interpolation=cv2.INTER_NEAREST)
    img = np.full((1400, 1000), 255, np.uint8)
    x = (1000 - q.shape[1]) // 2
    img[200:200 + q.shape[0], x:x + q.shape[1]] = q
    for text, y, size in [("Send a distress message", 110, 1.8),
                          ("Scan with your phone camera", 175, 1.0),
                          ("The swarm will crack your secret PIN", 200 + q.shape[0] + 70, 1.0),
                          (url.replace("https://", ""), 200 + q.shape[0] + 130, 0.7)]:
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, size, 2)
        cv2.putText(img, text, ((1000 - tw) // 2, y), cv2.FONT_HERSHEY_SIMPLEX, size, 0, 2)
    return img


def _cloudflared():
    exe = shutil.which("cloudflared")
    links = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links",
                         "cloudflared.exe")
    for p in [r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
              r"C:\Program Files\cloudflared\cloudflared.exe", links]:
        if not exe and os.path.exists(p):
            exe = p
    if not exe:
        raise SystemExit("cloudflared not found. Install it once:  winget install "
                         "Cloudflare.cloudflared   (then open a NEW cmd window)")
    return exe


def start_tunnel(cmd, timeout):
    """Run a tunnel command; -> (process, public url) or (None, None) if no link in time."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True)
    except OSError as e:
        print("  can't start %s (%s)" % (cmd[0], e), flush=True)
        return None, None
    found = queue.Queue()

    def read():                      # keep reading forever, or the pipe fills and it freezes
        for line in proc.stdout:
            url = find_url(line)
            if url:
                found.put(url)
        found.put(None)               # it exited
    threading.Thread(target=read, daemon=True).start()
    try:
        url = found.get(timeout=timeout)
    except queue.Empty:
        url = None
    if not url:
        proc.terminate()
        return None, None
    return proc, url


def main():
    import cv2
    port = sys.argv[1] if len(sys.argv) > 1 else "8080"
    local = "localhost:%s" % port
    tries = []
    try:
        tries.append(("Cloudflare", [_cloudflared(), "tunnel", "--url", "http://" + local]))
    except SystemExit as e:
        print("  %s" % e, flush=True)
    tries.append(("localhost.run", ["ssh", "-o", "StrictHostKeyChecking=accept-new",
                                    "-o", "ServerAliveInterval=30", "-o", "ExitOnForwardFailure=yes",
                                    "-R", "80:" + local, "nokey@localhost.run"]))
    proc = url = None
    for name, cmd in tries:
        print("starting public link via %s (up to 30 s)..." % name, flush=True)
        proc, url = start_tunnel(cmd, 30)
        if url:
            break
        print("  %s didn't give a link (blocked on this network?)" % name, flush=True)
    if not url:
        raise SystemExit("no public link possible here. Fallback: judges type on this laptop "
                         "(http://192.168.137.1:%s) and use the printed LIVE cards." % port)
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        img = poster(url)
        cv2.imwrite(OUT, img)
        print("\n  PUBLIC LINK:  %s\n  QR saved to:  %s\n  (wait ~10 s before the first "
              "scan; Ctrl-C or q in the QR window stops the link)\n" % (url, OUT), flush=True)
        cv2.namedWindow("Scan me", cv2.WINDOW_NORMAL)
        cv2.imshow("Scan me", img)
        while proc.poll() is None:
            if cv2.waitKey(500) & 0xFF == ord("q"):
                break
        if proc.poll() is not None:
            print("the tunnel stopped — run this again for a new link", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
