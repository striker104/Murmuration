#!/usr/bin/env bash
# Make the Murmuration node start at boot and restart if it dies (Pi or UNO Q).
#
#   cd ~/murmuration && bash adapters/pi/install_service.sh pi      # on the Pi
#   cd ~/murmuration && bash adapters/pi/install_service.sh unoq    # on the UNO Q
#   bash adapters/pi/install_service.sh remove                       # undo
#
# Afterwards:
#   journalctl -u murmuration -f        live output (Ctrl-C stops WATCHING, node keeps running)
#   sudo systemctl restart murmuration  after copying new code / config
#   sudo systemctl stop murmuration     stop it (e.g. to run it by hand)
set -e

NAME="$1"
DIR="$PWD"   # run from the murmuration folder (the one with core/)
CONFIG="${CONFIG:-config.json}"
UNIT=/etc/systemd/system/murmuration.service

if [ "$NAME" = "remove" ]; then
  sudo systemctl disable --now murmuration 2>/dev/null || true
  sudo rm -f "$UNIT"
  sudo systemctl daemon-reload
  echo "removed"
  exit 0
fi

if [ -z "$NAME" ]; then
  echo "usage: bash install_service.sh pi|unoq|remove"; exit 1
fi
if [ ! -f "$DIR/core/run_node.py" ] || [ ! -f "$DIR/$CONFIG" ]; then
  echo "run this from the folder that has core/ and $CONFIG (got $DIR)"; exit 1
fi

# which program: the node + this device's display (falls back to the plain node)
case "$NAME" in
  pi)   MODULE=adapters.pi.led ;;
  unoq) MODULE=adapters.unoq.matrix ;;
  *)    MODULE=core.run_node ;;
esac
if [ "$MODULE" != core.run_node ] && [ ! -d "$DIR/adapters" ]; then
  echo "no adapters/ folder here -> running the plain node (no LED/matrix)"
  MODULE=core.run_node
fi
PRE=""
if [ "$MODULE" = adapters.pi.led ]; then
  # '+' = run as root: lets the node switch the built-in LED
  PRE="ExecStartPre=+/bin/sh $DIR/adapters/pi/led_setup.sh"
fi

# a node started by hand would hold the ports -> stop it first
pkill -f "core.run_node|adapters.pi.led|adapters.unoq.matrix" 2>/dev/null \
  && echo "stopped a node that was running by hand" || true

sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=Murmuration node ($NAME)
After=network.target

[Service]
User=$(id -un)
WorkingDirectory=$DIR
$PRE
ExecStart=$(command -v python3) -u -m $MODULE --name $NAME --config $CONFIG --log murmuration.log
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now murmuration
sleep 2
systemctl --no-pager --lines=5 status murmuration || true
echo
echo "installed: '$NAME' now starts at boot. Watch it with:  journalctl -u murmuration -f"
