"""Terminal stand-in for the dashboard: listens on the telemetry port and prints
each node's belief once a second. Don't run it at the same time as dashboard/server.py
(they both want port 5006).

    python sim/watch.py
    python sim/watch.py --once 6        (print for ~6 s then exit; used for testing)
"""
import json
import os
import socket
import sys
import time

ROOT = os.path.join(os.path.dirname(__file__), "..")


def fmt_job(j):
    if j["done"]:
        return "#%s done%s" % (j["id"], " pin=" + j["pin"] if j.get("pin") else "")
    return "#%s %3d%% %s" % (j["id"], int(j["progress"] * 100), j["owner"] or "-")


def main(argv):
    config = os.environ.get("MURM_CONFIG", os.path.join(ROOT, "config.local.json"))
    with open(config) as f:
        cfg = json.load(f)
    port = cfg["dashboard"]["telemetry_port"]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", port))
    s.settimeout(0.2)
    latest, heard = {}, {}
    stop_at = time.monotonic() + float(argv[1]) if argv[:1] == ["--once"] else None
    next_print = time.monotonic() + 1
    while stop_at is None or time.monotonic() < stop_at:
        try:
            data, _ = s.recvfrom(65535)
            msg = json.loads(data)
            if msg.get("type") == "telemetry":
                latest[msg["node"]] = msg
                heard[msg["node"]] = time.monotonic()
        except socket.timeout:
            pass
        if time.monotonic() >= next_print:
            next_print += 1
            print("-" * 70)
            for n in sorted(cfg["nodes"]):
                m = latest.get(n)
                if m is None or time.monotonic() - heard[n] > 2.0:
                    print("%-7s NO SIGNAL" % n)
                    continue
                jobs = sorted(m["jobs"].values(), key=lambda j: int(j["id"]))
                print("%-7s %-10s cur=%-4s try=%-6s blk=%s  %s" % (
                    n, m["mode"], m["current"], m.get("trying") or "-",
                    ",".join(m["blocked"]) or "-",
                    "  ".join(fmt_job(j) for j in jobs)))
            sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv[1:])
