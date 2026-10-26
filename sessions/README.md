# Sessions

One JSON spec per instrument/kit. Generate the MIDI clip + hit map:

    python3 capture/session.py sessions/nord3p_kit1.json sessions/out

`out/<Name>.mid` goes in the DAW (set the tempo it prints). `out/<Name>_map.json` stays with the recordings.
