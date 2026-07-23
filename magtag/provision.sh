#!/usr/bin/env bash
#
# Provision one MagTag with a unique placard number — the least-effort way to
# program many identical devices.
#
#   ./provision.sh <artwork-number> [/path/to/CIRCUITPY]
#
# For each device: plug it in over USB, run the command with its number, unplug.
# Everything else (code.py, Wi-Fi, SITE_URL, libraries) is identical across all
# devices, so only the number changes per board.
#
# One-time setup:
#   1. cp settings.toml.example settings.base.toml   # then fill in Wi-Fi + SITE_URL
#      (settings.base.toml is git-ignored; its PLACARD_NUMBER line is ignored — the
#       script sets the real number per device.)
#   2. (optional) put the CircuitPython libraries in ./lib to sync them too.
set -euo pipefail

NUM="${1:?usage: provision.sh <artwork-number> [CIRCUITPY path]}"
DRIVE="${2:-/Volumes/CIRCUITPY}"
HERE="$(cd "$(dirname "$0")" && pwd)"

case "$NUM" in ''|*[!0-9]*) echo "error: number must be a positive integer" >&2; exit 1;; esac
[ -d "$DRIVE" ] || { echo "error: CIRCUITPY drive not found at '$DRIVE'" >&2; exit 1; }
[ -f "$HERE/settings.base.toml" ] || {
    echo "error: create $HERE/settings.base.toml (cp settings.toml.example settings.base.toml, fill in Wi-Fi + SITE_URL)" >&2
    exit 1
}

cp "$HERE/code.py" "$DRIVE/code.py"

# settings.toml = base (minus any PLACARD_NUMBER) + this device's number
grep -v -E '^[[:space:]]*PLACARD_NUMBER' "$HERE/settings.base.toml" > "$DRIVE/settings.toml"
printf '\nPLACARD_NUMBER = "%s"\n' "$NUM" >> "$DRIVE/settings.toml"

# Optionally sync libraries if you keep a local ./lib copy.
if [ -d "$HERE/lib" ]; then
    mkdir -p "$DRIVE/lib"
    cp -R "$HERE/lib/." "$DRIVE/lib/"
fi

sync
echo "✓ Provisioned $DRIVE  →  PLACARD_NUMBER=$NUM"
echo "  Eject the drive; the MagTag will reboot and show placard #$NUM."
