#!/bin/sh
# Run as root (the systemd service does this before starting the node):
# stop the kernel blinking the built-in LED and let the node user switch it.
for l in /sys/class/leds/ACT /sys/class/leds/led0; do
  if [ -d "$l" ]; then
    echo none > "$l/trigger"
    chmod a+w "$l/brightness"
  fi
done
exit 0
