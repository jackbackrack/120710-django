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
import json

import alarm
import board
import displayio
import wifi
import adafruit_connection_manager
import adafruit_miniqr
import adafruit_requests
from adafruit_magtag.magtag import MagTag

# ── Config (all from settings.toml; identical code on every device) ──────────
PLACARD_NUMBER = int(os.getenv("PLACARD_NUMBER", "0"))
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", str(6 * 60 * 60)))
SITE_URL = os.getenv("SITE_URL", "https://example.com").rstrip("/")
# The venue this device lives at — resolves to that site's current show. Shared by
# all devices at a venue (set it in settings.base.toml). If blank, falls back to
# the site-wide "current show".
SITE_SLUG = (os.getenv("SITE_SLUG", "") or "").strip()

magtag = MagTag()

# ── Screen layout (296 x 128) ────────────────────────────────────────────────
# Text on the left (~190px), QR code on the right. Order: title, year(s), artist,
# medium, dimensions.
_TW = 30   # wrap width (chars) for the left text column at scale 1
TITLE  = magtag.add_text(text_position=(4, 4),   text_scale=2, text_anchor_point=(0, 0),
                         text_wrap=15, line_spacing=0.85)
YEAR   = magtag.add_text(text_position=(4, 44),  text_scale=1, text_anchor_point=(0, 0))
ARTIST = magtag.add_text(text_position=(4, 60),  text_scale=1, text_anchor_point=(0, 0), text_wrap=_TW)
MEDIUM = magtag.add_text(text_position=(4, 82),  text_scale=1, text_anchor_point=(0, 0), text_wrap=_TW)
DIMS   = magtag.add_text(text_position=(4, 110), text_scale=1, text_anchor_point=(0, 0), text_wrap=_TW)


def _message(text):
    for i in (YEAR, ARTIST, MEDIUM, DIMS):
        magtag.set_text("", i, auto_refresh=False)
    magtag.set_text(text, TITLE, auto_refresh=False)


def _add_qr(url, scale=2, border=2):
    """Draw a QR code for `url` on the right side of the screen."""
    qr = adafruit_miniqr.QRCode(qr_type=None, error_correct=adafruit_miniqr.L)
    qr.add_data(url.encode("utf-8"))
    qr.make()
    m = qr.matrix
    side = (m.width + 2 * border) * scale
    bmp = displayio.Bitmap(side, side, 2)
    pal = displayio.Palette(2)
    pal[0] = 0xFFFFFF   # background
    pal[1] = 0x000000   # modules
    for qy in range(m.height):
        for qx in range(m.width):
            if m[qx, qy]:
                px = (qx + border) * scale
                py = (qy + border) * scale
                for dy in range(scale):
                    for dx in range(scale):
                        bmp[px + dx, py + dy] = 1
    tile = displayio.TileGrid(bmp, pixel_shader=pal)
    tile.x = 296 - side - 4
    tile.y = max(0, (128 - side) // 2)
    # Append to the group that's actually on screen.
    group = getattr(magtag, "splash", None) or magtag.display.root_group
    group.append(tile)
    print("QR added (%dx%d px) for %s" % (side, side, url))


def _show_placard(data):
    aw = data.get("artwork", {})
    artists = ", ".join(aw.get("artists", []) or [])
    magtag.set_text(aw.get("name", "Untitled"), TITLE, auto_refresh=False)
    magtag.set_text(str(aw.get("year", "") or ""), YEAR, auto_refresh=False)
    magtag.set_text(artists, ARTIST, auto_refresh=False)
    magtag.set_text(aw.get("medium", "") or "", MEDIUM, auto_refresh=False)
    magtag.set_text(aw.get("dimensions", "") or "", DIMS, auto_refresh=False)
    url = aw.get("url") or ""
    print("placard url:", repr(url))
    if url:
        try:
            _add_qr(url)
        except Exception as err:      # QR is a bonus — never fail the placard over it
            print("QR error:", err)
    else:
        print("No 'url' in the JSON — redeploy the site so the placard API returns it.")


def fetch_placard(number):
    """Return (status_code, body_text) for the placard endpoint."""
    if not wifi.radio.connected:
        wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"),
                           os.getenv("CIRCUITPY_WIFI_PASSWORD"))
    pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
    ssl_context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
    session = adafruit_requests.Session(pool, ssl_context)
    if SITE_SLUG:
        url = "%s/site/%s/placard/%d/data/" % (SITE_URL, SITE_SLUG, number)
    else:
        url = "%s/placard/%d/data/" % (SITE_URL, number)
    print("GET", url)
    # Ask for uncompressed JSON — the board doesn't gunzip, and a gzipped body
    # would look like a "JSON syntax error".
    resp = session.get(url, headers={"Accept": "application/json",
                                     "Accept-Encoding": "identity"}, timeout=20)
    status, body = resp.status_code, resp.text
    resp.close()
    print("HTTP", status, "(%d bytes)" % len(body))
    return status, body


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
        status, body = fetch_placard(PLACARD_NUMBER)
        try:
            data = json.loads(body)
        except Exception:
            data = None
        if data is None:
            # Not JSON — show the status + a snippet so the cause is visible
            # (e.g. an HTML error/redirect page, or a wrong SITE_URL).
            snippet = " ".join(body.split())[:48]
            print("Non-JSON body:", body[:200])
            _message("Bad reply #%d\nHTTP %d\n%s" % (PLACARD_NUMBER, status, snippet))
        elif data.get("error"):
            _message("No placard #%d\nin the current show" % PLACARD_NUMBER)
        else:
            _show_placard(data)
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
