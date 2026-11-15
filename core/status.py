"""One live status line for a node's terminal, redrawn in place (no scrolling):

    webcam / connected  #3 Jonas   try 04812  [##########..........]  48%   5210/s

status_line() is pure (unit-tested); StatusLine writes it with '\r' and clears it before
an event line is printed so logs still scroll above it. Standard library only.
"""
import sys

SPINNER = "|/-\\"
WIDTH = 78
BAR = 20


def status_line(name, snap, t, titles=None, rate=None):
    """snap: Runner.snapshot(); t: frame counter (turns the spinner)."""
    mode = snap["mode"]
    if mode == "crashed":
        body = "X CRASHED"
    else:
        cur = snap.get("current")
        job = snap["jobs"].get(cur) if cur else None
        if job is None:
            rescued = sum(1 for j in snap["jobs"].values() if j.get("done"))
            body = ". %-10s idle   %d rescued" % (mode, rescued)
        else:
            p = max(0.0, min(1.0, job["progress"]))
            fill = int(round(p * BAR))
            title = (titles or {}).get(cur, "")
            body = "%s %-10s #%s %-6s" % (SPINNER[t % len(SPINNER)], mode, cur, title[:6])
            if snap.get("trying"):
                body += " try %-6s" % snap["trying"]
            body += " [%s%s] %3d%%" % ("#" * fill, "." * (BAR - fill), int(p * 100))
            if rate:
                body += "  %d/s" % rate
    line = "%-6s %s" % (name, body)
    return line[:WIDTH].ljust(WIDTH)


class StatusLine:
    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.shown = 0

    def draw(self, text):
        self.out.write("\r" + text)
        self.out.flush()
        self.shown = len(text)

    def clear(self):
        """Blank the line (call before printing a normal log line)."""
        if self.shown:
            self.out.write("\r" + " " * self.shown + "\r")
            self.out.flush()
            self.shown = 0
