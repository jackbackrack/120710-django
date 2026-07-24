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
import terminalio
import wifi
import adafruit_connection_manager
import adafruit_miniqr
import adafruit_requests
from adafruit_display_text import label as text_label
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
# Text is drawn into our own group and stacked DYNAMICALLY: each line is placed
# below the actual rendered height of the one above it, so a title (or medium)
# that wraps to multiple lines pushes everything below it down — no overlap.
FONT = terminalio.FONT
LINE_H = FONT.get_bounding_box()[1]     # font line height (px) at scale 1
LEFT = 4                                # left margin (px)
TOP = 4                                 # top margin (px)
TITLE_WRAP = 17                         # chars/line for the scale-2 title
BODY_WRAP = 34                          # chars/line for the scale-1 body lines

placard_group = displayio.Group()
magtag.display.root_group.append(placard_group)

# The built-in font is ASCII only, so map the common Unicode we get from the site
# (× in dimensions, en/em dashes in year ranges, curly quotes, bullets) to ASCII.
_SUBST = {"×": "x", "–": "-", "—": "-", "‘": "'", "’": "'",
          "“": '"', "”": '"', "•": "*", "…": "..."}


def _ascii(s):
    if not s:
        return ""
    for k, v in _SUBST.items():
        s = s.replace(k, v)
    return s.encode("ascii", "ignore").decode("ascii")


def _wrap(text, max_chars):
    """Word-wrap ASCII text to <= max_chars per line (hard-breaking long words)."""
    words = _ascii(text).split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            lines.append(cur); cur = w
    lines.append(cur)
    out = []
    for ln in lines:
        while len(ln) > max_chars:
            out.append(ln[:max_chars]); ln = ln[max_chars:]
        out.append(ln)
    return out


def _clear():
    while len(placard_group):
        placard_group.pop()


def _add_line(text, scale, wrap_chars, y, gap):
    """Add a wrapped text block at top-left (LEFT, y); return the y below it."""
    lines = _wrap(text, wrap_chars)
    if not lines:
        return y
    lbl = text_label.Label(FONT, text="\n".join(lines), scale=scale, color=0x000000,
                           anchor_point=(0, 0), anchored_position=(LEFT, y),
                           line_spacing=1.0)
    placard_group.append(lbl)
    return y + len(lines) * LINE_H * scale + gap   # stack by actual line count


def _message(text):
    _clear()
    _add_line(text, 1, BODY_WRAP, TOP, 0)


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
    placard_group.append(tile)
    print("QR added (%dx%d px) for %s" % (side, side, url))


def _show_placard(data):
    _clear()
    aw = data.get("artwork", {})
    artists = ", ".join(aw.get("artists", []) or [])
    y = TOP
    y = _add_line(aw.get("name", "Untitled"), 2, TITLE_WRAP, y, LINE_H // 2)  # half-line after title
    y = _add_line(str(aw.get("year", "") or ""), 1, BODY_WRAP, y, 2)
    y = _add_line(artists, 1, BODY_WRAP, y, 2)
    y = _add_line(aw.get("medium", "") or "", 1, BODY_WRAP, y, 2)
    y = _add_line(aw.get("dimensions", "") or "", 1, BODY_WRAP, y, 2)
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
