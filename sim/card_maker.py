"""Card maker: people write distress messages -> each is locked with a RANDOM secret PIN at the
difficulty they chose -> beacons.json + printable cards. Nobody (not even us) knows the PINs.

    python sim/card_maker.py serve              web form on http://<this laptop>:8080
                                                (phones on the hotspot: http://192.168.137.1:8080)
    python sim/card_maker.py make entries.txt   from a file (lines or a Microsoft Forms CSV export)

Entry lines:   Maria | Trapped under desk, room 3B | hard        (easy / medium / hard)
Forms CSV:     columns whose names contain "name", "message", "difficult" are used.

New cards get ids from --start (default 20) upward and are ADDED to beacons.json (the preset
cards 0-11 stay). Previous file is kept as beacons.prev.json. The secret PINs go to
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
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.beacon import lock, unlock      # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BEACONS = os.path.join(ROOT, "beacons.json")
ENTRIES = os.path.join(ROOT, "sim", "entries.txt")
ANSWERS = os.path.join(ROOT, "answer_key.txt")
CARDS_DIR = os.path.join(ROOT, "cards")

MAX_NAME = 12
MAX_MESSAGE = 60
# level: (digits, lowest PIN, highest PIN). Random PIN inside the band: never a fluke like
# 0003 that finishes instantly, never so high it drags. Times at measured rates (x0.6 duty):
#   easy   Pi ~5-12 s       medium  UNO Q ~4-10 s     hard  laptop ~10-21 s
LEVELS = {"easy": (3, 400, 999), "medium": (4, 4000, 9999), "hard": (5, 30000, 65000)}
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


def make(entries, start_id=20):
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


# ---- web form ---------------------------------------------------------------------

def add_entry(path, form):
    """form = parse_qs dict. Appends one 'name | message | level' line. -> True if added."""
    name = _clean((form.get("name") or ["?"])[0], MAX_NAME) or "?"
    msg = _clean((form.get("message") or [""])[0], MAX_MESSAGE)
    level = _level((form.get("level") or ["medium"])[0])
    if not msg:
        return False
    with open(path, "a", encoding="utf-8") as f:
        f.write("%s | %s | %s\n" % (name, msg, level))
    return True


def render_page(entries, note=""):
    e = html.escape
    rows = "".join("<li><b>%s</b> (%s): %s</li>" % (e(n), e(l), e(m)) for n, m, l in entries)
    opts = "".join('<option value="%s"%s>%s</option>' % (l, " selected" if l == "medium" else "",
                                                        l.capitalize()) for l in LEVELS)
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Murmuration cards</title>
<style>body{font-family:sans-serif;max-width:520px;margin:16px auto;padding:0 16px;background:#111;color:#eee}
input,textarea,select,button{width:100%%;font-size:18px;margin:6px 0;padding:8px;box-sizing:border-box}
button{background:#2a7;color:#fff;border:0}pre{white-space:pre-wrap;background:#222;padding:8px}</style></head>
<body><h2>Send a distress message</h2>
<p>It gets locked with a secret PIN. The swarm has to crack it to read it.</p>
<form method="post" action="/add">
<input name="name" maxlength="%d" placeholder="Your name" required>
<textarea name="message" maxlength="%d" rows="3" placeholder="Your message" required></textarea>
<select name="level">%s</select><button>Lock it</button></form>
<h3>Messages so far (%d)</h3><ol>%s</ol>
<form method="post" action="/make"><button style="background:#a52">Make cards (operator)</button></form>
<pre>%s</pre></body></html>""" % (MAX_NAME, MAX_MESSAGE, opts, len(entries), rows, e(note))


def serve(port, start_id):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    state = {"note": "", "made": 0}

    def entries():
        if not os.path.exists(ENTRIES):
            return []
        with open(ENTRIES, encoding="utf-8") as f:
            return parse(f.read())

    class H(BaseHTTPRequestHandler):
        def _send(self, body, ctype="text/html; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/cards/") and self.path.endswith(".png"):
                p = os.path.join(CARDS_DIR, os.path.basename(self.path))
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        return self._send(f.read(), "image/png")
            self._send(render_page(entries(), state["note"]))

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
            if self.path == "/add":
                state["note"] = "Locked. Thank you!" if add_entry(ENTRIES, form) else "Empty message."
            elif self.path == "/make":
                todo = entries()[state["made"]:]
                if todo:
                    try:
                        state["note"] = make(todo, start_id + state["made"])
                        state["made"] += len(todo)
                    except Exception as ex:          # show it on the page, keep serving
                        state["note"] = "ERROR: %s" % ex
                else:
                    state["note"] = "No new messages."
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, *a):
            pass

    print("card maker on http://0.0.0.0:%d  (phones: http://192.168.137.1:%d)" % (port, port))
    HTTPServer(("", port), H).serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["serve", "make"])
    ap.add_argument("file", nargs="?", help="entries file for 'make'")
    ap.add_argument("--start", type=int, default=20, help="first card id for new cards")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    if a.cmd == "serve":
        serve(a.port, a.start)
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
