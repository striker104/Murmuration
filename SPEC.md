# Murmuration — Build Spec (the contract)

> Both teammates build against THIS file. Do not change the message formats, ports,
> merge rules or control commands without telling the other person first.
> Everything else (code style, internals, UI looks) is up to whoever owns that part.

---

## 0. What we're building (one paragraph)

Four devices ("nodes") rescue "trapped people" (AprilTag cards on a table) with **no boss**.
Two nodes can see (webcam laptop = zone A, Nicla = zone B). All four can do rescue jobs.
Every node keeps its own copy of the job list and shares it with whoever it can reach.
Laptop 2 is **not a node**: it only displays what each node believes and injects failures
(cut links, WiFi down, blind cameras, crash a node) so the judges can watch the team survive.

---

## 1. Who owns what

| Person | Devices | Owns (folders) |
|---|---|---|
| **A (you)** | Webcam laptop, UNO Q, Pi Zero W | `core/`, `adapters/webcam/`, `adapters/unoq/`, `adapters/pi/`, `sim/`, `tests/` |
| **B (friend)** | Laptop 2, Nicla Vision | `dashboard/`, `nicla/` |
| Both | — | `SPEC.md`, `config.json`, `config.local.json` |

---

## 2. Repo layout

```
murmuration/
  SPEC.md                 # this file
  CLAUDE.md               # tells Claude Code to follow SPEC.md
  config.json             # real devices (real IPs)
  config.local.json       # everything on 127.0.0.1 for testing on one laptop
  core/
    node.py               # PURE LOGIC: state, merge, decide, step. No sockets. Unit-testable.
    net.py                # UDP: gossip send/recv, telemetry send, control listener, block filter
    run_node.py           # CLI: python -m core.run_node --name pi --config config.json
  adapters/
    webcam/detect.py      # OpenCV AprilTag 36h11 -> node.spot(tag_id)
    unoq/                 # Arduino App Lab app: python/main.py + sketch/sketch.ino
    pi/led.py             # gpiozero LED blink by progress (optional)
  sim/
    run_local.sh          # starts 4 nodes on one laptop using config.local.json
    fake_camera.py        # type "7 A" to spawn card 7 in zone A
  nicla/
    main.py               # MicroPython (OpenMV): tag detection + compact port of core logic
  dashboard/
    server.py             # receives telemetry (5006), sends control (ctl ports), serves web page
    index.html
  tests/
    test_merge.py         # merge is commutative, associative, idempotent
    test_scenarios.py     # runs nodes in-process and checks each scenario's expected result
```

---

## 3. Config (`config.json`)

```json
{
  "nodes": {
    "webcam": {"ip": "192.168.1.11", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.12, "zone": "A"},
    "nicla":  {"ip": "192.168.1.12", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.05, "zone": "B"},
    "unoq":   {"ip": "192.168.1.13", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.12, "zone": null},
    "pi":     {"ip": "192.168.1.14", "gossip_port": 5005, "ctl_port": 5007, "speed": 0.08, "zone": null}
  },
  "dashboard": {"ip": "192.168.1.20", "telemetry_port": 5006}
}
```

`config.local.json` is identical except every `ip` is `127.0.0.1` and ports are unique per node
(gossip 5101–5104, ctl 5201–5204), dashboard `127.0.0.1:5006`.

`speed` = progress per second (0.12 ≈ 8 s per rescue, 0.05 = 20 s).

**Decode mode (optional):** top-level `"work": "decode"` (default `"counter"` = everything above,
unchanged). Then each node loads `beacons.json` (next to the config) and cracks the PIN that locks
each card's distress message — see `docs/superpowers/specs/2026-11-15-distress-decoding-design.md`.
Per node, optional `"rate"`: guesses/s to use instead of measuring it at start-up.
Cards with no beacon still use counter work (`speed`). Missing `beacons.json` → the node
refuses to start.
**Nodes that can't decode yet:** mark them per node with `"work": "counter"` (today: the Nicla).
A counter-mode node advances beacon jobs without checking PINs, so decoders would resume past
the real PIN (or the job would end "done" with no pin) — silently. So decoders treat gossip from
a counter-marked peer like this, for **beacon jobs only**: keep that the card exists (id, zone,
spotted_by → its camera spots still count), ignore its progress / done / claim / workers / pin.
Non-beacon jobs merge normally. A counter-marked node running A's code also runs counter mode.
When the Nicla decodes: delete its `"work": "counter"` and copy config.json everywhere.

---

## 4. Constants

| Name | Value | Meaning |
|---|---|---|
| `TICK` | 0.1 s | main loop period |
| `GOSSIP_INTERVAL` | 0.3 s | send full state to every reachable peer |
| `TELEMETRY_INTERVAL` | 0.5 s | send status to dashboard |
| `PEER_TIMEOUT` | 2.0 s | peer counts as unreachable if nothing heard for this long |
| `LEASE` | 4.0 s | a claimed job whose progress hasn't moved for this long can be taken over |
| `ISOLATION_GRACE` | 15 s | alone this long → stop trusting the backup split, take any job |
| `MEMBER_WINDOW` | 10.0 s | when I become isolated, peers heard within this long count as team members for the backup split |
| `OUTBID_MAX_PROGRESS` | 0.5 | a faster node may take over a job only if it's less than half done |
| `DETECT_STABLE_FRAMES` | 3 | tag must be seen in 3 consecutive frames before it becomes a job |

**Golden rule: never compare clock times between devices.** Clocks aren't synced (the Nicla has
no real clock). All timeouts use each node's *own* monotonic clock (`time.monotonic()` /
`time.ticks_ms()`), measured from when *it* last saw something change.

---

## 5. Data

### 5.1 Job record (shared, sent in gossip)

```json
"7": {
  "id": "7",            // tag id as string
  "sev": 7,             // severity = tag id; LOWER = MORE URGENT
  "zone": "A",          // which camera's half of the table
  "spotted_by": "webcam",
  "progress": 0.40,     // 0.0 .. 1.0, only ever goes up
  "done": false,
  "owner": "unoq",      // null if unclaimed
  "epoch": 0,           // increases every time someone TAKES OVER a job from another owner
  "bid": 8.33,          // owner's bid (lower is better) = 1/speed (+ small zone bonus)
                        // DECODE MODE: seconds per guess = 1/rate, no zone bonus
  "workers": ["unoq"],  // every node that ever advanced this job (for duplicate-effort score)
  "pin": null           // DECODE MODE: the PIN once found ("0420"), else null. Missing = null.
}
```

### 5.2 Local-only bookkeeping (never sent)

- `last_change[job_id]` — my monotonic time when that job's progress or claim last changed (from any source)
- `last_heard[peer]` — my monotonic time when I last received gossip from that peer
- `members_snapshot` — taken at the moment I go from "some peers" to "no peers": every peer whose
  `last_heard` is within `MEMBER_WINDOW` (10 s), **blocked or not**. Used for the backup split.
  *(Changed from "peers reachable just before isolation": on a real WiFi cut peers time out
  one by one over ~0.3 s, so that list could hold only 1 peer and nodes would disagree on shares.
  The 10 s window gives every node the same full list. Local-only — no message format change.)*
- `isolated_since` — when I last went from "some peers" to "no peers"
- `blocked` — set of peer names I'm pretending I can't reach (fault injection)
- `current` — job id I'm working on, or null

---

## 6. Merge rule (the heart of it)

`merge(local_job, remote_job)` field by field:

| Field | Rule |
|---|---|
| `done` | `a.done or b.done` |
| `progress` | `max(a, b)` |
| claim (`owner`, `epoch`, `bid`) | take the claim from whichever side wins: **higher `epoch`** wins; tie → **lower `bid`**; tie → **lower `owner` name**. A `null` owner loses to any real owner at the same epoch. |
| `workers` | union (sorted) |
| `pin` | non-null wins; both non-null → **lower** string (missing field = null) |
| `id`, `sev` | keep local (identical anyway — both come from the tag id) |
| `zone`, `spotted_by` | take both from the side whose `spotted_by` name is **lower** (alphabetically). Needed because a card on the middle line can be spotted by both cameras with different zones — "keep local" would never converge and isn't commutative |

Unknown job in remote → copy it in.
If the merge changed `progress` or the claim → set `last_change[job] = now`.

**Must be:** commutative, associative, idempotent. `tests/test_merge.py` checks this.
Because of this, it doesn't matter what order messages arrive in, or if some are lost or duplicated.

---

## 7. Decision loop (every `TICK`)

```
1. receive all pending gossip → merge each job; update last_heard[sender]
2. reachable = peers not blocked and heard within PEER_TIMEOUT
   mode = "connected" if reachable else ("isolated" if alone < ISOLATION_GRACE else "alone_long")
3. if current job is done, or its owner is no longer me → current = null
4. if current is null → choose a job (see 7.1) and claim it
5. if current → progress += speed * TICK (cap 1.0); add me to workers; last_change = now;
                if progress >= 1.0 → done = true
   DECODE MODE (job has a beacon): instead try the next PINs in order, starting at guess
   n = round(progress * 10^digits), about rate*TICK*0.6 guesses per tick (at least 1);
   progress = n / 10^digits. Found → pin = guess, progress = 1.0, done = true.
   All guesses tried without a match → done = true, pin stays null.
6. every GOSSIP_INTERVAL → send full job list to every reachable-or-not, non-blocked peer
   (send to all non-blocked peers even if silent — that's how we rediscover them)
7. every TELEMETRY_INTERVAL → send telemetry to dashboard
```

### 7.1 Choosing a job

Candidates depend on mode:

- **connected:** jobs not done AND (unclaimed OR lease expired OR I can outbid)
  - *lease expired* = `now - last_change[job] > LEASE`
  - *outbid* = owner is someone else, same epoch, `my_bid < job.bid`, and `progress < OUTBID_MAX_PROGRESS`
  - DECODE MODE instead: *take-back* = owner is someone else, same epoch, `my_bid < 0.5 * job.bid`,
    at **any** progress (resuming from the exact guess wastes nothing)
- **isolated** (alone < 15 s): jobs not done AND unclaimed AND in **my share**
  - my share: `members = sorted(members_snapshot + [me])`; job is mine if `int(job.id) % len(members) == members.index(me)`
  - don't touch jobs other nodes claimed — they're probably still working on them
- **alone_long** (alone ≥ 15 s): any job not done (others might be dead)

Pick the **lowest `sev`** (most urgent), tie → lowest id.

Claim it:
- unclaimed or outbid → `owner = me, bid = my_bid` (same epoch)
- lease expired / alone_long takeover from another owner → `owner = me, epoch += 1, bid = my_bid`
- progress is KEPT (we continue from where they got to)

`my_bid = 1/speed`, minus 0.5 if the job is in my own camera zone.

If two nodes claim at once, the merge rule picks the same winner on both; the loser sees
`owner != me` at step 3 and moves on. No voting, no boss.

### 7.2 Spotting (camera nodes only)

On a tag id stable for `DETECT_STABLE_FRAMES` frames, if not `blind` and job id unknown →
create job `{progress:0, done:false, owner:null, epoch:0, bid:null, workers:[]}`.
Spotting works with or without network — it's local.

---

## 8. Network messages (UDP, JSON, UTF-8)

### 8.1 Gossip — node → node, port `gossip_port`

```json
{"type": "gossip", "v": 1, "from": "unoq", "seq": 1234, "jobs": { "7": {...}, "3": {...} }}
```
Receiver ignores it if `from` is in its `blocked` set. Sender skips blocked peers.
**Chunking:** a sender splits its job list across several gossip messages of **at most 6 jobs
each** (one packet ≈ 1200 bytes, below the ~1470-byte WiFi limit, so it's never fragmented —
the Nicla may not reassemble fragments). All chunks are sent every `GOSSIP_INTERVAL`, done jobs
included (late/revived nodes need them to catch up). Receivers merge each message on its own —
the merge rule already handles partial lists. Receivers use a buffer of at least 2048 bytes
(`recvfrom(2048)`). Telemetry (§8.2) goes to a laptop and is NOT chunked.

### 8.2 Telemetry — node → dashboard, port 5006

```json
{"type": "telemetry", "v": 1, "node": "pi",
 "mode": "connected" | "isolated" | "alone_long" | "crashed",
 "reachable": ["unoq", "webcam"],
 "blocked": ["nicla"],
 "blind": false,
 "current": "7",
 "trying": "0421",      // DECODE MODE: PIN being tried right now (null if idle/counter)
 "rate": 183.0,         // DECODE MODE: my guesses per second (null in counter mode)
 "jobs": { ...same as gossip... },
 "seq": 99}
```
The dashboard also tracks *when it last got telemetry from each node*. If > 2 s → show
"NO SIGNAL" for that node (that's what a REAL cut / unplug looks like).

### 8.3 Control — dashboard → node, port `ctl_port`

Control is the test harness, **not** part of the team. It is never blocked by `block`.

| `cmd` | Extra fields | Effect on the node |
|---|---|---|
| `block` | `peers: [..]` | add to `blocked` (both send + receive) |
| `unblock` | `peers: [..]` | remove from `blocked` |
| `unblock_all` | — | `blocked = {}` |
| `isolate` | — | `blocked = all other nodes` |
| `blind` | — | camera stops creating jobs (cameras only) |
| `unblind` | — | camera spots again |
| `crash` | — | stop working AND stop gossip — both sending AND receiving (incoming gossip is read and thrown away, not merged); keep telemetry with `mode:"crashed"` |
| `revive` | — | undo crash; keeps its old job list (it rejoins and catches up). **Also restarts its local clocks:** `last_change[every job] = now`, `isolated_since = now`, `current = null` — so its frozen view doesn't make every job look abandoned and it doesn't jump into `alone_long` and steal live jobs before the first gossip arrives |
| `flaky` | `loss: 0.0–1.0` | randomly drop that fraction of gossip sent and received |
| `reset` | — | clear all jobs + state (new demo round), then **ignore all incoming gossip for 1.0 s** so stale packets still in flight (or from a node that hasn't reset yet) can't bring old jobs back |

Example: `{"type":"ctl","cmd":"block","peers":["nicla"]}`

*Test-only extra (A's sim, not a dashboard button, Nicla can ignore it):*
`{"type":"ctl","cmd":"spot","tag":7}` makes a camera node act as if it saw tag 7
(respects `blind`). Used by `sim/fake_camera.py`.

**UDP can drop control messages:** the dashboard sends every control command **3 times, 0.1 s
apart**. Every command is safe to repeat (repeating it has the same effect as sending it once).

---

## 9. Failure scenarios (dashboard buttons) + what MUST happen

Dashboard composes these from the control commands above.

| Button | Commands sent | Expected result (acceptance test) |
|---|---|---|
| **Heal all** | all: `unblock_all`, `unblind`, `revive`, `flaky 0` | all columns agree within ~1 s |
| **WiFi down** | all: `isolate` | every node shows `isolated`; each keeps working its own share; cameras keep spotting; after 15 s → `alone_long`, takes anything |
| **Cut link X–Y** | X: `block [Y]`, Y: `block [X]` | still converge — news hops through the others |
| **Split** (`[webcam,unoq]` vs `[nicla,pi]`) | each side blocks every node on the other side | two mini-teams; columns differ between halves but agree within each half; on heal → merge, nothing lost |
| **Cameras blind** | webcam, nicla: `blind` | no new jobs; known jobs still all finish |
| **One camera blind** | that camera: `blind` | other zone carries on normally |
| **Crash X** | X: `crash` | after `LEASE` (4 s) another node takes X's job and continues from X's progress |
| **Revive X** | X: `revive` | X catches up immediately, no restart, no confusion |
| **Flaky 30 %** | all: `flaky 0.3` | everything still converges, just a bit slower |
| **Reset round** | all: `reset` | clean slate for next demo |

**Real failures** (do these too in the demo, nobody can call them fake):
pull the Pi's power · turn a laptop's WiFi off · close laptop 2's lid (nothing must change).

### Scoreboard (dashboard computes from telemetry)

- people rescued = jobs with `done`
- urgent-first score = average finish order of the 3 lowest `sev` jobs
- duplicate effort = jobs with `len(workers) > 1`
- time to agree after heal = seconds from "Heal all" until all columns have identical `done` sets

---

## 10. Device-specific notes

### Webcam laptop (A) — `adapters/webcam/detect.py`
- `cv2.aruco` with dictionary `DICT_APRILTAG_36h11` (`pip install opencv-contrib-python`)
- show the camera window with boxes + the node's job list and progress overlaid
- runs the same `core` node in the same process

### UNO Q (A) — `adapters/unoq/`
- Arduino App Lab app. Python side runs `core` (copy `core/` into the app's python folder).
- Python → MCU via Bridge: `show(job_id, pct, speed_milli)` ~every 0.3 s; `idle()` when free.
- **Sketch failsafe:** the MCU keeps its own counter. If no `show` arrives for > 1 s, it keeps
  advancing the bar at the last `speed` until 100 %. Draw job number + progress bar on the 8×13 matrix.
- User button → toggles `isolate` on itself ("jam my radio").
- **FIRST THING:** confirm a Python App Lab app can send/receive UDP on the LAN (tiny echo test).
  If it can't, run `core` as a normal Python process over SSH on the UNO Q Linux side instead.

### Pi Zero W (A) — `adapters/pi/`
- Python 3 standard library only (no numpy, no opencv). `gpiozero` only for the LED (optional).
- Log every event to `murmuration.log` with local time: `20:31:12 rescued #4 (mode=isolated)`.
  After a REAL WiFi cut, show this log to prove it kept working.

### Nicla Vision (B) — `nicla/main.py`
- OpenMV MicroPython. WiFi via `network.WLAN(network.STA_IF)`, UDP via `socket` non-blocking.
- Detection: grayscale, QQVGA/160×120, `img.find_apriltags()` (defaults to 36h11), big tags.
- Port only what's needed of the core: job dict, merge, decide, step. Use `time.ticks_ms()`.
  `ticks_ms()` wraps around — **always** use `time.ticks_diff(now, then)`, never `now - then`.
- Read UDP with `recvfrom(2048)` (see §8.1 chunking).
- LED: **blue** connected+spotting · **green** rescuing · **red** isolated.
- **Fallback if short on time:** Nicla is spotter-only — it sends gossip containing jobs it
  spotted but never claims or works on them. *(Known, accepted side effect: in the backup split
  the Nicla still counts as a member, so its ~¼ share waits until `alone_long` (15 s) during
  WiFi down.)*

### Person B — to do for decode mode (distress decoding)
Full design: `docs/superpowers/specs/2026-11-15-distress-decoding-design.md`. Reference code:
`core/beacon.py` (A). The swarm already runs decode mode on webcam + UNO Q + Pi.

**Nicla (`nicla/main.py`)**
1. **Keep the `pin` field** when merging (§6: non-null wins; both non-null → lower string).
   Today the Nicla rebuilds each job without it — fix this even before the rest.
2. **Slow hash:** copy `pbkdf2_ref` from `core/beacon.py` (hand-written HMAC-SHA256, no `hmac`
   module, no `.copy()`). `check = sha256(key)` first 8 bytes as hex. Verify against
   `python -m core.beacon --vectors` (run on a laptop) — all 4 lines must match exactly.
3. **Beacons:** copy `beacons.json` (repo root) onto the Nicla; `rounds` is in it (500).
   Memory: only `digits`, `salt`, `check` per card are needed (skip `ct`/`title`).
4. **Crack between camera frames:** for my current job, `n = round(progress * 10**digits)`;
   try a few PINs (`str(n)` zero-padded to `digits`), keeping each loop under ~60 ms; found →
   `pin = guess, progress = 1.0, done = true`; else `progress = n / 10**digits`. All tried with
   no match → `done = true`, pin stays null.
5. **Bid** for beacon jobs = `1 / my_guesses_per_s` (measure once at boot: time ~20 guesses).
   No zone bonus. **Take-back** (§7.1 decode): when free and connected, claim a job owned by
   someone else at any progress if `my_bid < 0.5 * job.bid` (same epoch). Cards without a beacon:
   unchanged counter work.
6. Telemetry: add `"trying"` (current guess string or null) and `"rate"` (guesses/s).
7. Done? Tell A → A removes `"work": "counter"` from the Nicla in config.json.

**Dashboard (`dashboard/`)**
1. Per node column: show `trying` (big digits, the PIN being tried now) and `rate`.
   `CRASHED` when mode is crashed (trying is null then).
2. Per job: progress bar as today; when `done` and `pin` is set → show 🔓 + the PIN, and the
   decoded message: `core.beacon.unlock(beacons["7"], pin, rounds)` with
   `rounds, beacons = core.beacon.load_beacons("beacons.json")` (server side, Python).
   `done` with `pin` null = "unsolved" (shouldn't happen).
3. A "decoded messages" strip: each rescued card's title + message, newest first.
4. Unknown extra telemetry fields must be ignored (they are, today).

### Laptop 2 (B) — `dashboard/`
- `server.py`: listens UDP 5006, keeps latest telemetry per node, serves `index.html`
  and a `/state` JSON endpoint; POST `/scenario/<name>` sends the control commands.
- `index.html`: one column per node showing ITS OWN belief (job list, progress bars, owner,
  mode colour, "NO SIGNAL" if stale); scenario buttons; scoreboard.
- Build it first against `dashboard/fake_telemetry.py` (sends made-up telemetry) so you
  don't have to wait for A's nodes.

---

## 11. Milestones (both check in at each)

| Time | A (you) | B (friend) | Joint check |
|---|---|---|---|
| 21:15 | repo + config + UNO Q UDP test | Nicla WiFi + UDP test | both devices can send a packet to a laptop |
| 23:30 | `core` + `sim` + merge tests: 4 nodes on one laptop | dashboard on fake telemetry + buttons | — |
| 00:30 | — | — | **dashboard drives the local sim; every scenario in §9 passes** |
| 02:00 | webcam spots cards → jobs | Nicla detects tags locally | card on table appears in all columns |
| 04:00 | UNO Q app + matrix + failsafe | Nicla full node (or spotter-only) | 4 real devices on dashboard |
| 05:00 | Pi + log (+ LED) | full scenario run on real hardware | all §9 tests pass on hardware |
| 05:00–07:00 | A sleeps | B: story wording, card designs | — |
| 07:00–08:00 | fixes | B sleeps (07:00–09:00) | — |
| **08:00** | **FEATURE FREEZE** | | |
| 09:00–11:30 | rehearse demo × 5, record backup video | | |

If behind, cut in this order: Nicla working jobs (spotter-only) → UNO Q MCU failsafe → scoreboard.

---

## 12. DRAFT — live locks (judge messages)  ⚠ needs B's OK — A's side is BUILT + tested

**Goal:** a judge types a message on their phone (card maker page, `sim/card_maker.py serve`
on the webcam laptop, port 8080), gets told "your card is #103", holds pre-printed card #103 up
to a camera, and the swarm cracks it live. No copying files, no restarts.

**Card ids:** 0–99 = presets in `beacons.json`. **100–199 = live cards** (pre-print 100–119).
200+ = cards made from a file with `card_maker.py make` (they go into `beacons.json`).
A card that IS in `beacons.json` is never treated as live, whatever its number.

### 12.1 New optional job field `lock`
```json
"lock": {"title": "Ana", "digits": 4, "salt": "9f3a1c07", "check": "e2c242e6d2d0b4f4"}
```
Same meaning as a `beacons.json` entry, minus `ct` (the scrambled message stays on the card
maker). `title` ≤ 12 chars, `digits` 3–5.
- **Merge:** non-null wins; both non-null → the one whose `json.dumps(lock, sort_keys=True)`
  string is lower (they're identical in practice). Missing field = null.
- **A job's beacon** = the `beacons.json` entry for its id if there is one, else its `lock`.
- **Learning:** whenever a merged job has a `lock` and I have none for that id, I store it.
  Whenever I know a live lock for a job, my copy of the job carries it (so gossip spreads it to
  nodes that missed the control command).

### 12.2 New control command `lock` (card maker → every node's `ctl_port`)
```json
{"type": "ctl", "cmd": "lock", "id": "103", "lock": {"title": "Ana", "digits": 4, "salt": "...", "check": "..."}}
```
- Node stores the lock for that id; if the job exists, attaches it. It does **not** create a job
  (the card must still be spotted by a camera).
- Sent 3× 0.1 s apart when the judge taps "Lock it", then **every live lock is re-sent every 5 s**
  (so a rebooted node catches up). `reset` clears jobs but **keeps** live locks.

### 12.2b The judge's QR code (the normal way in)
After "Lock it", the judge's phone shows a QR code: `M1:<id>:<digits>:<SALT>:<CHECK>`
(uppercase hex → compact QR; no name — names come from `/locks.json`). The **webcam** scans it
(A's `adapters/webcam/detect.py`), learns the lock (same as the `lock` command) and spots card
`<id>`. Other nodes learn the lock through gossip (§12.1). The Nicla does **not** scan QR codes
(too few pixels) — it learns locks from gossip. Printed LIVE cards 100–119 (AprilTags) are only
a backup if a phone won't scan; the lock still arrives by the `lock` command.

### 12.3 Safety rules (decode mode, live cards only)
1. A live card whose lock I don't know yet is **not worked or claimed** (it stays in the job
   list, unowned) until I learn its lock. Cards < 100 with no beacon: counter work as today.
2. A received copy of a live card **without** a `lock` field is treated as "this card exists"
   only (keep id/zone/spotted_by; ignore its progress/done/claim/workers/pin) — a node that
   doesn't carry the lock can't have checked any PINs. Same treatment as a counter-marked peer.
*(Together these stop fake counter progress on a live card. Bonus: a Nicla that doesn't support
locks yet is harmless — it just doesn't help with live cards.)*

### 12.4 Gossip size
Locks add ~100 bytes per live job, so a gossip message holds **at most 6 jobs AND at most
1200 bytes** of JSON; start a new message when either would be exceeded. Receivers unchanged.

### 12.5 Dashboard
`GET http://192.168.137.1:8080/locks.json` → `{"rounds": 500, "beacons": {"103": {title,
digits, salt, check, ct}}}` (poll every ~2 s). When job 103 is done with a `pin`:
`core.beacon.unlock(beacons["103"], pin, rounds)` → the judge's message.

### 12.6 Who builds what
| A (core, card maker, tests) | B (Nicla, dashboard) |
|---|---|
| `lock` field + merge + learning, `lock` command, safety rule, byte-limited chunks; card maker "Lock it" sends live + `/locks.json`; LIVE cards 100–119 to print | Nicla: same 4 rules (field/merge/learn, command, safety rule, byte-limited chunks). Dashboard: poll `/locks.json`, show title + message on unlock |
