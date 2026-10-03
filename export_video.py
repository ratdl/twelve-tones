#!/usr/bin/env python3
"""Record Live and MIDI, then render a synchronised Phase Orrery video."""

import argparse
from array import array
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave

import mido

STOP_FILE = Path(__file__).resolve().parent / '.export-stop'


def select_port(names, requested):
    matches = [n for n in names if n.casefold() == requested.casefold()]
    matches = matches or [n for n in names if requested.casefold() in n.casefold()]
    if len(matches) != 1:
        raise RuntimeError(f'Expected one port matching {requested!r}; available: {names}')
    return matches[0]


def release_notes(output, hard=False):
    for channel in range(12):
        output.send(mido.Message('control_change', channel=channel, control=64, value=0))
        for note in range(128):
            output.send(mido.Message('note_off', channel=channel, note=note))
        output.send(mido.Message('control_change', channel=channel, control=123, value=0))
        if hard:
            output.send(mido.Message('control_change', channel=channel, control=120, value=0))


def capture(args, folder):
    if sys.platform != 'win32':
        raise RuntimeError('Live audio capture currently requires Windows WASAPI loopback.')
    import pyaudiowpatch as pa

    chuck = shutil.which('chuck')
    if not chuck:
        raise RuntimeError('chuck must be on PATH')
    audio_path = folder / 'audio.wav'
    midi_path = folder / 'midi.json'
    events = []
    levels = []
    lock = threading.Lock()
    ready = threading.Event()
    finish_audio = threading.Event()
    started = None
    samples = 0
    peak_level = 0.0
    overflowed = threading.Event()
    child = None
    stopped = threading.Event()
    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: stopped.set())

    with pa.PyAudio() as audio:
        devices = list(audio.get_loopback_device_info_generator())
        if args.audio_device:
            matches = [d for d in devices if args.audio_device.casefold() in d['name'].casefold()]
            if len(matches) != 1:
                raise RuntimeError(f'Expected one audio device; available: {[d["name"] for d in devices]}')
            device = matches[0]
        else:
            device = audio.get_default_wasapi_loopback()
        rate = int(device['defaultSampleRate'])
        channels = min(2, device['maxInputChannels'])
        print(f'Capturing: {device["name"]}, {rate} Hz, {channels} channels', flush=True)
        with wave.open(str(audio_path), 'wb') as wav:
            wav.setparams((channels, 4, rate, 0, 'NONE', 'not compressed'))

            def on_audio(data, frame_count, timing, status):
                nonlocal started, samples, peak_level
                if finish_audio.is_set():
                    return (None, pa.paComplete)
                if status & pa.paInputOverflow:
                    overflowed.set()
                values = array('f', data)
                peak = max((abs(v) for v in values), default=0)
                pcm = array('i', (round(max(-1, min(1, v)) * 2147483647) for v in values))
                with lock:
                    if started is None:
                        started = time.monotonic()
                        ready.set()
                    wav.writeframesraw(pcm.tobytes())
                    samples += frame_count
                    levels.append((samples / rate, peak))
                    peak_level = max(peak_level, peak)
                return (None, pa.paContinue)

            def on_midi(msg):
                if started is not None and msg.type in ('note_on', 'note_off'):
                    with lock:
                        events.append({'time': time.monotonic() - started, 'message': msg.dict()})

            in_name = select_port(mido.get_input_names(), args.port)
            out_name = select_port(mido.get_output_names(), args.port)
            with mido.open_input(in_name, callback=on_midi), mido.open_output(out_name) as output:
                stream = audio.open(format=pa.paFloat32, channels=channels, rate=rate,
                                    input=True, input_device_index=device['index'],
                                    frames_per_buffer=1024, stream_callback=on_audio)
                try:
                    if not ready.wait(5):
                        raise RuntimeError('Audio capture did not start')
                    # A previous performance may still be reverberating.
                    idle_deadline = time.monotonic() + 60
                    while True:
                        if stopped.wait(0.05) or STOP_FILE.exists():
                            raise KeyboardInterrupt
                        if overflowed.is_set() or not stream.is_active():
                            raise RuntimeError('Audio capture dropped samples or stopped; export aborted')
                        with lock:
                            end = samples / rate
                            recent = [p for t, p in levels if t >= end - 3]
                            if end >= 3 and recent and max(recent) <= 1e-6:
                                idle_peak = max(recent)
                                lead_frames = samples
                                break
                        if time.monotonic() > idle_deadline:
                            raise RuntimeError('Audio output did not become idle; stop other audio and retry')
                    child = subprocess.Popen([chuck, f'twelve-tones.ck:{args.port}'],
                                             cwd=Path(__file__).resolve().parent,
                                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    play_started = time.monotonic()
                    while time.monotonic() - play_started < args.duration:
                        if stopped.wait(0.05) or STOP_FILE.exists():
                            raise KeyboardInterrupt
                        if child.poll() is not None:
                            raise RuntimeError('ChucK exited during capture')
                        if overflowed.is_set() or not stream.is_active():
                            raise RuntimeError('Audio capture dropped samples or stopped; export aborted')
                    child.terminate()
                    child.wait(timeout=5)
                    release_notes(output)  # No All Sound Off: preserve Live release/reverb tails.
                    tail_started = time.monotonic()
                    print('Playing finished; recording the Live tail until sustained silence.', flush=True)
                    while True:
                        if stopped.wait(0.05) or STOP_FILE.exists():
                            raise KeyboardInterrupt
                        if overflowed.is_set() or not stream.is_active():
                            raise RuntimeError('Audio capture dropped samples or stopped; export aborted')
                        with lock:
                            end = samples / rate
                            recent = [peak for t, peak in levels if t >= end - 3]
                        # Require a tail below -120 dBFS and at least 90 dB
                        # below the take's peak, allowing the measured idle noise.
                        floor = max(min(1e-6, peak_level * 10 ** (-90 / 20)), idle_peak * 2)
                        if time.monotonic() - tail_started >= 3 and recent and max(recent) <= floor:
                            break
                        if time.monotonic() - tail_started > args.max_tail:
                            raise RuntimeError('Tail did not reach the silence threshold; export aborted')
                    print(f'Tail captured: {time.monotonic() - tail_started:.1f} seconds', flush=True)
                finally:
                    if child is not None and child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait()
                    release_notes(output, hard=True)
                    finish_audio.set()
                    # Let the callback return paComplete before closing. Closing
                    # an active WASAPI callback stream can block its Python thread.
                    finish_deadline = time.monotonic() + 5
                    while stream.is_active() and time.monotonic() < finish_deadline:
                        time.sleep(0.02)
                    stream.close()
    if peak_level < 1e-4:
        raise RuntimeError('No useful audio captured. Check Live monitoring and the audio device.')
    if peak_level >= 1:
        raise RuntimeError('The recorded input clipped; reduce Live master Utility gain and retry')
    with wave.open(str(audio_path), 'rb') as wav:
        params = wav.getparams()
        pcm = array('i', wav.readframes(wav.getnframes()))
    del pcm[:lead_frames * channels]
    lead_seconds = lead_frames / rate
    events = [{'time': e['time'] - lead_seconds, 'message': e['message']}
              for e in events if e['time'] >= lead_seconds]
    # The natural decay is already at the measured noise/silence floor. Taper only
    # that final second, then append one second of exact digital silence.
    fade_samples = rate * channels
    for i in range(fade_samples):
        index = len(pcm) - fade_samples + i
        pcm[index] = round(pcm[index] * (1 - i / fade_samples))
    pcm.extend([0] * fade_samples)
    with wave.open(str(audio_path), 'wb') as wav:
        wav.setparams(params)
        wav.writeframes(pcm.tobytes())
    duration = len(pcm) / (rate * channels)
    gain_db = -1 - 20 * math.log10(peak_level)
    midi_path.write_text(json.dumps({'duration': duration, 'events': events,
                                    'audio_gain_db': gain_db}), encoding='utf-8')
    print(f'Audio peak normalization: {gain_db:+.1f} dB (target -1 dBFS)', flush=True)
    return audio_path, midi_path, duration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='Twelve Tones')
    parser.add_argument('--audio-device', help='Unique substring of the Live output loopback device')
    parser.add_argument('--duration', type=float, default=300, help='Playing seconds, excluding the tail')
    parser.add_argument('--max-tail', type=float, default=180, help='Abort if the tail exceeds this many seconds')
    parser.add_argument('--output', type=Path, default=Path('visualiser.mov'))
    parser.add_argument('--stop', action='store_true', help='Cancel the active export in this repository')
    args = parser.parse_args()
    if args.stop:
        STOP_FILE.write_text('stop', encoding='utf-8')
        print('Export cancellation requested.', flush=True)
        return
    STOP_FILE.unlink(missing_ok=True)
    if args.duration <= 0 or args.max_tail < 3:
        parser.error('duration must be positive and max-tail must be at least 3')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        parser.error('ffmpeg and ffprobe must be on PATH')
    root = Path(__file__).resolve().parent
    output = args.output.resolve()
    # Keep the existing movie until capture, encoding and validation succeed.
    with tempfile.TemporaryDirectory(prefix='twelve-tones-export-') as temp:
        folder = Path(temp)
        audio, midi, duration = capture(args, folder)
        def cancel(_signal, _frame):
            raise KeyboardInterrupt
        for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), cancel)
        encoded = folder / ('video' + output.suffix)
        renderer = subprocess.Popen([
            sys.executable, str(root / 'visualiser.py'), '--export', str(encoded),
            '--midi-recording', str(midi), '--audio', str(audio)])
        try:
            while renderer.poll() is None:
                if STOP_FILE.exists():
                    raise KeyboardInterrupt
                time.sleep(0.1)
            if renderer.returncode != 0:
                raise RuntimeError('Visualiser export failed')
        finally:
            if renderer.poll() is None:
                # Give the renderer a chance to close FFmpeg itself, including
                # on Windows where terminate() bypasses Python signal handlers.
                STOP_FILE.write_text('stop', encoding='utf-8')
                try:
                    renderer.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    renderer.terminate()
                    try:
                        renderer.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        renderer.kill()
                        renderer.wait()
        info = json.loads(subprocess.check_output(
            ['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(encoded)]))
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        sound = next(s for s in info['streams'] if s['codec_type'] == 'audio')
        if ((video['width'], video['height']) != (3840, 2160)
                or video['codec_name'] != 'h264' or sound['codec_name'] != 'aac'
                or video['avg_frame_rate'] != '30/1' or video['pix_fmt'] != 'yuv420p'
                or video.get('color_primaries') != 'bt709' or video.get('color_transfer') != 'bt709'
                or video.get('color_space') != 'bt709'
                or sound['sample_rate'] != '48000' or sound['channels'] != 2
                or abs(float(info['format']['duration']) - duration) > 0.1):
            raise RuntimeError('Encoded video failed format validation')
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=output.suffix, delete=False) as file:
            staged = Path(file.name)
        try:
            shutil.copyfile(encoded, staged)
            os.replace(staged, output)
        finally:
            staged.unlink(missing_ok=True)
        print(f'Exported {output}: 3840x2160, 30 fps, {duration:.2f} seconds', flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Export cancelled; playback stopped. Existing video preserved.', file=sys.stderr)
        sys.exit(130)
