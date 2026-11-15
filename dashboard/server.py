"""Murmuration dashboard server (laptop 2). Standard library only. See SPEC.md 8.2, 8.3, 9.

    python dashboard/server.py --config config.local.json      (then open http://127.0.0.1:8000)

The dashboard is NOT a node. It only:
  - listens for telemetry (UDP) and remembers the latest message from each node
  - serves index.html and GET /state
  - turns POST /scenario/<name> into control commands (each sent 3 times, 0.1 s apart)
  - keeps a rescue timeline (timeline.py) built from the telemetry, served in /state
  - in decode mode, unlocks each card's message once its PIN is found (decoded.py)
  - polls the card maker's /locks.json every 2 s for judges' live cards (SPEC §12.5)
"""
import argparse
import json
import os
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from decoded import Decoded, beacon
from timeline import Timeline

HERE = os.path.dirname(os.path.abspath(__file__))
STALE_AFTER = 2.0        # no telemetry for this long -> "NO SIGNAL"
CTL_REPEATS = 3
CTL_GAP = 0.1
DEFAULT_SPLIT = [["webcam", "unoq"], ["nicla", "pi"]]
LOCKS_EVERY = 2.0        # SPEC §12.5: poll the card maker's /locks.json this often


def default_locks_url(cfg):
    """The card maker runs on the webcam laptop, port 8080 (SPEC §12.5). None = don't poll."""
    if "locks_url" in cfg:
        return cfg["locks_url"] or None
    if cfg.get("work") != "decode" or "webcam" not in cfg["nodes"]:
        return None
    return "http://%s:8080/locks.json" % cfg["nodes"]["webcam"]["ip"]


def load_config(path):
    if not os.path.exists(path):
        path = os.path.join(HERE, "..", path)      # allow running from inside dashboard/
    with open(path) as f:
        cfg = json.load(f)
    cfg["_dir"] = os.path.dirname(os.path.abspath(path))       # beacons.json lives next to the config
    return cfg


def load_beacons(cfg):
    """-> (rounds, {card id: beacon}); (0, {}) in counter mode or if beacons.json can't be read."""
    if cfg.get("work") != "decode":
        return 0, {}
    path = os.path.join(cfg.get("_dir", os.path.join(HERE, "..")), "beacons.json")
    try:
        return beacon.load_beacons(path)
    except (OSError, ValueError, KeyError, AttributeError) as e:
        print("decode mode but can't load %s (%s): messages will not be decoded" % (path, e))
        return 0, {}


# ---------------------------------------------------------------- scenarios (SPEC 9)

def split_groups(cfg):
    names = list(cfg["nodes"])
    if all(n in names for g in DEFAULT_SPLIT for n in g):
        return DEFAULT_SPLIT
    half = len(names) // 2
    return [names[:half], names[half:]]


def build_commands(name, params, cfg):
    """Scenario name -> list of (node, control message). Raises ValueError on bad input."""
    names = list(cfg["nodes"])
    cameras = [n for n in names if cfg["nodes"][n].get("zone")]

    def node(key):
        value = params.get(key)
        if value not in names:
            raise ValueError("scenario '%s' needs %s = one of %s" % (name, key, names))
        return value

    def everyone(cmd, **extra):
        return [(n, dict(type="ctl", cmd=cmd, **extra)) for n in names]

    def one(n, cmd, **extra):
        return [(n, dict(type="ctl", cmd=cmd, **extra))]

    if name == "heal":
        return everyone("unblock_all") + everyone("unblind") + everyone("revive") + everyone("flaky", loss=0.0)
    if name == "wifi_down":
        return everyone("isolate")
    if name == "cut":
        x, y = node("x"), node("y")
        if x == y:
            raise ValueError("cut needs two different nodes")
        return one(x, "block", peers=[y]) + one(y, "block", peers=[x])
    if name == "uncut":                # put one cut link back (clicking it on the network graph)
        x, y = node("x"), node("y")
        if x == y:
            raise ValueError("uncut needs two different nodes")
        return one(x, "unblock", peers=[y]) + one(y, "unblock", peers=[x])
    if name == "split":
        left, right = split_groups(cfg)
        out = []
        for n in left:
            out += one(n, "block", peers=list(right))
        for n in right:
            out += one(n, "block", peers=list(left))
        return out
    if name == "cameras_blind":
        return [c for n in cameras for c in one(n, "blind")]
    if name == "blind":
        return one(node("x"), "blind")
    if name == "unblind":
        return one(node("x"), "unblind")
    if name == "crash":
        return one(node("x"), "crash")
    if name == "revive":
        return one(node("x"), "revive")
    if name == "flaky":
        loss = float(params.get("loss", 0.3))
        if not 0.0 <= loss <= 1.0:
            raise ValueError("loss must be between 0 and 1")
        return everyone("flaky", loss=loss)
    if name == "reset":
        return everyone("reset")
    raise ValueError("unknown scenario '%s'" % name)


def describe(name, params):
    """What a button press did, in plain English (event log + timeline markers)."""
    x, y = params.get("x"), params.get("y")
    labels = {
        "heal": "Healed everything", "wifi_down": "Took Wi-Fi down", "split": "Split the team",
        "cameras_blind": "Blinded both cameras", "reset": "Reset the round",
        "cut": "Cut the link between %s and %s" % (x, y),
        "uncut": "Restored the link between %s and %s" % (x, y),
        "blind": "Blinded %s's camera" % x, "unblind": "Restored %s's camera" % x,
        "crash": "Crashed %s" % x, "revive": "Revived %s" % x,
        "flaky": "Started dropping %d%% of messages" % round(float(params.get("loss", 0.3)) * 100),
    }
    return labels.get(name, name)


# ---------------------------------------------------------------- state

class Dashboard:
    def __init__(self, cfg, clock=time.monotonic, beacons=None):
        self.cfg = cfg
        self.clock = clock        # OUR monotonic clock (tests pass a fake one)
        self.names = list(cfg["nodes"])
        self.lock = threading.Lock()
        self.latest = {}          # node -> last telemetry message
        self.heard_at = {}        # node -> OUR monotonic time we received it
        self.finish_order = []    # job ids in the order we first saw them done
        self.score_quiet_until = 0.0
        self.heal_at = None
        self.agree_time = None
        self.events = []          # recent button presses, newest last
        self.flaky = 0.0          # loss we last asked for (nodes do not report it)
        self.round_start = self.clock()            # timeline times are seconds since this
        self.timeline = Timeline(self.names)
        self.decoded = Decoded(*(beacons or load_beacons(cfg)))
        self.ctl_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # ---- telemetry in

    def on_telemetry(self, msg):
        node = msg.get("node")
        if msg.get("type") != "telemetry" or node not in self.names or not isinstance(msg.get("jobs"), dict):
            return
        now = self.clock()
        with self.lock:
            self.latest[node] = msg
            self.heard_at[node] = now
            if now >= self.score_quiet_until:
                for jid in sorted(msg["jobs"], key=_sev_key(msg["jobs"])):
                    job = msg["jobs"][jid]
                    if isinstance(job, dict) and self._rescued(jid, job) and jid not in self.finish_order:
                        self.finish_order.append(jid)
                try:
                    self.timeline.on_telemetry(msg, now - self.round_start)
                except Exception:
                    pass           # a timeline bug must never stop telemetry (it runs the whole demo)
                try:
                    self.decoded.on_telemetry(msg, now - self.round_start)
                except Exception:
                    pass
            self._check_agreement(now)

    def _rescued(self, jid, job):
        """Done -- and for a card with a locked message, only once its PIN is found. (A node still in
        counter mode marks such a card done without a PIN; the decoders ignore that, so do we.)"""
        if not job.get("done"):
            return False
        return not self.decoded.needs_pin(jid) or isinstance(job.get("pin"), str)

    # ---- live judge cards (SPEC §12.5)

    def fetch_locks(self, url, timeout=1.5):
        """One poll of the card maker's /locks.json. Returns True if it worked."""
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            with self.lock:
                self.decoded.set_live(data)
            return True
        except Exception:          # card maker not running / network down: keep what we had
            return False

    def poll_locks(self, url):
        ok = None
        while True:
            now_ok = self.fetch_locks(url)
            if now_ok != ok:
                ok = now_ok
                print("judge cards: %s %s" % ("reading" if ok else "can't reach (will keep trying)", url),
                      flush=True)
            time.sleep(LOCKS_EVERY)

    def _check_agreement(self, now):
        """Time to agree after heal: every live column shows the same set of done jobs."""
        if self.heal_at is None or self.agree_time is not None:
            return
        live = [n for n in self.names if n in self.latest and now - self.heard_at[n] <= STALE_AFTER]
        if len(live) < 2 or any(self.heard_at[n] <= self.heal_at for n in live):
            return                 # wait until every live node has reported since the heal
        done_sets = set()
        for n in live:
            jobs = self.latest[n]["jobs"]
            done_sets.add(frozenset(j for j in jobs if jobs[j].get("done")))
        if len(done_sets) == 1:
            self.agree_time = now - self.heal_at

    def open_telemetry_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.cfg["dashboard"]["telemetry_port"]))
        return sock

    def listen(self, sock):
        while True:
            try:
                data, _addr = sock.recvfrom(65535)     # telemetry is not chunked
                msg = json.loads(data)
            except (OSError, ValueError):
                continue
            if isinstance(msg, dict):
                self.on_telemetry(msg)

    # ---- control out

    def run_scenario(self, name, params):
        commands = build_commands(name, params, self.cfg)
        now = self.clock()
        with self.lock:
            if name == "heal":
                self.heal_at = now
                self.agree_time = None
                self.flaky = 0.0
            if name == "flaky":
                self.flaky = float(params.get("loss", 0.3))
            if name == "reset":
                self.finish_order = []
                self.flaky = 0.0
                self.heal_at = None
                self.agree_time = None
                self.score_quiet_until = now + 1.5     # nodes ignore stale gossip for 1 s; so do we
                for msg in self.latest.values():
                    msg["jobs"] = {}
                    msg["current"] = None
                self.timeline.reset()
                self.decoded.reset()
                self.round_start = now
            else:
                self.timeline.mark(describe(name, params), now - self.round_start)
            self.events.append({"time": time.strftime("%H:%M:%S"), "text": describe(name, params)})
            self.events = self.events[-12:]
        threading.Thread(target=self._send, args=(commands,), daemon=True).start()
        return commands

    def _send(self, commands):
        for i in range(CTL_REPEATS):                   # UDP can drop: repeat, commands are idempotent
            for node, msg in commands:
                addr = (self.cfg["nodes"][node]["ip"], self.cfg["nodes"][node]["ctl_port"])
                try:
                    self.ctl_sock.sendto(json.dumps(msg).encode(), addr)
                except OSError:
                    pass
            if i < CTL_REPEATS - 1:
                time.sleep(CTL_GAP)

    # ---- state out

    def state(self):
        now = self.clock()
        with self.lock:
            nodes = {}
            for n in self.names:
                age = now - self.heard_at[n] if n in self.heard_at else None
                nodes[n] = {
                    "zone": self.cfg["nodes"][n].get("zone"),
                    "speed": self.cfg["nodes"][n].get("speed"),
                    "age": age,
                    "stale": age is None or age > STALE_AFTER,
                    "telemetry": self.latest.get(n),
                }
            try:
                for n in self.names:
                    if n in self.heard_at and nodes[n]["stale"]:
                        self.timeline.silent(n, max(0.0, self.heard_at[n] - self.round_start))
                timeline = self.timeline.snapshot(now - self.round_start)
            except Exception:
                timeline = None    # page shows "timeline unavailable", everything else still works
            try:
                decoded = self.decoded.snapshot()
            except Exception:
                decoded = []
            return {
                "order": self.names,
                "nodes": nodes,
                "split": split_groups(self.cfg),
                "flaky": self.flaky,
                "score": self._score(now),
                "events": list(self.events),
                "timeline": timeline,
                "work": "decode" if self.cfg.get("work") == "decode" else "counter",
                "beacons": self.decoded.titles(),      # card id -> {title, digits}
                "decoded": decoded,                    # unlocked messages, newest first
            }

    def _score(self, now):
        # Team-wide picture = every node's belief merged (done is OR, workers is union).
        done, workers, sev = set(), {}, {}
        for msg in self.latest.values():
            for jid, job in msg["jobs"].items():
                if not isinstance(job, dict):
                    continue
                sev[jid] = job.get("sev", 0)
                workers.setdefault(jid, set()).update(job.get("workers") or [])
                if self._rescued(jid, job):
                    done.add(jid)
        urgent = sorted(sev, key=lambda j: (sev[j], j))[:3]
        ranks = [self.finish_order.index(j) + 1 for j in urgent if j in self.finish_order]
        return {
            "rescued": len(done),
            "total": len(sev),
            "urgent_ids": urgent,
            "urgent_finished": len(ranks),
            "urgent_avg_order": sum(ranks) / len(ranks) if ranks else None,   # best possible = 2.0
            "duplicates": sum(1 for w in workers.values() if len(w) > 1),
            "agree_time": self.agree_time,
            "agree_waiting": (now - self.heal_at) if self.heal_at is not None and self.agree_time is None else None,
        }


def _sev_key(jobs):
    def key(jid):
        job = jobs[jid]
        return (job.get("sev", 0) if isinstance(job, dict) else 0, jid)
    return key


# ---------------------------------------------------------------- http

def make_handler(dash):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, body, ctype="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._reply(200, f.read(), "text/html; charset=utf-8")
            elif path == "/state":
                self._reply(200, dash.state())
            else:
                self._reply(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0]
            if not path.startswith("/scenario/"):
                self._reply(404, {"ok": False, "error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                params = json.loads(self.rfile.read(length)) if length else {}
                if not isinstance(params, dict):
                    raise ValueError("body must be a JSON object")
                commands = dash.run_scenario(path[len("/scenario/"):], params)
            except ValueError as e:
                self._reply(400, {"ok": False, "error": str(e)})
                return
            self._reply(200, {"ok": True, "sent": [{"node": n, "msg": m} for n, m in commands]})

        def log_message(self, fmt, *args):
            pass                                        # /state is polled 3x a second: keep quiet

    return Handler


def main():
    ap = argparse.ArgumentParser(description="Murmuration dashboard")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--host", default="127.0.0.1", help="web page address (0.0.0.0 to share on the LAN)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--locks-url", default=None,
                    help="card maker's /locks.json for judge cards (default: webcam laptop, port 8080; '' = off)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    dash = Dashboard(cfg)
    try:
        sock = dash.open_telemetry_socket()
    except OSError as e:
        raise SystemExit("Cannot listen for telemetry on UDP port %d (%s). "
                         "Is another dashboard already running? Stop it first."
                         % (cfg["dashboard"]["telemetry_port"], e))
    threading.Thread(target=dash.listen, args=(sock,), daemon=True).start()
    locks_url = default_locks_url(cfg) if args.locks_url is None else (args.locks_url or None)
    if locks_url:
        threading.Thread(target=dash.poll_locks, args=(locks_url,), daemon=True).start()
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(dash))
    print("telemetry: UDP %d   nodes: %s" % (cfg["dashboard"]["telemetry_port"], ", ".join(dash.names)))
    print("dashboard: http://%s:%d" % (args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
