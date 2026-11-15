# Dashboard: slim node strip

Date: 2026-11-15 · Owner: Person B (`dashboard/`) · Status: approved and built

## Why

The four node boxes at the bottom of the page are too full. Most of what they hold is already
shown elsewhere: status on the network graph, guess rate and current job on the timeline.

## Goal

Replace the four boxes with one compact "Nodes" panel: a single line per node.

## Non-goals and anti-goals

- The rescue timeline and the network graph do not change.
- The score tiles, the "Simulate a failure" panel and the decoded-messages strip do not change.
- No change to `server.py`, `/state`, SPEC.md or any file outside `dashboard/index.html`.
- Failure if: a crash or blind button stops working, or the page breaks when a node has never
  been heard from.

## Approaches considered

- A. Fold the nodes into the timeline rows. Removes the section entirely, page fits one screen.
- B. Fold the nodes into the graph (PIN under each circle, click to crash).
- **C. Keep a bottom strip, one slim line per node (chosen by Timi).** Smallest change; the page
  keeps a fourth section but it becomes short.

## Design

One panel titled "Nodes". One line per node, in config order:

```
● webcam  camera A    24119  for #0 Maria      3,000 guesses/s          [Crash] [Blind camera]
● nicla   camera B     5471  for #7 Ella         300 guesses/s          [Crash] [Blind camera]
○ unoq                CRASHED                  1,500 guesses/s          [Revive]
● pi                  IDLE                       600 guesses/s          [Crash]
```

- **Status dot** in the node's mode colour (green connected, amber isolated, red alone, grey
  crashed or no signal), with the mode as hover text. It replaces the coloured badge and the
  coloured top border.
- **Name and camera zone.**
- **What it is doing.** Decode mode: the PIN being tried in large digits and the card it is for,
  or `IDLE`, `CRASHED`, `NO SIGNAL`, `COUNTER MODE`. Counter mode: the current job and its
  percent, or `IDLE`.
- **Guess rate** (decode mode only).
- **Notes**, only when there is something to say: "camera blind", "last heard N s ago",
  "never heard from this node".
- **Buttons**, right-aligned, same commands as today: Crash / Revive, and Blind camera /
  Restore camera for the two camera nodes.

Removed from the page: the per-node job rows, the "waiting" chips and the "nothing left to do"
line. Jobs are shown by the timeline, the graph rings and the score tiles.

## Added on approval: click a link on the graph

Timi asked for this when approving. Clicking a link on the network graph cuts it; clicking a
cut link puts it back. Cutting uses the existing `cut` scenario. Restoring needs a new dashboard
scenario, `uncut` (x, y), which sends the SPEC 8.3 `unblock` command to both ends, so
`server.py` does change after all (no protocol change). The "Cut the link between" row in the
failure panel stays.

## Claude's calls (approved by Timi, 2026-11-15)

- Two lines per row on wide screens (webcam and nicla, then unoq and pi, matching the graph),
  one per row on narrow screens. Four full-width lines would be mostly empty space.
- Unclaimed jobs are no longer listed anywhere by number. "N still trapped" on the score tile
  is the only sign of a card nobody has picked up yet.
- Button labels shortened to "Crash", "Revive", "Blind camera", "Restore camera" to fit a line.

## Testing

By screenshot against a local decode round (cards spotted, one node crashed, one never heard),
and by pressing each button and checking the command reaches the node. The existing dashboard
tests must still pass; they do not cover the page itself.
