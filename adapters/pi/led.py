"""Pi node + LED: runs the core node and shows its current job on an LED.

    python3 -m adapters.pi.led --name pi --config config.json --log murmuration.log
    python3 -m adapters.pi.led ... --rgb 17,27,22      (external RGB LED: red -> green)

Built-in green ACT LED (default):
    idle            off
    working         blinks; faster as progress goes up (1 s period at 0 %, 0.15 s at 100 %)
    just rescued    solid on for 1.5 s
    crashed         off
External RGB LED (--rgb R,G,B GPIO pins, needs gpiozero): colour fades red -> green with
progress, solid green flash on rescue, off when idle.

The built-in LED needs its trigger set to 'none' and write access (adapters/pi/led_setup.sh,
run as root — the systemd service does that). Without access the node still runs.
Standard library only (+ gpiozero for --rgb).
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from core.net import Runner, load_config   # noqa: E402
from core.run_node import make_logger       # noqa: E402

LED_DIRS = ["/sys/class/leds/ACT", "/sys/class/leds/led0"]
RESCUE_FLASH = 1.5


def blink_on(progress, t):
    """Working pattern: 50 % duty, period shrinks from 1.0 s to 0.15 s as progress grows."""
    period = 1.0 - 0.85 * max(0.0, min(1.0, progress))
    return (t % period) < period / 2


def led_state(snap, now, rescued_at):
    """-> ('off'|'on'|'blink', progress)"""
    if snap["mode"] == "crashed":
        return "off", 0.0
    if now - rescued_at < RESCUE_FLASH:
        return "on", 1.0
    cur = snap["current"]
    if cur and cur in snap["jobs"]:
        return "blink", snap["jobs"][cur]["progress"]
    return "off", 0.0


class SysfsLed:
    def __init__(self, invert=False):
        self.path = None
        self.invert = invert
        self.last = None
        for d in LED_DIRS:
            if os.path.exists(os.path.join(d, "brightness")):
                self.path = os.path.join(d, "brightness")
                break
        if self.path is None:
            print("led: no built-in LED found — node keeps running, LED off", flush=True)

    def set(self, on, progress):
        if self.path is None or on == self.last:
            return
        try:
            with open(self.path, "w") as f:
                f.write("1" if on != self.invert else "0")
            self.last = on
        except OSError as e:
            print("led: can't write %s (%s) — run led_setup.sh as root" % (self.path, e),
                  flush=True)
            self.path = None


class RgbLed:
    def __init__(self, pins):
        from gpiozero import RGBLED      # only needed for --rgb
        self.led = RGBLED(*pins)

    def set(self, on, progress):
        if not on:
            self.led.color = (0, 0, 0)
        else:
            p = max(0.0, min(1.0, progress))
            self.led.color = (1.0 - p, p, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="pi")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--log", default=None)
    ap.add_argument("--rgb", default=None, help="R,G,B GPIO pins of an external RGB LED")
    ap.add_argument("--invert", action="store_true", help="built-in LED is lit at 0")
    args = ap.parse_args()

    state = {"rescued_at": -99.0}
    base_logger = make_logger(args.name, args.log)

    def on_event(kind, job_id, mode):
        if kind == "rescued":
            state["rescued_at"] = time.monotonic()
        base_logger(kind, job_id, mode)

    runner = Runner(args.name, load_config(args.config), on_event)
    runner.start_thread()
    led = RgbLed([int(p) for p in args.rgb.split(",")]) if args.rgb else SysfsLed(args.invert)
    print("[%s] node up, LED: %s" % (args.name, "rgb " + args.rgb if args.rgb else "built-in"),
          flush=True)
    try:
        while True:
            now = time.monotonic()
            kind, progress = led_state(runner.snapshot(), now, state["rescued_at"])
            if args.rgb:
                # RGB shows colour steadily while working (blinking colour is hard to read)
                led.set(kind != "off", progress)
            else:
                led.set(kind == "on" or (kind == "blink" and blink_on(progress, now)), progress)
            time.sleep(0.05)
    except KeyboardInterrupt:
        led.set(False, 0.0)


if __name__ == "__main__":
    main()
