"""Start all 4 nodes on this laptop (config.local.json). Works in PowerShell, cmd, Git Bash, Mac, Linux.

    python sim/run_local.py

Ctrl-C stops them all. (sim/run_local.sh does the same but needs Git Bash — on Windows,
'bash' in PowerShell opens WSL, which can't talk to Windows programs on 127.0.0.1.)
"""
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NAMES = ["webcam", "nicla", "unoq", "pi"]


def main():
    procs = []
    for n in NAMES:
        procs.append(subprocess.Popen(
            [sys.executable, "-u", "-m", "core.run_node", "--name", n,
             "--config", "config.local.json"],
            cwd=ROOT))
    print("started %d nodes; Ctrl-C to stop" % len(procs), flush=True)
    try:
        while True:
            for n, p in zip(NAMES, procs):
                if p.poll() is not None:
                    print("!! node %s exited (code %s) — check the error above" % (n, p.returncode),
                          flush=True)
                    raise KeyboardInterrupt
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
