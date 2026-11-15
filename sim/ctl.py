"""Send a control command to nodes from the terminal (stand-in for the dashboard buttons).

    python sim/ctl.py all isolate
    python sim/ctl.py unoq crash
    python sim/ctl.py webcam,nicla blind
    python sim/ctl.py webcam block nicla        (peers after the command)
    python sim/ctl.py all flaky 0.3
    python sim/ctl.py all heal                  (= unblock_all, unblind, revive, flaky 0)

Each command is sent 3 times, 0.1 s apart (SPEC §8.3). Default config: config.local.json.
"""
import json
import os
import socket
import sys
import time

ROOT = os.path.join(os.path.dirname(__file__), "..")


def send(cfg, names, msg):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    data = json.dumps(msg).encode()
    for _ in range(3):
        for n in names:
            c = cfg["nodes"][n]
            s.sendto(data, (c["ip"], c["ctl_port"]))
        time.sleep(0.1)


def main(argv):
    config = os.environ.get("MURM_CONFIG", os.path.join(ROOT, "config.local.json"))
    with open(config) as f:
        cfg = json.load(f)
    if len(argv) < 2:
        print(__doc__)
        return 1
    who, cmd, rest = argv[0], argv[1], argv[2:]
    names = sorted(cfg["nodes"]) if who == "all" else who.split(",")

    if cmd == "heal":
        for c in ["unblock_all", "unblind", "revive"]:
            send(cfg, names, {"type": "ctl", "cmd": c})
        send(cfg, names, {"type": "ctl", "cmd": "flaky", "loss": 0.0})
    else:
        msg = {"type": "ctl", "cmd": cmd}
        if cmd in ("block", "unblock"):
            msg["peers"] = rest[0].split(",") if rest else []
        elif cmd == "flaky":
            msg["loss"] = float(rest[0]) if rest else 0.0
        elif cmd == "spot":
            msg["tag"] = int(rest[0])
        send(cfg, names, msg)
    print("sent %s -> %s" % (cmd, ",".join(names)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
