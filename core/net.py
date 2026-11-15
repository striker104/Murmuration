"""UDP plumbing around a core.node.Node (SPEC §8). Standard library only (runs on the Pi).

    runner = Runner("pi", load_config("config.json"))
    runner.run_forever()                 # blocks; Ctrl-C to stop

Camera adapters run the loop in a thread and call runner.spot(tag) from their own loop.
"""
import json
import os
import random
import socket
import sys
import threading
import time

from core import beacon
from core.node import Node, TICK, GOSSIP_INTERVAL, TELEMETRY_INTERVAL

MAX_PACKET = 2048          # SPEC §8.1: receive buffer


def load_config(path):
    with open(path) as f:
        cfg = json.load(f)
    cfg["_dir"] = os.path.dirname(os.path.abspath(path))   # where beacons.json lives
    return cfg


def decode_setup(name, config):
    """'work': 'decode' -> (beacons, rate, check); otherwise (None, None, None) = counter mode."""
    if config.get("work", "counter") != "decode":
        return None, None, None
    if config["nodes"][name].get("work") == "counter":
        print("[%s] marked \"work\": \"counter\" -> counter mode" % name, flush=True)
        return None, None, None
    path = os.path.join(config.get("_dir", "."), "beacons.json")
    try:
        rounds, beacons = beacon.load_beacons(path)
    except (OSError, ValueError, KeyError) as e:
        # refuse to start (like a port clash): a counter-mode node in a decoding swarm would
        # push progress past PINs nobody checked, and those messages would never unlock
        raise SystemExit("[%s] decode mode but can't load %s (%s) — copy beacons.json next "
                         "to the config, or set \"work\": \"counter\"" % (name, path, e))
    rate = config["nodes"][name].get("rate")
    how = "from config"
    if not rate:
        rate = round(beacon.bench(rounds), 1)
        how = "measured"
    print("[%s] decode mode: %d beacons, %.0f guesses/s (%s)" % (name, len(beacons), rate, how),
          flush=True)

    def check(b, pin):
        return beacon.check(b, pin, rounds)
    return beacons, float(rate), check


def counter_peers(name, config):
    """Decode mode: peers marked "work": "counter" in config (can't decode yet, e.g. Nicla)."""
    if config.get("work", "counter") != "decode":
        return []
    return [n for n, c in config["nodes"].items() if n != name and c.get("work") == "counter"]


def _encode(msg):
    return json.dumps(msg, separators=(",", ":")).encode("utf-8")


def _udp_socket(bind_ip, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # (no SO_REUSEADDR on purpose: a leftover node on the same port must fail loudly)
    if sys.platform == "win32" and hasattr(socket, "SIO_UDP_CONNRESET"):
        # Windows: stop "port unreachable" from a dead peer killing our recv (WSAECONNRESET)
        s.ioctl(socket.SIO_UDP_CONNRESET, False)
    s.bind((bind_ip, port))
    s.setblocking(False)
    return s


class Runner:
    def __init__(self, name, config, on_event=None):
        nodes = config["nodes"]
        if name not in nodes:
            raise SystemExit("unknown node %r (config has %s)" % (name, ", ".join(sorted(nodes))))
        me = nodes[name]
        self.name = name
        self.addr = {n: (c["ip"], c["gossip_port"]) for n, c in nodes.items()}
        dash = config["dashboard"]
        self.dashboard = (dash["ip"], dash["telemetry_port"])
        self.on_event = on_event or (lambda kind, job_id, mode: None)

        now = time.monotonic()
        beacons, rate, check = decode_setup(name, config)
        now = time.monotonic()           # after the benchmark
        self.node = Node(name, list(nodes), me["speed"], me.get("zone"), now=now,
                         beacons=beacons, rate=rate, check=check,
                         counter_peers=counter_peers(name, config))
        self.lock = threading.Lock()     # node is shared with camera threads

        # local sim: bind to 127.0.0.1 (no firewall prompt); real devices: all interfaces
        bind_ip = "127.0.0.1" if me["ip"] == "127.0.0.1" else ""
        self.gossip_sock = _udp_socket(bind_ip, me["gossip_port"])
        self.ctl_sock = _udp_socket(bind_ip, me["ctl_port"])

        self.next_gossip = now
        self.next_telemetry = now
        self.running = False

    # -- called from camera adapters -------------------------------------
    def spot(self, tag_id):
        with self.lock:
            return self.node.spot(tag_id, time.monotonic())

    def snapshot(self):
        """Copy of what the node believes, for drawing overlays."""
        with self.lock:
            n = self.node
            return {
                "jobs": json.loads(json.dumps(n.jobs)),
                "current": n.current,
                "mode": "crashed" if n.crashed else n.mode,
                "blind": n.blind,
                "trying": n.trying,
                "reachable": sorted(n.reachable(time.monotonic())),
            }

    def control(self, cmd, **extra):
        """Apply a control command locally (e.g. keyboard shortcut in the camera window)."""
        msg = {"type": "ctl", "cmd": cmd}
        msg.update(extra)
        with self.lock:
            self.node.on_control(msg, time.monotonic())

    # -- the loop ----------------------------------------------------------
    def _recv_all(self, sock):
        """All pending packets as [(msg, (src_ip, src_port))]."""
        out = []
        while True:
            try:
                data, src = sock.recvfrom(MAX_PACKET)
            except BlockingIOError:
                return out
            except (ConnectionResetError, OSError):
                # Windows/Linux can surface ICMP errors here; ignore and keep draining
                return out
            try:
                out.append((json.loads(data.decode("utf-8")), src))
            except (ValueError, UnicodeDecodeError):
                pass

    def _learn_address(self, sender, src_ip):
        """Peer's IP changed (DHCP)? Learn it from the gossip it sent us.

        Only for accepted gossip from a known node name (on_gossip checked that).
        Keep the configured PORT: a peer may send from a different socket than it listens on.
        """
        old_ip, port = self.addr[sender]
        if src_ip != old_ip:
            self.addr[sender] = (src_ip, port)
            print("[%s] %s address %s -> %s (learned from its gossip)"
                  % (self.name, sender, old_ip, src_ip), flush=True)

    def _send(self, data, addr):
        try:
            self.gossip_sock.sendto(data, addr)
        except OSError:
            pass   # network down: that's the whole point, keep going

    def step(self):
        """One loop iteration (SPEC §7). Never blocks."""
        now = time.monotonic()
        node = self.node
        with self.lock:
            for msg, _ in self._recv_all(self.ctl_sock):
                node.on_control(msg, now)
            for msg, src in self._recv_all(self.gossip_sock):
                if random.random() < node.loss:          # flaky: drop on receive
                    continue
                if node.on_gossip(msg, now):
                    self._learn_address(msg["from"], src[0])

            node.tick(now)

            gossip, targets = [], []
            if now >= self.next_gossip:
                self.next_gossip = now + GOSSIP_INTERVAL
                gossip = [_encode(m) for m in node.gossip_messages()]
                targets = node.gossip_targets()
            telemetry = None
            if now >= self.next_telemetry:
                self.next_telemetry = now + TELEMETRY_INTERVAL
                telemetry = _encode(node.telemetry(now))
            events = node.pop_events()
            loss = node.loss

        for data in gossip:
            for peer in targets:
                if random.random() < loss:                # flaky: drop on send
                    continue
                self._send(data, self.addr[peer])
        if telemetry is not None:
            self._send(telemetry, self.dashboard)
        for ev in events:
            self.on_event(*ev)

    def run_forever(self):
        self.running = True
        next_tick = time.monotonic()
        while self.running:
            self.step()
            next_tick += TICK
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()   # fell behind (slow Pi): don't try to catch up

    def start_thread(self):
        t = threading.Thread(target=self.run_forever, daemon=True)
        t.start()
        return t

    def stop(self):
        self.running = False
