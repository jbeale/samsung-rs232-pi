#!/usr/bin/env python3
"""
Samsung Ex-Link (RS-232 serial) HTTP control server.

Drives a Samsung TV's "Ex Link" service port over a USB->serial adapter and
exposes simple HTTP endpoints (/on, /off, /input/hdmi1, ...). RS-232 control
is far more reliable than HDMI-CEC (Anynet+) or the network API on 2015-era
Samsung J-series TVs.

Wiring / setup:
  - USB-serial adapter on the Pi -> TV "Ex Link" 3.5mm service jack.
  - Serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit (8N1).
  - The adapter usually enumerates as /dev/ttyUSB0 (override with EXLINK_PORT).

The Ex-Link packet format is 6 bytes + 1 checksum byte:
    0x08 0x22 <b3> <b4> <b5> <b6> <checksum>
where checksum makes the 7 bytes sum to 0 mod 256 (two's complement).
We compute the checksum automatically so the command tables below only
list the 6 payload bytes -- no hand-calculated checksums to get wrong.
"""
import os
import sys
import time
import threading

import serial
from flask import Flask, jsonify, abort, request

# ---- Serial configuration ----
PORT = os.environ.get("EXLINK_PORT", "/dev/ttyUSB0")
BAUD = int(os.environ.get("EXLINK_BAUD", "9600"))

app = Flask(__name__)

_ser = None
_ser_lock = threading.Lock()
# Ex-Link is effectively a one-way control protocol: there is no reliable
# "query power state" command, so we just remember what we last commanded.
_assumed_power = "unknown"


def _open_serial():
    """Open (or reopen) the serial port. Returns the serial object."""
    global _ser
    if _ser is not None and _ser.is_open:
        return _ser
    _ser = serial.Serial(
        port=PORT,
        baudrate=BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.3,
        write_timeout=2,
    )
    return _ser


def packet(b3, b4, b5, b6):
    """Build a full Ex-Link packet (6 payload bytes + checksum)."""
    body = bytes([0x08, 0x22, b3, b4, b5, b6])
    checksum = (-sum(body)) & 0xFF
    return body + bytes([checksum])


def send(pkt):
    """Send a raw 7-byte packet; return any bytes the TV echoes back.

    A valid command is acknowledged by the TV with 03 0C F1. Note: many TVs
    stop acknowledging in deep standby, and some never ack -- an empty response
    does not always mean failure.
    """
    with _ser_lock:
        try:
            ser = _open_serial()
            ser.reset_input_buffer()
            ser.write(pkt)
            ser.flush()
            time.sleep(0.1)
            resp = ser.read(16)
        except serial.SerialException:
            # Drop the handle so the next call reopens (handles unplug/replug).
            global _ser
            try:
                if _ser:
                    _ser.close()
            except Exception:
                pass
            _ser = None
            raise
    return resp


# ---- Verified command set (power / volume / mute) ----
CMD = {
    "power_toggle": packet(0x00, 0x00, 0x00, 0x00),  # 08 22 00 00 00 00 D6
    "power_off":    packet(0x00, 0x00, 0x00, 0x01),  # 08 22 00 00 00 01 D5
    "power_on":     packet(0x00, 0x00, 0x00, 0x02),  # 08 22 00 00 00 02 D4
    "vol_up":       packet(0x01, 0x00, 0x01, 0x00),  # 08 22 01 00 01 00 D4
    "vol_down":     packet(0x01, 0x00, 0x02, 0x00),  # 08 22 01 00 02 00 D3
    "mute":         packet(0x02, 0x00, 0x00, 0x00),  # 08 22 02 00 00 00 D4
}

# ---- Source/input selection ----
# Per Samsung's official RS232 worksheet: 08 22 0A 00 <type> <index>.
# <type> picks the source family, <index> the port within it.
# Verified: HDMI1 = 08 22 0A 00 05 00.
SOURCE_CODES = {
    "tv":     (0x00, 0x00),
    "av1":    (0x01, 0x00), "av2": (0x01, 0x01), "av3": (0x01, 0x02),
    "comp1":  (0x03, 0x00), "comp2": (0x03, 0x01), "comp3": (0x03, 0x02),
    "pc1":    (0x04, 0x00),
    "hdmi1":  (0x05, 0x00), "hdmi2": (0x05, 0x01),
    "hdmi3":  (0x05, 0x02), "hdmi4": (0x05, 0x03),
    "dvi1":   (0x06, 0x00),
}


def source_packet(name):
    typ, idx = SOURCE_CODES[name]
    return packet(0x0A, 0x00, typ, idx)


# ---- Frame TV modes (08 22 0B 0B <cmd> <on/off>) ----
ART_ON   = packet(0x0B, 0x0B, 0x0E, 0x01)   # 08 22 0b 0b 0e 01 b1
ART_OFF  = packet(0x0B, 0x0B, 0x0E, 0x00)   # 08 22 0b 0b 0e 00 b2
AMBIENT_ON  = packet(0x0B, 0x0B, 0x10, 0x01)
AMBIENT_OFF = packet(0x0B, 0x0B, 0x10, 0x00)

# ---- Remote-key emulation: 08 22 0D 00 00 <keycode> (worksheet "Key Map") ----
KEY_MAP = {
    "source": 0x01, "power": 0x02, "sleep": 0x03,
    "1": 0x04, "2": 0x05, "3": 0x06, "volup": 0x07,
    "4": 0x08, "5": 0x09, "6": 0x0A, "voldown": 0x0B,
    "7": 0x0C, "8": 0x0D, "9": 0x0E, "mute": 0x0F,
    "chdown": 0x10, "0": 0x11, "chup": 0x12, "prech": 0x13,
    "menu": 0x1A, "tv": 0x1B, "info": 0x1F, "exit": 0x2D,
    "enter": 0x2E, "ok": 0x2E,
    "return": 0x58, "up": 0x60, "down": 0x61, "right": 0x62, "left": 0x65,
    "home": 0x76, "hdmi": 0x8B,
}


def key_packet(name):
    return packet(0x0D, 0x00, 0x00, KEY_MAP[name])


def send_keys(names, gap=0.25):
    """Send a sequence of remote keys with a small gap so the TV registers
    each press (used for menu code sequences like hospitality mode)."""
    for n in names:
        send(key_packet(n))
        time.sleep(gap)


def set_volume(level):
    """Set absolute volume 0..100: 08 22 01 00 00 <level>."""
    level = max(0, min(100, int(level)))
    return send(packet(0x01, 0x00, 0x00, level))


# ---- reject browser prefetch/prerender hits ----
# Chrome's omnibox precaches predicted URLs as you type, which would fire a
# command you never hit Enter on. Speculative requests carry one of these
# headers; bounce them with 204 before any route handler (and its side effect)
# runs. Real clicks/curl/Companion don't send these.
@app.before_request
def _block_prefetch():
    h = request.headers
    sec_purpose = h.get("Sec-Purpose", "").lower()
    if (h.get("Purpose", "").lower() == "prefetch"
            or h.get("X-Purpose", "").lower() in ("prefetch", "preview")
            or h.get("X-Moz", "").lower() == "prefetch"
            or "prefetch" in sec_purpose
            or "prerender" in sec_purpose):
        return ("", 204)


# Never let a result get cached/replayed by the browser or a proxy.
@app.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---- error handling ----
@app.errorhandler(serial.SerialException)
def _serial_error(e):
    # Most commonly: the USB-serial adapter isn't plugged in / wrong port.
    return jsonify(
        ok=False,
        error="serial",
        detail=str(e),
        hint=f"Check the USB-serial adapter and that {PORT} exists (ls -l {PORT}).",
    ), 503


# ---- HTTP endpoints ----
@app.get("/")
def index():
    return jsonify(
        service="Samsung Ex-Link control",
        port=PORT,
        baud=BAUD,
        endpoints=[
            "/on", "/off", "/toggle", "/status",
            "/input/1  (HDMI1)", "/input/<hdmi1|hdmi2|tv|...>",
            "/artmode  (Frame: art mode on)", "/artmode/off",
            "/ambient", "/ambient/off",
            "/hospitality  (Mute-1-1-9-Enter, TV on)",
            "/service  (Mute-1-8-2-Power, TV in standby)", "/key/<name>",
            "/volume/up", "/volume/down", "/volume/<0-100>", "/mute",
            "/raw/<hex>  (e.g. /raw/0822000000  -> checksum auto-added)",
        ],
        sources=sorted(SOURCE_CODES),
        keys=sorted(KEY_MAP),
    )


@app.get("/on")
def http_on():
    global _assumed_power
    send(CMD["power_on"])
    _assumed_power = "on"
    return jsonify(ok=True, power="on")


@app.get("/off")
def http_off():
    global _assumed_power
    send(CMD["power_off"])
    _assumed_power = "standby"
    return jsonify(ok=True, power="standby")


@app.get("/toggle")
def http_toggle():
    send(CMD["power_toggle"])
    return jsonify(ok=True)


@app.get("/status")
def http_status():
    # NOTE: Ex-Link has no reliable power-state query; this reflects the last
    # command we sent, not a live reading from the TV.
    return jsonify(power=_assumed_power, note="assumed (Ex-Link is one-way)")


@app.get("/input/<port>")
def http_input(port):
    # Accept either a number ("1" -> hdmi1) or a name ("hdmi1", "tv", ...).
    key = port.lower()
    if key.isdigit():
        key = f"hdmi{key}"
    if key not in SOURCE_CODES:
        abort(400, f"Unknown source '{port}'. Known: {', '.join(sorted(SOURCE_CODES))}")
    send(source_packet(key))
    return jsonify(ok=True, selected=key)


@app.get("/artmode")
@app.get("/artmode/<state>")
def http_artmode(state="on"):
    # Frame TV Art Mode. Default /artmode = on (the common case: drop to art
    # instead of a hard power-off). /artmode/off exits art mode.
    send(ART_OFF if state.lower() == "off" else ART_ON)
    return jsonify(ok=True, artmode=("off" if state.lower() == "off" else "on"))


@app.get("/ambient")
@app.get("/ambient/<state>")
def http_ambient(state="on"):
    send(AMBIENT_OFF if state.lower() == "off" else AMBIENT_ON)
    return jsonify(ok=True, ambient=("off" if state.lower() == "off" else "on"))


@app.get("/key/<name>")
def http_key(name):
    key = name.lower()
    if key not in KEY_MAP:
        abort(400, f"Unknown key '{name}'. Known: {', '.join(sorted(KEY_MAP))}")
    send(key_packet(key))
    return jsonify(ok=True, key=key)


@app.get("/hospitality")
def http_hospitality():
    # Enter the hospitality/hotel service menu: Mute, 1, 1, 9, Enter.
    # (Done with the TV ON.)
    send_keys(["mute", "1", "1", "9", "enter"])
    return jsonify(ok=True, entered="hospitality menu (Mute-1-1-9-Enter)")


@app.get("/service")
def http_service():
    # Enter the factory SERVICE menu: Mute, 1, 8, 2, Power. The TV must be in
    # STANDBY first; the trailing Power key boots it into the service menu.
    # CAUTION: factory menu -- changing the wrong setting can brick the TV.
    send_keys(["mute", "1", "8", "2", "power"])
    return jsonify(ok=True, entered="service menu (Mute-1-8-2-Power)",
                   note="TV must be in standby first; Power boots into the menu")


@app.get("/volume/<level>")
def http_volume(level):
    if level == "up":
        send(CMD["vol_up"])
        return jsonify(ok=True, volume="up")
    if level == "down":
        send(CMD["vol_down"])
        return jsonify(ok=True, volume="down")
    if level.isdigit():
        set_volume(level)
        return jsonify(ok=True, volume=max(0, min(100, int(level))))
    abort(400, "Use /volume/up, /volume/down, or /volume/<0-100>")


@app.get("/mute")
def http_mute():
    send(CMD["mute"])
    return jsonify(ok=True)


@app.get("/raw/<hexstr>")
def http_raw(hexstr):
    """Send arbitrary payload bytes; checksum is appended automatically.
    Pass the 6 payload bytes (e.g. /raw/082200000002) and the checksum is
    added; pass any other length and it is sent verbatim. Handy for discovery."""
    try:
        data = bytes.fromhex(hexstr)
    except ValueError:
        abort(400, "Bad hex")
    if len(data) == 6:
        data = data + bytes([(-sum(data)) & 0xFF])
    resp = send(data)
    return jsonify(ok=True, sent=data.hex(), response=resp.hex())


def _selftest(args):
    """CLI mode for bench testing without HTTP, e.g.:
        python exlink_server.py on
        python exlink_server.py input hdmi1
        python exlink_server.py raw 082200000002
    """
    cmd = args[0]
    if cmd in ("on", "off", "toggle", "mute"):
        send(CMD[{"on": "power_on", "off": "power_off",
                  "toggle": "power_toggle", "mute": "mute"}[cmd]])
    elif cmd == "input":
        send(source_packet(args[1].lower()))
    elif cmd == "artmode":
        send(ART_OFF if args[1:] == ["off"] else ART_ON)
    elif cmd == "ambient":
        send(AMBIENT_OFF if args[1:] == ["off"] else AMBIENT_ON)
    elif cmd == "key":
        send(key_packet(args[1].lower()))
    elif cmd == "hospitality":
        send_keys(["mute", "1", "1", "9", "enter"])
    elif cmd == "service":
        send_keys(["mute", "1", "8", "2", "power"])
    elif cmd == "vol":
        if args[1] == "up":
            send(CMD["vol_up"])
        elif args[1] == "down":
            send(CMD["vol_down"])
        else:
            set_volume(args[1])
    elif cmd == "raw":
        data = bytes.fromhex(args[1])
        if len(data) == 6:
            data += bytes([(-sum(data)) & 0xFF])
        print("response:", send(data).hex())
    else:
        print("usage: exlink_server.py [on|off|toggle|mute|artmode [off]|"
              "ambient [off]|input <name>|key <name>|hospitality|service|"
              "vol <up|down|N>|raw <hex>]")
        return
    print(f"sent: {cmd} {' '.join(args[1:])}".strip())


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _selftest(sys.argv[1:])
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("EXLINK_HTTP_PORT", "8080")))
