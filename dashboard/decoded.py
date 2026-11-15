"""Decoded distress messages: once a node reports a card done with its PIN, unlock the message.

Pure logic: no sockets, no clock (the caller passes `t` = seconds since the round started, on
the DASHBOARD's clock). Uses core/beacon.py, as SPEC "Person B — to do for decode mode" says.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from core import beacon  # noqa: E402


class Decoded:
    def __init__(self, rounds=0, beacons=None):
        self.rounds = rounds
        self.beacons = beacons or {}      # card id -> beacon (title, digits, salt, check, ct)
        self.reset()

    def reset(self):
        """New round: forget everything."""
        self.found = {}                   # card id -> entry
        self.order = []                   # card ids, oldest first

    def titles(self):
        """What the page needs to label a card: {id: {title, digits}}."""
        return {jid: {"title": b.get("title"), "digits": b.get("digits")} for jid, b in self.beacons.items()}

    def on_telemetry(self, msg, t):
        jobs = msg.get("jobs")
        if not isinstance(jobs, dict):
            return
        for jid, job in jobs.items():
            jid = str(jid)
            if jid not in self.beacons or not isinstance(job, dict) or not job.get("done"):
                continue
            pin = job.get("pin")
            if not isinstance(pin, str):
                continue
            old = self.found.get(jid)
            if old and old["pin"] <= pin:             # SPEC 6: two PINs for one card -> lower string wins
                continue
            b = self.beacons[jid]
            try:
                ok = beacon.check(b, pin, self.rounds)
                message = beacon.unlock(b, pin, self.rounds) if ok else None
            except Exception:                         # a broken beacons.json entry
                ok, message = False, None
            self.found[jid] = {"job": jid, "title": b.get("title"), "pin": pin, "ok": ok,
                               "message": message, "t": old["t"] if old else t}
            if not old:
                self.order.append(jid)

    def snapshot(self):
        """Newest first."""
        return [dict(self.found[jid]) for jid in reversed(self.order)]
