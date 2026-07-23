# code.py — MagTag placard display (single fixed artwork number, deep-sleep).
#
# Every device runs this SAME file. The only per-device difference is the
# placard number, set in settings.toml as PLACARD_NUMBER — so 40 devices are
# provisioned by changing one value (see provision.sh), not by editing code.
#
# On each wake it fetches its one placard from the website, draws it on the
# e-ink screen, then deep-sleeps until a button is pressed or the timer fires.
# The e-ink image persists while asleep, so power draw between updates is ~nil.
#
#   API:  GET {SITE_URL}/placard/<number>/data/   (public JSON, no login)
#
# settings.toml:  CIRCUITPY_WIFI_SSID, CIRCUITPY_WIFI_PASSWORD, SITE_URL,
#                 PLACARD_NUMBER
# /lib (Adafruit bundle for your CircuitPython major version):
#   adafruit_magtag, adafruit_portalbase, adafruit_requests,
#   adafruit_connection_manager, adafruit_display_text, adafruit_bitmap_font,
#   neopixel

import os
import time

import alarm
import board
import wifi
import adafruit_connection_manager
import adafruit_requests
from adafruit_magtag.magtag import MagTag

# ── Config (all from settings.toml; identical code on every device) ──────────
PLACARD_NUMBER = int(os.getenv("PLACARD_NUMBER", "0"))
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", str(6 * 60 * 60)))
SITE_URL = os.getenv("SITE_URL", "https://example.com").rstrip("/")

magtag = MagTag()

# ── Screen layout (296 x 128) ────────────────────────────────────────────────
HEADER = magtag.add_text(text_position=(4, 6),   text_scale=1, text_anchor_point=(0, 0))
TITLE  = magtag.add_text(text_position=(4, 22),  text_scale=2, text_anchor_point=(0, 0),
                         text_wrap=22, line_spacing=0.85)
ARTIST = magtag.add_text(text_position=(4, 66),  text_scale=1, text_anchor_point=(0, 0),
                         text_wrap=46)
META   = magtag.add_text(text_position=(4, 88),  text_scale=1, text_anchor_point=(0, 0),
                         text_wrap=46)
PRICE  = magtag.add_text(text_position=(4, 116), text_scale=1, text_anchor_point=(0, 0))


def _message(text):
    for i in (HEADER, ARTIST, META, PRICE):
        magtag.set_text("", i, auto_refresh=False)
    magtag.set_text(text, TITLE, auto_refresh=False)


def _show_placard(data):
    aw = data.get("artwork", {})
    artists = ", ".join(aw.get("artists", []) or [])
    year = aw.get("year", "")
    parts = [str(year) if year else "", aw.get("medium", ""), aw.get("dimensions", "")]
    price = aw.get("price", "") or ""
    if aw.get("is_sold"):
        price = (price + "  (SOLD)").strip()
    magtag.set_text("#%s   %s" % (data.get("number", "?"), data.get("show", "")),
                    HEADER, auto_refresh=False)
    magtag.set_text(aw.get("name", "Untitled"), TITLE, auto_refresh=False)
    magtag.set_text(artists, ARTIST, auto_refresh=False)
    magtag.set_text(" • ".join(p for p in parts if p), META, auto_refresh=False)
    magtag.set_text(price, PRICE, auto_refresh=False)


def fetch_placard(number):
    if not wifi.radio.connected:
        wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"),
                           os.getenv("CIRCUITPY_WIFI_PASSWORD"))
    pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
    ssl_context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
    session = adafruit_requests.Session(pool, ssl_context)
    resp = session.get("%s/placard/%d/data/" % (SITE_URL, number), timeout=20)
    try:
        return resp.json()
    finally:
        resp.close()


# ── Programming escape hatch ─────────────────────────────────────────────────
# Hold BUTTON_A while the board wakes to STAY AWAKE instead of deep-sleeping, so
# the CIRCUITPY drive stays mounted (writable) and the serial REPL is available
# for copying new code / editing settings. Press RESET when done to resume normal
# operation. (In deep sleep the drive is unmounted and you can't reprogram it.)
if not magtag.peripherals.buttons[0].value:
    _message("Programming mode\nEdit files, then\npress RESET")
    try:
        magtag.refresh()
    except Exception:
        pass
    magtag.peripherals.neopixels.brightness = 0.2
    magtag.peripherals.neopixels.fill((40, 30, 0))   # amber = awake for programming
    while True:                                       # Ctrl-C here drops to the REPL
        time.sleep(1)

# ── Draw this device's placard ───────────────────────────────────────────────
if PLACARD_NUMBER <= 0:
    _message("Set PLACARD_NUMBER\nin settings.toml")
else:
    magtag.peripherals.neopixels.brightness = 0.15
    magtag.peripherals.neopixels.fill((0, 0, 60))     # blue = working
    try:
        data = fetch_placard(PLACARD_NUMBER)
        if isinstance(data, dict) and not data.get("error"):
            _show_placard(data)
        else:
            _message("No placard #%d\nin the current show" % PLACARD_NUMBER)
    except Exception as err:
        _message("Can't load #%d\n%s" % (PLACARD_NUMBER, err))
    magtag.peripherals.neopixels.fill((0, 0, 0))

# Refresh, and let the e-ink update finish before power is cut (avoids ghosting).
while getattr(magtag.display, "time_to_refresh", 0):
    time.sleep(0.5)
try:
    magtag.refresh()
except Exception:
    time.sleep(5)
    try:
        magtag.refresh()
    except Exception:
        pass
time.sleep(3)

# ── Deep sleep until any button or the refresh timer ─────────────────────────
magtag.peripherals.neopixel_disable = True
magtag.peripherals.speaker_disable = True

# The MagTag library claims the buttons as DigitalInOut; release those pins so we
# can arm them as deep-sleep wake alarms (otherwise: "BUTTON_A in use").
for _btn in magtag.peripherals.buttons:
    _btn.deinit()

time_alarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + REFRESH_SECONDS)
button_alarms = [
    alarm.pin.PinAlarm(pin=pin, value=False, pull=True)
    for pin in (board.BUTTON_A, board.BUTTON_B, board.BUTTON_C, board.BUTTON_D)
]
alarm.exit_and_deep_sleep_until_alarms(time_alarm, *button_alarms)
