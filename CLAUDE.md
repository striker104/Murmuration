# Murmuration — instructions for Claude Code

We are two people at a hackathon building Murmuration: a leaderless swarm of 4 devices that
keeps working when the network fails. Read SPEC.md before doing anything. It is the contract
between the two of us.

## Rules

- Follow SPEC.md exactly for message formats, ports, merge rule, decision loop and control
  commands. If something in the spec seems wrong or missing, STOP and tell me — don't silently
  change the protocol, because my teammate's code depends on it.
- Only edit the folders owned by the person you're working with (see SPEC.md §1).
  - Person A owns: `core/`, `adapters/webcam/`, `adapters/unoq/`, `adapters/pi/`, `sim/`, `tests/`
  - Person B owns: `dashboard/`, `nicla/`
- Keep `core/node.py` pure logic (no sockets, no sleeps) so it can be unit-tested and reused.
- The Pi Zero W is slow: anything that runs on it uses the Python standard library only
  (+ optional gpiozero).
- The Nicla runs OpenMV MicroPython, not CPython: no dataclasses, no typing, small memory,
  `time.ticks_ms()`.
- Never compare timestamps between devices; use each device's own monotonic clock.
- Prefer simple, readable code over clever code. We have ~15 hours and need to debug at 4am.
- After building anything, run it (or its test) before saying it works.

## How to test without hardware

`sim/run_local.sh` starts 4 nodes on this laptop using `config.local.json`.
`sim/fake_camera.py` spawns cards. `dashboard/fake_telemetry.py` fakes nodes for the dashboard.
