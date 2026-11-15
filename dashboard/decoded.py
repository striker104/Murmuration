"""Decoded distress messages: once a node reports a card done with its PIN, unlock the message.

Pure logic: no sockets, no clock (the caller passes `t` = seconds since the round started, on
the DASHBOARD's clock). Uses core/beacon.py, as SPEC "Person B — to do for decode mode" says.

Two kinds of card (SPEC §12):
    preset  in beacons.json (ids 0-99, 200+), loaded once at start
    live    a judge's message, id 100+ and NOT in beacons.json; its lock (with the scrambled
            message `ct`) comes from the card maker's /locks.json, which the server polls
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from core import beacon  # noqa: E402

LIVE_START = 100            # SPEC §12: ids from here up may be live judge cards


class Decoded:
    def __init__(self, rounds=0, beacons=None):
        self.rounds = rounds
        self.beacons = beacons or {}      # card id -> beacon (title, digits, salt, check, ct)
        self.live_rounds = 0
        self.live = {}                    # live card id -> beacon, from /locks.json
        self.reset()

    def reset(self):
        """New round: forget what was found. Live locks are kept (SPEC §12.2)."""
        self.found = {}                   # card id -> entry
        self.order = []                   # card ids, oldest first

    def set_live(self, data):
        """The card maker's /locks.json: {"rounds": 500, "beacons": {"103": {...}}}.
        A card that is in beacons.json is never live, whatever its number. Raises on junk."""
        rounds = int(data["rounds"])
        live = {}
        for jid, b in data["beacons"].items():
            jid = str(jid)
            if jid in self.beacons or not jid.isdigit() or int(jid) < LIVE_START:
                continue
            if isinstance(b, dict) and "ct" in b and "salt" in b and "check" in b:
                live[jid] = b
        self.live_rounds, self.live = rounds, live

    def is_live(self, jid):
        jid = str(jid)
        return jid not in self.beacons and jid.isdigit() and int(jid) >= LIVE_START

    def needs_pin(self, jid):
        """Counts as rescued only once its PIN is found: it has a locked message."""
        return str(jid) in self.beacons or self.is_live(jid)

    def _lock(self, jid):
        """-> (beacon, rounds), or (None, 0) if we can't unlock this card (yet)."""
        if jid in self.beacons:
            return self.beacons[jid], self.rounds
        if jid in self.live:
            return self.live[jid], self.live_rounds
        return None, 0

    def titles(self):
        """What the page needs to label a card: {id: {title, digits, judge}}."""
        out = {jid: {"title": b.get("title"), "digits": b.get("digits"), "judge": False}
               for jid, b in self.beacons.items()}
        for jid, b in self.live.items():
            out[jid] = {"title": b.get("title"), "digits": b.get("digits"), "judge": True}
        return out

    def on_telemetry(self, msg, t):
        jobs = msg.get("jobs")
        if not isinstance(jobs, dict):
            return
        for jid, job in jobs.items():
            jid = str(jid)
            if not isinstance(job, dict) or not job.get("done"):
                continue
            pin = job.get("pin")
            if not isinstance(pin, str):
                continue
            b, rounds = self._lock(jid)
            if b is None:
                continue                              # a live card whose lock hasn't arrived yet
            old = self.found.get(jid)
            if old and old["pin"] <= pin:             # SPEC 6: two PINs for one card -> lower string wins
                continue
            try:
                ok = beacon.check(b, pin, rounds)
                message = beacon.unlock(b, pin, rounds) if ok else None
            except Exception:                         # a broken beacons.json entry
                ok, message = False, None
            self.found[jid] = {"job": jid, "title": b.get("title"), "pin": pin, "ok": ok,
                               "message": message, "t": old["t"] if old else t,
                               "judge": jid in self.live}
            if not old:
                self.order.append(jid)

    def snapshot(self):
        """Newest first."""
        return [dict(self.found[jid]) for jid in reversed(self.order)]
