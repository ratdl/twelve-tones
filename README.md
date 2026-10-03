# Twelve Tones

Twelve Tones is a generative music project built from 12 independent pentatonic voices, each playing on its own repeating time cycle. ChucK, a programming language for music and audio with precise control over timing, was used to generate the note sequences and send them as MIDI to Ableton Live through the `Twelve Tones` virtual MIDI bus (using loopMIDI on Windows). Live supplies the instrument sounds, while the Phase Orrery visualiser in the movie represents each voice as an orbiting satellite. Different cycle lengths and pseudorandom note choices create shifting musical relationships; the Windows exporter can be used to capture the performance and its decay tail as a 4K video.

**Note**: The majority of the information below is (unusually) biased for Windows, but the project was started on macOS and will no doubt be runnable on macOS with a bit of judicious tweaking.

## MIDI Check

```powershell
.\run.ps1 -Check
```

This sends a middle-C note for three seconds through `Twelve Tones` and prints `PASS` when the matching MIDI message is received back. This verifies MIDI loopback, not Live's instruments or audible output. The launcher sends note-release messages when the check finishes or is interrupted. Live is optional for this check; an open, monitoring Live track may sound the test note.

## (Windows) Playback with Ableton Live

Requires ChucK on `PATH`, loopMIDI, Ableton Live, and the Python environment described below. The supplied set has been verified in Live 11 Suite on Windows.

ChucK:    https://chuck.stanford.edu/
loopMIDI: https://www.tobias-erichsen.de/software/loopmidi.html
Live:     https://www.ableton.com/en/live/

1. Start loopMIDI with a single port named `Twelve Tones` and keep it running.
2. Open `twelve-tones Project/twelve-tones.als` in Live.
3. In **Preferences > Link/Tempo/MIDI**, enable **Track** for the `Twelve Tones` input. The set routes its 12 instrument tracks from this port on channels 1–12, each with **Monitor In**.
4. From the repository directory in PowerShell, run:

   ```powershell
   .\run.ps1
   ```

Press Ctrl-C in the launching terminal to stop. During normal shutdown, the launcher stops its child processes and sends note-release messages on all 12 channels. Use `.\run.ps1 -Visualiser` to launch the visualiser with playback; closing that window also stops the generator. `-Duration 60` runs a one-minute session of generator playback, followed by cleanup. Live's transport does not need to run for monitored MIDI input. Force-killing the supervisor bypasses its cleanup; effects or instrument release tails may outlast note-offs.

All instrument, group, return, and master mixer faders are set to 0 dB. Each instrument's final track tain Utility sets its output level; Group Gain and Return Gain Utilities control those buses. Use the master's Master Headroom Utility to keep the combined output below clipping. Target instrument peaks around −12 dBFS and master peaks between −6 and −3 dBFS during playback.

The numbers above the meters are peak readouts, not fader settings.

ChucK sends MIDI to `Twelve Tones` by name. With `SILENT` enabled in the script, Live supplies all instruments and audio. Run ChucK normally; its [`--silent`/`-s` option](https://chuck.stanford.edu/doc/program/options.html) removes audio-clock pacing while retaining logical ChucK timing.

## macOS and the Visualiser

On macOS, enable IAC Driver in Audio MIDI Setup and name its bus `Twelve Tones`, using [Ableton's IAC setup instructions](https://help.ableton.com/hc/en-us/articles/209774225-Setting-up-a-virtual-MIDI-bus). Select the input containing `Twelve Tones` in Live and retain channels 1–12 and Monitor In. Run `./run.sh` to launch the generator and Phase Orrery visualiser together, or run the generator alone with the same shutdown handling from an activated Python environment:

```sh
python playback.py
```

The optional `visualiser.py` requires Python, `mido`, `python-rtmidi`, and `pygame`. Both the generator and visualiser select `Twelve Tones` on Windows and macOS. Use `--port <name>` to select another visualiser input. The Python supervisor and visualiser prefer an exact port name, then a unique substring match.

The launchers have been runtime-tested on Windows; macOS runtime behavior has not been verified here.

On Windows, launch it with Live playback and shared shutdown handling:

```powershell
.\run.ps1 -Visualiser
```

Create a local virtual environment using your installed Python executable. Use the interpreter location specified in `run.ps1` on Windows or `run.sh` on macOS so the launcher can find it. Activate that environment, then install the dependencies:

```sh
python -m pip install mido python-rtmidi pygame
```

Local Python environments, bytecode, caches, and generated packaging output are excluded from version control.

The orrery renders without text overlays. Press **F** to toggle fullscreen or **Esc** to quit. When launched through `run.sh` or `run.ps1 -Visualiser`, quitting the visualiser stops playback too. Running `visualiser.py` independently only controls the visualisation; it does not own a separately started generator.

## Exporting Video

On Windows, open the Live set with the MIDI input monitoring described above. The exporter records Live's Windows output loopback and the MIDI performance, then renders the same Phase Orrery graphics offline at native 3840×2160, 30 fps. Keep other applications quiet: audio played through that output is also captured.

Install the additional recording dependency in the activated Python environment:

```sh
python -m pip install PyAudioWPatch
```

FFmpeg and FFprobe must be on `PATH`. Run:

```powershell
.\run.ps1 -ExportVideo -AudioDevice SAMSUNG
```

Use a unique substring of Live's output device name for `-AudioDevice`; omit it only when Live uses Windows' default output. The default records five minutes of playing plus the release/reverb tail. `-Duration` changes the playing time. Note-offs preserve the natural decay. Audio is captured as floating point and stored as 32-bit PCM. Before playing, the exporter waits for three quiet seconds below −120 dBFS and measures idle noise; it aborts if the output stays active for 60 seconds. After playing, it waits for three seconds below −120 dBFS and at least 90 dB below the take's peak, or within 6 dB of the measured idle floor when that is higher. It tapers the final second, then adds one second of exact digital silence (−∞ dBFS). It aborts if the tail remains above that threshold for 180 seconds. A constant gain targets a −1 dBFS sample peak before AAC encoding, preserving the performance's dynamics.

The result replaces `visualiser.mov` only after encoding and format validation. It uses H.264 High, progressive 4K, BT.709, a 40 Mbps video target, 48 kHz stereo AAC at 384 kbps, and fast-start metadata. These follow [YouTube's encoding guidance](https://support.google.com/youtube/answer/1722171?hl=en); the [MOV container is supported](https://support.google.com/youtube/troubleshooter/2888402?hl=en). Ctrl-C stops playback and cancels the export. To cancel from another terminal, run `.\run.ps1 -StopExport`; the exporter releases MIDI notes and stops encoding. The existing video is preserved if capture or encoding fails.

## Pattern Mathematics

The piece combines fixed rhythmic cycles with changing note choices, so repeating the same timing pattern does not necessarily repeat the same music. The calculations below derive the cycles' first shared phase alignment using their least common multiple, then examine note-sequence matching under an ideal independent random-choice model. These results describe the nominal timing and stated probability assumptions; they do not establish an exact repetition period for the running program or its audio.

`twelve-tones.ck` runs 12 concurrent tone generators on different periodic cycles. Each cycle selects a pentatonic degree using ChucK's pseudorandom generator. Track 12 also chooses an octave; tracks 4, 5, 6, and 10 vary note duration while retaining their fixed onset cycles.

If we were to ask how long the piece takes to repeat, we can approach the answer from two points of view:

1. **Rhythmic Recurrence**: When all 12 cycles momentarily return to the same phase relationship they had at t = 0.
2. **Note-sequence Recurrence**: When the exact sequence of chosen notes repeats.

The first depends on the nominal cycle lengths. The second also depends on the note choices.

With exact nominal cycle lengths, the first return to the initial phase alignment is approximately **6.82 × 10²⁴ years**. This is a mathematical timing model, not a measured or guaranteed runtime period. With that in mind, the note-choice model and its assumptions appear below.

### Rhythmic Recurrence

For exact nominal onset cycles, rhythmic recurrence is their least common multiple (LCM). These rational cycle lengths are commensurable: they share a 10 ms unit. Their large LCM produces long-period phasing. The current cycles, grouped by internal ChucK track number, are:

| Track | MIDI channel | Cycle (ms) | Cycle (s) |
|------:|-------------:|-----------:|----------:|
| T1 | 8 | 23,470 | 23.47 |
| T2 | 9 | 29,890 | 29.89 |
| T3 | 12 | 41,030 | 41.03 |
| T4 | 3 | 8,190 | 8.19 |
| T5 | 1 | 5,230 | 5.23 |
| T6 | 2 | 7,510 | 7.51 |
| T7 | 5 | 13,790 | 13.79 |
| T8 | 6 | 17,370 | 17.37 |
| T9 | 7 | 19,130 | 19.13 |
| T10 | 4 | 11,590 | 11.59 |
| T11 | 10 | 31,610 | 31.61 |
| T12 | 11 | 37,270 | 37.27 |

All cycles are multiples of 10 ms. Dividing by that common factor gives:

| Cycle / 10 ms | Factorisation |
|-------------:|:--------------|
| 2347 | prime |
| 2989 | 7² · 61 |
| 4103 | 11 · 373 |
| 819 | 3² · 7 · 13 |
| 523 | prime |
| 751 | prime |
| 1379 | 7 · 197 |
| 1737 | 3² · 193 |
| 1913 | prime |
| 1159 | 19 · 61 |
| 3161 | 29 · 109 |
| 3727 | prime |

The LCM uses each of the 16 distinct primes at its largest required power:

```text
LCM / 10 ms = 3² · 7² · 11 · 13 · 19 · 29 · 61 · 109
            · 193 · 197 · 373 · 523 · 751 · 1913 · 2347 · 3727

LCM = 215349567542924815835646914112020010 ms
    ≈ 2.153 × 10³² seconds
    ≈ 6.824 × 10²⁴ years (365.25 days per year)
```

These cycles have few shared factors, so their LCM is enormous. For comparison, cycles of 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, and 41 seconds have an LCM of about 4.8 million years. The current millisecond cycles extend the phase recurrence by roughly 18 orders of magnitude.

### Note-sequence Recurrence

Returning to the same rhythmic phase does not reproduce the same notes. In the ideal timing model, counting note onsets in the half-open interval from time zero up to the LCM, the generators make:

```text
N = Σ (LCM / cycle_i) ≈ 1.882 × 10³² note events
```

Under an ideal independent, uniform random-choice model, each event picks one of five pentatonic degrees. The probability of matching all those degrees across two complete periods is:

```text
5^(-N) ≈ 10^(-1.316 × 10³²)
```

Track 12's octave choices further reduce the ideal-model probability of matching full pitches. Random duration choices would also need to match to reproduce the complete MIDI note-on/note-off sequence. Matching a few notes or a short phrase is possible; this calculation concerns all pentatonic degrees across an entire nominal rhythmic period.

### Is the Piece Strictly Non-repeating?

| Meaning of repetition | Result |
|:----------------------|:-------|
| All 12 nominal cycles return to their initial phase alignment | Approximately 6.82 × 10²⁴ years in the exact timing model |
| Two complete periods contain identical note choices | Negligible probability under the independent-choice model |

[`Math.random2`](https://chuck.stanford.edu/doc/reference/base.html) produces pseudorandom values. Independent uniform choices are an analytical model, not a proven property of this implementation. A deterministic finite-state model eventually revisits a state, but no actual PRNG or complete audio recurrence period has been derived here. Runtime clock accuracy and finite numeric precision also limit extrapolation of the nominal LCM. The project does not guarantee that all phrases are unique or that playback can run forever without repeating.

**Last Word**: 6.82 × 10²⁴ years is a REALLY long time...
