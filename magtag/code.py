# code.py — MagTag placard display (DEEP-SLEEP / battery version).
#
# Each MagTag holds a list of placard NUMBERS (the numbered artworks in the
# current show). On each wake it fetches one placard from the website, draws it
# on the e-ink screen, then deep-sleeps until a button is pressed or the refresh
# timer fires. The e-ink image persists while asleep, so power draw between
# updates is ~nil.
#
#   API:  GET {SITE_URL}/placard/<number>/data/   (public JSON, no login)
#
# Buttons (wake from sleep):  A = previous   B = next   D = refresh current
# The current index is kept in alarm.sleep_memory so it survives deep sleep.
#
# Config in settings.toml (see settings.toml.example):
#   CIRCUITPY_WIFI_SSID, CIRCUITPY_WIFI_PASSWORD, SITE_URL
# Libraries in /lib (Adafruit bundle):
#   adafruit_magtag, adafruit_portalbase, adafruit_requests,
#   adafruit_display_text, adafruit_bitmap_font, neopixel

import os
import time

import alarm
import board
import wifi
import adafruit_connection_manager
import adafruit_requests
from adafruit_magtag.magtag import MagTag

# ── Per-device configuration ─────────────────────────────────────────────────
# The show placard numbers THIS device cycles through. One entry = a single
# fixed placard (the buttons then just refresh it).
PLACARD_NUMBERS = [1, 2, 3, 4, 5]

REFRESH_SECONDS = 6 * 60 * 60          # wake and re-fetch at least this often
SITE_URL = os.getenv("SITE_URL", "https://example.com").rstrip("/")

# ── Restore state + decide what this wake should do ──────────────────────────
# alarm.sleep_memory persists across deep sleep (cleared only on power loss).
count = len(PLACARD_NUMBERS)
index = alarm.sleep_memory[0] if count and alarm.sleep_memory[0] < count else 0

wake = alarm.wake_alarm
if count and wake is not None and getattr(wake, "pin", None) is not None:
    if wake.pin is board.BUTTON_A:        # previous
        index = (index - 1) % count
    elif wake.pin is board.BUTTON_B:      # next
        index = (index + 1) % count
    # BUTTON_C: reserved.  BUTTON_D / TimeAlarm / first boot: just refresh.
alarm.sleep_memory[0] = index

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
    # Cached, warning-free pool + SSL context (the modern adafruit_requests pattern).
    pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
    ssl_context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
    session = adafruit_requests.Session(pool, ssl_context)
    resp = session.get("%s/placard/%d/data/" % (SITE_URL, number), timeout=20)
    try:
        return resp.json()
    finally:
        resp.close()


# ── Draw this wake's placard ─────────────────────────────────────────────────
if not count:
    _message("No placard numbers\nconfigured")
else:
    number = PLACARD_NUMBERS[index]
    magtag.peripherals.neopixels.brightness = 0.15
    magtag.peripherals.neopixels.fill((0, 0, 60))     # blue = working
    try:
        data = fetch_placard(number)
        if isinstance(data, dict) and not data.get("error"):
            _show_placard(data)
        else:
            _message("No placard #%d\nin the current show" % number)
    except Exception as err:
        _message("Can't load #%d\n%s" % (number, err))
    magtag.peripherals.neopixels.fill((0, 0, 0))

# Wait out the panel cooldown, refresh, and let the update finish before we cut
# power in deep sleep (a partial refresh would ghost the image).
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

# ── Power down until a button or the refresh timer ───────────────────────────
magtag.peripherals.neopixel_disable = True     # cut NeoPixel + speaker power
magtag.peripherals.speaker_disable = True

time_alarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + REFRESH_SECONDS)
button_alarms = [
    alarm.pin.PinAlarm(pin=pin, value=False, pull=True)
    for pin in (board.BUTTON_A, board.BUTTON_B, board.BUTTON_C, board.BUTTON_D)
]
alarm.exit_and_deep_sleep_until_alarms(time_alarm, *button_alarms)
