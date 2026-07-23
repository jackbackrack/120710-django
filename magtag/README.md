# MagTag placard displays

CircuitPython for the Adafruit MagTag (ESP32-S2, 2.9" e-ink) to show a numbered
artwork's placard from the gallery site, live over Wi-Fi.

Each device holds a list of **placard numbers** (the numbered artworks in the
current show). It fetches the public JSON placard for a number and renders the
title, artist(s), year/medium/dimensions, and price on the e-ink screen. The
website decides which show is "current," so devices only carry numbers.

- API used (public, no login): `GET {SITE_URL}/placard/<number>/data/`
- Buttons: **A** = previous, **B** = next, **D** = refresh. Also wakes and
  re-fetches on its own every `REFRESH_SECONDS`.
- **Deep sleep between updates** for battery life: it wakes on a button press or
  the timer, draws once, then powers down. The e-ink image persists while asleep,
  so it draws almost no power in between. The current placard index is kept in
  `alarm.sleep_memory` so it survives sleep. Expect a few seconds after a button
  press (wake → Wi-Fi → fetch → e-ink redraw) before the screen changes.

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
- **Power:** `code.py` deep-sleeps between refreshes (battery-friendly). If you'd
  rather it stay awake (USB/mains, instant buttons), swap the deep-sleep block at
  the end for a `while True` polling loop.
- **E-ink longevity:** it waits out the panel's cooldown before refreshing, and
  lets the update finish before deep-sleeping so the image can't ghost.
- **Wake pins:** the MagTag buttons are RTC-capable on the ESP32-S2, so they can
  wake the board from deep sleep.
