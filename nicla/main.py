# Murmuration - Nicla Vision node (OpenMV MicroPython).  See SPEC.md.
#
# Copy THIS FILE to the Nicla as main.py (OpenMV IDE: Tools > Save open script to OpenMV Cam).
# Optional: also copy config.json next to it; otherwise the CONFIG below is used.
# Decode mode (SPEC 3): also copy beacons.json (repo root) next to it, or the Nicla won't start.
#
# The top half (constants, merge, Node) is pure logic with no hardware in it, so it also
# imports on a laptop: dashboard/fake_telemetry.py and nicla/test_logic.py reuse it.
# The bottom half (run_nicla) is the camera / WiFi / LED part and only runs on the board.

import json
import time
import random
import hashlib

try:
    from binascii import unhexlify
except ImportError:                    # older MicroPython names
    from ubinascii import unhexlify

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
STATIC_IP = ("192.168.137.22", "255.255.255.0", "192.168.137.1", "192.168.137.1")   # must match config.json; None = DHCP
VERBOSE = True

# Same content as config.json. Used when there is no config.json on the Nicla's flash.
# "work": "decode" = crack each card's PIN (SPEC 3). A node that can't decode yet is marked
# "work": "counter" on its own line, here AND in everyone's config.json.
CONFIG = {
    "work": "decode",
    "nodes": {
        "webcam": {"ip": "192.168.137.1", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.12, "zone": "A"},
        "nicla":  {"ip": "192.168.137.22", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.05, "zone": "B"},
        "unoq":   {"ip": "192.168.137.43", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.12, "zone": None},
        "pi":     {"ip": "192.168.137.55", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.08, "zone": None},
    },
    "dashboard": {"ip": "192.168.137.18", "telemetry_port": 5006},
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
TAKEBACK_RATIO = 0.5       # decode mode: take a claimed job at ANY progress if I'm 2x faster
LIVE_START = 100           # SPEC 12: card ids from here up may be judges' live cards
GOSSIP_MAX_BYTES = 1200    # SPEC 12.4: a gossip message also stays under this many bytes of JSON
CRACK_MS = 60              # decode mode: time spent guessing PINs between two camera frames
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


# ---------------------------------------------------------------- distress beacons (decode mode)
#
# Each card's message is locked with a PIN. One guess = one slow hash:
#     key = PBKDF2-HMAC-SHA256(pin, salt, rounds, 32 bytes)      right iff sha256(key)[:8] == check
# Ported from core/beacon.py (pbkdf2_ref). No hmac module and no .copy() on the board.

def pin_str(n, digits):
    """7, 4 -> '0007'"""
    s = str(n)
    return "0" * (digits - len(s)) + s


def pbkdf2(pin_bytes, salt_bytes, rounds):
    key = pin_bytes
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    key = key + b"\x00" * (64 - len(key))
    ikey = bytes([k ^ 0x36 for k in key])
    okey = bytes([k ^ 0x5C for k in key])
    sha = hashlib.sha256
    u = sha(okey + sha(ikey + salt_bytes + b"\x00\x00\x00\x01").digest()).digest()
    # XOR all the rounds together as one big number: much faster on the board than byte by byte
    out = int.from_bytes(u, "little")
    for _ in range(rounds - 1):
        u = sha(okey + sha(ikey + u).digest()).digest()
        out ^= int.from_bytes(u, "little")
    return out.to_bytes(32, "little")


def pin_ok(b, pin, rounds):
    """True if `pin` (string) unlocks beacon b. This is ONE guess of real work."""
    key = pbkdf2(pin.encode(), b["salt"], rounds)
    return hashlib.sha256(key).digest()[:8] == b["check"]


def hash_selftest():
    """Raises if this port's hash is wrong (it would then never find a PIN, silently).
    Values from `python -m core.beacon --vectors`."""
    for pin, salt, rounds, check in (("0420", "a1b2c3d4", 1, "39d2891d0679d7d1"),
                                     ("0420", "a1b2c3d4", 2, "da4d37d56c63ce67"),
                                     ("07", "00112233", 500, "e2c242e6d2d0b4f4")):
        b = {"salt": unhexlify(salt), "check": unhexlify(check)}
        if not pin_ok(b, pin, rounds):
            raise ValueError("slow hash self-test failed (rounds=%d)" % rounds)


def load_beacons(text):
    """Text of beacons.json -> (rounds, {card id: {digits, salt, check}}).
    The message itself (ct, title) is dropped: only the dashboard needs it, and memory is small."""
    data = json.loads(text)
    out = {}
    for jid in data["beacons"]:
        b = data["beacons"][jid]
        out[str(jid)] = {"digits": int(b["digits"]), "salt": unhexlify(b["salt"]), "check": unhexlify(b["check"])}
    return int(data["rounds"]), out


def measure_rate(rounds, guesses=20):
    """Guesses per second this device manages (real guesses, wrong PINs)."""
    b = {"salt": b"\x00\x11\x22\x33", "check": b"\x00" * 8}
    start = ticks_ms()
    for n in range(guesses):
        pin_ok(b, pin_str(n, 4), rounds)
    ms = ticks_diff(ticks_ms(), start)
    return guesses * 1000.0 / max(ms, 1)


# ---------------------------------------------------------------- live locks (SPEC 12)
#
# A judge's card (id 100+, not in beacons.json) has its lock sent live, in a `lock` field:
#     {"title": "Ana", "digits": 4, "salt": "9f3a1c07", "check": "e2c242e6d2d0b4f4"}

def valid_lock(lock):
    """A lock we can actually crack: digits 1-6, hex salt, 16-hex check."""
    if not isinstance(lock, dict):
        return False
    try:
        unhexlify(lock["salt"])
        return 1 <= int(lock["digits"]) <= 6 and len(lock["check"]) == 16 and len(unhexlify(lock["check"])) == 8
    except Exception:
        return False


def lock_fields(lock):
    return {"title": str(lock.get("title", ""))[:12], "digits": int(lock["digits"]),
            "salt": lock["salt"], "check": lock["check"]}


def lock_bytes(lock):
    """The form crack() uses (like a beacons.json entry here)."""
    return {"digits": int(lock["digits"]), "salt": unhexlify(lock["salt"]), "check": unhexlify(lock["check"])}


def lock_key(lock):
    """json.dumps(lock, sort_keys=True), which MicroPython's json can't do. Merge: lower wins."""
    return "{" + ", ".join(json.dumps(k) + ": " + json.dumps(lock[k]) for k in sorted(lock)) + "}"


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
        "pin": j.get("pin") if isinstance(j.get("pin"), str) else None,   # missing = null
        "lock": lock_fields(j["lock"]) if valid_lock(j.get("lock")) else None,
    }


def blank_job(jid, zone, spotted_by):
    return {
        "id": jid, "sev": int(jid), "zone": zone, "spotted_by": spotted_by,
        "progress": 0.0, "done": False, "owner": None, "epoch": 0, "bid": None,
        "workers": [], "pin": None, "lock": None,
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
    pin = a.get("pin")                    # a found PIN is never lost; two differ -> lower string
    if pin is None or (b.get("pin") is not None and b["pin"] < pin):
        pin = b.get("pin")
    lock = a.get("lock")                  # a live lock is never lost; two differ -> lower JSON string
    if not lock or (b.get("lock") and lock_key(b["lock"]) < lock_key(lock)):
        lock = b.get("lock")
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
        "pin": pin,
        "lock": lock or None,
    }


# ---------------------------------------------------------------- node (SPEC 5, 7, 8)

class Node:
    """Pure logic: no sockets, no sleeps. Every method that needs the time takes `now`
    (a ticks_ms value from this device's own clock)."""

    def __init__(self, name, peers, speed, zone, now, spotter_only=False, log=None,
                 beacons=None, rounds=0, rate=None, counter_peers=()):
        """beacons=None -> counter mode (progress += speed * time), exactly as before.
        Decode mode: beacons = {card id: {digits, salt, check}}, rounds = hash rounds,
        rate = my guesses per second. Cards with no beacon still use counter work.
        counter_peers = peers that can't decode yet (SPEC 3): on beacon jobs only their
        spots are believed."""
        self.name = name
        self.peers = sorted(peers)        # the OTHER nodes' names
        self.speed = speed
        self.zone = zone
        self.spotter_only = spotter_only
        self.log = log                    # function(str) or None

        self.beacons = beacons            # None = counter mode
        self.rounds = rounds
        self.rate = rate
        self.counter_peers = list(counter_peers) if beacons is not None else []
        self.live_locks = {}              # SPEC 12: card id -> lock learned live (kept across reset)
        self.live_bytes = {}              # the same, in the form crack() uses
        self.trying = None                # PIN guessed last (shown on the dashboard)
        self.last_crack = now
        self.credit = 0.0                 # laptop stand-in: guesses earned but not yet made

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
            if remote["lock"] and jid not in self.live_locks:
                self.learn_lock(jid, remote["lock"])         # SPEC 12.1: learned from gossip
            if ((sender in self.counter_peers and self.beacon_for(jid) is not None)
                    or (self.is_live(jid) and not remote["lock"])):
                # it never checked a PIN (counter-marked, or a live card it has no lock for):
                # believe only that the card exists (its spot)
                remote = blank_job(jid, remote["zone"], remote["spotted_by"])
            local = self.jobs.get(jid)
            if local is None:
                self.jobs[jid] = remote
                self.last_change[jid] = now
            else:
                merged = merge_jobs(local, remote)
                if (merged["progress"] != local["progress"] or merged["owner"] != local["owner"]
                        or merged["epoch"] != local["epoch"] or merged["bid"] != local["bid"]):
                    self.last_change[jid] = now
                self.jobs[jid] = merged
            self.attach_lock(jid)

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
            self.current = None
        elif cmd == "revive":
            if self.crashed:              # no-op on a healthy node, so it is safe to repeat
                self.crashed = False
                self.current = None
                self.isolated_since = now
                self.had_peers = False
                self.members_snapshot = [
                    p for p in self.peers
                    if p in self.last_heard and ticks_diff(now, self.last_heard[p]) <= MEMBER_WINDOW_MS
                ]
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
            self._log("RESET")                       # live locks are kept (SPEC 12.2)
        elif cmd == "lock":
            # SPEC 12.2: a judge's card lock, from the card maker. Never creates a job.
            jid = str(msg.get("id"))
            if self.learn_lock(jid, msg.get("lock"), replace=True):
                self.attach_lock(jid)
        elif cmd == "spot":
            # TEST-ONLY (sim/fake_camera.py, and the card maker's judge cards on the webcam)
            if msg.get("tag") is not None and str(msg.get("tag")).isdigit():
                self.spot(int(msg.get("tag")), now)

    # ---- spotting (SPEC 7.2) - local, works with or without network

    def spot(self, tag_id, now):
        jid = str(tag_id)
        if self.blind or self.crashed or jid in self.jobs:
            return False
        self.jobs[jid] = blank_job(jid, self.zone, self.name)
        self.attach_lock(jid)
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

    # ---- which cards I can crack (beacons.json, or a judge's live lock)

    def is_live(self, jid):
        """A judge's card (SPEC 12): id 100+ and not in beacons.json."""
        return jid.isdigit() and int(jid) >= LIVE_START and not (self.beacons and jid in self.beacons)

    def beacon_for(self, jid):
        """The lock I crack this card with, or None (counter mode, or no lock known)."""
        if self.beacons is None:
            return None
        return self.beacons.get(jid) or self.live_bytes.get(jid)

    def decoding(self, job):
        return self.beacon_for(job["id"]) is not None

    def learn_lock(self, jid, lock, replace=False):
        if not self.is_live(jid) or not valid_lock(lock) or (jid in self.live_locks and not replace):
            return False
        fields = lock_fields(lock)
        if self.live_locks.get(jid) != fields:       # the card maker re-sends every 5 s: log once
            self.live_locks[jid] = fields
            self.live_bytes[jid] = lock_bytes(lock)
            self._log("lock for judge card #%s (%s)" % (jid, fields["title"]))
        return True

    def attach_lock(self, jid):
        """My copy of a live card carries its lock, so gossip spreads it (SPEC 12.1)."""
        job = self.jobs.get(jid)
        if job is not None and not job.get("lock") and jid in self.live_locks:
            job["lock"] = dict(self.live_locks[jid])

    def my_bid(self, job):
        if self.decoding(job):
            return round(1.0 / self.rate, 6)      # seconds per guess, no zone bonus
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
            self.trying = None
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
        if self.current is None:
            self.trying = None
            return
        job = self.jobs[self.current]
        if self.name not in job["workers"]:
            job["workers"] = sorted(job["workers"] + [self.name])
        self.last_change[self.current] = now
        if self.decoding(job):
            return                        # the guessing itself happens in crack()
        self.trying = None
        job["progress"] = min(1.0, job["progress"] + self.speed * dt_ms / 1000.0)
        if job["progress"] >= 1.0:
            job["done"] = True
            self._log("rescued #%s (mode=%s)" % (self.current, self.mode))

    def crack(self, now, guesses=None):
        """Decode mode work: try the next PINs of my current job, in order 0 .. 10^digits - 1.
        The Nicla calls this between camera frames with a small `guesses`. With guesses=None
        (laptop stand-in) it makes as many as `rate` allows for the time since the last call."""
        dt_ms = min(max(ticks_diff(now, self.last_crack), 0), 1000)
        self.last_crack = now
        job = self.jobs.get(self.current) if self.current is not None else None
        if (self.crashed or job is None or job["done"] or job["owner"] != self.name
                or not self.decoding(job)):
            self.credit = 0.0
            return
        if guesses is None:
            self.credit += self.rate * dt_ms / 1000.0
            guesses = int(self.credit)
            self.credit -= guesses
        b = self.beacon_for(job["id"])
        total = 10 ** b["digits"]
        n = int(round(job["progress"] * total))       # resume at the exact next guess
        while guesses > 0 and n < total:
            guess = pin_str(n, b["digits"])
            self.trying = guess
            if pin_ok(b, guess, self.rounds):
                job["pin"] = guess
                job["progress"] = 1.0
                job["done"] = True
                self.last_change[job["id"]] = now
                self._log("rescued #%s: PIN %s (mode=%s)" % (job["id"], guess, self.mode))
                return
            n += 1
            guesses -= 1
        job["progress"] = n / float(total)
        self.last_change[job["id"]] = now
        if n >= total:                    # every PIN tried, none fits: done, pin stays null
            job["done"] = True
            self._log("unsolved #%s (mode=%s)" % (job["id"], self.mode))

    def _choose(self, now):
        best = None                       # (sev, id, takeover?)
        for jid in self.jobs:
            job = self.jobs[jid]
            if job["done"]:
                continue
            if self.is_live(jid) and not self.decoding(job):
                continue                  # SPEC 12.3: a judge's card I have no lock for: leave it
            owner = job["owner"]
            takeover = False
            if owner == self.name:
                pass                      # already mine (e.g. after revive): just resume
            elif self.mode == "connected":
                if owner is None:
                    pass
                elif ticks_diff(now, self.last_change.get(jid, now)) > LEASE_MS:
                    takeover = True       # lease expired
                elif job["bid"] is None:
                    continue
                elif self.decoding(job):
                    if not self.my_bid(job) < TAKEBACK_RATIO * job["bid"]:
                        continue          # take-back: only if 2x faster, but at any progress
                elif not (self.my_bid(job) < job["bid"] and job["progress"] < OUTBID_MAX_PROGRESS):
                    continue              # outbid: same epoch, lower bid wins the merge
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
        """Full job list split into packets of at most GOSSIP_CHUNK jobs AND at most
        GOSSIP_MAX_BYTES of JSON (SPEC 12.4: locks make jobs bigger).
        Always at least one (possibly empty) message: it doubles as the heartbeat."""
        chunks = [{}]
        size = 0
        for jid in sorted(self.jobs):
            n = len(json.dumps(self.jobs[jid])) + len(jid) + 6      # job + key + separators
            if chunks[-1] and (len(chunks[-1]) >= GOSSIP_CHUNK or size + n > GOSSIP_MAX_BYTES - 80):
                chunks.append({})
                size = 0
            chunks[-1][jid] = self.jobs[jid]
            size += n
        out = []
        for chunk in chunks:
            self.seq += 1
            out.append({"type": "gossip", "v": 1, "from": self.name, "seq": self.seq, "jobs": chunk})
        return out

    def telemetry(self, now):
        self.seq += 1
        return {
            "type": "telemetry", "v": 1, "node": self.name,
            "mode": "crashed" if self.crashed else self.mode,
            "reachable": [] if self.crashed else self.reachable(now),
            "blocked": sorted(self.blocked),
            "blind": self.blind,
            "current": self.current,
            "trying": None if self.crashed else self.trying,
            "rate": self.rate,            # null in counter mode
            "jobs": self.jobs,
            "seq": self.seq,
        }


def decodes(cfg, name):
    """Does this node crack PINs? (SPEC 3: top-level "work": "decode", and not marked "counter".)"""
    return cfg.get("work") == "decode" and cfg["nodes"][name].get("work") != "counter"


def node_from_config(cfg, name, now, spotter_only=False, log=None, beacons_text=None):
    """beacons_text = the text of beacons.json; needed in decode mode (raises without it)."""
    me = cfg["nodes"][name]
    peers = [n for n in cfg["nodes"] if n != name]
    if not decodes(cfg, name):
        return Node(name, peers, me["speed"], me.get("zone"), now, spotter_only, log)
    if beacons_text is None:
        raise ValueError("decode mode needs beacons.json (or mark this node \"work\": \"counter\")")
    hash_selftest()
    rounds, beacons = load_beacons(beacons_text)
    rate = me.get("rate") or measure_rate(rounds)
    counter_peers = [p for p in peers if cfg["nodes"][p].get("work") == "counter"]
    if log:
        log("decode mode: %d beacons, %d guesses/s" % (len(beacons), int(rate)))
    return Node(name, peers, me["speed"], me.get("zone"), now, spotter_only, log,
                beacons, rounds, float(rate), counter_peers)


# ---------------------------------------------------------------- hardware (Nicla only)

def _load_config():
    try:
        with open("config.json") as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return CONFIG


def _load_beacons_text():
    try:
        with open("beacons.json") as f:
            return f.read()
    except OSError:
        return None


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

    # Decode mode without beacons.json (or with a broken hash) stops here with an error, red LED on:
    # a node that only counts would push progress past PINs nobody checked (SPEC 3).
    beacons_text = _load_beacons_text() if decodes(cfg, NODE_NAME) else None
    node = node_from_config(cfg, NODE_NAME, ticks_ms(), SPOTTER_ONLY, log, beacons_text)
    beacons_text = None                          # free the memory
    per_loop = max(1, int(node.rate * CRACK_MS / 1000)) if node.rate else 0
    now = ticks_ms()
    node.last_step = node.last_crack = node.isolated_since = now      # measuring the rate took a moment
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

        # 5 (decode mode). a few PIN guesses between camera frames
        if per_loop:
            node.crack(ticks_ms(), per_loop)

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
