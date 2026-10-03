#!/usr/bin/env python3
"""
Phase Orrery: A visual companion for twelve-tones.ck.

Receives MIDI from the same bus that twelve-tones.ck writes to and renders 12 satellites orbiting a central sun, each at its track's nominal cycle period.

Notes bloom the satellites in pentatonic colours; phase trails accumulate over time so the phasing drift becomes visible to the eye.

Keys: F = fullscreen toggle, Esc = quit
Run:  python3 visualiser.py [--port NAME_SUBSTRING]
"""

import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import queue
import random
import signal
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import mido
import pygame
import pygame.gfxdraw


# ---- Track definitions (mirror twelve-tones.ck) -------------------------

# (channel, cycle_seconds, base_note, register_tier 0..4)
# Channels are sorted by cycle length: Ch 1 (index 0) = shortest / innermost,
# Ch 12 (index 11) = longest / outermost. The bracketed track number is the
# internal ChucK trackN() identifier that still owns this channel's voice.
TRACKS: List[Tuple[int, float, int, int]] = [
    (0,   5.23, 60, 2),   # Ch 1   mid           C4      [Track 5]
    (1,   7.51, 72, 3),   # Ch 2   mid-high      C5      [Track 6]
    (2,   8.19, 60, 2),   # Ch 3   mid           C4      [Track 4]
    (3,  11.59, 84, 4),   # Ch 4   high          C6      [Track 10]
    (4,  13.79, 72, 3),   # Ch 5   mid-high      C5      [Track 7]
    (5,  17.37, 84, 4),   # Ch 6   high          C6      [Track 8]
    (6,  19.13, 84, 4),   # Ch 7   high          C6      [Track 9]
    (7,  23.47, 24, 0),   # Ch 8   very low      C1      [Track 1]
    (8,  29.89, 36, 1),   # Ch 9   low           C2      [Track 2]
    (9,  31.61, 24, 0),   # Ch 10  very low      C1      [Track 11]
    (10, 37.27, 60, 2),   # Ch 11  full spectrum C2..C5  [Track 12]
    (11, 41.03, 48, 2),   # Ch 12  low-mid       C3      [Track 3]
]

# Matches PENTATONIC_SCALE in twelve-tones.ck.
PENTATONIC_STEPS = [0, 2, 4, 7, 9]

# Hue per pentatonic scale degree: root / 2nd / 3rd / 5th / 6th.
PENTATONIC_HUES = [
    (255, 190,  90),   # root - warm gold
    (130, 230, 140),   # 2nd  - spring green
    (130, 200, 255),   # 3rd  - sky blue
    (220, 150, 245),   # 5th  - violet
    (255, 130, 150),   # 6th  - rose
]

# Satellite body tint per register tier.
TIER_HUES = [
    (180,  90,  40),   # very low - amber
    (210, 130,  60),   # low      - copper
    ( 80, 180, 180),   # mid      - teal
    (130, 140, 230),   # mid-high - periwinkle
    (200, 140, 240),   # high     - violet
]

BG_COLOR    = ( 12,  10,  20)
GRID_COLOR  = ( 30,  28,  44)
STAR_COLOR  = (210, 200, 170)
ORBIT_COLOR = (130, 120,  90)
TRAIL_COLOR = (200, 180, 130)
SUN_COLOR   = (255, 220, 150)

TRAIL_SECONDS = 60.0 # hard ceiling, never keep history longer than this
TRAIL_CYCLES  = 1.5  # per-ring fade window, in cycle-lengths of that ring,
                     # so inner rings fade fast enough to not look solid
BLOOM_SECONDS = 2.2

DEFAULT_MIDI_PORT = "Twelve Tones"

# ---- Runtime state ------------------------------------------------------

@dataclass
class Satellite:
    channel: int
    cycle_s: float
    base_note: int
    tier: int
    phase: float = 0.0
    is_on: bool = False
    current_note: Optional[int] = None
    current_velocity: float = 0.0
    last_on_t: float = -1e9
    blooms: list = field(default_factory=list) # (start_t, hue_idx, velocity)
    trail: list = field(default_factory=list)  # (phase, timestamp)

    def advance(self, dt: float) -> None:
        self.phase = (self.phase + 2.0 * math.pi * dt / self.cycle_s) % (2.0 * math.pi)


def pentatonic_index_for(note: int) -> int:
    # All ChucK track bases are C-rooted, so the semitone offset mod 12 is
    # the scale degree index.
    step = note % 12
    for i, s in enumerate(PENTATONIC_STEPS):
        if s == step:
            return i
    return 0


# ---- MIDI input ---------------------------------------------------------

def pick_input_port(requested: Optional[str]) -> str:
    names = mido.get_input_names()
    if not names:
        raise SystemExit(
            "No MIDI input ports found.\n"
            "Start the Twelve Tones loopMIDI port, or enable the macOS IAC bus named Twelve Tones."
        )
    requested = requested or DEFAULT_MIDI_PORT
    exact = [n for n in names if n.casefold() == requested.casefold()]
    matches = exact or [n for n in names if requested.casefold() in n.casefold()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(
            f"Multiple MIDI inputs match '{requested}':\n  "
            + "\n  ".join(matches)
            + "\nPass --port <full-port-name> to select one."
        )
    raise SystemExit(
        f"No MIDI input port matching '{requested}'. Available ports:\n  "
        + "\n  ".join(names)
        + "\nPass --port <name-substring> to select another input."
    )


def open_midi(port_name: str, q: "queue.Queue"):
    def on_msg(msg):
        if msg.type == "note_on" and msg.velocity > 0:
            q.put(("on", msg.channel, msg.note, msg.velocity / 127.0))
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            q.put(("off", msg.channel, msg.note, 0.0))
    return mido.open_input(port_name, callback=on_msg)


# ---- Rendering helpers --------------------------------------------------

def build_starfield(w: int, h: int, n: int = 220):
    rng = random.Random(1337)
    return [(rng.randint(0, w - 1), rng.randint(0, h - 1),
             rng.randint(70, 210)) for _ in range(n)]


def make_glow(color, max_radius: int = 22) -> pygame.Surface:
    d = max_radius * 2
    s = pygame.Surface((d, d), pygame.SRCALPHA)
    for r in range(max_radius, 0, -1):
        t = 1.0 - r / max_radius
        alpha = int(110 * t * t)
        pygame.draw.circle(s, (*color, alpha), (max_radius, max_radius), r)
    return s


def make_trail_dot(color, max_radius: int = 4) -> pygame.Surface:
    """Soft radial stamp used for trail samples. Anti-aliased by stacking
    concentric circles with a squared alpha falloff. Each blit pastes a
    smooth dot whose edges fade to transparent instead of clipping a hard
    pixel boundary."""
    d = max_radius * 2 + 2
    s = pygame.Surface((d, d), pygame.SRCALPHA)
    c = d // 2
    for r in range(max_radius, 0, -1):
        t = 1.0 - r / (max_radius + 0.5)
        alpha = int(230 * t * t)
        pygame.draw.circle(s, (*color, alpha), (c, c), r)
    return s


def make_sun(radius: int) -> pygame.Surface:
    d = radius * 3
    s = pygame.Surface((d, d), pygame.SRCALPHA)
    for r in range(radius * 3 // 2, 0, -1):
        t = 1.0 - r / (radius * 3 / 2)
        alpha = int(60 * t * t)
        pygame.draw.circle(s, (*SUN_COLOR, alpha), (d // 2, d // 2), r)
    pygame.draw.circle(s, SUN_COLOR, (d // 2, d // 2), radius)
    return s


def draw_grid(surf, size, spacing=96):
    w, h = size
    for x in range(0, w, spacing):
        pygame.draw.line(surf, GRID_COLOR, (x, 0), (x, h), 1)
    for y in range(0, h, spacing):
        pygame.draw.line(surf, GRID_COLOR, (0, y), (w, y), 1)


def compute_radii(size, n: int, cycle_ranks: List[int]) -> List[int]:
    r_min = int(min(size) * 0.08)
    r_max = int(min(size) * 0.42)
    radii = [0] * n
    for rank, idx in enumerate(cycle_ranks):
        t = rank / max(1, n - 1)
        radii[idx] = int(r_min + (r_max - r_min) * t)
    return radii


def lerp(a, b, t): return a + (b - a) * t


# ---- Main loop ----------------------------------------------------------

def render_main(cleanup):
    parser = argparse.ArgumentParser(description="Phase Orrery for Twelve Tones")
    parser.add_argument("--port", help=f"MIDI input name or substring (default: {DEFAULT_MIDI_PORT})")
    parser.add_argument("--export", help="Render a 3840x2160, 30 fps H.264 movie using FFmpeg")
    parser.add_argument("--midi-recording", help="Timestamped MIDI JSON from export_video.py")
    parser.add_argument("--audio", help="Recorded Live WAV audio for the exported movie")
    args = parser.parse_args()
    if args.export and (not args.midi_recording or not args.audio):
        parser.error('--export requires --midi-recording and --audio')
    recording = None
    encoder = None
    if args.export:
        if not shutil.which('ffmpeg'):
            parser.error('ffmpeg must be on PATH')
        with open(args.midi_recording, encoding='utf-8') as file:
            recording = json.load(file)
        os.environ['SDL_VIDEODRIVER'] = 'dummy'
        os.environ['SDL_AUDIODRIVER'] = 'dummy'

    pygame.init()
    cleanup.callback(pygame.quit)
    pygame.display.set_caption("Phase Orrery for twelve tones")

    size = (3840, 2160) if args.export else (1280, 800)
    scale = 3 if args.export else 1
    screen = pygame.display.set_mode(size, pygame.RESIZABLE | pygame.DOUBLEBUF)

    # Convert SIGINT into a quiet flag-flip instead of a KeyboardInterrupt
    # traceback: the main loop polls `stop_requested` and exits cleanly
    # through the normal cleanup path below.
    stop_requested = False
    def _handle_sigint(_sig, _frame):
        nonlocal stop_requested
        stop_requested = True
    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    q: "queue.Queue" = queue.Queue()
    midi_port = None
    if not args.export:
        port_name = pick_input_port(args.port)
        print(f"Phase Orrery listening on: {port_name}")
        midi_port = open_midi(port_name, q)
        cleanup.callback(midi_port.close)
    else:
        encoder = subprocess.Popen([
            'ffmpeg', '-hide_banner', '-loglevel', 'warning', '-y',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '3840x2160',
            '-r', '30', '-i', 'pipe:0', '-i', args.audio,
            '-vf', 'scale=in_range=full:out_range=tv:out_color_matrix=bt709,setsar=1',
            '-c:v', 'libx264', '-preset', 'fast', '-profile:v', 'high',
            '-pix_fmt', 'yuv420p', '-b:v', '40M', '-maxrate', '45M', '-bufsize', '80M',
            '-g', '15', '-bf', '2', '-color_primaries', 'bt709',
            '-color_trc', 'bt709', '-colorspace', 'bt709',
            '-x264-params', 'colorprim=bt709:transfer=bt709:colormatrix=bt709',
            '-c:a', 'aac', '-b:a', '384k', '-ar', '48000', '-ac', '2',
            '-af', f'volume={recording.get("audio_gain_db", 0)}dB',
            '-movflags', '+faststart', '-use_editlist', '0',
            '-t', str(recording['duration']), args.export
        ], stdin=subprocess.PIPE)
        def stop_encoder():
            if encoder.poll() is None:
                encoder.terminate()
                encoder.wait()
        cleanup.callback(stop_encoder)

    sats = [Satellite(channel=c, cycle_s=cs, base_note=bn, tier=tr)
            for (c, cs, bn, tr) in TRACKS]

    cycle_order = sorted(range(len(sats)), key=lambda i: sats[i].cycle_s)

    tier_glows = [make_glow(c, 22 * scale) for c in TIER_HUES]
    trail_dots = [make_trail_dot((min(255, c[0] + 80),
                                  min(255, c[1] + 80),
                                  min(255, c[2] + 80)), 4 * scale)
                  for c in TIER_HUES]

    clock = pygame.time.Clock()
    fullscreen = False
    starfield = build_starfield(*size)
    t_start = time.monotonic()
    sun_pulse = 0.0
    radii = compute_radii(size, len(sats), cycle_order)

    running = True
    frame = 0
    event_index = 0
    frame_count = math.ceil(recording['duration'] * 30) if recording else None
    while running and not stop_requested:
        if args.export and (Path(__file__).resolve().parent / '.export-stop').exists():
            stop_requested = True
            break
        if recording:
            if frame >= frame_count:
                break
            dt = 0 if frame == 0 else 1 / 30
            now = frame / 30
            while event_index < len(recording['events']) and recording['events'][event_index]['time'] <= now:
                event = recording['events'][event_index]
                msg = event['message']
                kind = 'on' if msg['type'] == 'note_on' and msg['velocity'] > 0 else 'off'
                q.put((kind, msg['channel'], msg['note'], msg['velocity'] / 127))
                event_index += 1
        else:
            dt = clock.tick(60) / 1000.0
            now = time.monotonic() - t_start

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE and not fullscreen:
                size = (event.w, event.h)
                screen = pygame.display.set_mode(size, pygame.RESIZABLE | pygame.DOUBLEBUF)
                starfield = build_starfield(*size)
                radii = compute_radii(size, len(sats), cycle_order)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_f:
                    fullscreen = not fullscreen
                    if fullscreen:
                        screen = pygame.display.set_mode((0, 0),
                                                         pygame.FULLSCREEN | pygame.DOUBLEBUF)
                        size = screen.get_size()
                    else:
                        size = (1280, 800)
                        screen = pygame.display.set_mode(size,
                                                         pygame.RESIZABLE | pygame.DOUBLEBUF)
                    starfield = build_starfield(*size)
                    radii = compute_radii(size, len(sats), cycle_order)

        # Drain MIDI queue.
        while True:
            try:
                kind, ch, note, vel = q.get_nowait()
            except queue.Empty:
                break
            if not (0 <= ch < len(sats)):
                continue
            sat = sats[ch]
            if kind == "on":
                sat.is_on = True
                sat.current_note = note
                sat.current_velocity = vel
                sat.last_on_t = now
                sat.blooms.append((now, pentatonic_index_for(note), vel))
                sun_pulse = min(1.0, sun_pulse + 0.22)
            else:
                sat.is_on = False
                sat.current_note = None

        # Advance satellites + prune history. Each ring has its own cutoff
        # so inner (fast) rings don't accumulate enough history to look like
        # solid filled discs.
        for sat in sats:
            sat.advance(dt)
            sat.trail.append((sat.phase, now))
            sat_window = min(TRAIL_SECONDS, sat.cycle_s * TRAIL_CYCLES)
            sat_cutoff = now - sat_window
            if sat.trail and sat.trail[0][1] < sat_cutoff:
                # Drop from the front without rebuilding.
                drop = 0
                for _, ts in sat.trail:
                    if ts < sat_cutoff:
                        drop += 1
                    else:
                        break
                if drop:
                    del sat.trail[:drop]
            if sat.blooms:
                sat.blooms = [b for b in sat.blooms if now - b[0] < BLOOM_SECONDS]

        sun_pulse = max(0.0, sun_pulse - dt * 0.55)

        # ---- render ----
        screen.fill(BG_COLOR)
        draw_grid(screen, size, spacing=96 * scale)

        for (x, y, b) in starfield:
            screen.set_at((x, y), (b, max(60, b - 30), max(40, b - 60)))

        cx, cy = size[0] // 2, size[1] // 2

        # Orbit rings, anti-aliased single-pixel circles.
        for i in range(len(sats)):
            pygame.gfxdraw.aacircle(screen, cx, cy, radii[i], ORBIT_COLOR)

        # Phase trails, soft radial-glow stamps blitted along the orbit.
        # The pre-rendered sprite has anti-aliased edges (squared alpha
        # falloff) so the trail reads as a smooth glowing arc instead of a
        # string of hard 2px pearls. Per-sample alpha is set via the shared
        # sprite's set_alpha, which is multiplied with the per-pixel alpha.
        trail_surf = pygame.Surface(size, pygame.SRCALPHA)
        for i, sat in enumerate(sats):
            r = radii[i]
            if len(sat.trail) < 2:
                continue
            sat_window = min(TRAIL_SECONDS, sat.cycle_s * TRAIL_CYCLES)
            sprite = trail_dots[sat.tier]
            half = sprite.get_width() // 2
            # Sample dense enough that consecutive sprites overlap on the
            # outermost (largest-circumference) ring without leaving gaps.
            step = max(1, len(sat.trail) // 280)
            for k in range(0, len(sat.trail), step):
                ph, ts = sat.trail[k]
                age = now - ts
                a = 1.0 - age / sat_window
                if a <= 0:
                    continue
                # Squared fade for a punchy head + small linear floor so the
                # tail still leaves a faint smear before vanishing.
                alpha = int(255 * (a * a + 0.18 * a))
                if alpha <= 0:
                    continue
                sprite.set_alpha(alpha)
                x = cx + r * math.cos(ph)
                y = cy + r * math.sin(ph)
                trail_surf.blit(sprite, (int(x) - half, int(y) - half))
        screen.blit(trail_surf, (0, 0))

        # Alignment lines: two satellites momentarily on the same spoke.
        sat_positions = []
        for i, sat in enumerate(sats):
            r = radii[i]
            sx = cx + r * math.cos(sat.phase)
            sy = cy + r * math.sin(sat.phase)
            sat_positions.append((sx, sy, sat.phase))
        align_surf = None
        for i in range(len(sat_positions)):
            for j in range(i + 1, len(sat_positions)):
                d_ang = abs(((sat_positions[i][2] - sat_positions[j][2]
                              + math.pi) % (2 * math.pi)) - math.pi)
                if d_ang < math.radians(2.5):
                    if align_surf is None:
                        align_surf = pygame.Surface(size, pygame.SRCALPHA)
                    pygame.draw.aaline(align_surf, (240, 230, 200, 55),
                                       sat_positions[i][:2], sat_positions[j][:2])
        if align_surf is not None:
            screen.blit(align_surf, (0, 0))

        # Note blooms: filled disk sized by velocity, expands slightly and
        # fades over BLOOM_SECONDS. Colored by the note's pentatonic degree.
        for i, sat in enumerate(sats):
            sx, sy, _ = sat_positions[i]
            for (t0, hue_idx, vel) in sat.blooms:
                age = now - t0
                prog = age / BLOOM_SECONDS
                radius = int(scale * (8 + 36 * (0.35 + 0.65 * vel) * (0.55 + 0.45 * prog)))
                alpha = max(0, int(150 * (1 - prog) * (0.45 + 0.55 * vel)))
                if alpha <= 0 or radius <= 0:
                    continue
                color = PENTATONIC_HUES[hue_idx]
                d = radius * 2 + 4
                surf = pygame.Surface((d, d), pygame.SRCALPHA)
                pygame.draw.circle(surf, (*color, alpha),
                                   (d // 2, d // 2), radius)
                screen.blit(surf, (sx - d // 2, sy - d // 2))

        # Satellites with glow.
        for i, sat in enumerate(sats):
            sx, sy, _ = sat_positions[i]
            tier = TIER_HUES[sat.tier]
            body_r = 4 * scale
            if sat.is_on:
                age = now - sat.last_on_t
                puls = max(0.0, 1.0 - age / 0.5)
                body_r = int(scale * (4 + 8 * puls * (0.3 + 0.7 * sat.current_velocity)))
            glow = tier_glows[sat.tier]
            gw = glow.get_width()
            screen.blit(glow, (sx - gw // 2, sy - gw // 2))
            bright = (min(255, tier[0] + 80),
                      min(255, tier[1] + 80),
                      min(255, tier[2] + 80))
            pygame.draw.circle(screen, bright, (int(sx), int(sy)), body_r)

        # Central sun.
        sun_radius = int(scale * (14 + 12 * sun_pulse))
        sun_surf = make_sun(sun_radius)
        sw = sun_surf.get_width()
        screen.blit(sun_surf, (cx - sw // 2, cy - sw // 2))

        if encoder:
            encoder.stdin.write(pygame.image.tobytes(screen, 'RGB'))
            if frame % 300 == 0:
                print(f'Rendering {frame / 30:.0f} / {recording["duration"]:.0f} seconds', flush=True)
            frame += 1
        else:
            pygame.display.flip()

    if encoder:
        encoder.stdin.close()
        if stop_requested:
            raise KeyboardInterrupt
        if encoder.wait() != 0:
            raise RuntimeError('FFmpeg export failed')

    if stop_requested:
        print()  # clean newline after ^C before the prompt returns

    # Graceful cleanup. Wrap the MIDI close because rtmidi can raise on
    # teardown depending on platform and we don't want that to block us
    # getting to pygame.quit().
    try:
        if midi_port:
            midi_port.close()
    except Exception:
        pass
    pygame.quit()

    if stop_requested and not args.export:
        # rtmidi's listener thread and SDL's event thread are non-daemon
        # on macOS and can keep the interpreter alive after cleanup,
        # making ^C feel like it hung. Force exit on the interrupt path.
        os._exit(0)


def main():
    with ExitStack() as cleanup:
        render_main(cleanup)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Reached only if ^C lands during startup before the main-loop
        # signal handler is installed. Suppress the traceback.
        print()
