#!/usr/bin/env python3
"""
Session spec -> MIDI clip + hit map.

    python3 capture/session.py sessions/nord3p_kit1.json

Writes <out>/<name>.mid and <out>/<name>_map.json next to the spec.
The map is what capture.py uses to find and label every hit, so keep it
with the recordings.

Spec (JSON):
{
  "name": "Nord3P Kit1",
  "midi_channel": 10,             # 1-16
  "bpm": 80,                      # 80 BPM 4/4 -> 1 bar = 3.0 s
  "bars_per_hit": 1,              # default spacing; override per sound
  "gap_bars": 2,                  # silence between sounds
  "velocities": [20, 45, 70, 95, 115, 127],
  "round_robins": 4,
  "passes": ["clean", "low", "med", "high", "bonkers"],
  "sounds": [
    {"name": "kick",  "note": 60, "role": "kick"},
    {"name": "crash", "note": 69, "role": "cymbal", "bars_per_hit": 2}
  ]
}
Roles: kick snare clap rim hat pedalhat openhat tom cymbal ride bell perc zap.
The role picks the sampler pad (GM drum map, see layout.py) unless a sound
sets "slot": <note> itself.

Changing the Nord mid-session (derived sounds):
    {"name": "open hat", "note": 67, "role": "openhat", "cc": {"21": 110}}
Before that sound's block the clip sends each CC to the Nord channel that
plays note 67. Default "cc_mode": "individual" (manual p.14, documented):
on the Nord set the individual MIDI channels Ch.1..Ch.6 for Nord channels
1..6 (global stays 10), and the CC goes out on that MIDI channel.
"cc_mode": "global" uses Channel Select (CC 70) on the global channel instead;
its values are not documented clearly (manual example: ch 4 -> 71), so set
"channel_select_values" from what your Nord actually sends. Put derived sounds AFTER the sounds
they borrow from (the change sticks until you reload the preset), and reload the
preset before every pass so each pass starts identical.
Next preset, same pattern (one clip covers several Nord presets):
    "programs": [{"program": 0, "suffix": ""}, {"program": 1, "suffix": " B"}]
repeats every sound once per program, sending Program Change before each run
(add "bank": [msb, lsb] to send CC0/CC32 first). Names get the suffix so they
stay unique ("kick", "kick B"). A single sound can also carry "program": n.

Subtle round-robin variation (so RR hits really differ):
    "rr_cc": {"31": [62, 63, 65, 66]}
sends value k before round robin k of every layer (here: tiny tone-pitch moves).
Use absolute values near the preset's own setting.

Nord 3P CCs worth using: 21 noise decay, 50 tone decay, 31/63 tone pitch
(MSB/LSB), 14 noise filter freq, 23 dist amount, 56 click level.
"""
import json, os, sys
import mido

SYNC_NOTE_DEFAULT = None   # sync hit uses the first sound's note at velocity 127
NORD_NOTE_TO_CHANNEL = {60: 1, 62: 2, 64: 3, 65: 4, 67: 5, 69: 6}   # factory note map
CHANNEL_SELECT_CC = 70


def build(spec):
    bpm = spec.get("bpm", 80)
    tpb = 480
    bar_ticks = 4 * tpb
    bar_s = 4 * 60.0 / bpm
    ch = spec.get("midi_channel", 10) - 1
    vels = spec["velocities"]
    rr = spec["round_robins"]
    gap = spec.get("gap_bars", 2)
    sounds = spec["sounds"]
    sync_note = spec.get("sync_note", sounds[0]["note"])
    note_ch = {int(k): v for k, v in spec.get("nord_channels", {}).items()} or NORD_NOTE_TO_CHANNEL
    cc_mode = spec.get("cc_mode", "individual")
    sel_vals = spec.get("channel_select_values")                        # CC70 values for ch 1..6 (global mode)
    indiv = {int(k): v for k, v in spec.get("individual_channels", {}).items()} or {i: i for i in range(1, 7)}
    if cc_mode == "global" and not sel_vals:
        raise SystemExit('cc_mode "global" needs "channel_select_values": [ch1..ch6 values your Nord sends for CC 70]')
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from layout import assign_slots
    progs = spec.get("programs")
    if progs:                                  # repeat the sound list once per Nord preset
        sounds = [dict(s, name=s["name"] + p.get("suffix", f" {i+1}" if i else ""),
                       program=p["program"], **({"bank": p["bank"]} if p.get("bank") else {}))
                  for i, p in enumerate(progs) for s in sounds]
    sounds = [dict(s, slot_fixed=s.get("slot") is not None) for s in sounds]
    sounds = assign_slots(sounds)

    events, hits = [("note", 0, sync_note, 127, ch)], []
    cur_prog = None
    bar = 2                                    # bar 1 sync, bar 2 silent
    for si, s in enumerate(sounds):
        bph = s.get("bars_per_hit", spec.get("bars_per_hit", 1))
        t = bar * bar_ticks - 2 * tpb          # 1.5 s before the block: previous tails have died
        step = tpb // 8
        if s.get("program") is not None and s.get("program") != cur_prog:
            if s.get("bank"):
                events.append(("cc", t, 0, int(s["bank"][0]), ch)); t += step
                events.append(("cc", t, 32, int(s["bank"][1]), ch)); t += step
            events.append(("pc", t, int(s["program"]), 0, ch)); t += 2 * step
            cur_prog = s["program"]
        need_sel = s.get("cc") or s.get("rr_cc")
        ch_n = s.get("nord_channel") or note_ch.get(s["note"])
        if need_sel and ch_n is None:
            raise SystemExit(f"{s['name']}: can't tell which Nord channel plays note {s['note']} — add \"nord_channel\"")
        def send_cc(t0, pairs, gap):
            """individual: CC on the Nord channel's own MIDI channel; global: CC70 select first."""
            evs = []
            if cc_mode == "global":
                evs.append(("cc", t0, CHANNEL_SELECT_CC, sel_vals[ch_n - 1], ch)); t0 += gap
                mch = ch
            else:
                mch = indiv[ch_n] - 1
            for cc, val in pairs:
                evs.append(("cc", t0, int(cc), int(val), mch)); t0 += gap
            return evs
        if s.get("cc"):
            evs = send_cc(t, sorted(s["cc"].items(), key=lambda kv: int(kv[0])), step)
            events += evs; t = evs[-1][1] + step
        rr_cc = s.get("rr_cc") or {}
        for li, v in enumerate(vels):
            for k in range(rr):
                if rr_cc:                      # beat 4 of the bar before this hit: tail is gone
                    events += send_cc(bar * bar_ticks - tpb,
                                      [(cc, vals[k % len(vals)]) for cc, vals in
                                       sorted(rr_cc.items(), key=lambda kv: int(kv[0]))], tpb // 16)
                events.append(("note", bar * bar_ticks, s["note"], v, ch))
                hits.append(dict(index=len(hits), sound=si, name=s["name"], role=s.get("role", "perc"),
                                 note=s["note"], velocity=v, layer=li + 1, rr=k + 1,
                                 seconds=round(bar * bar_s, 4), window=round(bph * bar_s, 4)))
                bar += bph
        bar += gap
    bar += 2
    # One MIDI track per MIDI channel: DAWs (Ableton) send a whole track on ONE channel,
    # so notes/program changes (global ch) and each Nord channel's CCs get their own track.
    mid = mido.MidiFile(type=1, ticks_per_beat=tpb)
    nlen = tpb // 8
    chans = [ch] + sorted({e[4] for e in events if e[4] != ch})
    for c in chans:
        tr = mido.MidiTrack(); mid.tracks.append(tr)
        label = f"{spec['name']} notes (MIDI ch {c+1})" if c == ch else f"Nord ch {[k for k, v in indiv.items() if v - 1 == c][0] if c in [v - 1 for v in indiv.values()] else '?'} CCs (MIDI ch {c+1})"
        tr.append(mido.MetaMessage("track_name", name=label, time=0))
        if c == ch:
            tr += [mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0),
                   mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0)]
        now = 0
        for kind, t, a1, a2, mch in sorted([e for e in events if e[4] == c], key=lambda e: e[1]):
            if kind == "cc":
                tr.append(mido.Message("control_change", channel=mch, control=a1, value=a2, time=t - now)); now = t
            elif kind == "pc":
                tr.append(mido.Message("program_change", channel=mch, program=a1, time=t - now)); now = t
            else:
                tr.append(mido.Message("note_on", channel=mch, note=a1, velocity=a2, time=t - now)); now = t
                tr.append(mido.Message("note_off", channel=mch, note=a1, velocity=0, time=nlen)); now += nlen
        tr.append(mido.MetaMessage("end_of_track", time=max(0, bar * bar_ticks - now)))
    mapping = dict(name=spec["name"], bpm=bpm, seconds_per_bar=bar_s, midi_channel=ch + 1,
                   velocities=vels, round_robins=rr, passes=spec.get("passes", ["clean"]),
                   sounds=[dict(name=s["name"], note=s["note"], role=s.get("role", "perc"), slot=s["slot"], slot_fixed=s["slot_fixed"],
                                 **{k: s[k] for k in ("cc", "rr_cc", "program", "bank") if s.get(k) is not None})
                            for s in sounds],
                   sync=dict(seconds=0.0, note=sync_note, velocity=127),
                   total_seconds=round(bar * bar_s, 3), hits=hits)
    return mid, mapping


def main():
    spec_path = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(spec_path))
    spec = json.load(open(spec_path))
    mid, mapping = build(spec)
    os.makedirs(out, exist_ok=True)
    stem = spec["name"].replace(" ", "_")
    mid.save(os.path.join(out, f"{stem}.mid"))
    json.dump(mapping, open(os.path.join(out, f"{stem}_map.json"), "w"), indent=1)
    m = mapping["total_seconds"]
    for s in mapping["sounds"]:
        extra = "".join([f"  PC {s['program']}" if "program" in s else "",
                         f"  CC {s['cc']}" if s.get("cc") else "", f"  RR-CC {s['rr_cc']}" if s.get("rr_cc") else ""])
        print(f"   pad {s['slot']:3d}  {s['name']:10s} ({s['role']}){extra}")
    print(f"{stem}: {len(mapping['hits'])} hits, {m/60:.1f} min per pass, "
          f"{len(mapping['passes'])} passes -> {m*len(mapping['passes'])/60:.0f} min total "
          f"(set your DAW to {mapping['bpm']} BPM, MIDI ch {mapping['midi_channel']})")
    if len(mid.tracks) > 1:
        print("   MIDI file has one track per channel — in the DAW set each track's output channel to match its name:")
        for t in mid.tracks:
            print("     -", t.name)


if __name__ == "__main__":
    main()
