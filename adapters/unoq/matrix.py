"""UNO Q node + LED matrix: runs the core node and shows its current job on the matrix.

    python3 -m adapters.unoq.matrix --name unoq --config config.json --log murmuration.log

Talks to the MCU sketch (adapters/unoq/sketch/sketch.ino) through the arduino-router's
Unix socket with msgpack-RPC:  show(job, pct, speed_milli)   (job -1 = idle).
If the router/sketch isn't there, the node keeps running and the display is skipped.
Standard library only (a tiny msgpack encoder is built in).
"""
import argparse
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from core.net import Runner, load_config   # noqa: E402
from core.run_node import make_logger       # noqa: E402

ROUTER_SOCK = "/var/run/arduino-router.sock"
UPDATE_EVERY = 0.3


# ---- minimal msgpack (only what we send: ints, short strings, lists) --------------

def pack(obj):
    if isinstance(obj, bool) or obj is None:
        return b"\xc3" if obj is True else (b"\xc2" if obj is False else b"\xc0")
    if isinstance(obj, int):
        # smallest encoding, exactly like the msgpack library
        if 0 <= obj < 128:
            return struct.pack("B", obj)
        if -32 <= obj < 0:
            return struct.pack("b", obj)
        if 0 <= obj <= 0xFF:
            return b"\xcc" + struct.pack(">B", obj)
        if 0 <= obj <= 0xFFFF:
            return b"\xcd" + struct.pack(">H", obj)
        if 0 <= obj <= 0xFFFFFFFF:
            return b"\xce" + struct.pack(">I", obj)
        if -128 <= obj < 0:
            return b"\xd0" + struct.pack(">b", obj)
        if -32768 <= obj < 0:
            return b"\xd1" + struct.pack(">h", obj)
        return b"\xd2" + struct.pack(">i", obj)
    if isinstance(obj, str):
        raw = obj.encode("utf-8")
        if len(raw) < 32:
            return struct.pack("B", 0xA0 | len(raw)) + raw
        return b"\xd9" + struct.pack("B", len(raw)) + raw
    if isinstance(obj, (list, tuple)):
        if len(obj) >= 16:
            raise ValueError("list too long for this tiny encoder")
        return struct.pack("B", 0x90 | len(obj)) + b"".join(pack(x) for x in obj)
    raise TypeError("can't pack %r" % (obj,))


def rpc_request(msgid, method, params):
    """msgpack-RPC request: [0, msgid, method, params]."""
    return pack([0, msgid, method, list(params)])


# ---- the display ------------------------------------------------------------------

class MatrixDisplay:
    def __init__(self, path=ROUTER_SOCK):
        self.path = path
        self.msgid = 0
        self.retry_at = 0.0
        self.warned = False

    def show(self, job, pct, speed_milli):
        now = time.monotonic()
        if now < self.retry_at:
            return False
        self.msgid = (self.msgid + 1) % 0x7FFFFFFF
        req = rpc_request(self.msgid, "show", [int(job), int(pct), int(speed_milli)])
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.3)
            try:
                s.connect(self.path)
                s.sendall(req)
                try:
                    s.recv(256)          # the response; we don't need its contents
                except socket.timeout:
                    pass
            finally:
                s.close()
            if self.warned:
                print("matrix: back", flush=True)
                self.warned = False
            return True
        except (OSError, AttributeError) as e:   # AttributeError: no AF_UNIX (Windows)
            if not self.warned:
                print("matrix: can't reach router (%s) — node keeps running, display off" % e,
                      flush=True)
                self.warned = True
            self.retry_at = now + 5.0
            return False


def matrix_args(snap, speed):
    """(job, pct, speed_milli) for the current node state. job -1 = idle."""
    cur = snap["current"]
    if snap["mode"] == "crashed" or not cur or cur not in snap["jobs"]:
        return (-1, 0, 0)
    job = snap["jobs"][cur]
    return (int(cur), int(job["progress"] * 100), int(round(speed * 1000)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="unoq")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    runner = Runner(args.name, load_config(args.config), make_logger(args.name, args.log))
    runner.start_thread()
    print("[%s] node up, matrix via %s" % (args.name, ROUTER_SOCK), flush=True)
    display = MatrixDisplay()
    try:
        while True:
            display.show(*matrix_args(runner.snapshot(), runner.node.speed))
            time.sleep(UPDATE_EVERY)
    except KeyboardInterrupt:
        display.show(-1, 0, 0)


if __name__ == "__main__":
    main()
