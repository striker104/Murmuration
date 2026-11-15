"""In-process simulator: 4 Nodes, fake clock, fake network. No sockets, no sleeps.

Messages are JSON round-tripped (catches anything that won't serialise) and size-checked.
Each node gossips on a slightly different tick phase, like real devices.
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.node import Node, TICK, GOSSIP_INTERVAL  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config.local.json")
MAX_GOSSIP_BYTES = 1400


class Sim:
    def __init__(self, seed=1, beacons=None, rates=None, check=None):
        """beacons/rates/check given -> decode mode (rates: {name: guesses per second})."""
        with open(CONFIG) as f:
            cfg = json.load(f)["nodes"]
        names = sorted(cfg)
        self.t = 0.0
        self.rng = random.Random(seed)
        self.nodes = {
            n: Node(n, names, cfg[n]["speed"], cfg[n]["zone"], now=self.t,
                    beacons=beacons, rate=(rates or {}).get(n), check=check)
            for n in names
        }
        self.inbox = {n: [] for n in names}
        self.wire_down = False          # True = REAL network failure (not a 'block')
        self.links_down = set()          # frozenset({a, b}) = real cable cut between two
        self.max_bytes = 0
        # stagger gossip phase per node (0.0, 0.1, 0.2 ...)
        self.phase = {n: i % 3 for i, n in enumerate(names)}
        self.ticks = 0

    def ctl(self, names, cmd, **extra):
        msg = {"type": "ctl", "cmd": cmd}
        msg.update(extra)
        for n in ([names] if isinstance(names, str) else names):
            self.nodes[n].on_control(msg, self.t)

    def spot(self, camera, *tags):
        for tag in tags:
            self.nodes[camera].spot(tag, self.t)

    def _deliver_ok(self, src, dst):
        if self.wire_down or frozenset((src, dst)) in self.links_down:
            return False
        # flaky drops on send AND on receive (SPEC §8.3)
        if self.rng.random() < self.nodes[src].loss:
            return False
        if self.rng.random() < self.nodes[dst].loss:
            return False
        return True

    def step(self):
        self.t = round(self.t + TICK, 6)
        self.ticks += 1
        # 1. deliver what was sent last tick
        for n, node in self.nodes.items():
            msgs, self.inbox[n] = self.inbox[n], []
            for m in msgs:
                node.on_gossip(m, self.t)
        # 2-5. decide + work
        for node in self.nodes.values():
            node.tick(self.t)
        # 6. gossip
        every = int(round(GOSSIP_INTERVAL / TICK))
        for n, node in self.nodes.items():
            if (self.ticks + self.phase[n]) % every:
                continue
            for msg in node.gossip_messages():
                raw = json.dumps(msg, separators=(",", ":"))
                self.max_bytes = max(self.max_bytes, len(raw.encode()))
                for dst in node.gossip_targets():
                    if self._deliver_ok(n, dst):
                        self.inbox[dst].append(json.loads(raw))

    def run(self, seconds):
        for _ in range(int(round(seconds / TICK))):
            self.step()

    def run_until(self, cond, timeout):
        for _ in range(int(round(timeout / TICK))):
            if cond():
                return True
            self.step()
        return cond()

    # -- views ---------------------------------------------------------------
    def view(self, n):
        """What node n believes, normalised for comparison."""
        return json.dumps(self.nodes[n].jobs, sort_keys=True)

    def agree(self, names=None):
        names = names or list(self.nodes)
        return len(set(self.view(n) for n in names)) == 1

    def done_ids(self, n):
        return sorted(j for j, v in self.nodes[n].jobs.items() if v["done"])

    def all_done(self, n, ids):
        jobs = self.nodes[n].jobs
        return all(str(i) in jobs and jobs[str(i)]["done"] for i in ids)
