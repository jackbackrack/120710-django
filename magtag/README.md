# MagTag placard displays

CircuitPython for the Adafruit MagTag (ESP32-S2, 2.9" e-ink) to show one
numbered artwork's placard from the gallery site, live over Wi-Fi.

Every device runs the **same** `code.py`; the only per-device difference is the
placard number, set in `settings.toml` as `PLACARD_NUMBER`. So programming 40
devices is just "set one number per board," not editing code.

- API used (public, no login): `GET {SITE_URL}/placard/<number>/data/`
- The website resolves "the current show," so devices only carry a number —
  rotate the show and the same devices show the new show's pieces.
- **Deep sleep between updates** for battery life: it wakes on any button or the
  `REFRESH_SECONDS` timer, draws once, then powers down (the e-ink image
  persists). Expect a few seconds after a press (wake → Wi-Fi → fetch → redraw).

## One-time setup (per computer)
1. Install CircuitPython (8/9/10) on a MagTag.
2. Copy the Adafruit **bundle** libraries into `/lib` (bundle matching your
   CircuitPython major version): `adafruit_magtag`, `adafruit_portalbase`,
   `adafruit_requests`, `adafruit_connection_manager`, `adafruit_display_text`,
   `adafruit_bitmap_font`, `neopixel`. (Optional: keep a copy in `magtag/lib/`
   so `provision.sh` can sync it too.)
3. `cp settings.toml.example settings.base.toml`, then fill in Wi-Fi + `SITE_URL`
   (same for every device). `settings.base.toml` is git-ignored.

## Programming many devices (least effort)
For each board: plug in USB, then run

```
./provision.sh <artwork-number>
```

e.g. `./provision.sh 7`. The script copies `code.py`, writes `settings.toml`
(your base + `PLACARD_NUMBER=<number>`), optionally syncs `lib/`, and flushes.
Eject and the board reboots showing that placard. Repeat with the next number.

- Default drive is `/Volumes/CIRCUITPY`; pass a path as the 2nd arg if different
  (or if you mount several at once).
- Track which number went on which board however you like (a sticker on the back
  is simplest).

## Single device by hand
Copy `code.py` + `/lib`, then `settings.toml.example` → `settings.toml` and set
`CIRCUITPY_WIFI_*`, `SITE_URL`, and `PLACARD_NUMBER`.

## Notes
- **Numbers, not database ids:** the endpoint keys on the show artwork *number*.
- **CircuitPython 10 (incl. alphas):** use libraries from the **10.x** bundle;
  mixing 8/9 `.mpy` files gives an "incompatible .mpy" error.
- **E-ink longevity:** it waits out the panel cooldown and lets the refresh
  finish before sleeping, so the image can't ghost.
- **Power:** to stay awake instead (USB/mains, instant buttons), replace the
  deep-sleep block at the end of `code.py` with a `while True` polling loop.
