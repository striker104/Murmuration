"""Rescue timeline: turns successive telemetry messages into a history the page can draw.

Pure logic: no sockets, no clock. The caller passes `t` = seconds since the round started,
measured on the DASHBOARD's own clock (never a node's). Design:
dashboard/docs/2026-11-15-timeline-dashboard-design.md

    segment  one stretch of one node working on one job (a bar on that node's lane)
    outage   a stretch where a node was crashed or sent nothing
    marker   a scenario button press
    colors   job id -> colour index, in the order jobs first appeared this round
"""


def _progress(job):
    p = job.get("progress")
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        return 0.0
    return float(p)


TAKEOVER_SLACK = 0.15      # the old worker may advance a little before it notices it lost the job


class Timeline:
    MAX_SEGMENTS = 600

    def __init__(self, names):
        self.names = list(names)
        self.reset()

    def reset(self):
        """New round: forget everything."""
        self.segments = []        # oldest first
        self.outages = []
        self.markers = []
        self.colors = {}
        self.open_seg = {}        # node -> its open segment (also in self.segments)
        self.open_out = {}        # node -> its open outage (also in self.outages)
        self.cut = {}             # node -> the segment silent() closed, until we hear from it again

    # ---- input

    def on_telemetry(self, msg, t):
        node = msg.get("node")
        jobs = msg.get("jobs")
        if node not in self.names or not isinstance(jobs, dict):
            return
        jobs = {str(k): v for k, v in jobs.items() if isinstance(v, dict)}
        self._assign_colors(jobs)

        crashed = msg.get("mode") == "crashed"
        if crashed:
            out = self.open_out.get(node)
            if not out or out["kind"] != "crashed":      # e.g. it was "no signal" and came back crashed
                self._close_outage(node, t)
                self._open_outage(node, "crashed", t)
        else:
            self._close_outage(node, t)

        current = msg.get("current")
        current = None if current is None else str(current)
        working = current
        if crashed or current not in jobs or jobs[current].get("done"):
            working = None

        # Back after a silence: we only lost telemetry, the node may have kept working.
        cut = self.cut.pop(node, None)
        if cut and not crashed and any(g is cut for g in self.segments):
            job = jobs.get(cut["job"])
            if job and job.get("done"):                  # it finished the job while we couldn't hear it
                cut.update(end=max(t, cut["start"]), p1=_progress(job), outcome="done")
            elif working == cut["job"]:                  # still on the same job: one bar, not two
                cut.update(end=None, outcome="live")
                self.open_seg[node] = cut

        seg = self.open_seg.get(node)
        if seg and seg["job"] == working:
            seg["p1"] = _progress(jobs[working])
            return
        if seg:
            job = jobs.get(seg["job"])
            if job:
                seg["p1"] = _progress(job)
            self._close_segment(node, t, "done" if job and job.get("done") else "left")
        if working is not None:
            p = _progress(jobs[working])
            new = {"node": node, "job": working, "start": t, "end": None, "p0": p, "p1": p,
                   "outcome": "live", "from": self._previous_worker(working, node, p)}
            self.segments.append(new)
            self.open_seg[node] = new
            del self.segments[:-self.MAX_SEGMENTS]

    def silent(self, node, since):
        """The node has sent nothing since round time `since`. Safe to call repeatedly."""
        if node in self.open_seg:
            self.cut[node] = self.open_seg[node]
            self._close_segment(node, since, "left")
        out = self.open_out.get(node)
        if out and out["kind"] == "no_signal":
            return
        self._close_outage(node, since)
        self._open_outage(node, "no_signal", since)

    def mark(self, text, t):
        self.markers.append({"t": t, "text": text})
        del self.markers[:-100]

    # ---- output

    def snapshot(self, t):
        return {
            "now": t,
            "segments": [dict(s) for s in self.segments],
            "outages": [dict(o) for o in self.outages],
            "markers": [dict(m) for m in self.markers],
            "colors": dict(self.colors),
        }

    # ---- helpers

    def _assign_colors(self, jobs):
        def key(jid):
            sev = jobs[jid].get("sev", 0)
            return (sev if isinstance(sev, (int, float)) else 0, jid)
        for jid in sorted(jobs, key=key):
            if jid not in self.colors:
                self.colors[jid] = len(self.colors)

    def _previous_worker(self, job, node, p0):
        """Whose unfinished work on this job is this node carrying on from (None if nobody's).

        A takeover keeps the progress (SPEC 7.1), so the new bar starts about where the old one
        got to. A node that starts far below that never heard of the other's work: that is
        duplicate effort, not a takeover."""
        for seg in reversed(self.segments):
            if seg["job"] == job and seg["node"] != node and seg["outcome"] != "done"                     and seg["p1"] <= p0 + TAKEOVER_SLACK:
                return seg["node"]
        return None

    def _close_segment(self, node, t, outcome):
        seg = self.open_seg.pop(node)
        seg["end"] = max(t, seg["start"])
        seg["outcome"] = outcome

    def _open_outage(self, node, kind, t):
        out = {"node": node, "kind": kind, "start": t, "end": None}
        self.outages.append(out)
        self.open_out[node] = out
        del self.outages[:-200]

    def _close_outage(self, node, t):
        out = self.open_out.pop(node, None)
        if out:
            out["end"] = max(t, out["start"])
