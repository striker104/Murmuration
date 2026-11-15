"""Card maker: people write distress messages -> each is locked with a RANDOM secret PIN at the
difficulty they chose -> beacons.json + printable cards. Nobody (not even us) knows the PINs.

    python sim/card_maker.py serve              LIVE judge cards: judges type on THIS laptop at
                                                http://localhost:8080. "Lock it" -> next card
                                                100-199, lock sent to every node, then the webcam
                                                node is told it spotted it (no printed card);
                                                dashboard reads /locks.json (SPEC §12)
    python sim/card_maker.py make entries.txt   from a file (lines or a Microsoft Forms CSV export)
    python sim/card_maker.py fresh              forget live card numbers (then restart all nodes)

Entry lines:   Maria | Trapped under desk, room 3B | hard        (easy / medium / hard)
Forms CSV:     columns whose names contain "name", "message", "difficult" are used.

File-made cards get ids from --start (default 200; 100-199 = live judge cards) upward and are ADDED to beacons.json (the preset
cards 0-99 stay). Previous file is kept as beacons.prev.json. The secret PINs go to
answer_key.txt (git-ignored) so you can check afterwards. After making cards: copy
beacons.json to every device (+ Nicla, + dashboard laptop) and restart the nodes.
"""
import argparse
import csv
import html
import io
import json
import os
import random
import shutil
import sys
import time
from urllib.parse import parse_qs, quote_plus, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.beacon import lock, unlock      # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BEACONS = os.path.join(ROOT, "beacons.json")
LIVE_STATE = os.path.join(ROOT, "sim", "live.json")
ANSWERS = os.path.join(ROOT, "answer_key.txt")
CARDS_DIR = os.path.join(ROOT, "cards")

MAX_NAME = 12
MAX_MESSAGE = 60
# level: (digits, lowest PIN, highest PIN). Random PIN inside the band: never a fluke like
# 0003 that finishes instantly, never so high it drags. Times at measured rates (x0.6 duty):
#   easy   Pi ~5-12 s       medium  UNO Q ~4-10 s     hard  laptop ~10-21 s
from sim.live_locks import LEVELS   # noqa: E402  (easy/medium/hard -> digits + PIN band)
RATES = [("laptop", 3100), ("unoq", 1000), ("pi", 83)]   # effective guesses/s while running


# ---- reading entries ------------------------------------------------------------

def _clean(text, limit):
    return " ".join(str(text).replace("|", "/").split())[:limit]


def _level(word):
    w = str(word).strip().lower()
    return w if w in LEVELS else "medium"


def parse(text):
    """Pipe lines or a CSV export -> [(name, message, level)]."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return []
    if "|" not in lines[0] and "," in lines[0]:
        return _parse_csv(text)
    out = []
    for ln in lines:
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) < 2:
            continue
        name = parts[0]
        if len(parts) >= 3 and parts[-1].lower() in LEVELS:
            msg, level = " | ".join(parts[1:-1]), parts[-1]
        else:
            msg, level = " | ".join(parts[1:]), "medium"
        msg = " ".join(msg.split())[:MAX_MESSAGE]
        if msg:
            out.append((" ".join(name.split())[:MAX_NAME] or "?", msg, _level(level)))
    return out


def _parse_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    head = [h.lower() for h in rows[0]]

    def col(word):
        for i, h in enumerate(head):
            if word in h:
                return i
        return None
    # "name" must not grab the message column ("Your distress message" has no "name")
    ci_msg, ci_lvl = col("message"), col("difficult")
    ci_name = next((i for i, h in enumerate(head) if "name" in h and i != ci_msg), None)
    out = []
    for r in rows[1:]:
        if ci_msg is None or ci_msg >= len(r) or not r[ci_msg].strip():
            continue
        name = r[ci_name] if ci_name is not None and ci_name < len(r) else "?"
        level = r[ci_lvl] if ci_lvl is not None and ci_lvl < len(r) else "medium"
        out.append((_clean(name, MAX_NAME) or "?", _clean(r[ci_msg], MAX_MESSAGE), _level(level)))
    return out


# ---- making beacons ---------------------------------------------------------------

def pick_pin(level, rng):
    digits, lo, hi = LEVELS[level]
    return str(rng.randint(lo, hi)).zfill(digits)


def build(entries, start_id, rounds, rng=None):
    """-> (beacons {id: beacon}, pins {id: pin})"""
    rng = rng or random.SystemRandom()
    beacons, pins = {}, {}
    for k, (name, msg, level) in enumerate(entries):
        cid = str(start_id + k)
        pin = pick_pin(level, rng)
        salt = "%08x" % rng.getrandbits(32)
        b = lock(name, msg, pin, LEVELS[level][0], salt, rounds)
        assert unlock(b, pin, rounds) == msg
        beacons[cid], pins[cid] = b, pin
    return beacons, pins


def write_beacons(path, beacons, rounds):
    if os.path.exists(path):
        shutil.copyfile(path, os.path.join(os.path.dirname(path), "beacons.prev.json"))
    with open(path, "w") as f:
        json.dump({"rounds": rounds, "beacons": beacons}, f, indent=1, sort_keys=True)


def make(entries, start_id=200):
    """Add the entries to the repo's beacons.json, write answer key + card sheets. -> report."""
    with open(BEACONS) as f:
        current = json.load(f)
    rounds = int(current["rounds"])
    beacons, pins = build(entries, start_id, rounds)
    merged = dict(current["beacons"])
    merged.update(beacons)
    write_beacons(BEACONS, merged, rounds)
    with open(ANSWERS, "a", encoding="utf-8") as f:
        for cid in sorted(pins, key=int):
            f.write("#%s %s pin=%s\n" % (cid, beacons[cid]["title"], pins[cid]))

    lines = ["card  name          level   " + "  ".join("%6s" % n for n, _ in RATES) + " (s)"]
    for k, (name, _, level) in enumerate(entries):
        cid = str(start_id + k)
        secs = ["%6.1f" % ((int(pins[cid]) + 1) / r) for _, r in RATES]
        lines.append("#%-4s %-13s %-7s %s" % (cid, name, level, "  ".join(secs)))
    files = _cards([int(c) for c in beacons])
    lines.append("wrote %s (%d new, %d total), cards: %s"
                 % (BEACONS, len(beacons), len(merged), ", ".join(files)))
    lines.append("NEXT: copy beacons.json to every device + Nicla + dashboard, restart nodes")
    return "\n".join(lines)


def _cards(ids):
    """Printable sheets (needs OpenCV, laptop only)."""
    import cv2
    from adapters.webcam.make_tags import page, COLS, ROWS
    os.makedirs(CARDS_DIR, exist_ok=True)
    per = COLS * ROWS
    ids = sorted(ids)
    files = []
    for p in range(0, len(ids), per):
        chunk = ids[p:p + per]
        name = "tags_%d-%d.png" % (chunk[0], chunk[-1])
        cv2.imwrite(os.path.join(CARDS_DIR, name), page(chunk))
        files.append(name)
    return files


# ---- web form: LIVE judge cards (SPEC §12) -------------------------------------------

def qr_image(text, scale=10):
    """QR code as a grayscale image (OpenCV, no extra library), with a white border."""
    import cv2
    params = cv2.QRCodeEncoder.Params()
    params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M
    q = cv2.QRCodeEncoder.create(params).encode(text)
    q = cv2.resize(q, (q.shape[1] * scale, q.shape[0] * scale), interpolation=cv2.INTER_NEAREST)
    return cv2.copyMakeBorder(q, 4 * scale, 4 * scale, 4 * scale, 4 * scale,
                              cv2.BORDER_CONSTANT, value=255)


def qr_png(text):
    import cv2
    ok, buf = cv2.imencode(".png", qr_image(text))
    return buf.tobytes()


def form_entry(form):
    """parse_qs dict -> (name, message, level), or None if the message is empty."""
    name = _clean((form.get("name") or ["?"])[0], MAX_NAME) or "?"
    msg = _clean((form.get("message") or [""])[0], MAX_MESSAGE)
    level = _level((form.get("level") or ["medium"])[0])
    return (name, msg, level) if msg else None


def render_page(cards, card=None, error=""):
    """cards = [(id, name, level)] handed out so far; card = the one this visitor just got."""
    e = html.escape
    rows = "".join("<li>#%s <b>%s</b> (%s)</li>" % (e(c), e(n), e(l)) for c, n, l in cards)
    opts = "".join('<option value="%s"%s>%s</option>' % (l, " selected" if l == "medium" else "",
                                                        l.capitalize()) for l in LEVELS)
    got = ""
    if card:
        got = ('<div class="got">Your message is card <span>#%s</span>It is in the swarm now: '
               'watch the dashboard while the devices crack your secret PIN.</div>' % e(card))
    err = '<p class="err">%s</p>' % e(error) if error else ""
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Murmuration</title>
<style>body{font-family:sans-serif;max-width:520px;margin:16px auto;padding:0 16px;background:#111;color:#eee}
input,textarea,select,button{width:100%%;font-size:18px;margin:6px 0;padding:8px;box-sizing:border-box}
button{background:#2a7;color:#fff;border:0}.err{color:#f66}
.got{background:#253;padding:12px;margin:12px 0;font-size:18px}.got span{display:block;font-size:56px;font-weight:bold}</style></head><body>%s%s<h2>Send a distress message</h2>
<p>It gets locked with a secret PIN nobody knows. The swarm has to crack it to read it.</p>
<form method="post" action="/add">
<input name="name" maxlength="%d" placeholder="Your name" required>
<textarea name="message" maxlength="%d" rows="3" placeholder="Your message" required></textarea>
<select name="level">%s</select><button>Lock it</button></form>
<h3>Live cards (%d)</h3><ol>%s</ol></body></html>""" % (
        got, err, MAX_NAME, MAX_MESSAGE, opts, len(cards), rows)


def serve(port, config_path, spot_node="webcam"):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from core.net import load_config
    from sim.live_locks import LiveLocks, ctl_targets, send_all, submit

    with open(BEACONS) as f:
        preset = json.load(f)
    live = LiveLocks(LIVE_STATE, int(preset["rounds"]), taken=preset["beacons"], answers=ANSWERS)
    config = load_config(config_path)
    targets = ctl_targets(config)
    mutex = threading.Lock()

    def cards():
        b = live.state["beacons"]
        return [(c, b[c]["title"], {3: "easy", 4: "medium", 5: "hard"}.get(b[c]["digits"], "?"))
                for c in sorted(b, key=int)]

    def resend_forever():
        while True:                          # SPEC §12.2: every live lock again every 5 s
            time.sleep(5.0)
            with mutex:
                msgs = live.all_messages()
            if msgs:
                send_all(msgs, targets, repeat=1)
    threading.Thread(target=resend_forever, daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def _send(self, body, ctype="text/html; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")    # dashboard may fetch this
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/locks.json":
                with mutex:
                    return self._send(json.dumps(live.locks_json()), "application/json")
            q = parse_qs(url.query)
            with mutex:
                page = render_page(cards(), (q.get("card") or [None])[0], (q.get("err") or [""])[0])
            self._send(page)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            entry = form_entry(parse_qs(self.rfile.read(n).decode("utf-8", "replace")))
            where = "/?err=Please+type+a+message"
            if entry and self.path == "/add":
                try:
                    with mutex:            # lock -> every node, then 'spotted' -> webcam
                        cid = submit(live, entry, config, spot_node=spot_node)
                    print("live card #%s for %s (%s): lock sent to %d nodes, spotted by %s"
                          % (cid, entry[0], entry[2], len(targets), spot_node), flush=True)
                    where = "/?card=%s" % cid
                except ValueError as ex:
                    where = "/?err=" + quote_plus(str(ex))
            self.send_response(303)
            self.send_header("Location", where)
            self.end_headers()

        def log_message(self, *a):
            pass

    print("card maker: judges type on this laptop at  http://localhost:%d" % port)
    print("locks go to: %s" % ", ".join("%s:%d" % t for t in targets), flush=True)
    ThreadingHTTPServer(("", port), H).serve_forever()


def fresh(path=None):
    """Forget all live card numbers (start again at #100). -> True if something was deleted.
    Afterwards RESTART every node so they forget the old live locks too."""
    path = path or LIVE_STATE
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["serve", "make", "fresh"])
    ap.add_argument("file", nargs="?", help="entries file for 'make'")
    ap.add_argument("--start", type=int, default=200, help="first card id for file-made cards")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--spot-node", default="webcam",
                    help="node that 'spots' typed messages (the camera node on this laptop)")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"),
                    help="where to send live locks (every node's ctl port)")
    a = ap.parse_args()
    if a.cmd == "fresh":
        print("live cards reset: next judge card is #100" if fresh() else "nothing to reset")
        print("NOW restart every node (Pi, UNO Q services, webcam, Nicla) so they forget old locks")
        return
    if a.cmd == "serve":
        serve(a.port, a.config, a.spot_node)
    else:
        if not a.file:
            raise SystemExit("usage: card_maker.py make entries.txt")
        with open(a.file, encoding="utf-8-sig") as f:
            entries = parse(f.read())
        if not entries:
            raise SystemExit("no entries found in %s" % a.file)
        print(make(entries, a.start))


if __name__ == "__main__":
    main()
