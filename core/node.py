"""Murmuration node: PURE LOGIC. No sockets, no sleeps, no clock reads.

Every method that needs the time takes `now` (this device's own monotonic seconds).
net.py feeds it real time; tests feed it fake time. See SPEC.md §5-§8.

Plain dicts on purpose: the Nicla port (MicroPython) can copy this almost line by line.
Python 3.7 compatible (Pi Zero W).
"""

# ---- constants (SPEC §4) -------------------------------------------------
TICK = 0.1
GOSSIP_INTERVAL = 0.3
TELEMETRY_INTERVAL = 0.5
PEER_TIMEOUT = 2.0
LEASE = 4.0
ISOLATION_GRACE = 15.0
MEMBER_WINDOW = 10.0
OUTBID_MAX_PROGRESS = 0.5
DETECT_STABLE_FRAMES = 3
RESET_QUIET = 1.0          # SPEC §8.3 reset: ignore gossip this long after a reset
CHUNK_JOBS = 6             # SPEC §8.1: max jobs per gossip message
WORK_FRACTION = 0.6        # decode mode: spend ~60 % of a tick hashing, rest for gossip
TAKEBACK_RATIO = 0.5       # decode mode: free node takes a job at ANY progress if 2x faster


# ---- merge rule (SPEC §6) --------------------------------------------------

def claim_key(job):
    """Sort key for a claim. The SMALLER key wins.

    higher epoch wins -> tie: a real owner beats null -> tie: lower bid -> tie: lower owner name.
    """
    owner = job.get("owner")
    bid = job.get("bid")
    return (
        -job.get("epoch", 0),
        owner is None,
        bid if bid is not None else float("inf"),
        owner or "",
    )


def origin_key(job):
    """zone/spotted_by come from the side with the lower spotted_by name (SPEC §6)."""
    return (job.get("spotted_by") or "", job.get("zone") or "")


def merge_job(a, b):
    """Merge two copies of the same job. Returns a NEW dict; never mutates a or b.

    Commutative, associative, idempotent (tests/test_merge.py checks this).
    """
    out = dict(a)
    out["done"] = bool(a.get("done")) or bool(b.get("done"))
    out["progress"] = max(a.get("progress", 0.0), b.get("progress", 0.0))

    win = a if claim_key(a) <= claim_key(b) else b
    out["owner"] = win.get("owner")
    out["epoch"] = win.get("epoch", 0)
    out["bid"] = win.get("bid")

    out["workers"] = sorted(set(a.get("workers", [])) | set(b.get("workers", [])))

    org = a if origin_key(a) <= origin_key(b) else b
    out["zone"] = org.get("zone")
    out["spotted_by"] = org.get("spotted_by")

    # decode mode: a found PIN is never lost (if two differ, the lower string, so all agree)
    pins = [p for p in (a.get("pin"), b.get("pin")) if p is not None]
    out["pin"] = min(pins) if pins else None
    # id, sev: keep local (identical anyway)
    return out


def claim_of(job):
    return (job.get("owner"), job.get("epoch", 0), job.get("bid"))


def new_job(tag_id, zone, spotted_by):
    tid = str(tag_id)
    return {
        "id": tid,
        "sev": int(tid),
        "zone": zone,
        "spotted_by": spotted_by,
        "progress": 0.0,
        "done": False,
        "owner": None,
        "epoch": 0,
        "bid": None,
        "workers": [],
        "pin": None,
    }


# ---- the node ---------------------------------------------------------------

class Node:
    def __init__(self, name, peers, speed, zone=None, now=0.0,
                 beacons=None, rate=None, check=None):
        """beacons=None -> counter mode (progress += speed*TICK).

        Decode mode: beacons = {card_id: beacon}, rate = my guesses/s (measured by net.py),
        check(beacon, pin_str) -> bool does ONE real slow-hash guess (core/beacon.py).
        Cards with no beacon still use counter work.
        """
        self.name = name
        self.peers = sorted(p for p in peers if p != name)
        self.speed = float(speed)
        self.zone = zone                # camera zone ("A"/"B") or None

        # shared state
        self.jobs = {}                  # id -> job dict (SPEC §5.1)

        # local-only bookkeeping (SPEC §5.2)
        self.last_change = {}           # id -> my time
        self.last_heard = {}            # peer -> my time
        self.members_snapshot = []
        self.isolated_since = now       # at boot we've heard nobody
        self.was_connected = False
        self.blocked = set()
        self.current = None

        # decode mode (None = counter mode)
        self.beacons = beacons
        self.rate = rate
        self.check = check
        self.trying = None              # last PIN guessed (for the dashboard)

        # fault injection flags
        self.blind = False
        self.crashed = False
        self.loss = 0.0                 # 'flaky': net.py drops this fraction of gossip
        self.quiet_until = -1.0         # after 'reset', ignore gossip until this time

        self.mode = "isolated"
        self.gossip_seq = 0
        self.telemetry_seq = 0
        self.events = []                # (kind, job_id, mode) for logs; caller pops them

    # -- helpers ------------------------------------------------------------
    def _decoding(self, job):
        return bool(self.beacons) and job["id"] in self.beacons

    def my_bid(self, job):
        if self._decoding(job):
            return round(1.0 / self.rate, 6)       # seconds per guess; no zone bonus
        bid = 1.0 / self.speed
        if self.zone is not None and job.get("zone") == self.zone:
            bid -= 0.5
        return round(bid, 3)

    def reachable(self, now):
        return [p for p in self.peers
                if p not in self.blocked
                and p in self.last_heard
                and now - self.last_heard[p] <= PEER_TIMEOUT]

    def _event(self, kind, job_id):
        self.events.append((kind, job_id, self.mode))

    def pop_events(self):
        ev = self.events
        self.events = []
        return ev

    # -- inputs ---------------------------------------------------------------
    def on_gossip(self, msg, now):
        """Merge a gossip message (SPEC §8.1). Returns True if it was accepted."""
        if self.crashed or now < self.quiet_until:
            return False
        if msg.get("type") != "gossip" or msg.get("v") != 1:
            return False
        sender = msg.get("from")
        if sender not in self.peers or sender in self.blocked:
            return False
        self.last_heard[sender] = now
        for jid, remote in (msg.get("jobs") or {}).items():
            self._merge_in(str(jid), remote, now)
        return True

    def _merge_in(self, jid, remote, now):
        local = self.jobs.get(jid)
        if local is None:
            self.jobs[jid] = merge_job(remote, remote)   # normalised copy
            self.last_change[jid] = now
            return
        merged = merge_job(local, remote)
        if (merged["progress"] != local["progress"]
                or claim_of(merged) != claim_of(local)):
            self.last_change[jid] = now
        self.jobs[jid] = merged

    def spot(self, tag_id, now):
        """Camera saw a (stable) tag. SPEC §7.2. Returns True if a new job was created."""
        if self.blind or self.crashed:
            return False
        jid = str(tag_id)
        if jid in self.jobs:
            return False
        self.jobs[jid] = new_job(jid, self.zone, self.name)
        self.last_change[jid] = now
        self._event("spotted", jid)
        return True

    def on_control(self, msg, now):
        """Control command from the dashboard (SPEC §8.3). Never blocked."""
        if msg.get("type") != "ctl":
            return
        cmd = msg.get("cmd")
        peers = [p for p in (msg.get("peers") or []) if p in self.peers]
        if cmd == "block":
            self.blocked.update(peers)
        elif cmd == "unblock":
            self.blocked.difference_update(peers)
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
                self.crashed = True
                self.current = None
                self._event("crashed", None)
        elif cmd == "revive":
            if self.crashed:
                self.crashed = False
                # SPEC §8.3: restart local clocks so the frozen view can't steal live jobs
                for jid in self.jobs:
                    self.last_change[jid] = now
                self.isolated_since = now
                self.was_connected = False
                self.members_snapshot = self._recent_members(now)
                self.current = None
                self._event("revived", None)
        elif cmd == "flaky":
            try:
                self.loss = min(1.0, max(0.0, float(msg.get("loss", 0.0))))
            except (TypeError, ValueError):
                pass
        elif cmd == "reset":
            self.jobs = {}
            self.last_change = {}
            self.current = None
            self.blocked = set()
            self.blind = False
            self.crashed = False
            self.loss = 0.0
            self.quiet_until = now + RESET_QUIET
            self._event("reset", None)
        elif cmd == "spot":
            # TEST-ONLY extension used by sim/fake_camera.py (not sent by the dashboard)
            if msg.get("tag") is not None:
                self.spot(msg.get("tag"), now)

    # -- the decision loop (SPEC §7), call every TICK --------------------------
    def _recent_members(self, now):
        return [p for p in self.peers
                if p in self.last_heard and now - self.last_heard[p] <= MEMBER_WINDOW]

    def _update_mode(self, now):
        reach = self.reachable(now)
        if reach:
            self.was_connected = True
            self.mode = "connected"
        else:
            if self.was_connected:
                # just went from "some peers" to "no peers" (SPEC §5.2)
                self.was_connected = False
                self.isolated_since = now
                self.members_snapshot = self._recent_members(now)
            alone = now - self.isolated_since
            self.mode = "isolated" if alone < ISOLATION_GRACE else "alone_long"
        return reach

    def _in_my_share(self, job):
        members = sorted(set(self.members_snapshot) | {self.name})
        return int(job["id"]) % len(members) == members.index(self.name)

    def _candidate(self, job, now):
        """Return how I'd claim this job: 'claim' | 'takeover' | 'resume' | None."""
        if job["done"]:
            return None
        owner = job["owner"]
        if owner == self.name:
            return "resume"
        if self.mode == "connected":
            if owner is None:
                return "claim"
            if now - self.last_change.get(job["id"], now) > LEASE:
                return "takeover"
            if job["bid"] is not None:
                if self._decoding(job):
                    if self.my_bid(job) < TAKEBACK_RATIO * job["bid"]:
                        return "claim"  # take-back: much faster, any progress, same epoch
                elif self.my_bid(job) < job["bid"] and job["progress"] < OUTBID_MAX_PROGRESS:
                    return "claim"      # outbid: same epoch, my bid
            return None
        if self.mode == "isolated":
            if owner is None and self._in_my_share(job):
                return "claim"
            return None
        # alone_long: anything not done
        return "claim" if owner is None else "takeover"

    def _choose(self, now):
        best = None
        best_how = None
        for job in self.jobs.values():
            how = self._candidate(job, now)
            if how is None:
                continue
            key = (job["sev"], int(job["id"]))
            if best is None or key < (best["sev"], int(best["id"])):
                best, best_how = job, how
        if best is None:
            return
        jid = best["id"]
        if best_how == "takeover":
            best["epoch"] = best.get("epoch", 0) + 1
            self._event("took_over", jid)
        elif best_how == "claim":
            self._event("claimed", jid)
        best["owner"] = self.name
        best["bid"] = self.my_bid(best)
        self.last_change[jid] = now
        self.current = jid

    def _work_decode(self, job):
        """Try the next batch of PINs in order 0..10^digits-1. Returns 'found'|'exhausted'|None."""
        b = self.beacons[job["id"]]
        digits = int(b["digits"])
        total = 10 ** digits
        n = int(round(job["progress"] * total))       # resume at the exact next guess
        budget = max(1, int(self.rate * TICK * WORK_FRACTION))
        while budget > 0 and n < total:
            guess = "0" * (digits - len(str(n))) + str(n)
            self.trying = guess
            if self.check(b, guess):
                job["pin"] = guess
                job["progress"] = 1.0
                return "found"
            n += 1
            budget -= 1
        job["progress"] = n / float(total)
        return "exhausted" if n >= total else None

    def tick(self, now):
        if self.crashed:
            self.trying = None
            return
        self._update_mode(now)

        job = self.jobs.get(self.current) if self.current else None
        if job is None or job["done"] or job["owner"] != self.name:
            self.current = None

        if self.current is None:
            self._choose(now)

        if not self.current:
            self.trying = None
            return
        job = self.jobs[self.current]
        if self.name not in job["workers"]:
            job["workers"] = sorted(job["workers"] + [self.name])
        self.last_change[self.current] = now
        if self._decoding(job):
            result = self._work_decode(job)
            if result:
                job["done"] = True
                self._event("rescued" if result == "found" else "unsolved", self.current)
                self.current = None
            return
        self.trying = None
        job["progress"] = round(min(1.0, job["progress"] + self.speed * TICK), 4)
        if job["progress"] >= 1.0:
            job["done"] = True
            self._event("rescued", self.current)
            self.current = None

    # -- outputs ----------------------------------------------------------------
    def gossip_targets(self):
        """Every non-blocked peer, heard from or not (that's how we rediscover them)."""
        if self.crashed:
            return []
        return [p for p in self.peers if p not in self.blocked]

    def gossip_messages(self):
        """Full job list, chunked into messages of <= CHUNK_JOBS jobs (SPEC §8.1).

        Always at least ONE message, even with zero jobs: gossip is also the heartbeat
        that tells peers we're alive (otherwise everyone looks isolated until a card appears).
        """
        if self.crashed:
            return []
        ids = sorted(self.jobs, key=lambda j: int(j))
        msgs = []
        for i in range(0, max(len(ids), 1), CHUNK_JOBS):
            self.gossip_seq += 1
            msgs.append({
                "type": "gossip", "v": 1, "from": self.name, "seq": self.gossip_seq,
                "jobs": {j: self.jobs[j] for j in ids[i:i + CHUNK_JOBS]},
            })
        return msgs

    def telemetry(self, now):
        self.telemetry_seq += 1
        return {
            "type": "telemetry", "v": 1, "node": self.name,
            "mode": "crashed" if self.crashed else self.mode,
            "reachable": [] if self.crashed else sorted(self.reachable(now)),
            "blocked": sorted(self.blocked),
            "blind": self.blind,
            "current": self.current,
            "trying": None if self.crashed else self.trying,
            "rate": self.rate,
            "jobs": self.jobs,
            "seq": self.telemetry_seq,
        }


class TagStabilizer:
    """A tag becomes 'stable' after DETECT_STABLE_FRAMES consecutive frames (SPEC §7.2).

    feed(ids_seen_this_frame) -> list of ids that just became stable.
    """

    def __init__(self, frames=DETECT_STABLE_FRAMES):
        self.frames = frames
        self.streak = {}

    def feed(self, ids):
        seen = set(str(i) for i in ids)
        newly = []
        for tid in seen:
            n = self.streak.get(tid, 0) + 1
            self.streak[tid] = n
            if n == self.frames:
                newly.append(tid)
        for tid in list(self.streak):
            if tid not in seen:
                del self.streak[tid]
        return newly
