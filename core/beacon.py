"""Distress beacons: a message locked with a PIN (see docs/superpowers/specs/*distress*).

    key   = PBKDF2-HMAC-SHA256(pin, salt, rounds, 32)     pin = zero-padded ASCII digits
    check = sha256(key)[:8] as hex                        a guess is right iff it matches
    ct    = utf8(message) XOR sha256(key + bytes([i])) for i = 0, 1, 2 ...

Standard library only. `pbkdf2_ref` is written by hand (no hmac module, no .copy()) so the
Nicla's MicroPython port can copy it line by line; CPython uses hashlib.pbkdf2_hmac (fast).

    python -m core.beacon --vectors      print test values for checking another port
"""
import hashlib
import json
import sys
import time


def pin_str(n, digits):
    """7, 4 -> '0007'"""
    s = str(n)
    return "0" * (digits - len(s)) + s


def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def pbkdf2_ref(pin_bytes, salt_bytes, rounds):
    """Reference PBKDF2-HMAC-SHA256 with dklen 32 (one block). NICLA: copy this one."""
    key = pin_bytes
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    key = key + b"\x00" * (64 - len(key))
    ikey = bytes(k ^ 0x36 for k in key)
    okey = bytes(k ^ 0x5C for k in key)

    def hmac(msg):
        inner = hashlib.sha256(ikey + msg).digest()
        return hashlib.sha256(okey + inner).digest()

    u = hmac(salt_bytes + b"\x00\x00\x00\x01")
    out = u
    for _ in range(rounds - 1):
        u = hmac(u)
        out = _xor(out, u)
    return out


def derive(pin, beacon, rounds):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(beacon["salt"]), rounds, 32)


def _check_of(key):
    return hashlib.sha256(key).hexdigest()[:16]


def _keystream_xor(key, data):
    out = b""
    for i in range(0, len(data), 32):
        block = hashlib.sha256(key + bytes([i // 32])).digest()
        out += _xor(data[i:i + 32], block)
    return out


def check(beacon, pin, rounds):
    """True iff `pin` (string) unlocks this beacon. This is ONE guess of real work."""
    return _check_of(derive(pin, beacon, rounds)) == beacon["check"]


def lock(title, message, pin, digits, salt_hex, rounds):
    key = derive(pin, {"salt": salt_hex}, rounds)
    return {
        "title": title,
        "digits": digits,
        "salt": salt_hex,
        "check": _check_of(key),
        "ct": _keystream_xor(key, message.encode("utf-8")).hex(),
    }


def unlock(beacon, pin, rounds):
    """The decoded message text (garbage-in, garbage-out if pin is wrong)."""
    key = derive(pin, beacon, rounds)
    return _keystream_xor(key, bytes.fromhex(beacon["ct"])).decode("utf-8", "replace")


def load_beacons(path):
    """-> (rounds, {card_id_str: beacon})"""
    with open(path) as f:
        data = json.load(f)
    return int(data["rounds"]), {str(k): v for k, v in data["beacons"].items()}


def bench(rounds, seconds=0.3):
    """Guesses per second this device manages (real check() calls, wrong PINs)."""
    b = {"salt": "00112233", "check": "0" * 16}
    n = 0
    t0 = time.monotonic()
    while True:
        check(b, pin_str(n % 10000, 4), rounds)
        n += 1
        dt = time.monotonic() - t0
        if dt >= seconds:
            return n / dt


def _vectors():
    print("pbkdf2_ref(pin, salt, rounds) -> key hex   (check = first 16 hex of sha256(key))")
    for pin, salt, rounds in [("0420", "a1b2c3d4", 1), ("0420", "a1b2c3d4", 2),
                              ("07", "00112233", 500), ("12345", "deadbeef", 500)]:
        key = pbkdf2_ref(pin.encode(), bytes.fromhex(salt), rounds)
        print("  %-6s %s %4d -> %s  check %s" % (pin, salt, rounds, key.hex(), _check_of(key)))


if __name__ == "__main__":
    if "--vectors" in sys.argv:
        _vectors()
    else:
        print("rounds=500: %.0f guesses/s on this device" % bench(500, 1.0))
