# Distress Decoding (checkpoint 1, Person A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jobs become real work: each node brute-forces the PIN that locks a card's distress message, resumable from the exact guess by any node.

**Architecture:** `core/beacon.py` holds the slow hash plus lock/unlock/check. `core/node.py` gains an optional decode mode: the beacons dict and a `check(beacon, pin) -> bool` function are injected, and the node stays pure. `core/net.py` loads `beacons.json`, measures this device's guesses/s once at start-up, and passes the result in. Counter mode stays the default and is byte-for-byte unchanged.

**Tech Stack:** Python 3.7+ standard library only (`hashlib`, `hmac`, `json`), unittest.

**Spec:** `docs/superpowers/specs/2026-11-15-distress-decoding-design.md` (read it first). Also read `SPEC.md` §5–§8.

## Global Constraints
- `core/node.py` stays pure: no sockets, no sleeps, no clock reads. Hashing inside `tick()` is allowed, because it is compute.
- Standard library only (it runs on the Pi Zero W). Python 3.7 compatible.
- Counter mode (`"work"` absent or `"counter"`) must behave exactly as today: all 32 existing tests pass unchanged.
- Gossip packets stay at most 6 jobs and at most 1400 bytes (the harness checks `max_bytes`).
- Merge stays commutative, associative and idempotent, now including `pin`.
- Edit only Person A folders, plus `SPEC.md` and `beacons.json` (both shared). Never touch `dashboard/` or `nicla/`.
- Run every test before claiming it works: `python -m unittest discover -s tests -t .`

## Review Focus
1. **Beacon whose PIN is never found** (corrupt file, wrong rounds): the node must mark the job done with `pin` null and emit `"unsolved"`. It must not spin forever. → test in Task 3.
2. **Card id with no beacon in decode mode** (e.g. tag 30): it falls back to counter progress at `speed`. → test in Task 3.
3. **Job taken over mid-way**: the taker resumes at exactly `round(progress*total)` and skips no PIN. Overlap with the old owner is allowed. → test in Task 3.
4. **Fast node keeps stealing**: take-back happens only when the node is free and `my_bid < 0.5*job.bid`, and never in isolated mode. → test in Task 3.
5. **`beacons.json` missing while `"work": "decode"`**: print a clear warning and run in counter mode. Do not crash the node at boot. → test in Task 4.

---

### Task 1: `core/beacon.py` — slow hash, lock, unlock, check

**Files:**
- Create: `core/beacon.py`
- Test: `tests/test_beacon.py`

**Interfaces:**
- Produces:
  - `pbkdf2_ref(pin_bytes, salt_bytes, rounds) -> bytes(32)`: hand-written HMAC-SHA256 loop using only `hashlib.sha256`. It is the reference the Nicla ports.
  - `derive(pin_str, beacon, rounds) -> bytes`: uses `hashlib.pbkdf2_hmac("sha256", ...)` for speed.
  - `pin_str(n, digits) -> str`: zero-padded.
  - `check(beacon, pin, rounds) -> bool`.
  - `lock(title, message, pin, digits, salt_hex, rounds) -> beacon dict`.
  - `unlock(beacon, pin, rounds) -> str`.
  - `load_beacons(path) -> (rounds, {id: beacon})`.
  - `bench(rounds, seconds=0.3) -> guesses_per_s`.
  - CLI `python -m core.beacon --vectors` prints test vectors for the Nicla.
- Formats come from the design §"Shared file":
  - `check` = first 8 bytes of `sha256(key)`, as hex (16 chars).
  - `ct` = utf8 XOR the keystream `sha256(key + bytes([i]))`, i = 0, 1, …

- [ ] **Step 1: Write failing tests** (`tests/test_beacon.py`)
  - `test_ref_matches_hashlib`: `pbkdf2_ref(b"0420", bytes.fromhex("a1b2c3d4"), 50) == hashlib.pbkdf2_hmac("sha256", b"0420", bytes.fromhex("a1b2c3d4"), 50, 32)`. Also check 1 round, and a salt longer than 64 bytes.
  - `test_lock_unlock_roundtrip`: `b = lock("Maria", "Trapped under desk, room 3B", "0420", 4, "a1b2c3d4", 10)`; then `unlock(b, "0420", 10) == "Trapped under desk, room 3B"`, `check(b, "0420", 10)` is true, `check(b, "0421", 10)` is false, and a message longer than 32 bytes round-trips.
  - `test_pin_str`: `pin_str(7, 4) == "0007"`.
  - `test_load_beacons`: write a temp JSON `{"rounds": 10, "beacons": {"7": b}}`, then assert `load_beacons(p) == (10, {"7": b})`.
- [ ] **Step 2: Run them and expect a FAIL** (no module): `python -m unittest tests.test_beacon -v`
- [ ] **Step 3: Implement `core/beacon.py`.** In `pbkdf2_ref`, write HMAC by hand: key padded to 64 bytes, `ipad` 0x36, `opad` 0x5c, block index `b"\x00\x00\x00\x01"`, XOR-accumulate U1..Un. Use no `hmac` module and no `.copy()`, because MicroPython lacks them. Add a comment saying this is what the Nicla copies.
- [ ] **Step 4: Run and expect a PASS**, then run `python -m core.beacon --vectors` and see vectors printed.
- [ ] **Step 5: Commit**: `git add core/beacon.py tests/test_beacon.py && git commit -m "beacon: slow hash, lock/unlock/check"`

### Task 2: `pin` field + merge rule

**Files:**
- Modify: `core/node.py`: `new_job` adds `"pin": None`; `merge_job` sets `out["pin"]`.
- Modify: `tests/test_merge.py`: `random_copy` sets `j["pin"] = rng.choice([None, None, "0420", "0007"])`.
- Modify: `SPEC.md` §5.1 (pin field) and §6 (merge row).

**Interfaces:**
- Produces: the `job["pin"]` str or None. Merge rule: non-null wins; if both are non-null, the lower string wins. Write it with `min()` over the non-null values.

- [ ] **Step 1:** Add `pin` to `random_copy`, plus a rule test `test_pin_non_null_wins`:
  - `merge(pin=None, pin="0420").pin == "0420"`
  - `merge("0420", "0007").pin == "0007"`
- [ ] **Step 2:** Run `python -m unittest tests.test_merge` and expect a FAIL (KeyError, or the pin is lost).
- [ ] **Step 3:** Implement. Use `a.get("pin")`, so old peers that don't send `pin` still merge.
- [ ] **Step 4:** Run the full suite and expect all tests to pass.
- [ ] **Step 5: Commit**: `"merge: pin field"`

### Task 3: Decode mode in `Node`

**Files:**
- Modify: `core/node.py`
- Modify: `tests/harness.py`: `Sim(seed=1, beacons=None, rounds=None, rates=None)` passes the decode kwargs to each Node.
- Create: `tests/test_decode.py`

**Interfaces:**
- Consumes: from Task 1, `beacon.check`, `beacon.pin_str`.
- Produces:
  - `Node(name, peers, speed, zone=None, now=0.0, beacons=None, rounds=None, rate=None, check=None)`.
  - `beacons=None` means counter mode, exactly as today.
  - Decode constants: `WORK_FRACTION = 0.6` and `TAKEBACK_RATIO = 0.5`.
  - New fields: `node.trying` (str or None) and `node.rate` (float or None).
  - Telemetry gains `"trying"` and `"rate"`.
  - Events gain `"unsolved"`.

**Behaviour, decided here:**
- `_decoding(job)` is true iff `self.beacons` is set and `job["id"] in self.beacons`.
- `my_bid(job)`:
  - When decoding: `round(1.0 / self.rate, 6)`, with no zone bonus (no routing).
  - Otherwise: unchanged.
- Per tick when decoding:
  - `total = 10 ** digits`; `n = int(round(progress * total))`.
  - Work `budget = max(1, int(rate * TICK * WORK_FRACTION))`.
  - Try `pin_str(n..)` until found, `n == total`, or the budget is spent. Set `self.trying` to the last guess.
  - Then `progress = n / total`, unrounded, or `1.0` when found. When found: `pin` is set, `done` is true, and event `"rescued"`.
  - When `n == total` with no hit: `done` is true, `pin` stays None, and event `"unsolved"`.
  - Non-decoding jobs keep the existing `speed * TICK` step.
- `_candidate`, connected branch only: when decoding, the outbid condition is `job["bid"] is not None and self.my_bid(job) < TAKEBACK_RATIO * job["bid"]` at any progress. Otherwise keep `OUTBID_MAX_PROGRESS`.
- `trying` resets to None when idle or crashed.

- [ ] **Step 1: Write failing tests** (`tests/test_decode.py`). Build beacons with `lock(..., rounds=1)` for speed, and pass `check=lambda b, p: beacon.check(b, p, 1)`.
  - `test_single_node_finds_pin`:
    - Setup: one Node (no peers), rate 50 (so the budget is 3 guesses/tick), beacon "3" with digits 2 and pin "17", spot "3".
    - Tick until done, at most 100 ticks.
    - Assert `pin == "17"`, `progress == 1.0`, and a `"rescued"` event.
  - `test_resume_from_exact_guess`:
    - Setup: job "3" with `progress = 0.42`, digits 2, owned by me.
    - Wrap `check` to record the guesses.
    - Assert the first guess is `"42"`, and that no guess is below 42.
  - `test_unsolved_beacon_finishes`:
    - Setup: the `check` function always returns False.
    - Assert `done`, `pin is None`, and an `"unsolved"` event.
  - `test_no_beacon_card_uses_counter`:
    - Setup: decode mode with spot "30", which has no beacon.
    - Assert that after 1 tick the progress is `speed * TICK`.
  - `test_takeback_any_progress`:
    - Setup: Sim with decode beacons. Rates: webcam 1000, pi 10, others 10.
    - Pi owns job "5" at progress 0.7 with `bid = 0.1`. The webcam is free and connected.
    - After 1 s, assert `owner == "webcam"` and that the epoch is unchanged.
  - `test_no_takeback_when_not_much_faster`: rates 10 vs 15 gives no take-back.
  - `test_counter_mode_bids_unchanged`: `Node("pi", [], 0.08).my_bid(new_job("3", None, "webcam")) == 12.5`.
  - `test_telemetry_has_trying_and_rate`.
  - `test_gossip_size_with_pins`:
    - Setup: Sim with 12 decoded jobs, each carrying a `pin`.
    - Assert `sim.max_bytes <= 1400`.
- [ ] **Step 2:** Run and expect a FAIL (unexpected kwarg).
- [ ] **Step 3:** Implement per the "Behaviour" list. Keep the decode work in one helper, `_work_decode(job, now)`, so `tick()` stays readable.
- [ ] **Step 4:** Run the full suite and expect all tests to pass, including the old scenario tests.
- [ ] **Step 5:** Update SPEC.md:
  - §5.1: bid meaning in decode mode.
  - §7.1: the take-back rule.
  - §8.2: telemetry `trying` and `rate`.
  - §3: config `"work"` and `"rate"`.

  Each note is marked "decode mode only".
- [ ] **Step 6: Commit**: `"node: decode mode (crack PINs, resume, take-back)"`

### Task 4: Wire decode mode into `Runner`

**Files:**
- Modify: `core/net.py`
- Test: `tests/test_net.py`, which gains `test_decode_config_loads_beacons` and `test_decode_missing_file_falls_back`.

**Interfaces:**
- Consumes: from Task 1, `load_beacons` and `bench`; from Task 3, the Node kwargs.
- Produces: `Runner(name, config, on_event=None, config_dir=".")`. When `config.get("work") == "decode"`:
  - Load `<config_dir>/beacons.json`.
  - `rate = nodes[name].get("rate") or bench(rounds)`.
  - Print `"[pi] decode mode: 183 guesses/s (measured)"`.
  - Pass `check=lambda b, p: beacon.check(b, p, rounds)`.
- If the file is missing: print a warning and use counter mode.
- `load_config` callers pass `os.path.dirname(path)`. These are `run_node.py`, `adapters/pi/led.py`, `adapters/unoq/matrix.py` and `adapters/webcam/detect.py`; add a `config_dir` param via `load_config` returning the path alongside, or pass it explicitly (implementer's choice, but be consistent).
- `snapshot()` gains `"trying"`.

- [ ] **Step 1:** Write the 2 tests. They use the real `Runner`, with a temp config on 127.0.0.1 ports 5301+ that has `"work": "decode"` and `"rate": 100`. Assert `runner.node.beacons` and `runner.node.rate == 100`. For the missing file, assert `runner.node.beacons is None`.
- [ ] **Step 2:** Run and expect a FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full suite and expect a PASS.
- [ ] **Step 5: Commit**: `"net: decode mode config + rate benchmark"`

### Task 5: Preset beacons + local end-to-end run

**Files:**
- Create: `sim/make_beacons.py`, which writes `beacons.json` (repo root) from a list in the file.
- Create: `beacons.json` (generated).
- Create: `config.decode.local.json`, a copy of `config.local.json` plus `"work": "decode"`.

**Decided here:**
- `rounds` is 500.
- Cards 0–11 get beacons. The salt is the first 4 bytes of `sha256("murmuration-<id>")`, as hex, so regenerating gives the same file.
- Starting tiers (retuned in checkpoint 3 after measuring the devices):
  - ids 0–2: 5 digits, PINs ≈ 30000–60000
  - ids 3–5: 4 digits, PINs ≈ 4000–9000
  - ids 6–8: 3 digits, PINs ≈ 300–900
  - ids 9–11: 2 digits, PINs ≈ 40–90
- Messages are short rescue stories of 60 bytes or fewer (e.g. "Maria, 34: trapped under desk, room 3B, leg injured").
- The script prints each card's PIN and the estimated time at 5500/900/180/30 guesses/s.

- [ ] **Step 1:** Write the script and run `python sim/make_beacons.py`. Expect `beacons.json` with 12 entries and an estimate table.
- [ ] **Step 2:** Verify that every beacon unlocks with its PIN: `python -c "from core.beacon import *; r,b=load_beacons('beacons.json'); ..."`.
- [ ] **Step 3: End-to-end run.**
  - Start `MURM_CONFIG=config.decode.local.json python sim/run_local.py`.
  - Spot cards 3 and 9 via `sim/ctl.py`.
  - Watch them reach done in `sim/watch.py`, which shows a `trying` column (add it to watch.py).
  - Unlock the found PINs and print the messages.
- [ ] **Step 4:** Run the full suite and expect a PASS. **Commit**: `"presets: beacons.json + decode local config"`.

---
Checkpoint 2–3 (not this plan): deploy to the Pi and UNO Q, measure the real rates, retune the PINs in `make_beacons.py`, and give Person B the vectors.
