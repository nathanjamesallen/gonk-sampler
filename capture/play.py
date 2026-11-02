#!/usr/bin/env python3
"""
Play a session .mid straight to a MIDI port — every channel, CC and program
change exactly as written. Use this instead of importing the clip into the
DAW (Ableton can drop or re-channel CCs and program changes on import).
The DAW only records audio.

    pip3 install mido python-rtmidi          # once
    python3 capture/play.py --list           # show MIDI output ports
    python3 capture/play.py sessions/out/Nord3P_Kick_Wild.mid --port "MIDI Express"
    python3 capture/play.py some.mid --dry-run   # print what would be sent, no MIDI needed

Workflow: arm the audio track in the DAW, hit record, then run this. The
silent lead-in bar plus the sync hit line everything up afterwards, so it
doesn't matter exactly when you press record. Ctrl-C stops and sends
all-notes-off.
"""
import argparse, sys, time
import mido


def describe(msg):
    if msg.type == "control_change":
        return f"ch {msg.channel + 1:2d}  CC {msg.control:3d} = {msg.value}"
    if msg.type == "program_change":
        return f"ch {msg.channel + 1:2d}  PROGRAM {msg.program}"
    if msg.type == "note_on":
        return f"ch {msg.channel + 1:2d}  note {msg.note} vel {msg.velocity}"
    return str(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("midi", nargs="?")
    ap.add_argument("--port", help="part of the output port name, e.g. 'MIDI Express' or 'MOTU'")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the schedule instead of sending")
    ap.add_argument("--countdown", type=float, default=3.0, help="seconds to wait before starting")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if a.list:
        for n in mido.get_output_names():
            print(n)
        return
    if not a.midi:
        sys.exit("give a .mid file (or --list)")
    mid = mido.MidiFile(a.midi)
    msgs = [m for m in mid if not m.is_meta or m.type == "end_of_track"]   # merged, time = seconds delta
    n_cc = sum(1 for m in msgs if m.type == "control_change")
    n_pc = sum(1 for m in msgs if m.type == "program_change")
    n_on = sum(1 for m in msgs if m.type == "note_on" and m.velocity)
    chans = sorted({m.channel + 1 for m in msgs if hasattr(m, "channel")})
    print(f"{a.midi}: {mid.length:.1f} s, {n_on} notes, {n_cc} CCs, {n_pc} program changes, MIDI channels {chans}")

    if a.dry_run:
        t = 0.0
        for m in msgs:
            t += m.time
            if m.type in ("control_change", "program_change") or (m.type == "note_on" and m.velocity):
                print(f"{t:8.3f}s  {describe(m)}")
        return

    names = mido.get_output_names()
    match = [n for n in names if a.port and a.port.lower() in n.lower()]
    if not match:
        sys.exit(f"no output port matching {a.port!r}. Ports: {names}")
    with mido.open_output(match[0]) as out:
        print(f"-> {match[0]}. Start recording now; playing in {a.countdown:.0f} s ...")
        time.sleep(a.countdown)
        t0 = time.perf_counter(); t = 0.0
        try:
            for m in msgs:
                t += m.time
                if m.is_meta:
                    continue
                wait = t0 + t - time.perf_counter()
                if wait > 0.002:
                    time.sleep(wait - 0.001)
                while time.perf_counter() < t0 + t:      # last ms: spin for tight timing
                    pass
                out.send(m)
                if not a.quiet and (m.type != "note_off" and not (m.type == "note_on" and m.velocity == 0)):
                    print(f"{t:8.3f}s  {describe(m)}")
        except KeyboardInterrupt:
            print("\nstopped")
        finally:
            for c in chans:
                out.send(mido.Message("control_change", channel=c - 1, control=123, value=0))
    print("done — stop recording after the last tail has died away")


if __name__ == "__main__":
    main()
