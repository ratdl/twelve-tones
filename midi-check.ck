// Three-second loopback check; does not require Ableton or the visualiser.
"Twelve Tones" => string bus;
if (me.args() > 0) me.arg(0) => bus;
MidiOut output;
MidiIn input;
if (!output.open(bus) || !input.open(bus))
{
    <<< "FAIL: cannot open MIDI bus", bus >>>;
    me.exit();
}
<<< "Loopback:", output.name(), input.name() >>>;
// Give Windows' asynchronous input connection time to become ready.
200::ms => now;
MidiMsg sent;
0x90 => sent.data1;
60 => sent.data2;
64 => sent.data3;
<<< "Send result:", output.send(sent) >>>;
now + 3::second => time deadline;
0 => int received;
MidiMsg incoming;
while (now < deadline)
{
    while (input.recv(incoming))
    {
        // Ignore the generative piece if it is also running on this bus.
        if (incoming.data1 == 0x90 && incoming.data2 == 60 && incoming.data3 == 64)
        {
            received++;
            <<< "Received test note:", incoming.data1, incoming.data2, incoming.data3 >>>;
        }
    }
    10::ms => now;
}
0x80 => sent.data1;
0 => sent.data3;
output.send(sent);
<<< "Received messages:", received >>>;
if (received == 0) <<< "FAIL: no loopback MIDI received; check the Windows MIDI service/driver." >>>;
else <<< "PASS: virtual MIDI loopback delivered messages." >>>;
