"""Run one Murmuration node.

    python -m core.run_node --name pi --config config.json --log murmuration.log

Prints every event; with --log also appends lines like
    20:31:12 rescued #4 (mode=isolated)
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.net import Runner, load_config  # noqa: E402


def make_logger(name, log_path):
    def on_event(kind, job_id, mode):
        line = "%s %s%s (mode=%s)" % (
            time.strftime("%H:%M:%S"), kind, " #%s" % job_id if job_id else "", mode)
        print("[%s] %s" % (name, line), flush=True)
        if log_path:
            with open(log_path, "a") as f:
                f.write(line + "\n")
    return on_event


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--log", default=None, help="append events to this file")
    args = ap.parse_args()

    runner = Runner(args.name, load_config(args.config), make_logger(args.name, args.log))
    print("[%s] up: gossip %s, ctl %s" % (
        args.name, runner.gossip_sock.getsockname(), runner.ctl_sock.getsockname()), flush=True)
    try:
        runner.run_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
