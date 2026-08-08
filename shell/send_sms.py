#!/usr/bin/env python3
"""Send one SMS over the modem's second AT port.

Usage: send_sms.py <number> <text>

Bypasses gammu, which cannot drive this modem: libGammu only recognises an SMS
prompt of "> " while the SIM7670G emits a bare ">" (gammu/gammu#1177). Talks to
-if06 so it coexists with the SMS Gammu Gateway add-on on -if02.
"""

import fcntl
import os
import select
import sys
import termios
import time
import tty

# Second AT port. The add-on holds -if02; -if04 is the DIAG/QCDM port and
# returns binary. by-id, not ttyACM*, because the ACM numbering shifts on every
# USB re-enumeration.
DEVICE = "/dev/serial/by-id/usb-QualComm_QualComm_Compo_000000000001-if06"

# Inert on CDC-ACM (no UART behind it) but set so the port is configured
# correctly if the modem is ever moved to a real serial link.
BAUD = termios.B115200

# Serializes against other senders and any manual CLI use. The automation's
# mode: queued only serializes that one automation.
LOCK_PATH = "/tmp/send_sms.lock"
LOCK_TIMEOUT = 120.0

CMD_TIMEOUT = 10.0
PROMPT_TIMEOUT = 15.0
# 3GPP TS 23.040: network ack for a submitted message can legitimately take
# tens of seconds on a weak link.
SEND_TIMEOUT = 60.0

# Single-part GSM 7-bit message. Longer needs PDU mode with UDH concatenation.
MAX_TEXT = 160

CTRL_Z = b"\x1a"
ESC = b"\x1b"


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def normalize(number):
    """Accept what secrets.yaml already holds: bare NANP 10-digit numbers.

    AT+CMGS needs E.164, but the existing sms_oscar / sms_hazel secrets are
    stored as plain 10-digit strings, so normalise rather than reject.
    """
    digits = "".join(ch for ch in number if ch.isdigit())
    if not digits:
        fail(f"no digits in number {number!r}")
    if number.lstrip().startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    fail(f"cannot resolve {number!r} to E.164")


def open_port(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd, termios.TCSANOW)
    attrs = termios.tcgetattr(fd)
    attrs[2] |= termios.CLOCAL | termios.CREAD  # ignore modem control lines
    attrs[4] = BAUD
    attrs[5] = BAUD
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def read_until(fd, done, timeout):
    """Accumulate input until done(buf) is true. Returns (buf, matched)."""
    buf = b""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return buf, False
        if not select.select([fd], [], [], remaining)[0]:
            continue
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            continue
        except OSError as err:
            fail(f"read failed: {err}")
        if chunk:
            buf += chunk
            if done(buf):
                return buf, True


def command(fd, cmd, timeout=CMD_TIMEOUT):
    os.write(fd, cmd + b"\r")
    buf, ok = read_until(
        fd, lambda b: b"\r\nOK\r\n" in b or b"ERROR" in b, timeout
    )
    text = buf.decode("ascii", "replace").strip()
    if not ok:
        fail(f"timeout waiting for reply to {cmd.decode()}: {text!r}")
    if b"ERROR" in buf:
        fail(f"{cmd.decode()} rejected: {text!r}")
    return buf


def main():
    if len(sys.argv) != 3:
        fail("usage: send_sms.py <number> <text>")
    number, text = normalize(sys.argv[1]), sys.argv[2]
    if not text:
        fail("empty message")

    # Text mode is GSM 7-bit; anything outside it would need UCS2 and PDU mode.
    body = text.encode("ascii", "replace")
    if len(body) > MAX_TEXT:
        print(
            f"message truncated from {len(body)} to {MAX_TEXT} characters",
            file=sys.stderr,
        )
        body = body[:MAX_TEXT]

    lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + LOCK_TIMEOUT
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() > deadline:
                fail(f"another send held {LOCK_PATH} for {LOCK_TIMEOUT}s")
            time.sleep(0.5)

    try:
        fd = open_port(DEVICE)
    except OSError as err:
        fail(f"cannot open {DEVICE}: {err}")

    try:
        command(fd, b"AT")
        command(fd, b"AT+CMEE=2")  # verbose +CMS ERROR instead of bare ERROR
        command(fd, b"AT+CMGF=1")
        command(fd, b'AT+CSCS="GSM"')

        os.write(fd, b'AT+CMGS="' + number.encode("ascii") + b'"\r')
        # Match a bare ">" as well as "> " — this modem sends "\r\n>\r\n".
        buf, ok = read_until(fd, lambda b: b">" in b, PROMPT_TIMEOUT)
        if not ok:
            os.write(fd, ESC + b"\r")
            fail(
                "no SMS prompt after AT+CMGS: "
                f"{buf.decode('ascii', 'replace').strip()!r}"
            )

        os.write(fd, body + CTRL_Z)
        buf, ok = read_until(
            fd,
            lambda b: b"+CMGS:" in b or b"ERROR" in b,
            SEND_TIMEOUT,
        )
        reply = buf.decode("ascii", "replace").strip()
        if not ok:
            fail(f"no network acknowledgement within {SEND_TIMEOUT:.0f}s: {reply!r}")
        if b"ERROR" in buf:
            fail(f"send rejected: {reply!r}")

        reference = reply.split("+CMGS:")[1].split()[0].strip().rstrip("\r\n")
        print(f"sent to {number}, reference {reference}")
    finally:
        os.close(fd)
        os.close(lock_fd)


if __name__ == "__main__":
    main()
