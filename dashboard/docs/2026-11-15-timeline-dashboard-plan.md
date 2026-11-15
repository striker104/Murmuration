# Timeline Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dashboard tell the rescue story over time: a per-node timeline with job-coloured bars and visible takeovers, in a blue and black look with a swarm logo and reworded control buttons.

**Architecture:** A new pure-logic `Timeline` class turns successive telemetry messages into bars, outages, markers and colour assignments. `server.py` feeds it using its own monotonic clock and serves the result under a new `timeline` key in `/state`. `index.html` stays one self-contained file and draws the timeline as inline SVG inside a horizontally scrolling container.

**Tech Stack:** Python 3 standard library, `unittest`, plain HTML/CSS/JS with inline SVG. No libraries, CDNs or web fonts.

**Spec:** `dashboard/docs/2026-11-15-timeline-dashboard-design.md` (read it first; the timeline mockups it references show the intended look).

## Global Constraints

- Do not change SPEC.md, message formats, ports, control commands, or what any scenario sends.
- Only touch files under `dashboard/`.
- `server.py` and `timeline.py`: Python standard library only.
- `index.html`: one self-contained file; no external requests of any kind.
- Never use a node's clock. All times are the server's monotonic clock, as seconds since the round started.
- History is in memory only.
- Text no smaller than 12 px; lane names 15 px or more.
- The page must render with no nodes heard, with nodes that never report, and with `timeline` missing from `/state`.
- The four per-node belief columns stay on the page.
- `server.py` and `index.html` already have uncommitted (staged) changes. Build on the working tree as it is; those changes will ride along in the first commit that touches each file.
- Run tests from the repo root: `python -m unittest dashboard.test_timeline dashboard.test_server -v`.

## Review Focus

1. **Malformed telemetry** (a job that is not a dict, missing `progress`, `current` naming an unknown job): no exception, no bar. Test in Task 1.
2. **An exception inside the timeline code** must not kill the telemetry listener thread or drop the message from `latest`; the rest of the dashboard carries on. Test in Task 2.
3. **Repeated identical states** (a node sending `crashed` every 0.5 s, `silent()` called on every `/state` poll): exactly one outage, not one per call. Tests in Task 1.
4. **Reset with work in flight** (open bars, a node silent since before the reset, stale packets just after): nothing from the old round appears, no negative times, colours restart at 0. Tests in Tasks 1 and 2.
5. **Long rounds and many jobs** (more than 600 bars, more than 12 jobs, a takeover whose source bar was dropped): oldest bars dropped, colour index wraps on the page, a missing arrow source just means no arrow. Test for the cap in Task 1; page behaviour in Task 4's checklist.

---

### Task 1: `Timeline` history recorder

**Files:**
- Create: `dashboard/timeline.py`
- Test: `dashboard/test_timeline.py`

**Interfaces:**
- Consumes: nothing.
- Produces (used by Task 2):

```python
class Timeline:
    MAX_SEGMENTS = 600
    def __init__(self, names): ...            # names: list of node names
    def on_telemetry(self, msg, t): ...       # msg: SPEC 8.2 telemetry dict; t: round seconds (float)
    def silent(self, node, since): ...        # node has sent nothing since round time `since`
    def mark(self, text, t): ...              # a scenario button was pressed
    def reset(self): ...                      # new round: forget everything
    def snapshot(self, t): ...                # -> {"now": t, "segments": [...], "outages": [...],
                                              #     "markers": [...], "colors": {...}}
```

Shapes inside `snapshot` (exact keys):
- segment: `{"node", "job", "start", "end", "p0", "p1", "outcome", "from"}`; `end` is `None` and `outcome` is `"live"` while open; closed outcome is `"done"` or `"left"`; `from` is a node name or `None`.
- outage: `{"node", "kind", "start", "end"}`; `kind` is `"crashed"` or `"no_signal"`; `end` is `None` while open.
- marker: `{"t", "text"}`.
- colors: `{job_id: index}`, indexes 0, 1, 2… in order of first appearance.

Rules (from the spec, restated so this task stands alone):
- Node N is working on J when `mode != "crashed"`, `current` is J (compare as `str`), J is a dict in N's `jobs`, and that job is not `done`. Otherwise it is working on nothing.
- When that differs from N's open segment: close the open one at `t` (`outcome` = `"done"` if N's own `jobs` shows that job done, else `"left"`; `p1` = the progress N reports for it now, or the last `p1` if the job is gone), then open one for J with `p0` = `p1` = N's reported progress (0.0 if missing or not a number).
- While a segment stays open, each message updates its `p1`.
- `from` = node of the most recently started segment on J by a different node whose outcome is not `"done"` (open ones count). Otherwise `None`.
- `mode == "crashed"`: make sure a `crashed` outage is open for N. Any other mode: close N's open outage at `t`.
- `silent(node, since)`: close N's open segment at `since` as `"left"`; if N's open outage is not `no_signal`, close it at `since` and open a `no_signal` one starting at `since`. Calling it again changes nothing.
- Colours: for every job id in a message, in `(sev, id)` order, assign the next index if it has none.
- Keep only the newest `MAX_SEGMENTS` segments.
- Never raise on bad input: non-dict jobs are skipped, non-numeric progress counts as 0.0.

- [ ] **Step 1: Write the failing tests** in `dashboard/test_timeline.py`

```python
"""Timeline history: bars, takeovers, outages, colours.

    python -m unittest dashboard.test_timeline -v      (from the repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timeline  # noqa: E402

ALL = ["webcam", "nicla", "unoq", "pi"]


def tele(node, current, jobs, mode="connected"):
    """jobs: {id: progress}; progress 1.0 means done."""
    return {"type": "telemetry", "node": node, "mode": mode, "current": current,
            "jobs": {str(i): {"id": str(i), "sev": i, "progress": p, "done": p >= 1.0} for i, p in jobs.items()}}


class Bars(unittest.TestCase):
    def setUp(self):
        self.tl = timeline.Timeline(ALL)

    def segs(self, t=99.0):
        return self.tl.snapshot(t)["segments"]

    def test_pickup_then_finish(self):
        self.tl.on_telemetry(tele("pi", "3", {3: 0.0}), 1.0)
        self.tl.on_telemetry(tele("pi", "3", {3: 0.5}), 5.0)
        self.assertEqual(self.segs(), [{"node": "pi", "job": "3", "start": 1.0, "end": None,
                                        "p0": 0.0, "p1": 0.5, "outcome": "live", "from": None}])
        self.tl.on_telemetry(tele("pi", None, {3: 1.0}), 9.0)
        self.assertEqual(self.segs(), [{"node": "pi", "job": "3", "start": 1.0, "end": 9.0,
                                        "p0": 0.0, "p1": 1.0, "outcome": "done", "from": None}])

    def test_current_still_set_when_done(self):
        self.tl.on_telemetry(tele("pi", "3", {3: 0.9}), 1.0)
        self.tl.on_telemetry(tele("pi", "3", {3: 1.0}), 2.0)
        self.assertEqual([(s["end"], s["outcome"]) for s in self.segs()], [(2.0, "done")])

    def test_crash_then_takeover(self):
        self.tl.on_telemetry(tele("unoq", "7", {7: 0.1}), 8.0)
        self.tl.on_telemetry(tele("unoq", "7", {7: 0.5}, mode="crashed"), 12.0)
        self.tl.on_telemetry(tele("unoq", "7", {7: 0.5}, mode="crashed"), 12.5)
        self.tl.on_telemetry(tele("webcam", "7", {7: 0.5}), 16.0)
        snap = self.tl.snapshot(17.0)
        old, new = snap["segments"]
        self.assertEqual((old["end"], old["outcome"], old["p1"]), (12.0, "left", 0.5))
        self.assertEqual((new["node"], new["from"], new["p0"], new["outcome"]), ("webcam", "unoq", 0.5, "live"))
        self.assertEqual(snap["outages"], [{"node": "unoq", "kind": "crashed", "start": 12.0, "end": None}])
        self.tl.on_telemetry(tele("unoq", None, {7: 0.5}), 24.0)
        self.assertEqual(self.tl.snapshot(25.0)["outages"][0]["end"], 24.0)

    def test_no_from_after_a_finished_bar(self):
        self.tl.on_telemetry(tele("pi", "3", {3: 0.5}), 1.0)
        self.tl.on_telemetry(tele("pi", None, {3: 1.0}), 2.0)
        self.tl.on_telemetry(tele("webcam", "3", {3: 0.2}), 3.0)      # webcam has a stale view
        self.assertIsNone(self.segs()[1]["from"])

    def test_two_nodes_on_one_job(self):
        self.tl.on_telemetry(tele("pi", "5", {5: 0.3}), 1.0)
        self.tl.on_telemetry(tele("nicla", "5", {5: 0.1}), 2.0)
        a, b = self.segs()
        self.assertEqual((a["outcome"], b["outcome"], b["from"]), ("live", "live", "pi"))

    def test_silent_node(self):
        self.tl.on_telemetry(tele("pi", "3", {3: 0.2}), 1.0)
        self.tl.silent("pi", 1.0)
        self.tl.silent("pi", 1.0)
        snap = self.tl.snapshot(5.0)
        self.assertEqual((snap["segments"][0]["end"], snap["segments"][0]["outcome"]), (1.0, "left"))
        self.assertEqual(snap["outages"], [{"node": "pi", "kind": "no_signal", "start": 1.0, "end": None}])
        self.tl.on_telemetry(tele("pi", "3", {3: 0.2}), 6.0)
        snap = self.tl.snapshot(7.0)
        self.assertEqual(snap["outages"][0]["end"], 6.0)
        self.assertEqual(len(snap["segments"]), 2)

    def test_colours_follow_first_appearance(self):
        self.tl.on_telemetry(tele("pi", None, {9: 0.0, 4: 0.0}), 1.0)
        self.tl.on_telemetry(tele("unoq", None, {2: 0.0, 9: 0.0}), 2.0)
        self.assertEqual(self.tl.snapshot(3.0)["colors"], {"4": 0, "9": 1, "2": 2})

    def test_keeps_only_newest_segments(self):
        self.tl.MAX_SEGMENTS = 3
        for i in range(1, 6):
            self.tl.on_telemetry(tele("pi", str(i), {i: 0.0}), float(i))
        self.assertEqual([s["job"] for s in self.segs()], ["3", "4", "5"])

    def test_markers_and_reset(self):
        self.tl.on_telemetry(tele("pi", "3", {3: 0.2}, mode="crashed"), 1.0)
        self.tl.mark("Crashed pi", 1.0)
        self.assertEqual(self.tl.snapshot(2.0)["markers"], [{"t": 1.0, "text": "Crashed pi"}])
        self.tl.reset()
        self.assertEqual(self.tl.snapshot(0.0),
                         {"now": 0.0, "segments": [], "outages": [], "markers": [], "colors": {}})

    def test_malformed_telemetry_never_raises(self):
        bad = {"type": "telemetry", "node": "pi", "mode": "connected", "current": "1",
               "jobs": {"1": "junk", "2": {"id": "2"}}}
        self.tl.on_telemetry(bad, 1.0)
        self.assertEqual(self.segs(), [])
        self.tl.on_telemetry(dict(bad, current="99"), 2.0)
        self.assertEqual(self.segs(), [])
        self.tl.on_telemetry(dict(bad, current=2), 3.0)                # int id, no progress
        self.assertEqual((self.segs()[0]["job"], self.segs()[0]["p0"]), ("2", 0.0))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest dashboard.test_timeline -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'timeline'`

- [ ] **Step 3: Implement `Timeline` in `dashboard/timeline.py`**

Follow the Interfaces block and the rules above. Keep one open segment and one open outage per node in dicts keyed by node name; `snapshot` returns copies so the caller can serialise them outside the lock. Start the file with a short docstring saying it is pure logic (no sockets, no clock) and pointing at the spec.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m unittest dashboard.test_timeline -v`
Expected: 10 tests, OK

- [ ] **Step 5: Commit**

```bash
git add dashboard/timeline.py dashboard/test_timeline.py
git commit -m "dashboard: record rescue timeline from telemetry"
```

---

### Task 2: Wire the timeline into the server, reword events

**Files:**
- Modify: `dashboard/server.py` (`describe`, `Dashboard.__init__`, `on_telemetry`, `run_scenario`, `state`)
- Test: `dashboard/test_server.py`

**Interfaces:**
- Consumes: `timeline.Timeline` from Task 1 (`import timeline`, same folder).
- Produces (used by Tasks 3 and 4): `GET /state` gains `"timeline"` with the Task 1 `snapshot` shape. Every existing key is unchanged. `state["events"][i]["text"]` uses the new wording below.

Decisions:
- `Dashboard.__init__(self, cfg, clock=time.monotonic)`; every `time.monotonic()` inside the class becomes `self.clock()`.
- `self.round_start` = clock at construction, and again at `reset`. Round time = `now - self.round_start`.
- In `on_telemetry`, after `latest`/`heard_at` are stored and only when `now >= self.score_quiet_until`, call `self.timeline.on_telemetry(msg, now - self.round_start)` wrapped in `try/except Exception: pass`.
- In `state()`, for each node that has been heard and is stale, call `self.timeline.silent(n, max(0.0, self.heard_at[n] - self.round_start))`, then add `"timeline": self.timeline.snapshot(now - self.round_start)`. Wrap both in `try/except Exception` and fall back to `"timeline": None`.
- In `run_scenario`: on `reset`, call `self.timeline.reset()` and set `self.round_start = now`; for every other scenario call `self.timeline.mark(describe(name, params), now - self.round_start)`.
- `describe()` returns exactly:

| scenario | text |
|---|---|
| `heal` | `Healed everything` |
| `wifi_down` | `Took Wi-Fi down` |
| `split` | `Split the team` |
| `cameras_blind` | `Blinded both cameras` |
| `reset` | `Reset the round` |
| `cut` (x, y) | `Cut the link between x and y` |
| `blind` (x) | `Blinded x's camera` |
| `unblind` (x) | `Restored x's camera` |
| `crash` (x) | `Crashed x` |
| `revive` (x) | `Revived x` |
| `flaky` (loss 0.3) | `Started dropping 30% of messages` |

- [ ] **Step 1: Write the failing tests** (append to `dashboard/test_server.py`, before the `__main__` block)

```python
class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def tele(node, current, jobs, mode="connected"):
    return {"type": "telemetry", "node": node, "mode": mode, "current": current,
            "jobs": {str(i): {"id": str(i), "sev": i, "progress": p, "done": p >= 1.0, "workers": []}
                     for i, p in jobs.items()}}


class TimelineInState(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.dash = server.Dashboard(CFG, clock=self.clock)
        self.addCleanup(self.dash.ctl_sock.close)

    def test_bars_use_round_time(self):
        self.clock.t = 103.0
        self.dash.on_telemetry(tele("pi", "3", {3: 0.2}))
        self.clock.t = 104.0
        tl = self.dash.state()["timeline"]
        self.assertEqual(tl["now"], 4.0)
        self.assertEqual([(s["node"], s["job"], s["start"], s["outcome"]) for s in tl["segments"]],
                         [("pi", "3", 3.0, "live")])
        self.assertEqual(tl["colors"], {"3": 0})

    def test_silence_becomes_no_signal(self):
        self.clock.t = 101.0
        self.dash.on_telemetry(tele("pi", "3", {3: 0.2}))
        self.clock.t = 101.0 + server.STALE_AFTER + 0.5
        tl = self.dash.state()["timeline"]
        self.assertEqual(tl["segments"][0]["outcome"], "left")
        self.assertEqual(tl["outages"], [{"node": "pi", "kind": "no_signal", "start": 1.0, "end": None}])

    def test_scenario_adds_marker_with_new_wording(self):
        self.clock.t = 112.0
        self.dash.run_scenario("crash", {"x": "unoq"})
        st = self.dash.state()
        self.assertEqual(st["timeline"]["markers"], [{"t": 12.0, "text": "Crashed unoq"}])
        self.assertEqual(st["events"][-1]["text"], "Crashed unoq")

    def test_reset_starts_a_new_round(self):
        self.clock.t = 105.0
        self.dash.on_telemetry(tele("pi", "3", {3: 0.2}))
        self.clock.t = 130.0
        self.dash.run_scenario("reset", {})
        self.clock.t = 130.5
        self.dash.on_telemetry(tele("pi", "3", {3: 0.3}))          # stale packet in the quiet period
        self.clock.t = 134.0                                        # pi has been silent since before reset
        tl = self.dash.state()["timeline"]
        self.assertEqual(tl["now"], 4.0)
        self.assertEqual((tl["segments"], tl["markers"], tl["colors"]), ([], [], {}))
        self.assertTrue(all(o["start"] >= 0 for o in tl["outages"]))

    def test_timeline_failure_does_not_break_the_dashboard(self):
        def boom(*args):
            raise RuntimeError("bug")
        self.dash.timeline.on_telemetry = boom
        self.dash.timeline.snapshot = boom
        self.dash.on_telemetry(tele("pi", "3", {3: 0.2}))
        st = self.dash.state()
        self.assertIsNone(st["timeline"])
        self.assertEqual(st["nodes"]["pi"]["telemetry"]["current"], "3")


class EventWording(unittest.TestCase):
    def test_describe(self):
        cases = [("heal", {}, "Healed everything"), ("wifi_down", {}, "Took Wi-Fi down"),
                 ("split", {}, "Split the team"), ("cameras_blind", {}, "Blinded both cameras"),
                 ("reset", {}, "Reset the round"),
                 ("cut", {"x": "webcam", "y": "nicla"}, "Cut the link between webcam and nicla"),
                 ("blind", {"x": "nicla"}, "Blinded nicla's camera"),
                 ("unblind", {"x": "nicla"}, "Restored nicla's camera"),
                 ("crash", {"x": "pi"}, "Crashed pi"), ("revive", {"x": "pi"}, "Revived pi"),
                 ("flaky", {}, "Started dropping 30% of messages")]
        for name, params, text in cases:
            self.assertEqual(server.describe(name, params), text)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest dashboard.test_server -v`
Expected: the new tests FAIL (first error: `Dashboard.__init__() got an unexpected keyword argument 'clock'`); the existing 7 still pass.

- [ ] **Step 3: Implement the changes in `dashboard/server.py`** per the Decisions list. Update the module docstring to mention the timeline.

- [ ] **Step 4: Run all dashboard tests**

Run: `python -m unittest dashboard.test_timeline dashboard.test_server -v`
Expected: 23 tests, OK

- [ ] **Step 5: Check it live**

Run in two terminals from the repo root: `python dashboard/server.py --config config.local.json` and `python dashboard/fake_telemetry.py`. Then: `curl -s http://127.0.0.1:8000/state | python -c "import json,sys; t=json.load(sys.stdin)['timeline']; print(len(t['segments']), t['colors'])"`
Expected after about 10 s: a segment count above 0 and a non-empty colour map. The existing page at http://127.0.0.1:8000 still works.

- [ ] **Step 6: Commit**

```bash
git add dashboard/server.py dashboard/test_server.py
git commit -m "dashboard: serve timeline in /state, reword scenario events"
```

---

### Task 3: Blue and black look, logo, control panel, job colours

**Files:**
- Modify: `dashboard/index.html`

**Interfaces:**
- Consumes: `state.timeline.colors` from Task 2 (may be missing or `timeline` may be `null`).
- Produces (used by Task 4):
  - `const JOB_COLORS` — the 12 hex strings below, in this order.
  - `function jobColor(state, id)` — returns `JOB_COLORS[index % 12]`, or `"var(--dead)"` when the job has no index or `state.timeline` is missing.
  - An empty `<section id="timeline" class="panel"></section>` between the top row and the belief strip.
  - CSS variables `--bg`, `--panel`, `--panel2`, `--line`, `--text`, `--dim`, `--accent`, plus the existing `--ok`, `--warn`, `--bad`, `--dead`.

Decisions:
- Colours: `--bg: #04060a`, `--panel: #0a101b`, `--panel2: #070b13`, `--line: #142238`, `--text: #e6edf7`, `--dim: #6f8099`, `--accent: #4ea1ff`. Button border `#1d3558`. `--ok/--warn/--bad/--dead` keep their current values.
- `JOB_COLORS = ["#4c9aff","#ff9838","#36d07f","#f2555a","#a98bff","#ffd638","#ff7ac8","#1fc8e3","#b8794e","#dfe5ec","#a3b545","#b0527a"]`.
- Remove `--n0`…`--n3`, `COLORS` and the per-node `color` map. Nodes are identified by name only.
- Header: inline SVG swarm logo (a small flock of dart shapes in an arc, in `--accent` and lighter blues; see the mockup `controls-blue.html`), "Murmuration", tagline "four devices, no boss", the existing `#offline` badge. Use the same logo as the favicon via a `data:image/svg+xml` `<link rel="icon">`.
- Control panel titled "Simulate a failure", three groups. Same `data-s` values, element ids and click handlers as today; only labels, grouping and style change:

| Group | Label | Wiring (unchanged) |
|---|---|---|
| Network | Take Wi-Fi down | `data-s="wifi_down"` |
| Network | Split the team, with the two halves as a caption (`webcam + unoq \| nicla + pi`, built from `state.split`) | `data-s="split"`, `id="b-split"` |
| Network | Drop 30% of messages | `data-s="flaky"` |
| Network | "Cut the link between" `#cut-x` "and" `#cut-y`, button "Cut link" | `id="b-cut"` |
| Cameras | Blind both cameras | `data-s="cameras_blind"` |
| Recover | Heal everything (the one solid-blue button) | `data-s="heal"` |
| Recover | Reset the round (quiet, outlined) | `data-s="reset"` |

- Per-node buttons in the belief strip: "Crash this node" / "Revive this node", "Blind this camera" / "Restore this camera". Same scenario names as today.
- Buttons: dark fill, 1 px blue border, small inline line icon, soft blue glow on `:hover` and `:focus-visible`. No red buttons.
- Network map: the progress ring's stroke is `jobColor(state, t.current)`, set on each render.
- Belief strip: the four columns stay, more compact. Each job is one slim row: a colour chip in `jobColor`, `#id`, owner name in plain text (or "unclaimed" / "✓ rescued"), percent, and a thin progress bar in `jobColor`. Keep the takeover and workers tags. Same severity sort as today.
- Page order: header, top row (map | headline + scoreboard + control panel + events), `#timeline`, belief strip.
- Scoreboard tiles, headline logic, link classes and the 300 ms poll are unchanged apart from styling.

- [ ] **Step 1: Restyle and restructure `dashboard/index.html`** per the Decisions.

- [ ] **Step 2: Check the wiring did not change**

Run: `python -m unittest dashboard.test_server -v` (expected: OK), then with the server and `fake_telemetry.py` running:
`curl -s http://127.0.0.1:8000/ | grep -c -E 'data-s="(heal|wifi_down|split|cameras_blind|flaky|reset)"'`
Expected: `6`

- [ ] **Step 3: Check by eye at http://127.0.0.1:8000**

- Logo, wordmark and favicon show; the page is blue and black.
- Every button in the table is present with the exact label and does what it did before (press each one; watch the map and the events line).
- Per-node buttons flip between Crash/Revive and Blind/Restore wording.
- Each job has the same colour in all four belief columns and on the map ring; different jobs have different colours.
- Stop `fake_telemetry.py`: columns go to NO SIGNAL, nothing breaks. Stop the server: the "unreachable" badge shows.
- No errors in the browser console.

- [ ] **Step 4: Commit**

```bash
git add dashboard/index.html
git commit -m "dashboard: blue and black look, swarm logo, reworded controls, job colours"
```

---

### Task 4: The timeline on the page

**Files:**
- Modify: `dashboard/index.html` (fill `#timeline`, add `renderTimeline(state)` and call it from `render`)

**Interfaces:**
- Consumes: `state.timeline` (Task 2 shape), `state.order`, `state.nodes[name].zone` / `.speed`, `jobColor(state, id)` and `#timeline` from Task 3.
- Produces: nothing for later tasks.

Decisions:
- Structure inside `#timeline`: a title row ("Rescue timeline" on the left, a `#tl-live` button "Live" on the right, hidden while following), then a three-column grid: `#tl-labels` (fixed, about 170 px) | `#tl-scroll` (`overflow-x: auto`, containing `<svg id="tl-svg">`) | `#tl-counts` (fixed, about 60 px, header "RESCUED"). Then a one-line key: one colour per job · ✓ rescued · faded bar = left unfinished · arrow = who picked it up.
- Constants: `WINDOW_S = 60` (seconds visible), `LANE_H = 58`, `BAR_H = 32`, `TOP = 30` (room for marker labels), `AXIS_H = 22`.
- Scale: `pps = scroll.clientWidth / WINDOW_S`; `x(t) = t * pps`; SVG width = `max(scroll.clientWidth, (now + 3) * pps)`; SVG height = `TOP + LANE_H * order.length + AXIS_H`. Set width and height in pixels (no `viewBox` scaling) so text stays a fixed size.
- Lanes in `state.order`. Label: node name (15 px bold), "camera A/B" if it has a zone, a speed bar whose length is `speed / max(speed)` (green at 1, amber above 0.5, red otherwise), and `(1/speed)` rounded to one decimal followed by " s each" (8.3, 20, 8.3, 12.5 with the current config).
- Count column: number of that node's segments with `outcome === "done"`.
- Bars: rect from `x(start)` to `x(end ?? now)`, filled `jobColor(state, job)`. `done`: label `#id ✓`. `left`: 38% opacity plus a dashed outline in the job colour, label `#id`. `live`: label `#id` and a white 4 px cap on the right edge. With `from` set: label `#id ← from`. Draw the label only if the bar is at least 34 px wide; fall back to plain `#id` if the long label does not fit (under 90 px). Label text is dark (`#04060a`), 12.5 px bold.
- Every bar has a `<title>`: `#7 · webcam · 16.0–20.1 s · 50% → 100%`, plus ` · took over from unoq` when `from` is set.
- Takeover arrow: for a segment with `from`, find the latest-starting earlier segment with the same job and `node === from`. If found, draw a curved path in the job colour (2.5 px, with a dark 6 px underlay) from that bar's right end to this bar's left end, with a small arrowhead. If not found, draw nothing.
- Outages: dashed rounded box across the lane from `x(start)` to `x(end ?? now)`, text `CRASHED` or `NO SIGNAL`. If one of that node's `left` segments ends within 0.01 s of the outage start, append ` · dropped #J at P%` (P from that segment's `p1`). Hide the text if the box is narrower than the text.
- Markers: dashed vertical line through all lanes with `▼ text` above the lanes in `--accent`, 12 px. If a marker's label would overlap the previous one, drop it to a second row.
- Axis: a faint grid line and `N s` label every 5 s. "Now" is a solid light line with the label "now".
- Following: a `following` flag, true at start. On the scroll container's `scroll` event set `following = scrollLeft + clientWidth >= scrollWidth - 24`. After each render, if `following`, set `scrollLeft = scrollWidth`. `#tl-live` is visible only when not following; clicking it sets `following = true` and scrolls to the right edge. Style the scroll bar thin, in the blue scheme.
- Rebuild the SVG contents on every poll. If `state.timeline` is missing or `null`, show "timeline unavailable" in `#timeline` and leave the rest of the page working.
- Empty round (no segments): lanes, labels, axis and "now" still draw.

- [ ] **Step 1: Implement the timeline** per the Decisions.

- [ ] **Step 2: Check by eye** with `python dashboard/server.py --config config.local.json` and `python dashboard/fake_telemetry.py` running, at http://127.0.0.1:8000

- Bars appear in the right lanes as nodes pick up jobs, grow with "now", and get a ✓ when rescued. Slower nodes have visibly longer bars and their speed bar and "s each" match the config.
- Press "Crash this node" on a node that is mid-rescue: its bar fades, a CRASHED box opens with "dropped #J at P%", a marker "Crashed <node>" appears, and about 4 s later another lane starts a bar in the same colour labelled `#J ← <node>` with an arrow from the old bar. Hover shows the starting progress.
- Press "Revive this node": the CRASHED box closes.
- Press "Take Wi-Fi down", wait 20 s, then "Heal everything": markers appear and any job worked by two nodes shows the same colour in two lanes.
- After 60 s the view slides and stays on "now". Drag the scroll bar left: it stops following and "Live" appears; click "Live" and it returns. Labels and counts do not move while scrolling.
- Refresh the page: the history is still there.
- Press "Reset the round": the timeline clears and the clock restarts from 0.
- Stop `fake_telemetry.py`: after 2 s every lane shows NO SIGNAL. Restart it: the boxes close.
- Resize the window narrower and wider: the timeline still fits and scrolls.
- Run `python dashboard/fake_telemetry.py --cards 14` after a reset: 14 jobs appear, the 13th and 14th reuse the first two colours, nothing breaks.
- No errors in the browser console throughout.

- [ ] **Step 3: Run all tests once more**

Run: `python -m unittest dashboard.test_timeline dashboard.test_server -v`
Expected: 23 tests, OK

- [ ] **Step 4: Commit**

```bash
git add dashboard/index.html
git commit -m "dashboard: rescue timeline with takeovers, outages and scroll-back"
```
