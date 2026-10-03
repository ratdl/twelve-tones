#!/usr/bin/env bash
# Launch twelve-tones.ck and the Phase Orrery visualiser together.
# Ctrl-C (or either process exiting) tears the other down.

# On macOS, enable an IAC bus named 'Twelve Tones' before launching.

set -euo pipefail
cd "$(dirname "$0")"

command -v chuck >/dev/null || { echo "chuck not found in PATH"; exit 1; }

# Use the project venv if present (created via: python3 -m venv .venv &&
# .venv/bin/pip install mido python-rtmidi pygame). Falls back to system
# python3, which works only if those packages are installed there.
if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  command -v python3 >/dev/null || { echo "python3 not found in PATH"; exit 1; }
  PY=python3
fi

# The supervisor handles interrupts, child exits, and MIDI note release.
exec "$PY" playback.py --visualiser "$@"
