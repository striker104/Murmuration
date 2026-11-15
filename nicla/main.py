# Murmuration - Nicla Vision node (OpenMV MicroPython).  See SPEC.md.
#
# Copy THIS FILE to the Nicla as main.py (OpenMV IDE: Tools > Save open script to OpenMV Cam).
# Optional: also copy config.json next to it; otherwise the CONFIG below is used.
#
# The top half (constants, merge, Node) is pure logic with no hardware in it, so it also
# imports on a laptop: dashboard/fake_telemetry.py and nicla/test_logic.py reuse it.
# The bottom half (run_nicla) is the camera / WiFi / LED part and only runs on the board.

import json
import time
import random

try:
    import sensor  # only exists on the OpenMV board
    ON_NICLA = True
except ImportError:
    ON_NICLA = False

# ---------------------------------------------------------------- settings (edit these)

WIFI_SSID = "CHANGE_ME"
WIFI_KEY = "CHANGE_ME"
NODE_NAME = "nicla"
SPOTTER_ONLY = False   # SPEC 10 fallback: spot cards + gossip, but never claim or work
STATIC_IP = None       # e.g. ("192.168.1.12", "255.255.255.0", "192.168.1.1", "8.8.8.8")
VERBOSE = True

# Same content as config.json. Used when there is no config.json on the Nicla's flash.
CONFIG = {
    "nodes": {
        "webcam": {"ip": "192.168.1.11", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.12, "zone": "A"},
        "nicla":  {"ip": "192.168.1.12", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.05, "zone": "B"},
        "unoq":   {"ip": "192.168.1.13", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.12, "zone": None},
        "pi":     {"ip": "192.168.1.14", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.08, "zone": None},
    },
    "dashboard": {"ip": "192.168.1.20", "telemetry_port": 5006},
}

# ---------------------------------------------------------------- constants (SPEC 4), in ms

TICK_MS = 100
GOSSIP_INTERVAL_MS = 300
TELEMETRY_INTERVAL_MS = 500
PEER_TIMEOUT_MS = 2000
LEASE_MS = 4000
ISOLATION_GRACE_MS = 15000
MEMBER_WINDOW_MS = 10000
OUTBID_MAX_PROGRESS = 0.5
DETECT_STABLE_FRAMES = 3
GOSSIP_CHUNK = 6           # max jobs per gossip packet (SPEC 8.1)
RESET_QUIET_MS = 1000      # ignore gossip this long after a reset (SPEC 8.3)
RECV_BUF = 2048

# ---------------------------------------------------------------- clock

# Golden rule: only ever use OUR OWN monotonic clock, and only through ticks_diff().
if hasattr(time, "ticks_ms"):
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
else:  # laptop (CPython) stand-in
    def ticks_ms():
        return int(time.monotonic() * 1000)

    def ticks_diff(a, b):
        return a - b


def _rand():
    return random.getrandbits(10) / 1024.0


# ---------------------------------------------------------------- merge (SPEC 6)

def clean_job(jid, j):
    """Copy a job that came off the network into our own dict. Raises if it is malformed."""
    if str(j["id"]) != jid:
        raise ValueError("job id mismatch")
    return {
        "id": jid,
        "sev": int(j["sev"]),
        "zone": j.get("zone"),
        "spotted_by": j.get("spotted_by"),
        "progress": float(j["progress"]),
        "done": bool(j["done"]),
        "owner": j.get("owner"),
        "epoch": int(j["epoch"]),
        "bid": j.get("bid"),
        "workers": sorted(j.get("workers") or []),
    }


def claim_beats(a, b):
    """True if job a's claim (owner, epoch, bid) strictly beats job b's."""
    if a["epoch"] != b["epoch"]:
        return a["epoch"] > b["epoch"]
    ao = a["owner"]
    bo = b["owner"]
    if ao is None or bo is None:
        return ao is not None and bo is None
    abid = a["bid"] if a["bid"] is not None else 1e9
    bbid = b["bid"] if b["bid"] is not None else 1e9
    if abid != bbid:
        return abid < bbid
    return ao < bo


def merge_jobs(a, b):
    """Merge two copies of the same job. Commutative, associative, idempotent."""
    spot = a if (str(a["spotted_by"]), str(a["zone"])) <= (str(b["spotted_by"]), str(b["zone"])) else b
    claim = b if claim_beats(b, a) else a
    workers = list(a["workers"])
    for w in b["workers"]:
        if w not in workers:
            workers.append(w)
    return {
        "id": a["id"],
        "sev": a["sev"],
        "zone": spot["zone"],
        "spotted_by": spot["spotted_by"],
        "progress": max(a["progress"], b["progress"]),
        "done": a["done"] or b["done"],
        "owner": claim["owner"],
        "epoch": claim["epoch"],
        "bid": claim["bid"],
        "workers": sorted(workers),
    }


# ---------------------------------------------------------------- node (SPEC 5, 7, 8)

class Node:
    """Pure logic: no sockets, no sleeps. Every method that needs the time takes `now`
    (a ticks_ms value from this device's own clock)."""

    def __init__(self, name, peers, speed, zone, now, spotter_only=False, log=None):
        self.name = name
        self.peers = sorted(peers)        # the OTHER nodes' names
        self.speed = speed
        self.zone = zone
        self.spotter_only = spotter_only
        self.log = log                    # function(str) or None

        self.jobs = {}                    # shared state, sent in gossip
        self.last_change = {}             # job id -> my time its progress/claim last changed
        self.last_heard = {}              # peer -> my time I last got gossip from it
        self.members_snapshot = []
        self.isolated_since = now
        self.had_peers = False
        self.blocked = set()
        self.current = None

        self.blind = False
        self.crashed = False
        self.loss = 0.0
        self.quiet_until = None           # after reset: ignore gossip until this time
        self.mode = "isolated"
        self.seq = 0
        self.last_step = now

    def _log(self, text):
        if self.log:
            self.log(text)

    # ---- receiving

    def on_gossip(self, msg, now):
        if self.crashed:
            return                        # crashed: read and throw away
        sender = msg.get("from")
        if sender not in self.peers or sender in self.blocked:
            return
        if self.loss > 0 and _rand() < self.loss:
            return                        # flaky: dropped on receive
        self.last_heard[sender] = now
        if self.quiet_until is not None:
            if ticks_diff(now, self.quiet_until) < 0:
                return                    # just reset: don't let stale packets bring jobs back
            self.quiet_until = None
        jobs = msg.get("jobs") or {}
        for jid in jobs:
            try:
                remote = clean_job(jid, jobs[jid])
            except (KeyError, ValueError, TypeError):
                continue
            local = self.jobs.get(jid)
            if local is None:
                self.jobs[jid] = remote
                self.last_change[jid] = now
                continue
            merged = merge_jobs(local, remote)
            if (merged["progress"] != local["progress"] or merged["owner"] != local["owner"]
                    or merged["epoch"] != local["epoch"] or merged["bid"] != local["bid"]):
                self.last_change[jid] = now
            self.jobs[jid] = merged

    def on_control(self, msg, now):
        cmd = msg.get("cmd")
        if cmd == "block":
            for p in msg.get("peers") or []:
                if p in self.peers:
                    self.blocked.add(p)
        elif cmd == "unblock":
            for p in msg.get("peers") or []:
                self.blocked.discard(p)
        elif cmd == "unblock_all":
            self.blocked = set()
        elif cmd == "isolate":
            self.blocked = set(self.peers)
        elif cmd == "blind":
            self.blind = True
        elif cmd == "unblind":
            self.blind = False
        elif cmd == "crash":
            if not self.crashed:
                self._log("CRASH")
            self.crashed = True
        elif cmd == "revive":
            if self.crashed:              # no-op on a healthy node, so it is safe to repeat
                self.crashed = False
                self.current = None
                self.isolated_since = now
                self.had_peers = False
                for jid in self.jobs:
                    self.last_change[jid] = now
                self._log("REVIVE")
        elif cmd == "flaky":
            try:
                self.loss = min(1.0, max(0.0, float(msg.get("loss", 0))))
            except (ValueError, TypeError):
                pass
        elif cmd == "reset":
            self.jobs = {}
            self.last_change = {}
            self.current = None
            self.blocked = set()
            self.blind = False
            self.crashed = False
            self.loss = 0.0
            self.members_snapshot = []
            self.isolated_since = now
            self.had_peers = False
            self.quiet_until = now + RESET_QUIET_MS
            self._log("RESET")

    # ---- spotting (SPEC 7.2) - local, works with or without network

    def spot(self, tag_id, now):
        jid = str(tag_id)
        if self.blind or self.crashed or jid in self.jobs:
            return False
        self.jobs[jid] = {
            "id": jid, "sev": int(tag_id), "zone": self.zone, "spotted_by": self.name,
            "progress": 0.0, "done": False, "owner": None, "epoch": 0, "bid": None,
            "workers": [],
        }
        self.last_change[jid] = now
        self._log("spotted #%s" % jid)
        return True

    # ---- decision loop (SPEC 7), call every TICK

    def reachable(self, now):
        out = []
        for p in self.peers:
            if p in self.blocked or p not in self.last_heard:
                continue
            if ticks_diff(now, self.last_heard[p]) <= PEER_TIMEOUT_MS:
                out.append(p)
        return out

    def my_bid(self, job):
        bid = 1.0 / self.speed
        if self.zone is not None and job["zone"] == self.zone:
            bid -= 0.5
        return bid

    def in_my_share(self, job):
        members = sorted(self.members_snapshot + [self.name])
        return job["sev"] % len(members) == members.index(self.name)

    def step(self, now):
        # Real elapsed time, not a fixed TICK: the camera makes our loop uneven, and
        # `speed` is defined as progress per second.
        dt_ms = ticks_diff(now, self.last_step)
        self.last_step = now
        dt_ms = min(max(dt_ms, 0), 1000)

        # 2. who can I reach, what mode am I in
        reach = self.reachable(now)
        if self.had_peers and not reach:
            self.isolated_since = now
            self.members_snapshot = [
                p for p in self.peers
                if p in self.last_heard and ticks_diff(now, self.last_heard[p]) <= MEMBER_WINDOW_MS
            ]
        self.had_peers = len(reach) > 0
        if self.crashed:
            self.mode = "crashed"
            return
        if reach:
            mode = "connected"
        elif ticks_diff(now, self.isolated_since) < ISOLATION_GRACE_MS:
            mode = "isolated"
        else:
            mode = "alone_long"
        if mode != self.mode:
            self._log("mode %s -> %s" % (self.mode, mode))
        self.mode = mode

        # 3. drop the current job if it is finished or someone else won it
        if self.current is not None:
            job = self.jobs.get(self.current)
            if job is None or job["done"] or job["owner"] != self.name:
                self.current = None

        if self.spotter_only:
            return

        # 4. choose + claim
        if self.current is None:
            self._choose(now)

        # 5. work
        if self.current is not None:
            job = self.jobs[self.current]
            job["progress"] = min(1.0, job["progress"] + self.speed * dt_ms / 1000.0)
            if self.name not in job["workers"]:
                job["workers"] = sorted(job["workers"] + [self.name])
            self.last_change[self.current] = now
            if job["progress"] >= 1.0:
                job["done"] = True
                self._log("rescued #%s (mode=%s)" % (self.current, self.mode))

    def _choose(self, now):
        best = None                       # (sev, id, takeover?)
        for jid in self.jobs:
            job = self.jobs[jid]
            if job["done"]:
                continue
            owner = job["owner"]
            takeover = False
            if owner == self.name:
                pass                      # already mine (e.g. after revive): just resume
            elif self.mode == "connected":
                if owner is None:
                    pass
                elif ticks_diff(now, self.last_change.get(jid, now)) > LEASE_MS:
                    takeover = True       # lease expired
                elif (job["bid"] is not None and self.my_bid(job) < job["bid"]
                      and job["progress"] < OUTBID_MAX_PROGRESS):
                    pass                  # outbid: same epoch, lower bid wins the merge
                else:
                    continue
            elif self.mode == "isolated":
                if owner is not None or not self.in_my_share(job):
                    continue
            else:                         # alone_long: anything not done
                takeover = owner is not None
            if best is None or (job["sev"], jid) < (best[0], best[1]):
                best = (job["sev"], jid, takeover)
        if best is None:
            return
        job = self.jobs[best[1]]
        if job["owner"] != self.name:
            if best[2]:
                job["epoch"] += 1
            job["owner"] = self.name
            job["bid"] = self.my_bid(job)
            self._log("claim #%s (epoch %d, from %d%%)" % (best[1], job["epoch"], int(job["progress"] * 100)))
        self.current = best[1]
        self.last_change[best[1]] = now

    # ---- sending

    def gossip_targets(self):
        if self.crashed:
            return []
        return [p for p in self.peers if p not in self.blocked]

    def drop_outgoing(self):
        """Flaky link: True if this outgoing gossip packet should be thrown away."""
        return self.loss > 0 and _rand() < self.loss

    def gossip_messages(self):
        """Full job list split into packets of at most GOSSIP_CHUNK jobs.
        Always at least one (possibly empty) message: it doubles as the heartbeat."""
        ids = sorted(self.jobs)
        out = []
        i = 0
        while True:
            chunk = {}
            for jid in ids[i:i + GOSSIP_CHUNK]:
                chunk[jid] = self.jobs[jid]
            self.seq += 1
            out.append({"type": "gossip", "v": 1, "from": self.name, "seq": self.seq, "jobs": chunk})
            i += GOSSIP_CHUNK
            if i >= len(ids):
                return out

    def telemetry(self, now):
        self.seq += 1
        return {
            "type": "telemetry", "v": 1, "node": self.name,
            "mode": "crashed" if self.crashed else self.mode,
            "reachable": self.reachable(now),
            "blocked": sorted(self.blocked),
            "blind": self.blind,
            "current": self.current,
            "jobs": self.jobs,
            "seq": self.seq,
        }


def node_from_config(cfg, name, now, spotter_only=False, log=None):
    me = cfg["nodes"][name]
    peers = [n for n in cfg["nodes"] if n != name]
    return Node(name, peers, me["speed"], me.get("zone"), now, spotter_only, log)


# ---------------------------------------------------------------- hardware (Nicla only)

def _load_config():
    try:
        with open("config.json") as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return CONFIG


def _make_leds():
    try:
        import pyb
        return (pyb.LED(1), pyb.LED(2), pyb.LED(3))          # red, green, blue
    except (ImportError, ValueError):
        from machine import LED
        return (LED("LED_RED"), LED("LED_GREEN"), LED("LED_BLUE"))


def _tag_id(tag):
    tid = tag.id
    return tid() if callable(tid) else tid      # method on old firmware, attribute on new


def _drain(sock, limit):
    """Read up to `limit` waiting UDP packets (non-blocking). Returns a list of dicts."""
    out = []
    for _ in range(limit):
        try:
            data, _addr = sock.recvfrom(RECV_BUF)
        except OSError:
            break                                # nothing waiting (or WiFi is down)
        try:
            msg = json.loads(data)
        except ValueError:
            continue
        if isinstance(msg, dict):
            out.append(msg)
    return out


def run_nicla():
    import network
    import socket

    def log(text):
        if VERBOSE:
            print("[%s] %s" % (NODE_NAME, text))

    cfg = _load_config()
    me = cfg["nodes"][NODE_NAME]
    leds = _make_leds()
    led_state = [None]

    def set_led(colour):                         # "red" / "green" / "blue" / None
        if colour == led_state[0]:
            return
        led_state[0] = colour
        for led, name in zip(leds, ("red", "green", "blue")):
            if name == colour:
                led.on()
            else:
                led.off()

    set_led("red")

    # --- camera
    sensor.reset()
    sensor.set_pixformat(sensor.GRAYSCALE)
    sensor.set_framesize(sensor.QQVGA)           # 160x120: AprilTags need a small frame
    sensor.skip_frames(time=2000)
    sensor.set_auto_gain(False)
    sensor.set_auto_whitebal(False)

    # --- WiFi (keep going without it: spotting and rescuing are local)
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if STATIC_IP:
        wlan.ifconfig(STATIC_IP)
    wlan.connect(WIFI_SSID, WIFI_KEY)
    start = ticks_ms()
    while not wlan.isconnected() and ticks_diff(ticks_ms(), start) < 15000:
        time.sleep_ms(200)
    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        log("WiFi up, my IP is %s" % ip)
        if ip != me["ip"]:
            log("WARNING: config says I am %s - peers send there! Fix config.json or STATIC_IP" % me["ip"])
    else:
        log("no WiFi yet - starting isolated, will keep retrying")

    gsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    gsock.bind(("0.0.0.0", me["gossip_port"]))
    gsock.setblocking(False)
    csock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    csock.bind(("0.0.0.0", me["ctl_port"]))
    csock.setblocking(False)

    peer_addr = {}
    for p in cfg["nodes"]:
        if p != NODE_NAME:
            peer_addr[p] = (cfg["nodes"][p]["ip"], cfg["nodes"][p]["gossip_port"])
    dash_addr = (cfg["dashboard"]["ip"], cfg["dashboard"]["telemetry_port"])

    def send(obj, addr):
        try:
            gsock.sendto(json.dumps(obj), addr)
        except OSError:
            pass                                 # WiFi down / packet too big: carry on

    now = ticks_ms()
    node = node_from_config(cfg, NODE_NAME, now, SPOTTER_ONLY, log)
    streak = {}                                  # tag id -> consecutive frames seen
    next_tick = now
    next_gossip = now
    next_tele = now
    next_wifi_check = now + 10000

    while True:
        # camera: a tag must be seen DETECT_STABLE_FRAMES frames in a row to become a job
        img = sensor.snapshot()
        seen = []
        for tag in img.find_apriltags():         # default family is TAG36H11
            seen.append(_tag_id(tag))
        now = ticks_ms()
        for tid in seen:
            n = streak.get(tid, 0) + 1
            streak[tid] = min(n, DETECT_STABLE_FRAMES)
            if n >= DETECT_STABLE_FRAMES:
                node.spot(tid, now)              # no-op if blind or already known
        for tid in list(streak):
            if tid not in seen:
                del streak[tid]

        # 1. network in. Control is never blocked and still works while crashed.
        for msg in _drain(csock, 10):
            if msg.get("type") == "ctl":
                node.on_control(msg, now)
        for msg in _drain(gsock, 30):
            if msg.get("type") == "gossip":
                node.on_gossip(msg, now)

        # 2-5. decide + work
        if ticks_diff(now, next_tick) >= 0:
            next_tick = now + TICK_MS
            node.step(now)
            if node.mode != "connected":
                set_led(None if node.crashed else "red")
            elif node.current is not None:
                set_led("green")
            else:
                set_led("blue")

        # 6. gossip to every non-blocked peer, silent or not (that's how we find them again)
        if ticks_diff(now, next_gossip) >= 0:
            next_gossip = now + GOSSIP_INTERVAL_MS
            targets = node.gossip_targets()
            if targets:
                for msg in node.gossip_messages():
                    for p in targets:
                        if not node.drop_outgoing():
                            send(msg, peer_addr[p])

        # 7. telemetry (also while crashed)
        if ticks_diff(now, next_tele) >= 0:
            next_tele = now + TELEMETRY_INTERVAL_MS
            send(node.telemetry(now), dash_addr)

        # WiFi dropped for real? ask it to rejoin (connect() returns straight away)
        if ticks_diff(now, next_wifi_check) >= 0:
            next_wifi_check = now + 10000
            try:
                if not wlan.isconnected():
                    wlan.connect(WIFI_SSID, WIFI_KEY)
            except OSError:
                pass


if ON_NICLA:
    run_nicla()
