"""Fake nodes for building/testing the dashboard without A's code or any hardware.

Runs nodes on this laptop using the SAME logic as nicla/main.py, over real UDP on localhost,
so every dashboard button does something real (block, crash, split, ...).

    python dashboard/fake_telemetry.py                      # all 4 nodes from config.local.json
    python dashboard/fake_telemetry.py --only nicla         # just a stand-in Nicla (to join A's sim)
    python dashboard/fake_telemetry.py --cards 0            # no automatic cards
    python dashboard/fake_telemetry.py --config config.decode.local.json    # decode mode (cracks PINs)

It also plays "fake camera": every few seconds a camera node spots a new random card, until
--cards jobs exist. After "Reset round" it starts again. Blind cameras spot nothing.
"""
import argparse
import importlib.util
import json
import os
import random
import socket
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("nicla_main", os.path.join(HERE, "..", "nicla", "main.py"))
logic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(logic)


def udp_socket(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    s.setblocking(False)
    return s


def drain(sock, limit=50):
    out = []
    for _ in range(limit):
        try:
            data, _addr = sock.recvfrom(logic.RECV_BUF)
        except BlockingIOError:
            break
        except OSError:
            continue        # Windows reports "connection reset" when a peer's port is closed
        try:
            msg = json.loads(data)
        except ValueError:
            continue
        if isinstance(msg, dict):
            out.append(msg)
    return out


class FakeNode:
    def __init__(self, cfg, name, beacons_text=None):
        me = cfg["nodes"][name]
        now = logic.ticks_ms()
        self.node = logic.node_from_config(cfg, name, now, log=lambda t: print(
            "%s %-6s %s" % (time.strftime("%H:%M:%S"), name, t), flush=True), beacons_text=beacons_text)
        self.gsock = udp_socket(me["gossip_port"])
        self.csock = udp_socket(me["ctl_port"])
        self.peer_addr = {p: (c["ip"], c["gossip_port"]) for p, c in cfg["nodes"].items() if p != name}
        self.dash_addr = (cfg["dashboard"]["ip"], cfg["dashboard"]["telemetry_port"])
        self.next_tick = self.next_gossip = self.next_tele = now

    def send(self, obj, addr):
        try:
            self.gsock.sendto(json.dumps(obj).encode(), addr)
        except OSError:
            pass

    def poll(self):
        node = self.node
        now = logic.ticks_ms()
        for msg in drain(self.csock):
            if msg.get("type") == "ctl":
                node.on_control(msg, now)
        for msg in drain(self.gsock):
            if msg.get("type") == "gossip":
                node.on_gossip(msg, now)
        if now >= self.next_tick:
            self.next_tick = now + logic.TICK_MS
            node.step(now)
            node.crack(now)                 # decode mode: guess PINs at the node's configured rate
        if now >= self.next_gossip:
            self.next_gossip = now + logic.GOSSIP_INTERVAL_MS
            targets = node.gossip_targets()
            if targets:
                for msg in node.gossip_messages():
                    for p in targets:
                        if not node.drop_outgoing():
                            self.send(msg, self.peer_addr[p])
        if now >= self.next_tele:
            self.next_tele = now + logic.TELEMETRY_INTERVAL_MS
            self.send(node.telemetry(now), self.dash_addr)


def main():
    ap = argparse.ArgumentParser(description="Fake Murmuration nodes for the dashboard")
    ap.add_argument("--config", default="config.local.json")
    ap.add_argument("--only", nargs="*", help="run just these nodes (default: all)")
    ap.add_argument("--cards", type=int, default=8, help="auto-spawn cards until this many jobs exist")
    ap.add_argument("--every", type=float, default=3.0, help="seconds between auto-spawned cards")
    args = ap.parse_args()

    path = args.config if os.path.exists(args.config) else os.path.join(HERE, "..", args.config)
    with open(path) as f:
        cfg = json.load(f)
    names = args.only or list(cfg["nodes"])
    beacons_text = None
    if cfg.get("work") == "decode":         # beacons.json lives next to the config (SPEC 3)
        with open(os.path.join(os.path.dirname(os.path.abspath(path)), "beacons.json")) as f:
            beacons_text = f.read()
    fakes = [FakeNode(cfg, n, beacons_text) for n in names]
    cameras = [f for f in fakes if f.node.zone]
    print("fake nodes: %s  ->  dashboard %s:%d   (Ctrl+C to stop)" % (
        ", ".join(names), cfg["dashboard"]["ip"], cfg["dashboard"]["telemetry_port"]))

    next_card = time.monotonic() + 2.0
    while True:
        for f in fakes:
            f.poll()
        if cameras and args.cards > 0 and time.monotonic() >= next_card:
            next_card = time.monotonic() + args.every
            known = set()
            for f in fakes:
                known.update(f.node.jobs)
            if len(known) < args.cards:
                free = [i for i in range(1, 30) if str(i) not in known]
                random.choice(cameras).node.spot(random.choice(free), logic.ticks_ms())
        time.sleep(0.01)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
