# samsung-rs232-pi

Control a Samsung TV from a Raspberry Pi over its **RS-232 "Ex-Link" service port**, exposed as a tiny HTTP API. Built for reliable power/input control of 2015-era Samsung **J-series** TVs in an A/V install, where HDMI-CEC (Anynet+) and the network API are flaky.

```
curl http://pi.local:8080/on
curl http://pi.local:8080/input/hdmi1
curl http://pi.local:8080/off
```

## Why RS-232?

Samsung TVs are notoriously hard to control reliably:

- **HDMI-CEC / Anynet+** is sketchy on many sets — misses commands, drops out in standby.
- **The network API** (`samsungctl` / SmartThings) is inconsistent and often can't power the TV on from a true off state.
- **RS-232 (Ex-Link)** is the wired serial control bus Samsung's own commercial/hospitality gear uses. It's simple, fast, and — critically — can power the TV **on from hard-off**. A ~$10 USB-to-serial cable is all the hardware you need.

## Hardware

- Raspberry Pi (tested on a **Pi Zero 2 W**, any Pi works) running Raspberry Pi OS.
- A **USB-to-serial cable** into the TV's **3.5mm "Ex-Link" service jack**. A USB FTDI-to-3.5mm cable works well and keeps the wiring to a single lead. It typically enumerates as `/dev/ttyUSB0`.

**3.5mm Ex-Link pinout** (per Samsung's worksheet):

| Pin | Signal |
|-----|--------|
| Tip | Received Data (from TV) |
| Ring | Transmitted Data (to TV) |
| Sleeve | Ground |

**Serial settings:** 9600 baud, 8 data bits, no parity, 1 stop bit (8N1).

> **TV setup:** TVs with a native 3.5mm Ex-Link port are usually plug-and-play. Some models that use a USB control dongle need it enabled in the service menu: power off, on the remote press **Mute → 1 → 8 → 2 → Power**, then under **Control → Sub Option** set **EXT Link Support = ON** and **USB Serial = ON**.

## Install

On the Pi:

```bash
git clone https://github.com/jbeale/samsung-rs232-pi.git
cd samsung-rs232-pi
sudo ./install.sh
```

This creates a Python venv at `/opt/samsung-rs232`, installs Flask + pyserial, adds the service user to the `dialout` group, and installs+starts a `samsung-rs232` systemd service (auto-start on boot, auto-restart on crash).

Override the user or serial port if needed:

```bash
sudo SERVICE_USER=admin SERIAL_PORT=/dev/ttyUSB0 ./install.sh
```

Then verify:

```bash
curl http://localhost:8080/        # service info + endpoint list
curl http://localhost:8080/on
```

### Watchdog (recommended)

Headless Pis tucked above a ceiling can occasionally hang hard. Arm the hardware watchdog so the Pi auto-reboots itself instead of needing a physical power cycle:

```bash
sudo ./setup-watchdog.sh
```

This enables the SoC hardware watchdog via systemd (`RuntimeWatchdogSec=14`) and reboots on kernel panic. See [the script](setup-watchdog.sh) for details. (Note: the Pi's hardware watchdog maxes out at ~15s; it can't be set longer.)

### Logo splash on the Pi's HDMI output (optional)

The Pi's own HDMI output goes to a spare input on the TV. By default that shows a Linux login console. To display a full-screen logo there instead:

```bash
sudo ./setup-logo.sh /path/to/logo.png
```

This paints the PNG straight to the framebuffer at boot via a tiny `fbi` oneshot service — no desktop, no browser, negligible load on a Pi Zero 2 W. It also disables the console login on `tty1` so nothing overwrites the image (SSH is unaffected; reversible). Combined with `/input/<n>`, the logo makes a handy branded "holding" screen you can flip the TV to. Use a PNG at the panel's native resolution with the artwork centered on a solid background so any letterboxing blends in.

## HTTP API

All endpoints are `GET` for easy use from a browser, Bitfocus Companion, Home Assistant, etc.

| Endpoint | Action |
|----------|--------|
| `/on` / `/off` / `/toggle` | Power on / off / toggle |
| `/status` | Last-commanded power state (see note below) |
| `/input/<n>` | Select HDMI input by number (`/input/1` = HDMI1) |
| `/input/<name>` | Select source by name: `hdmi1..4`, `tv`, `av1..3`, `comp1..3`, `pc1`, `dvi1` |
| `/volume/up` / `/volume/down` | Volume up / down |
| `/volume/<0-100>` | Set absolute volume |
| `/mute` | Mute toggle |
| `/key/<name>` | Emulate a remote-control key (e.g. `/key/menu`, `/key/info`) |
| `/hospitality` | Enter hospitality/hotel menu (Mute-1-1-9-Enter, **TV on**) |
| `/service` | Enter factory service menu (Mute-1-8-2-Power, **TV in standby**) ⚠️ |
| `/artmode` / `/artmode/off` | Frame TVs: enter / exit Art Mode |
| `/ambient` / `/ambient/off` | Ambient Mode |
| `/raw/<hex>` | Send arbitrary bytes (6 payload bytes → checksum auto-added) |

> ⚠️ The **service menu** is the factory menu — changing the wrong setting can brick a TV. Only use it if you know what you're doing.

### CLI mode (bench testing without HTTP)

```bash
/opt/samsung-rs232/venv/bin/python /opt/samsung-rs232/exlink_server.py on
... input hdmi1
... key menu
... raw 082200000002
```

## Protocol notes

Ex-Link commands are 7 bytes: a fixed `08 22` header, four command/value bytes, and a checksum.

```
08 22 <b3> <b4> <b5> <b6> <checksum>
```

The **checksum** makes all 7 bytes sum to 0 mod 256 (two's-complement of the first six). This server computes it automatically, so the command tables only list the six payload bytes. Example — power off:

```
08 22 00 00 00 01  →  sum 0x2B  →  checksum 0xD5  →  08 22 00 00 00 01 D5
```

A valid command is acknowledged by the TV with **`03 0C F1`**. (Heads-up: many TVs stop acknowledging in deep standby, and some never ack — an empty response isn't always a failure. If *every* command suddenly stops being acknowledged, the TV firmware has likely hung; recover it with the remote.)

Command values come from Samsung's official *Consumer RS232 Control Worksheet* (the same doc that documents the service-menu entry and full key map).

## Notes for specific models

- **J-series (and similar consumer sets):** hard `/off` puts them in a fast-boot standby that stays serial-responsive, so `/on` reliably wakes them. Use `/on` and `/off` normally.
- **The Frame:** its One Connect box has a relay that physically *cuts power* on a hard standby — after that the serial port is dead and the TV can only be woken by the remote. On a Frame, prefer **Art Mode** as the "off" state (`/artmode` to rest, `/artmode/off` to wake), which keeps the One Connect box powered and serial-responsive.

## Browser prefetch

Because the endpoints are side-effecting `GET`s, the server rejects browser **prefetch/prerender** requests (Chrome's omnibox precaches URLs as you type) with a `204`, and sets `Cache-Control: no-store`. For bulletproofing against any accidental trigger, switch the routes to `POST` — browsers never speculatively POST.

## License

[MIT](LICENSE)
