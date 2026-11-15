# Dashboard redesign: rescue timeline as the hero

Date: 2026-11-15 · Owner: Person B (`dashboard/`) · Status: awaiting approval

## Why

The dashboard is what the judges watch. Today it shows only the present moment: who owns what
right now. The story we want to tell is about time: a node picks up a rescue, a node dies, and
a few seconds later another node carries on from where it stopped. A takeover currently shows
only as a small "takeover ×1" tag, with no when and no from whom.

The redesign makes that story the centre of the page and gives the page a look worth demoing.

## Goals

- A timeline that shows, per node, which job it was working on and when.
- A takeover is unmistakable: which job, who dropped it, who picked it up, at what progress.
- It is visible that some devices are faster than others.
- Sleek, branded page: "Murmuration" wordmark with a small swarm logo.
- Readable from the back of a room on a projector.

## Non-goals

- No change to SPEC.md: message formats, ports, control commands and scenarios stay as they are.
- No files outside `dashboard/` are touched.
- No history saved to disk. A server restart starts a blank timeline.
- No zooming the timeline. One fixed scale.
- No libraries, CDNs or web fonts. The venue network may be down; the page must work offline.
- Phone layout is not a priority.

## Anti-goals (failure even if it "works")

- The page breaks or goes blank mid-demo. It must render with no nodes heard, with nodes that
  never report, and if the timeline data is missing.
- Any scenario button behaves differently from today.
- The four per-node belief columns disappear. "Each node has its own copy" is half the story.
- Two different jobs look like the same job because they share a colour.

## Approaches considered

1. **Server records the history (chosen).** `server.py` already receives every telemetry
   message. It watches each node's `current` job and builds the timeline, served in `/state`.
   Survives a page refresh, identical on every screen, unit-testable.
2. **Page records the history.** No server change, but a refresh wipes the timeline and two
   screens disagree. Rejected: a refresh mid-demo is too likely.
3. **Nodes report their own events.** Most precise, but changes the telemetry format in SPEC.md
   and Person A's nodes. Rejected: outside our folders and against the contract.

Timi handed the build choice over; option 1 is Claude's pick.

## What Timi decided

- The timeline is the hero of the page; the belief columns shrink to a compact strip.
- One lane per node (four fixed rows), not one lane per job.
- Colour means the job. A job that changes hands keeps its colour in both lanes.
- Job colours must be distinct and should not be reused.
- Each lane shows that node's speed.
- The timeline slides as time passes, and a scroll bar lets you go back through the round.
- Murmuration wordmark with a little swarm logo.

- The page uses a blue and black colour scheme.
- The control buttons look sleek and modern, stay the same set as today, and are worded in
  proper, consistent English.

## Look: blue and black

- Near-black page (`#04060a`), panels a shade lighter with thin dark-blue borders, one electric
  blue accent (`#4ea1ff`) for titles, icons, focus and the logo. Text is cool off-white.
- Blue and black is the chrome. Two things keep their own colours because they carry meaning:
  the twelve job colours on bars and chips, and green/amber/red/grey for node mode and link
  state.
- Reference mockup: `.superpowers/brainstorm/1994-1794709404/content/controls-blue.html`.

## Control panel

Same scenarios, same commands sent, same endpoints. Only the look, grouping and wording change.
The panel is titled "Simulate a failure" and groups the buttons by what they break.

| Today | New label | Group |
|---|---|---|
| WiFi down | Take Wi-Fi down | Network |
| Split webcam+unoq \| nicla+pi | Split the team (with "webcam + unoq \| nicla + pi" as a caption) | Network |
| Flaky 30% | Drop 30% of messages | Network |
| Cut link [x] – [y] · Cut | Cut the link between [x] and [y] · Cut link | Network |
| Cameras blind | Blind both cameras | Cameras |
| Heal all | Heal everything | Recover |
| Reset round | Reset the round | Recover |
| Crash / Revive (per node) | Crash this node / Revive this node | Belief strip |
| Blind camera / Unblind camera (per node) | Blind this camera / Restore this camera | Belief strip |

- Wording rule: every button is a verb followed by what it acts on, in sentence case.
- Buttons: dark fill, thin blue border, a small line icon, a soft blue glow on hover and
  keyboard focus. "Heal everything" is the one solid-blue button. "Reset the round" is quieter
  so it is not pressed by accident.
- The recent-events line and the timeline markers use matching wording with the node named,
  for example "Crashed unoq", "Cut the link between webcam and nicla", "Healed everything".
  This changes the text in `describe()` in `server.py` and the tests that check it.

## Page layout (top to bottom)

1. **Header.** Swarm logo, "Murmuration", tagline "four devices, no boss", and the existing
   "server unreachable" badge.
2. **Top row.** Network map on the left; headline, scoreboard tiles, scenario buttons and the
   recent-events line on the right. Same content and behaviour as today, restyled.
3. **Rescue timeline.** Full width. The hero.
4. **Belief strip.** Four compact columns, one per node: name, mode badge, peer chips,
   Crash/Revive and Blind/Unblind buttons, then one slim row per job (colour chip, `#id`, owner
   name, percent, thin progress bar). Sorted by severity as today.

Reference mockup of the timeline:
`.superpowers/brainstorm/1994-1794709404/content/timeline-v3.html`.

## The timeline

```
            ▼ Crash unoq                 ▼ Revive unoq        now
webcam  [ #1 ✓ ][ #5 ✓      ][#7 ← unoq][ #8 ✓     ][#11|          4
 ▇▇▇▇ 8 s each                ↑
nicla   [ #4 ✓                         ][ #9          |          1
 ▇▇   20 s each               │
unoq    [ #2 ✓ ][#7 ]┄ CRASHED · dropped #7 at 50% ┄[#10 |          1
 ▇▇▇▇ 8 s each
pi      [ #3 ✓       ][ #6 ✓             ][ #12       |          2
 ▇▇▇  12.5 s each
        0 s    5 s    10 s    15 s    20 s    25 s    30 s
        ◄══════════════ scroll bar ═══════════════════►  [Live]
```

- **Lanes.** One per node, in config order. The label shows the node name, its camera zone if
  it has one, a small speed bar (length relative to the fastest configured `speed`) and
  "N s each" (`1/speed`). The right edge shows how many rescues that node finished.
- **Bars.** One bar per stretch of work on one job, filled with the job's colour and labelled
  `#id`. A finished rescue gets a ✓. A bar the node left unfinished is faded with a dashed
  outline. The bar a node is working on right now grows with "now".
- **Takeover.** When a node starts a job another node had worked on and not finished, the bar
  is labelled `#7 ← unoq` and a curved arrow in the job's colour runs from the end of the old
  bar to the start of the new one. Hovering shows the progress it started from.
- **Outages.** While a node reports `crashed`, or has sent nothing for 2 s, its lane shows a
  dashed box reading "CRASHED" or "NO SIGNAL", plus "dropped #7 at 50%" if it was mid-job.
- **Markers.** Every scenario button press is a labelled vertical line.
- **Sliding and scrolling.** Fixed scale: 60 seconds fills the visible width. "Now" sits near
  the right edge and the view follows it. A horizontal scroll bar under the timeline covers the
  whole round. Scrolling back stops the following; scrolling to the right edge or clicking
  "Live" resumes it. Lane labels and rescue counts stay fixed while the bars scroll.

### Colour

- Twelve distinct job colours (first pass, as in the mockup): `#4c9aff #ff9838 #36d07f #f2555a
  #a98bff #ffd638 #ff7ac8 #1fc8e3 #b8794e #dfe5ec #a3b545 #b0527a`.
- Colours are handed out in the order jobs first appear in a round, not by tag number, so any
  twelve cards get twelve different colours. Reset round starts again.
- A 13th job in one round reuses the palette from the start; the `#id` on every bar tells them
  apart. Twelve matches the documented card deck (`make_tags.py`, tags 1–12).
- Nodes no longer have their own colours anywhere on the page. They are identified by name.
  The map's progress ring and the belief strip use the job's colour.
- Green/amber/red/grey keep their existing meaning for node mode and link state.

## How the server builds the history

All in `dashboard/server.py`, in memory, using the server's own monotonic clock (never a
node's clock). Round time `t` is seconds since server start or the last Reset round.

For each telemetry message from node N:

- N is **working on J** if its mode is not `crashed`, `current` is J, J is in its `jobs`, and
  J is not done there. Otherwise it is working on nothing.
- If that differs from N's open bar: close the open bar at `t` (outcome `done` if N's own job
  list shows that job done, else `left`, with the progress N last reported), then open a new
  bar for J with its starting progress.
- A new bar's `from` is the node of the most recent bar on J by a different node that did not
  end `done` (including one still open, which is the duplicate-effort case). Otherwise none.
- Mode `crashed` opens a "crashed" outage for N; any other mode closes it.

Revised after the first run on real devices (2026-11-15):

- `from` also requires that the new bar starts at about the old bar's progress (within 0.15).
  A node that starts the job from scratch never heard of the other's work, so that is
  duplicate effort and gets no takeover label. A finished bar is skipped when looking back.
- A silence is only lost telemetry. When the node is heard again and is still on the same job,
  its bar is rejoined; if it finished the job meanwhile, the bar is marked done.
- A node that comes back from silence reporting `crashed` switches to a "crashed" outage.
- The page draws a takeover arrow only when the pickup is within 15 s of the drop.

Also, whenever state is requested: any node silent for more than `STALE_AFTER` (2 s) has its
open bar closed as `left` at the time it was last heard, and a "no signal" outage opened from
then. The next telemetry from that node closes the outage.

Reset round clears bars, outages, markers and colour assignments and restarts `t`. Telemetry
is ignored for the timeline during the existing 1.5 s quiet period after reset.

`/state` gains one key; nothing existing changes:

```json
"timeline": {
  "now": 30.2,
  "segments": [{"node": "webcam", "job": "7", "start": 16.0, "end": 20.1,
                "p0": 0.5, "p1": 1.0, "outcome": "done", "from": "unoq"}],
  "outages":  [{"node": "unoq", "kind": "crashed", "start": 12.0, "end": 24.0}],
  "markers":  [{"t": 12.0, "text": "Crash unoq"}],
  "colors":   {"1": 0, "2": 1, "7": 6}
}
```

An open bar or outage has `"end": null` and `outcome: "live"`. The server keeps the most recent
600 bars and drops older ones.

Accuracy: telemetry arrives every 0.5 s, so bar edges are accurate to about half a second and a
claim held for less than that may not appear. That is fine for a demo.

## Constraints

- Python standard library only in `server.py`; one self-contained `index.html`, inline SVG.
- Build on the current working tree: `server.py` and `index.html` have uncommitted changes.
- Text no smaller than 12 px; lane names 15 px or more.
- The page polls `/state` every 300 ms as today and redraws the timeline each time.

## Testing

- Unit tests in `dashboard/test_server.py`, driving `on_telemetry` with a controlled clock:
  pickup opens a bar; finishing closes it `done`; crash then another node's pickup gives
  `from` and the right starting progress; silence gives a "no signal" outage; two nodes on one
  job give two open bars; reset clears everything; colours follow first appearance.
- By hand: run `server.py` and `fake_telemetry.py` on `config.local.json`, crash a node
  mid-rescue, and check the faded bar, the arrow and the label. Scroll back, then return to
  live. Refresh the page and confirm the history is still there. Run every scenario button.

## Left to the builder

- Exact styling, spacing and type; swapping any two palette colours that look too close.
- The logo's geometry (a small flock of dart shapes in an arc), also used as the favicon.
- How marker labels avoid overlapping each other, and how the arrow routes across lanes.
- How the clock is made controllable for tests.

## Claude's calls (not yet confirmed by Timi)

- 60 seconds visible at once.
- "Live" button and follow/stop-following behaviour.
- Node colours removed page-wide in favour of names.
- A 13th job in a round reuses a colour.
- Rescue count per node on the right edge of each lane.
- The exact new button labels, the three groups, and the past-tense wording in the event log.
- Job colours and status colours stay multi-coloured inside the blue and black scheme.
- Destructive buttons are blue like the rest, not red; grouping tells them apart.
- Design doc lives in `dashboard/docs/` (inside our own folder) and is not committed yet.
