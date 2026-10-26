"""Nord Drum 3P sampling session clip: 6 channels x 6 velocities x 4 hits, one hit per bar at 80 BPM (= 3 s)."""
import mido, json
BPM, TPB = 80, 480
BAR = 4 * TPB                       # 4/4 at 80 BPM -> 1 bar = 3.0 s
NOTES = [60, 62, 64, 65, 67, 69]    # Nord 3P factory notes, channels 1-6
VELS = [20, 45, 70, 95, 115, 127]
HITS = 4
CH = 9                              # MIDI channel 10 (0-based 9) = Nord global channel default
LEN = TPB // 8                      # short note; Nord ignores note length
mid = mido.MidiFile(ticks_per_beat=TPB); tr = mido.MidiTrack(); mid.tracks.append(tr)
tr += [mido.MetaMessage('track_name', name='Nord3P Sampling Session', time=0),
       mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(BPM), time=0),
       mido.MetaMessage('time_signature', numerator=4, denominator=4, time=0)]
events, plan = [], []
bar = 0
events.append((0, 60, 127, 'sync'))           # bar 1: sync hit (channel 1, full velocity)
bar = 2                                       # bar 2 silent, sounds start bar 3
for ci, n in enumerate(NOTES):
    for vi, v in enumerate(VELS):
        for h in range(HITS):
            events.append((bar * BAR, n, v, f'ch{ci+1}'))
            plan.append(dict(bar=bar + 1, seconds=round(bar * 3.0, 3), channel=ci + 1, note=n,
                             velocity=v, layer=vi + 1, rr=h + 1))
            bar += 1
    bar += 2                                  # 2 empty bars (6 s) between sounds
bar += 2                                      # tail room at the end
now = 0
for t, n, v, _ in events:
    tr.append(mido.Message('note_on', channel=CH, note=n, velocity=v, time=t - now)); now = t
    tr.append(mido.Message('note_off', channel=CH, note=n, velocity=0, time=LEN)); now += LEN
tr.append(mido.MetaMessage('end_of_track', time=bar * BAR - now))
mid.save('Nord3P_Session_80bpm.mid')
json.dump(dict(bpm=BPM, seconds_per_bar=3.0, midi_channel=10, sync_hit=dict(bar=1, seconds=0.0, note=60, velocity=127),
               total_bars=bar, total_seconds=bar * 3.0, hits=plan), open('Nord3P_Session_map.json', 'w'), indent=1)
print(len(plan), 'hits,', bar, 'bars,', f'{bar*3/60:.1f} min')
