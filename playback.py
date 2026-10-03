#!/usr/bin/env python3
"""Own playback processes and release MIDI notes on every normal stop path."""

import argparse
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import mido


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='Twelve Tones')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--visualiser', action='store_true')
    mode.add_argument('--check', action='store_true')
    parser.add_argument('--duration', type=float, help='Stop after this many seconds')
    args = parser.parse_args()
    if args.duration is not None and args.duration <= 0:
        parser.error('--duration must be positive')
    chuck = shutil.which('chuck')
    if not chuck:
        parser.error('chuck must be on PATH')
    names = mido.get_output_names()
    matches = [name for name in names if name.casefold() == args.port.casefold()]
    matches = matches or [name for name in names if args.port.casefold() in name.casefold()]
    if len(matches) != 1:
        parser.error(f'Expected one output matching {args.port!r}; available: {names}')

    stopped = False

    def request_stop(_signal, _frame):
        nonlocal stopped
        stopped = True

    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request_stop)

    root = Path(__file__).resolve().parent
    children = []
    options = {'cwd': root}
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options['start_new_session'] = True

    # Open before spawning: playback must never start without a cleanup path.
    with mido.open_output(matches[0]) as output:
        try:
            if args.visualiser:
                children.append(subprocess.Popen(
                    [sys.executable, str(root / 'visualiser.py'), '--port', args.port], **options))
                ready_at = time.monotonic() + 1
                while not stopped and time.monotonic() < ready_at:
                    if children[0].poll() is not None:
                        return children[0].returncode
                    time.sleep(0.05)
            if stopped:
                return 0
            script = 'midi-check.ck' if args.check else 'twelve-tones.ck'
            children.append(subprocess.Popen([chuck, f'{script}:{args.port}'], **options))
            started = time.monotonic()
            while not stopped:
                for child in children:
                    if child.poll() is not None:
                        return child.returncode
                if args.duration is not None and time.monotonic() - started >= args.duration:
                    break
                time.sleep(0.05)
        finally:
            # Stop note producers before releasing notes, so none can restart.
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
            for child in reversed(children):
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            for channel in range(12):
                output.send(mido.Message('control_change', channel=channel, control=64, value=0))
                for note in range(128):
                    output.send(mido.Message('note_off', channel=channel, note=note, velocity=0))
                for control in (123, 120):
                    output.send(mido.Message('control_change', channel=channel, control=control, value=0))
            print('Playback stopped; MIDI notes released.', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
