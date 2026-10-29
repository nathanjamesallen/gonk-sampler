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
Before that sound's block the clip sends Channel Select (CC 70) for the Nord
channel that plays note 67, then each CC. Put derived sounds AFTER the sounds
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
    sel_vals = spec.get("channel_select_values", [0, 1, 2, 3, 4, 5])   # CC70 value for ch 1..6
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from layout import assign_slots
    progs = spec.get("programs")
    if progs:                                  # repeat the sound list once per Nord preset
        sounds = [dict(s, name=s["name"] + p.get("suffix", f" {i+1}" if i else ""),
                       program=p["program"], **({"bank": p["bank"]} if p.get("bank") else {}))
                  for i, p in enumerate(progs) for s in sounds]
    sounds = [dict(s, slot_fixed=s.get("slot") is not None) for s in sounds]
    sounds = assign_slots(sounds)

    events, hits = [("note", 0, sync_note, 127)], []
    cur_prog = None
    bar = 2                                    # bar 1 sync, bar 2 silent
    for si, s in enumerate(sounds):
        bph = s.get("bars_per_hit", spec.get("bars_per_hit", 1))
        t = bar * bar_ticks - 2 * tpb          # 1.5 s before the block: previous tails have died
        step = tpb // 8
        if s.get("program") is not None and s.get("program") != cur_prog:
            if s.get("bank"):
                events.append(("cc", t, 0, int(s["bank"][0]))); t += step
                events.append(("cc", t, 32, int(s["bank"][1]))); t += step
            events.append(("pc", t, int(s["program"]), 0)); t += 2 * step
            cur_prog = s["program"]
        need_sel = s.get("cc") or s.get("rr_cc")
        ch_n = s.get("nord_channel") or note_ch.get(s["note"])
        if need_sel and ch_n is None:
            raise SystemExit(f"{s['name']}: can't tell which Nord channel plays note {s['note']} — add \"nord_channel\"")
        if s.get("cc"):
            events.append(("cc", t, CHANNEL_SELECT_CC, sel_vals[ch_n - 1])); t += step
            for cc, val in sorted(s["cc"].items(), key=lambda kv: int(kv[0])):
                events.append(("cc", t, int(cc), int(val))); t += step
        rr_cc = s.get("rr_cc") or {}
        for li, v in enumerate(vels):
            for k in range(rr):
                if rr_cc:                      # beat 4 of the bar before this hit: tail is gone
                    tc = bar * bar_ticks - tpb
                    events.append(("cc", tc, CHANNEL_SELECT_CC, sel_vals[ch_n - 1]))
                    for j, (cc, vals) in enumerate(sorted(rr_cc.items(), key=lambda kv: int(kv[0]))):
                        events.append(("cc", tc + (j + 1) * (tpb // 16), int(cc), int(vals[k % len(vals)])))
                events.append(("note", bar * bar_ticks, s["note"], v))
                hits.append(dict(index=len(hits), sound=si, name=s["name"], role=s.get("role", "perc"),
                                 note=s["note"], velocity=v, layer=li + 1, rr=k + 1,
                                 seconds=round(bar * bar_s, 4), window=round(bph * bar_s, 4)))
                bar += bph
        bar += gap
    bar += 2
    mid = mido.MidiFile(ticks_per_beat=tpb)
    tr = mido.MidiTrack(); mid.tracks.append(tr)
    tr += [mido.MetaMessage("track_name", name=spec["name"], time=0),
           mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0),
           mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0)]
    now, nlen = 0, tpb // 8
    for kind, t, a1, a2 in sorted(events, key=lambda e: e[1]):
        if kind == "cc":
            tr.append(mido.Message("control_change", channel=ch, control=a1, value=a2, time=t - now)); now = t
            continue
        if kind == "pc":
            tr.append(mido.Message("program_change", channel=ch, program=a1, time=t - now)); now = t
            continue
        tr.append(mido.Message("note_on", channel=ch, note=a1, velocity=a2, time=t - now)); now = t
        tr.append(mido.Message("note_off", channel=ch, note=a1, velocity=0, time=nlen)); now += nlen
    tr.append(mido.MetaMessage("end_of_track", time=bar * bar_ticks - now))
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


if __name__ == "__main__":
    main()
