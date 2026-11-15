"""Public link for judges: their phones (any WiFi / 4G) open the card maker page.

    python sim/card_maker.py serve        (first, in another window)
    python sim/public_link.py             starts a Cloudflare quick tunnel, shows a big QR

Needs `cloudflared` once:  winget install Cloudflare.cloudflared
The link is new every run (so show the QR on screen; it's also saved as cards/join_qr.png).
Needs internet on this laptop. Ctrl-C stops the public link (the card maker keeps running for
people on the hotspot / this laptop). Anyone with the link can add live cards (100-199 only).
"""
import os
import re
import shutil
import subprocess
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "cards", "join_qr.png")
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


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


def main():
    import cv2
    port = sys.argv[1] if len(sys.argv) > 1 else "8080"
    proc = subprocess.Popen([_cloudflared(), "tunnel", "--url", "http://localhost:%s" % port],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print("starting public link (10-20 s)...", flush=True)
    url = None
    try:
        for line in proc.stdout:
            url = url or find_url(line)
            if url:
                break
        if not url:
            raise SystemExit("cloudflared stopped without a link — is this laptop online?")
        # keep reading its log forever, or the pipe fills up and the tunnel freezes
        threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        img = poster(url)
        cv2.imwrite(OUT, img)
        print("\n  PUBLIC LINK:  %s\n  QR saved to:  %s\n  (wait ~10 s before the first "
              "scan; Ctrl-C here stops the link)\n" % (url, OUT), flush=True)
        cv2.namedWindow("Scan me", cv2.WINDOW_NORMAL)
        cv2.imshow("Scan me", img)
        while proc.poll() is None:
            if cv2.waitKey(500) & 0xFF == ord("q"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
