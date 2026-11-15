"""UDP plumbing around a core.node.Node (SPEC §8). Standard library only (runs on the Pi).

    runner = Runner("pi", load_config("config.json"))
    runner.run_forever()                 # blocks; Ctrl-C to stop

Camera adapters run the loop in a thread and call runner.spot(tag) from their own loop.
"""
import json
import random
import socket
import sys
import threading
import time

from core.node import Node, TICK, GOSSIP_INTERVAL, TELEMETRY_INTERVAL

MAX_PACKET = 2048          # SPEC §8.1: receive buffer


def load_config(path):
    with open(path) as f:
        return json.load(f)


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
        self.node = Node(name, list(nodes), me["speed"], me.get("zone"), now=now)
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
        out = []
        while True:
            try:
                data, _ = sock.recvfrom(MAX_PACKET)
            except BlockingIOError:
                return out
            except (ConnectionResetError, OSError):
                # Windows/Linux can surface ICMP errors here; ignore and keep draining
                return out
            try:
                out.append(json.loads(data.decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                pass

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
            for msg in self._recv_all(self.ctl_sock):
                node.on_control(msg, now)
            for msg in self._recv_all(self.gossip_sock):
                if random.random() < node.loss:          # flaky: drop on receive
                    continue
                node.on_gossip(msg, now)

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
