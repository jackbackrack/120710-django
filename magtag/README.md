# MagTag placard displays

CircuitPython for the Adafruit MagTag (ESP32-S2, 2.9" e-ink) to show a numbered
artwork's placard from the gallery site, live over Wi-Fi.

Each device holds a list of **placard numbers** (the numbered artworks in the
current show). It fetches the public JSON placard for a number and renders the
title, artist(s), year/medium/dimensions, and price on the e-ink screen. The
website decides which show is "current," so devices only carry numbers.

- API used (public, no login): `GET {SITE_URL}/placard/<number>/data/`
- Buttons: **A** = previous, **B** = next, **D** = refresh. Auto-refreshes every
  `REFRESH_SECONDS`.

## Setup
1. Install CircuitPython 8 or 9 on the MagTag.
2. Copy the Adafruit CircuitPython **bundle** libraries into `/lib`:
   `adafruit_magtag`, `adafruit_portalbase`, `adafruit_requests`,
   `adafruit_display_text`, `adafruit_bitmap_font`, `neopixel`.
3. Copy `code.py` to the CIRCUITPY drive.
4. Copy `settings.toml.example` → `settings.toml` and fill in Wi-Fi + `SITE_URL`.
5. Edit `PLACARD_NUMBERS` in `code.py` per device (one number for a single
   fixed placard, or several to page through with the buttons).

## Notes
- **Numbers, not database ids:** the endpoint keys on the show artwork *number*
  (set on the show), which matches "the numbered artworks in the current show."
- **Power:** the loop version assumes USB/mains power (typical for wall placards).
  For battery use, switch to deep sleep between updates (wake on a button
  `PinAlarm` + a `TimeAlarm`) and keep the current index in `alarm.sleep_memory`;
  the e-ink image persists while asleep.
- **E-ink longevity:** avoid refreshing faster than a few seconds; `code.py`
  waits out the panel's cooldown before each refresh.
