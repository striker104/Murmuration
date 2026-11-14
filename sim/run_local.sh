#!/usr/bin/env bash
# Start all 4 nodes on this laptop (config.local.json). Ctrl-C stops them all.
#   bash sim/run_local.sh
# Then in other terminals:
#   python sim/fake_camera.py          spawn cards ("7 A")
#   python sim/watch.py                see every node's belief (or run the dashboard)
#   python sim/ctl.py all isolate      inject failures
cd "$(dirname "$0")/.."

# pick a python that actually runs (Windows has a fake 'python3' store alias)
if python3 -c "" >/dev/null 2>&1; then PY=python3; else PY=python; fi

pids=()
for n in webcam nicla unoq pi; do
  "$PY" -u -m core.run_node --name "$n" --config config.local.json &
  pids+=($!)
done

trap 'echo; echo stopping; kill "${pids[@]}" 2>/dev/null; exit 0' INT TERM
wait
