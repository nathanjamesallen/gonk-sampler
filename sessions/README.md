# Sessions

One JSON spec per instrument/kit. Generate the MIDI clip + hit map:

    python3 capture/session.py sessions/nord3p_kit1.json sessions/out

`out/<Name>.mid` goes in the DAW (set the tempo it prints). `out/<Name>_map.json` stays with the recordings.

## Kit A / Kit B (one clip, two presets)

`out/Nord3P_Kit_AB.mid` is the same clip for any Nord preset (identical to `Nord3P_Kit_A.mid` / `_B.mid`).
Round robins play at layer velocity -3 / 0 / +2 / +4, so each hit differs slightly
*relative to the loaded preset* (no absolute CCs that would overwrite the preset).

- Record preset A with it -> capture with `out/Nord3P_Kit_A_map.json` (pads: kick, snare, ...)
- Record preset B with it -> capture with `out/Nord3P_Kit_B_map.json` (pads: kick B, snare B, ...)
- `python3 capture/merge.py --out work/kitAB work/kitA work/kitB` -> 12 pads on the GM map
