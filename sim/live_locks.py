"""Live judge cards (SPEC §12): a message typed on the card maker page gets the next free card
id (100-199), a random secret PIN, and its lock is sent to every node's control port.

State (ids handed out + full beacons incl. the scrambled message) lives in sim/live.json so a
restart of the card maker doesn't reuse card numbers. Standard library only.
"""
import json
import os
import random
import socket
import time

from core.beacon import lock

LEVELS = {"easy": (3, 400, 999), "medium": (4, 4000, 9999), "hard": (5, 30000, 65000)}


class LiveLocks:
    def __init__(self, path, rounds, first=100, last=199, taken=(), answers=None):
        self.path = path
        self.rounds = rounds
        self.first, self.last = first, last
        self.taken = set(str(t) for t in taken)      # ids already used by beacons.json
        self.answers = answers                       # answer_key.txt path (or None)
        self.state = {"next": first, "beacons": {}}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.state = json.load(f)

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.path)

    def add(self, name, message, level, rng=None):
        """-> (card id, beacon, pin). Raises ValueError when 100-199 are used up."""
        rng = rng or random.SystemRandom()
        cid = self.state["next"]
        while str(cid) in self.taken or str(cid) in self.state["beacons"]:
            cid += 1
        if cid > self.last:
            raise ValueError("no live card numbers left (%d-%d used)" % (self.first, self.last))
        digits, lo, hi = LEVELS.get(level, LEVELS["medium"])
        pin = str(rng.randint(lo, hi)).zfill(digits)
        b = lock(name, message, pin, digits, "%08x" % rng.getrandbits(32), self.rounds)
        self.state["beacons"][str(cid)] = b
        self.state["next"] = cid + 1
        self._save()
        if self.answers:
            with open(self.answers, "a", encoding="utf-8") as f:
                f.write("#%d %s pin=%s (live)\n" % (cid, name, pin))
        return str(cid), b, pin

    def lock_message(self, cid):
        """The control command for one card: the lock WITHOUT the scrambled message."""
        b = self.state["beacons"][cid]
        return {"type": "ctl", "cmd": "lock", "id": cid,
                "lock": {k: b[k] for k in ("title", "digits", "salt", "check")}}

    def all_messages(self):
        return [self.lock_message(c) for c in sorted(self.state["beacons"], key=int)]

    def locks_json(self):
        """For the dashboard: everything it needs to unlock a live card once its PIN is found."""
        return {"rounds": self.rounds, "beacons": self.state["beacons"]}


def ctl_targets(config):
    """[(ip, ctl_port)] for every node in the config."""
    return [(c["ip"], c["ctl_port"]) for c in config["nodes"].values()]


def send_all(msgs, targets, repeat=3, gap=0.1):
    """UDP-send each message to each target, `repeat` times (SPEC §8.3 style)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for r in range(repeat):
            for m in msgs:
                data = json.dumps(m, separators=(",", ":")).encode("utf-8")
                for t in targets:
                    try:
                        s.sendto(data, t)
                    except OSError:
                        pass                 # a node is off the network: the 5 s resend covers it
            if r < repeat - 1:
                time.sleep(gap)
    finally:
        s.close()
