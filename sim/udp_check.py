"""Laptop side of the device network test.

    python sim/udp_check.py 192.168.137.54        (UNO Q)
    python sim/udp_check.py 192.168.137.55        (Pi)

Listens on UDP 5005 and prints everything that arrives; every second it also sends
"hi from laptop #N" to the device. Ctrl-C to stop.
"""
import socket
import sys
import time

PORT = 5005


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    target = (sys.argv[1], PORT)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if sys.platform == "win32" and hasattr(socket, "SIO_UDP_CONNRESET"):
        s.ioctl(socket.SIO_UDP_CONNRESET, False)
    s.bind(("", PORT))
    s.settimeout(0.2)
    print("listening on %d, sending to %s:%d  (Ctrl-C to stop)" % (PORT, target[0], PORT), flush=True)
    n, next_send = 0, time.monotonic()
    try:
        while True:
            if time.monotonic() >= next_send:
                n += 1
                next_send += 1.0
                try:
                    s.sendto(("hi from laptop #%d" % n).encode(), target)
                except OSError as e:
                    print("SEND FAILED: %s" % e, flush=True)
            try:
                data, addr = s.recvfrom(2048)
                print("GOT %r from %s" % (data, addr), flush=True)
            except (socket.timeout, ConnectionResetError):
                pass
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
