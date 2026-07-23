# code.py — MagTag placard display for the 120710 / eatart gallery site.
#
# Each MagTag holds a list of placard NUMBERS (the numbered artworks in the
# current show) and shows their placard — title, artist(s), year/medium/
# dimensions, price — fetched live from the website's placard API on the e-ink
# screen. The website resolves "the current show" itself, so these devices only
# need the numbers.
#
#   API:  GET {SITE_URL}/placard/<number>/data/   (public JSON, no login)
#
# Buttons:  A = previous placard   B = next placard   D = refresh from web
# It also re-fetches on its own every REFRESH_SECONDS.
#
# Config lives in settings.toml (see settings.toml.example):
#   CIRCUITPY_WIFI_SSID, CIRCUITPY_WIFI_PASSWORD, SITE_URL
# Required libraries in /lib (from the Adafruit CircuitPython bundle):
#   adafruit_magtag, adafruit_portalbase, adafruit_requests,
#   adafruit_display_text, adafruit_bitmap_font, neopixel
#
# Tested against CircuitPython 8/9 on the Adafruit MagTag (ESP32-S2).

import os
import time
import ssl

import wifi
import socketpool
import adafruit_requests
from adafruit_magtag.magtag import MagTag

# ── Per-device configuration ─────────────────────────────────────────────────
# The show placard numbers THIS device is responsible for. Use a single number
# for a one-piece placard, or several to let the buttons page through them.
PLACARD_NUMBERS = [1, 2, 3, 4, 5]

REFRESH_SECONDS = 6 * 60 * 60          # auto re-fetch from the web this often
SITE_URL = os.getenv("SITE_URL", "https://example.com").rstrip("/")

# E-ink should not be fully refreshed faster than this (panel longevity).
MIN_REFRESH_GAP = 3.0

magtag = MagTag()
magtag.peripherals.neopixels.brightness = 0.15

# ── Screen layout (296 x 128) ────────────────────────────────────────────────
HEADER = magtag.add_text(text_position=(4, 6),   text_scale=1, text_anchor_point=(0, 0))
TITLE  = magtag.add_text(text_position=(4, 22),  text_scale=2, text_anchor_point=(0, 0),
                         text_wrap=22, line_spacing=0.85)
ARTIST = magtag.add_text(text_position=(4, 66),  text_scale=1, text_anchor_point=(0, 0),
                         text_wrap=46)
META   = magtag.add_text(text_position=(4, 88),  text_scale=1, text_anchor_point=(0, 0),
                         text_wrap=46)
PRICE  = magtag.add_text(text_position=(4, 116), text_scale=1, text_anchor_point=(0, 0))

# ── Networking (native wifi + requests; creds from settings.toml) ────────────
_requests = None


def _session():
    global _requests
    if not wifi.radio.connected:
        wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"),
                           os.getenv("CIRCUITPY_WIFI_PASSWORD"))
    if _requests is None:
        pool = socketpool.SocketPool(wifi.radio)
        _requests = adafruit_requests.Session(pool, ssl.create_default_context())
    return _requests


def _blank():
    for i in (HEADER, TITLE, ARTIST, META, PRICE):
        magtag.set_text("", i, auto_refresh=False)


def _show_placard(data):
    aw = data.get("artwork", {})
    artists = ", ".join(aw.get("artists", []) or [])
    year = aw.get("year", "")
    parts = [str(year) if year else "", aw.get("medium", ""), aw.get("dimensions", "")]
    meta = " • ".join(p for p in parts if p)      # " • "
    price = aw.get("price", "") or ""
    if aw.get("is_sold"):
        price = (price + "  (SOLD)").strip()
    magtag.set_text("#%s   %s" % (data.get("number", "?"), data.get("show", "")),
                    HEADER, auto_refresh=False)
    magtag.set_text(aw.get("name", "Untitled"), TITLE, auto_refresh=False)
    magtag.set_text(artists, ARTIST, auto_refresh=False)
    magtag.set_text(meta, META, auto_refresh=False)
    magtag.set_text(price, PRICE, auto_refresh=False)


def _message(text):
    _blank()
    magtag.set_text(text, TITLE, auto_refresh=False)


_last_refresh_time = 0.0


def _safe_refresh():
    global _last_refresh_time
    gap = time.monotonic() - _last_refresh_time
    if gap < MIN_REFRESH_GAP:
        time.sleep(MIN_REFRESH_GAP - gap)
    # Wait out the panel's own refresh cooldown, then draw.
    while getattr(magtag.display, "time_to_refresh", 0):
        time.sleep(0.5)
    try:
        magtag.refresh()
    except Exception:      # RuntimeError "refresh too soon" etc.
        time.sleep(5)
        try:
            magtag.refresh()
        except Exception:
            pass
    _last_refresh_time = time.monotonic()


def fetch_and_show(number):
    url = "%s/placard/%d/data/" % (SITE_URL, number)
    magtag.peripherals.neopixels.fill((0, 0, 60))     # blue = loading
    try:
        resp = _session().get(url, timeout=20)
        data = resp.json()
        resp.close()
        if not isinstance(data, dict) or data.get("error"):
            _message("No placard #%d\nin the current show" % number)
        else:
            _show_placard(data)
    except Exception as err:
        _message("Can't load #%d\n%s" % (number, err))
    finally:
        magtag.peripherals.neopixels.fill((0, 0, 0))
    _safe_refresh()


# ── Main loop ────────────────────────────────────────────────────────────────
index = 0
if not PLACARD_NUMBERS:
    _message("No placard numbers\nconfigured")
    _safe_refresh()
    while True:
        time.sleep(60)

fetch_and_show(PLACARD_NUMBERS[index])
last_fetch = time.monotonic()

buttons = magtag.peripherals.buttons   # [A, B, C, D], pressed == value False

while True:
    now = time.monotonic()
    if not buttons[0].value:                       # A → previous
        index = (index - 1) % len(PLACARD_NUMBERS)
        fetch_and_show(PLACARD_NUMBERS[index]); last_fetch = now
    elif not buttons[1].value:                     # B → next
        index = (index + 1) % len(PLACARD_NUMBERS)
        fetch_and_show(PLACARD_NUMBERS[index]); last_fetch = now
    elif not buttons[3].value:                     # D → refresh current
        fetch_and_show(PLACARD_NUMBERS[index]); last_fetch = now
    elif now - last_fetch >= REFRESH_SECONDS:      # periodic auto-refresh
        fetch_and_show(PLACARD_NUMBERS[index]); last_fetch = now
    time.sleep(0.05)
