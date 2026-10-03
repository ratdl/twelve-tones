// ============================================================================
// File: twelve-tones.ck
// Description: 12-Track Generative Tone System
// Based on: "Introduction to Generative Music" by Alex Bainter
// More info on ChucK: https://chuck.stanford.edu/
// ============================================================================

/*
 * MIDI: use a bus named 'Twelve Tones': loopMIDI on Windows, IAC on macOS.
 * The Ableton p[roject monitors that input on channels 1–12.
 *
 * TWELVE-TONES GENERATIVE MUSIC SYSTEM
 *
 * A pure generative tone system based on long-period phasing.
 * Each track generates single tones at different timing cycles,
 * distributed across the audio spectrum from low to high.
 *
 * PRINCIPLES
 *
 * - Rational cycle lengths with a large least common multiple
 * - Randomness and probability for note selection
 * - Phasing through concurrent processes with different timing
 * - Single tones per track (no chords or harmony assumptions)
 * - No instrument role assumptions: pure tone generation
 *
 * ARCHITECTURE
 *
 * - Audio
 *   12 Voice instances (one per track). Each voice = two detuned
 *   SinOsc -> ADSR -> per-voice gain. All voices mix into a shared JCRev
 *   reverb bus and a dry bus, routed to DAC when SILENT is false,
 *   or to blackhole when SILENT is true. ChucK selects the audio device.
 *
 * - MIDI
 *   optional output so the Python visualiser can receive the same
 *   note stream. If no bus is available, MIDI-only mode exits;
 *   audible mode can continue with ChucK audio.
 *   - 12 sporked track shreds with independent timing.
 *   - Each track permanently owns one Voice, no shared pool / no
 *     allocation collisions between concurrent tracks.
 *
 *   TIMING CYCLES
 *
 * - Track 1:  23.47 seconds (very low register)
 * - Track 2:  29.89 seconds (low register)
 * - Track 3:  41.03 seconds (low-mid register)
 * - Track 4:   8.19 seconds (mid register)
 * - Track 5:   5.23 seconds (mid register)
 * - Track 6:   7.51 seconds (mid-high register)
 * - Track 7:  13.79 seconds (mid-high register)
 * - Track 8:  17.37 seconds (high register)
 * - Track 9:  19.13 seconds (high register)
 * - Track 10: 11.59 seconds (high register)
 * - Track 11: 31.61 seconds (very low register)
 * - Track 12: 37.27 seconds (full spectrum)
 *
 *   RECOMBINATION TIME
 *
 * Exact nominal onset-cycle LCM is approximately 6.824e24 years.
 * This is a timing-model result, not a complete audio recurrence period.
 *
 * USAGE:
 *
 * chuck twelve-tones.ck
 *
 * The visualiser and/or a DAW can optionally listen on the same bus.
 */

// ============================================================================
// GLOBAL CONFIGURATION
// ============================================================================

[0, 2, 4, 7, 9] @=> int PENTATONIC_SCALE[];

12 => int NUM_VOICES;

true => int DEBUG_OUTPUT;

// Set true to suppress audio while keeping realtime pacing (for MIDI-only
// workflows: the audio graph still drives the clock, but nothing reaches
// the speakers). Do NOT pass "-s" on the command line as that breaks
// realtime and makes every track fire as fast as the CPU allows.
true => int SILENT;

// MIDI channels are ordered by cycle length: Ch 1 = shortest cycle
// (innermost "ring"), Ch 12 = longest cycle (outermost). TRACKN_CHANNEL
// names refer to the internal track index (which keeps its voice,
// register, and cycle), but the channel it emits on is re-sorted below.
//
//   cycle     Track   → Ch (0-indexed value)
//    5.23s     5         1 (0)
//    7.51s     6         2 (1)
//    8.19s     4         3 (2)
//   11.59s     10        4 (3)
//   13.79s     7         5 (4)
//   17.37s     8         6 (5)
//   19.13s     9         7 (6)
//   23.47s     1         8 (7)
//   29.89s     2         9 (8)
//   31.61s     11       10 (9)
//   37.27s     12       11 (10)
//   41.03s     3        12 (11)
7 => int TRACK1_CHANNEL;   // 23.47s  -> Ch 8
8 => int TRACK2_CHANNEL;   // 29.89s  -> Ch 9
11 => int TRACK3_CHANNEL;  // 41.03s  -> Ch 12 (outermost)
2 => int TRACK4_CHANNEL;   //  8.19s  -> Ch 3
0 => int TRACK5_CHANNEL;   //  5.23s  -> Ch 1  (innermost)
1 => int TRACK6_CHANNEL;   //  7.51s  -> Ch 2
4 => int TRACK7_CHANNEL;   // 13.79s  -> Ch 5
5 => int TRACK8_CHANNEL;   // 17.37s  -> Ch 6
6 => int TRACK9_CHANNEL;   // 19.13s  -> Ch 7
3 => int TRACK10_CHANNEL;  // 11.59s  -> Ch 4
9 => int TRACK11_CHANNEL;  // 31.61s  -> Ch 10
10 => int TRACK12_CHANNEL; // 37.27s  -> Ch 11

// ============================================================================
// AUDIO OUTPUT SETUP
// ============================================================================

// Two shared master buses: a dry path and a reverb path. Voices send into
// both in parallel, giving a wet/dry mix without per-voice reverb copies.
// When SILENT is true we route to blackhole instead. The audio graph still
// runs at realtime (so MIDI pacing is correct) but nothing is heard.
Gain masterDry;
JCRev masterReverb;

if (SILENT)
{
    masterDry => blackhole;
    masterReverb => blackhole;
}
else
{
    masterDry => dac;
    masterReverb => dac;
}

0.85 => masterDry.gain;
0.22 => masterReverb.mix;

// ============================================================================
// MIDI OUTPUT SETUP (feeds the visualiser / a DAW)
// ============================================================================

MidiOut mout;

0 => int midiEnabled;

// Match the Live set's bus by name on macOS and Windows (loopMIDI).
// Optional override: chuck "twelve-tones.ck:Your MIDI Bus"
// Never fall back to port 0: on Windows that is usually the GS synth.
"Twelve Tones" => string midiBus;
if (me.args() > 0) me.arg(0) => midiBus;
-1 => int midiPort;
if (mout.open(midiBus))
{
    1 => midiEnabled;
    mout.num() => midiPort;
}

if (midiEnabled)
{
    <<< "=== MIDI Output Initialised ===" >>>;
    <<< "Port:", midiPort, " Name:", mout.name() >>>;
}
else
{
    <<< "=== Required MIDI bus unavailable:", midiBus, "===" >>>;
    <<< "On Windows, start loopMIDI and create this named port." >>>;
    <<< "On macOS, enable this IAC bus in Audio MIDI Setup." >>>;
    if (SILENT) me.exit();
    <<< "Continuing with ChucK audio only." >>>;
}
<<< "" >>>;

// ============================================================================
// VOICE CLASS: Audio synth + optional MIDI out
// ============================================================================

class Voice
{
    false => int isActive;
    int currentMidiNote;
    int currentChannel;
    int voiceID;
    int debugOutput;

    // MIDI output
    MidiOut @ midiOut;
    int midiEnabled;
    MidiMsg msg;

    // Synth UGens (wired in init()). Two slightly detuned sines per voice;
    // pure tones, shaped by an ADSR and blended into a shared reverb.
    SinOsc osc1;
    SinOsc osc2;
    Gain oscMix;
    ADSR env;
    Gain out;

    fun void init(int id, MidiOut midiOutRef, int midiOn, int debug)
    {
        id => voiceID;
        midiOutRef @=> midiOut;
        midiOn => midiEnabled;
        debug => debugOutput;

        // osc1+osc2 -> oscMix -> env -> out -> master buses (dry + reverb)
        osc1 => oscMix;
        osc2 => oscMix;
        oscMix => env => out;
        out => masterDry;
        out => masterReverb;

        0.0 => osc1.gain;
        0.0 => osc2.gain;
        1.0 => oscMix.gain;
        env.set(150::ms, 400::ms, 0.75, 1800::ms);
        0.085 => out.gain;   // headroom for 12 simultaneous voices
    }

    fun void noteOn(int midiNote, float velocity, int channel)
    {
        midiNote => currentMidiNote;
        channel => currentChannel;
        true => isActive;

        // --- audio ---
        Std.mtof(midiNote) => float freq;
        freq => osc1.freq;
        freq * 1.003 => osc2.freq;   // ~5-cent detune for warmth
        velocity * 0.5 => osc1.gain;
        velocity * 0.5 => osc2.gain;
        env.keyOn();

        // --- MIDI (optional) ---
        if (midiEnabled)
        {
            (velocity * 127.0) $ int => int midiVel;
            // Math.max/min return float in current ChucK, cast back to int.
            Math.max(1, Math.min(midiVel, 127)) $ int => midiVel;

            0x90 + channel => msg.data1;
            midiNote => msg.data2;
            midiVel => msg.data3;
            midiOut.send(msg);
        }

        if (debugOutput)
            <<< "t=", now/second, "s  [Voice", voiceID, "] noteOn: Ch", channel + 1,
                "Note", midiNote, "Vel", velocity >>>;
    }

    fun void noteOff()
    {
        if (!isActive)
            return;

        env.keyOff();

        if (midiEnabled)
        {
            0x80 + currentChannel => msg.data1;
            currentMidiNote => msg.data2;
            0 => msg.data3;
            midiOut.send(msg);
        }

        false => isActive;

        if (debugOutput)
            <<< "t=", now/second, "s  [Voice", voiceID, "] noteOff: Ch", currentChannel + 1 >>>;
    }

    fun int isNoteActive()
    {
        return isActive;
    }
}

// ============================================================================
// VOICE POOL: Track N owns voices[N-1], always.
// ============================================================================

Voice voices[NUM_VOICES];

for (0 => int i; i < NUM_VOICES; i++)
    voices[i].init(i, mout, midiEnabled, DEBUG_OUTPUT);

// ============================================================================
// TRACK 1: Tone Generator (23.47 second cycle)
// Very low register: MIDI 24-33 (C1-A1)
// ============================================================================

fun void track1()
{
    <<< "[Track 1] Starting (23.47s) - Very Low Register" >>>;

    voices[0] @=> Voice @ v;
    24 => int baseNote;  // C1

    while (true)
    {
        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.85, TRACK1_CHANNEL);

        21470::ms => now;
        v.noteOff();
        2000::ms => now; // Let the 1.8s release tail breathe
    }
}

// ============================================================================
// TRACK 2: Tone Generator (29.89 second cycle)
// Low register: MIDI 36-45 (C2-A2)
// ============================================================================

fun void track2()
{
    <<< "[Track 2] Starting (29.89s) - Low Register" >>>;

    voices[1] @=> Voice @ v;
    36 => int baseNote;  // C2

    while (true)
    {
        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.82, TRACK2_CHANNEL);

        27890::ms => now;
        v.noteOff();
        2000::ms => now;
    }
}

// ============================================================================
// TRACK 3: Tone Generator (41.03 second cycle)
// Low-mid register: MIDI 48-57 (C3-A3)
// ============================================================================

fun void track3()
{
    <<< "[Track 3] Starting (41.03s) - Low-Mid Register" >>>;

    voices[2] @=> Voice @ v;
    48 => int baseNote;  // C3

    while (true)
    {
        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.80, TRACK3_CHANNEL);

        39030::ms => now;
        v.noteOff();
        2000::ms => now;
    }
}

// ============================================================================
// TRACK 4: Tone Generator (8.19 second cycle)
// Mid register: MIDI 60-69 (C4-A4)
// ============================================================================

fun void track4()
{
    <<< "[Track 4] Starting (8.19s) - Mid Register" >>>;

    voices[3] @=> Voice @ v;
    60 => int baseNote;  // C4 (middle C)

    while (true)
    {
        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.78, TRACK4_CHANNEL);

        // Variable note duration (~55% or ~75% of cycle)
        dur noteDur;
        if (Math.random2f(0, 1) > 0.5)
            6000::ms => noteDur;
        else
            4500::ms => noteDur;

        noteDur => now;
        v.noteOff();

        // Remainder of the 8.19 second cycle
        8190::ms - noteDur => now;
    }
}

// ============================================================================
// TRACK 5: Tone Generator (5.23 second cycle)
// Mid register: MIDI 60-69 (C4-A4)
// ============================================================================

fun void track5()
{
    <<< "[Track 5] Starting (5.23s) - Mid Register" >>>;

    voices[4] @=> Voice @ v;
    60 => int baseNote;  // C4

    while (true)
    {
        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.78, TRACK5_CHANNEL);

        dur noteDur;
        if (Math.random2f(0, 1) > 0.6)
            3500::ms => noteDur;
        else
            2000::ms => noteDur;

        noteDur => now;
        v.noteOff();

        5230::ms - noteDur => now;
    }
}

// ============================================================================
// TRACK 6: Tone Generator (7.51 second cycle)
// Mid-high register: MIDI 72-81 (C5-A5)
// ============================================================================

fun void track6()
{
    <<< "[Track 6] Starting (7.51s) - Mid-High Register" >>>;

    voices[5] @=> Voice @ v;
    72 => int baseNote;  // C5

    while (true)
    {
        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.75, TRACK6_CHANNEL);

        dur noteDur;
        if (Math.random2f(0, 1) > 0.5)
            5000::ms => noteDur;
        else
            3000::ms => noteDur;

        noteDur => now;
        v.noteOff();

        7510::ms - noteDur => now;
    }
}

// ============================================================================
// TRACK 7: Tone Generator (13.79 second cycle)
// Mid-high register: MIDI 72-81 (C5-A5)
// ============================================================================

fun void track7()
{
    <<< "[Track 7] Starting (13.79s) - Mid-High Register" >>>;

    voices[6] @=> Voice @ v;
    72 => int baseNote;  // C5

    while (true)
    {
        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.75, TRACK7_CHANNEL);

        12000::ms => now;

        v.noteOff();

        13790::ms - 12000::ms => now;
    }
}

// ============================================================================
// TRACK 8: Tone Generator (17.37 second cycle)
// High register: MIDI 84-93 (C6-A6)
// ============================================================================

fun void track8()
{
    <<< "[Track 8] Starting (17.37s) - High Register" >>>;

    voices[7] @=> Voice @ v;
    84 => int baseNote;  // C6

    while (true)
    {
        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.72, TRACK8_CHANNEL);

        15370::ms => now;
        v.noteOff();
        2000::ms => now;
    }
}

// ============================================================================
// TRACK 9: Tone Generator (19.13 second cycle)
// High register: MIDI 84-93 (C6-A6)
// ============================================================================

fun void track9()
{
    <<< "[Track 9] Starting (19.13s) - High Register" >>>;

    voices[8] @=> Voice @ v;
    84 => int baseNote;  // C6

    while (true)
    {
        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.72, TRACK9_CHANNEL);

        1000::ms => now;

        v.noteOff();

        19130::ms - 1000::ms => now;
    }
}

// ============================================================================
// TRACK 10: Tone Generator (11.59 second cycle)
// High register: MIDI 84-93 (C6-A6)
// ============================================================================

fun void track10()
{
    <<< "[Track 10] Starting (11.59s) - High Register" >>>;

    voices[9] @=> Voice @ v;
    84 => int baseNote;  // C6

    while (true)
    {
        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.72, TRACK10_CHANNEL);

        dur noteDur;
        if (Math.random2f(0, 1) > 0.5)
            4000::ms => noteDur;
        else
            2000::ms => noteDur;

        noteDur => now;
        v.noteOff();

        11590::ms - noteDur => now;
    }
}

// ============================================================================
// TRACK 11: Tone Generator (31.61 second cycle)
// Very low register: MIDI 24-33 (C1-A1)
// ============================================================================

fun void track11()
{
    <<< "[Track 11] Starting (31.61s) - Very Low Register" >>>;

    voices[10] @=> Voice @ v;
    24 => int baseNote;  // C1

    while (true)
    {
        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.85, TRACK11_CHANNEL);

        29610::ms => now;
        v.noteOff();
        2000::ms => now;
    }
}

// ============================================================================
// TRACK 12: Tone Generator (37.27 second cycle)
// Full spectrum: MIDI 36-81 (varies across C2..A5)
// ============================================================================

fun void track12()
{
    <<< "[Track 12] Starting (37.27s) - Full Spectrum" >>>;

    voices[11] @=> Voice @ v;
    [36, 48, 60, 72] @=> int octaveBases[];

    while (true)
    {
        Math.random2(0, 3) => int octaveChoice;
        octaveBases[octaveChoice] => int baseNote;

        baseNote + PENTATONIC_SCALE[Math.random2(0, 4)] => int midiNote;

        v.noteOn(midiNote, 0.78, TRACK12_CHANNEL);

        35270::ms => now;
        v.noteOff();
        2000::ms => now;
    }
}

// ============================================================================
// MAIN PROGRAM
// ============================================================================

<<< "==================================================================" >>>;
<<< "TWELVE-TONES GENERATIVE TONE SYSTEM" >>>;
<<< "Based on principles from 'Introduction to Generative Music'" >>>;
<<< "by Alex Bainter" >>>;
<<< "==================================================================" >>>;
<<< "" >>>;
<<< "12 independent tone generators. MIDI channels ordered by cycle" >>>;
<<< "(Ch 1 = innermost / shortest, Ch 12 = outermost / longest):" >>>;
<<< "" >>>;
<<< "Ch  1:  5.23s - Mid Register (C4-A4)      [Track 5]" >>>;
<<< "Ch  2:  7.51s - Mid-High Register (C5-A5) [Track 6]" >>>;
<<< "Ch  3:  8.19s - Mid Register (C4-A4)      [Track 4]" >>>;
<<< "Ch  4: 11.59s - High Register (C6-A6)     [Track 10]" >>>;
<<< "Ch  5: 13.79s - Mid-High Register (C5-A5) [Track 7]" >>>;
<<< "Ch  6: 17.37s - High Register (C6-A6)     [Track 8]" >>>;
<<< "Ch  7: 19.13s - High Register (C6-A6)     [Track 9]" >>>;
<<< "Ch  8: 23.47s - Very Low Register (C1-A1) [Track 1]" >>>;
<<< "Ch  9: 29.89s - Low Register (C2-A2)      [Track 2]" >>>;
<<< "Ch 10: 31.61s - Very Low Register (C1-A1) [Track 11]" >>>;
<<< "Ch 11: 37.27s - Full Spectrum (C2-A5)     [Track 12]" >>>;
<<< "Ch 12: 41.03s - Low-Mid Register (C3-A3)  [Track 3]" >>>;
<<< "" >>>;
<<< "Audio:  ", (SILENT ? "suppressed (routed to blackhole, MIDI only)"
                          : "on (detuned SinOsc -> ADSR -> JCRev -> dac)") >>>;
<<< "MIDI:  ", (midiEnabled ? "on (port " + midiPort + ")" : "off") >>>;
<<< "==================================================================" >>>;
<<< "" >>>;

spork ~ track1();
spork ~ track2();
spork ~ track3();
spork ~ track4();
spork ~ track5();
spork ~ track6();
spork ~ track7();
spork ~ track8();
spork ~ track9();
spork ~ track10();
spork ~ track11();
spork ~ track12();

<<< "All 12 tone generators running..." >>>;
<<< "" >>>;

while (true)
{
    1::minute => now;
}
