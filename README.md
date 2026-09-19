# Tab5 Magic Mirror

A customizable "magic mirror" style dashboard for the [M5Stack Tab5](https://docs.m5stack.com/en/core/Tab5) (ESP32-P4), built on UIFlow2 / MicroPython. It runs a full-screen LVGL dashboard on the device itself, plus a built-in web UI for configuration, widget management, and live sensor/system data — no companion app, no cloud service required.

![Dashboard preview](pictures/preview_dashboard.png)

## Highlights

- **20+ widgets**: clock, weather, calendar (iCal), RSS, Home Assistant entities, PC/server status, sensor readings, notes/todo, custom logos, and more — arranged on one or more dashboard pages you design yourself in the web UI.
- **Multi-dashboard support**: build several dashboard layouts and switch between them, e.g. a day dashboard and a night/sensor dashboard, or per-room layouts.
- **Local sensor dashboard**: onboard BME688 (temperature/humidity/pressure/IAQ) and BMI270 accelerometer, with graphed history and quake/shock detection.
- **Home Assistant & Atom relay integration**: read and toggle entities/relays directly from the dashboard or web UI.
- **PC/server status**: pulls CPU, GPU, RAM and Docker status from a small companion server (`aida_sse_server.py`) running on a PC.
- **Web UI**: responsive, no-build-step HTML/CSS/JS served directly from the device — dashboard editor, widget configuration, notes, system settings, sensor history, and Wi-Fi setup.
- **Runs entirely on-device**: pure MicroPython, no cloud dependency, no app to install.

## Hardware

- M5Stack Tab5 (ESP32-P4, 1280×720 IPS touch display)
- UIFlow2 firmware, tested on **v2.5.3** (MicroPython v1.27.0)
- Optional: SD card for extended sensor history logging

## Getting started

1. Flash your Tab5 with UIFlow2 firmware (v2.5.3 or newer recommended).
2. Copy the project files to the device's `/flash` filesystem. `copy_to_tab5.sh` automates this via [`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html):
   ```bash
   ./copy_to_tab5.sh /dev/ttyACM0
   ```
3. Reboot the device. On first boot it starts in Wi-Fi access-point mode (`Tab5-Magic-Mirror`, a random per-device password shown on screen) so you can connect it to your Wi-Fi network from the web UI.
4. Open the device's IP address in a browser to configure widgets, dashboards, and sensors.

### Optional: precompiled boot (faster startup)

`tools/build_mpy.sh` precompiles the app to MicroPython bytecode (`.mpy`) using `mpy-cross`, splitting `main.py` into a thin `boot` stub plus a compiled `app.mpy`. This removes several seconds of on-device compilation at every boot. Requires `mpy-cross` matching the firmware's MicroPython version (`pip install mpy-cross==1.27.0.post2`):
```bash
./tools/build_mpy.sh
```

## Project structure

```
main.py                 App entry point: boot sequence, task scheduling, sensor loops
config.py                Config load/save (atomic writes, defaults, read-only cache)
web_server.py             Built-in HTTP server: dashboard editor, settings, API endpoints
fetch_worker.py           Background thread pool for network/blocking calls
wifi_manager.py           Wi-Fi STA/AP connection handling, early-connect boot optimization
widget_sources.py         Data fetching for RSS/iCal/weather/etc. widgets
ha_client.py               Home Assistant REST client
atom_client.py             ATOM relay board client
api_client.py               Generic HTTP API widget client
ntp_clock.py                NTP time sync
theme.py / i18n.py           Styling and translations
burger_menu.py               On-device navigation menu
clock_widget.py               Standalone clock screen

screens/                Full-screen UI views (dashboard, sensor history, settings, ...)
widgets/                 Individual dashboard widget implementations
sensors/                 Sensor drivers and processing (BME688, BMI270, mic, SD logging/reading, IAQ, quake trigger)
static/                 Web UI assets (HTML/CSS/JS)
tools/                  Build and diagnostic tooling (mpy-cross build, simulator, PNG conversion, boot stub)
pictures/               README preview images

aida_sse_server.py       Companion PC-side status server (see below)
copy_to_tab5.sh          Deploys the project to a connected Tab5 via mpremote
```

## Companion PC/server status server

`aida_sse_server.py` is a small standalone Python server meant to run on a PC or home server, exposing CPU/GPU/RAM/Docker status over HTTP for the `pc_status` / `server_status` widgets. It samples system state in a background thread (so widget requests never block on `nvidia-smi` or Docker calls) and exposes a `/health` endpoint for basic monitoring. Run it with:
```bash
python3 aida_sse_server.py
```

## Security

The web UI is unauthenticated by default (suitable for a trusted home network) but supports:
- **Optional HTTP Basic Auth**: set a password in System settings to require login for the web UI.
- **CSRF protection**: a per-boot random token is required on all state-changing requests.
- **Per-device random AP password**: the Wi-Fi setup access point uses a randomly generated password persisted to the device, not a hardcoded default.
- **Secret masking**: API keys and calendar URLs are never echoed back to the browser once saved.
- **Security headers**: CSP, X-Frame-Options, and related headers are set on all responses.

If exposing the device beyond your local network, put it behind a reverse proxy with HTTPS and enable the web UI password.

## Stability

The device is designed to run unattended for long periods:
- Config writes are atomic (write-temp, rotate backup, rename) to survive power loss mid-write.
- Background tasks are individually supervised and auto-restart on unexpected errors instead of taking down the whole app.
- A Wi-Fi watchdog detects a dropped connection or an idle access-point state and reconnects automatically.
- Network-dependent widgets read from a background-refreshed cache via a persistent thread pool, so a slow or failing network call never blocks the UI.
- If the main event loop ever crashes fatally, the device cleanly disconnects Wi-Fi and reboots itself rather than requiring a manual power cycle.

## Switching to the stock UIFlow2 menu

The System page in the web UI has a button to boot into the stock UIFlow2 startup menu (e.g. to reconfigure Wi-Fi using UIFlow2's own tools, or run UIFlow2 blockly programs). This requires the project to be deployed in precompiled form (`tools/build_mpy.sh`). When active, use only **RUN** from the UIFlow2 menu, never **DOWNLOAD** — DOWNLOAD overwrites `main.py` and will require re-deploying this project. The device automatically returns to running this app on the next boot after leaving the UIFlow2 menu.

This feature has been validated against the firmware's boot mechanism but not exhaustively tested on-device in every UIFlow2 menu state — test manually before relying on it.

## Known limitations

These are upstream MicroPython/UIFlow2/hardware limitations, not bugs in this project. Where possible, the app detects and reports them gracefully instead of silently producing bad data:
- **SD card**: SD card access is unreliable on some UIFlow2/MicroPython builds ([upstream issue #18984](https://github.com/m5stack/UIFlow2/issues/18984)). When unavailable, sensor history falls back to in-RAM buffers automatically.
- **Onboard microphone**: may return silence on some units/firmware versions. The app detects sustained all-zero readings and reports the sensor as unavailable rather than displaying fake "quiet" data.
- **BMI270 accelerometer**: may occasionally return all-zero readings. Detected the same way, avoiding false "calm" data and false shock-trigger suppression.
- **No hardware PNG decoder**: custom logo images are converted via a pure-Python PNG decoder at upload time (bounded to guard against decompression-bomb payloads); very large or unusual PNGs may not convert cleanly.

## License

MIT License — see below.

```
MIT License

Copyright (c) 2026 oxinon

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
