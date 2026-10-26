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
Roles: kick snare tom perc zap bell hat cymbal (drive colour + loudness target).
"""
import json, os, sys
import mido

SYNC_NOTE_DEFAULT = None   # sync hit uses the first sound's note at velocity 127


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

    events, hits = [(0, sync_note, 127)], []
    bar = 2                                    # bar 1 sync, bar 2 silent
    for si, s in enumerate(sounds):
        bph = s.get("bars_per_hit", spec.get("bars_per_hit", 1))
        for li, v in enumerate(vels):
            for k in range(rr):
                events.append((bar * bar_ticks, s["note"], v))
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
    for t, n, v in events:
        tr.append(mido.Message("note_on", channel=ch, note=n, velocity=v, time=t - now)); now = t
        tr.append(mido.Message("note_off", channel=ch, note=n, velocity=0, time=nlen)); now += nlen
    tr.append(mido.MetaMessage("end_of_track", time=bar * bar_ticks - now))
    mapping = dict(name=spec["name"], bpm=bpm, seconds_per_bar=bar_s, midi_channel=ch + 1,
                   velocities=vels, round_robins=rr, passes=spec.get("passes", ["clean"]),
                   sounds=[dict(name=s["name"], note=s["note"], role=s.get("role", "perc")) for s in sounds],
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
    print(f"{stem}: {len(mapping['hits'])} hits, {m/60:.1f} min per pass, "
          f"{len(mapping['passes'])} passes -> {m*len(mapping['passes'])/60:.0f} min total "
          f"(set your DAW to {mapping['bpm']} BPM, MIDI ch {mapping['midi_channel']})")


if __name__ == "__main__":
    main()
