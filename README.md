# Gonk Sampler

Long hardware recordings in → playable Decent Sampler instruments + sorted one-shot packs out.
Built around the Nord Drum 3P through the Gonkulator at GizmoTown.

![UI](docs/ui_preview.png)

## Quick start

    pip3 install numpy scipy soundfile librosa scikit-learn pillow matplotlib mido
    python3 pipeline/gonk.py take.wav --name "Gonk Nord 3P Dual" --subtitle "Nord Drum 3P · dual Gonkulator"

Outputs `<Name>.dslibrary` (drag onto Decent Sampler) and `<Name>_OneShots.zip`.
Check `gonk_out/_work/<take>/contact.png` before shipping a pack.

## Layout

| Path | What |
|---|---|
| `pipeline/gonk.py` | one command: slice → curate → build |
| `pipeline/gonk_slice.py` | onset detection, re-trigger cuts, tail trim, per-slice features |
| `pipeline/gonk_curate.py` | cleanliness scoring, sound families, 16-pad selection with round robin |
| `pipeline/gonk_build.py` | loudness matching, `.dspreset` (RR modes, deep edit page, drive/crush buses), art, packaging |
| `pipeline/contact_sheet.py` | spectrogram sheet of every pad's RR hits |
| `pipeline/mock_ui.py` | rough preview render of the preset UI (Decent Sampler itself is the real test) |
| `capture/` | assembly line (`layout.py` = GM pad map): `session.py` (spec → MIDI + map), `capture.py` (align + cut + QA), `build_layered.py` (instrument), `run.py` (one command) |
| `sessions/` | session specs (`nord3p_kit1.json`) and generated clips/maps |
| `tests/` | `simulate_session.py` — fake a recorded session to test without hardware |

## Capture assembly line (velocity layers × drive passes)

Three steps, any hardware:

1. **Describe the session** — `sessions/<kit>.json`: sounds (name, MIDI note, role), velocities, round robins, spacing, passes.
   `python3 capture/session.py sessions/nord3p_kit1.json sessions/out` → `.mid` clip + `_map.json`.
2. **Record** — set the DAW tempo it prints (80 BPM = 3 s per hit), play the clip from bar 1, once per drive setting.
   Turn the Gonkulator up between passes. Export each pass as `<Name>_<pass>.wav` (e.g. `Nord3P_Kit1_bonkers.wav`), same sample rate, starting at bar 1.
3. **Build** — `python3 capture/run.py sessions/out/Nord3P_Kit1_map.json ~/Rec/Nord3P_Kit1_*.wav --name "Gonk Nord Kit 1"`

If an export starts after bar 1 (no sync hit), add `--no-sync` to capture.py: the first sound is taken as the first hit.

Fix one sound without re-recording the kit: make a spec with just that sound (e.g. `sessions/nord3p_kit1_hat.json`), record it, capture it, then
`python3 capture/merge.py --out <merged> <kit capture> <patch capture>` and build from `<merged>`. Later captures win per note and pass.

What the capture step does: finds the bar-1 sync hit, fits latency + clock drift, re-finds every hit in a tight window,
cuts it to just before the next one, trims the dead tail, and prints a QA report (missing triggers, clipping,
velocity layers that don't get louder, round robins that don't match). It refuses to build with missing hits unless `--force`.

Pads follow the General MIDI drum map on the 16 pads of a 4×4 (kick 36, snare 38, clap 39, closed hat 42, open hat 46, crash 49, ride 51, toms 41–50); empty slots show dimmed, ready to fill from the next preset.
Session specs can also drive the Nord mid-clip: `"programs"` repeats the pattern for each preset (Program Change), `"cc"` makes derived sounds (e.g. an open hat from the hat with longer decay), `"rr_cc"` nudges a parameter per round robin. Check new CC ideas with `sessions/nord3p_cc_test.json` first.

What you get: one pad per sound, every pad with all velocity layers and round robins, a **GONK** menu that
switches passes (loudness-matched, so it changes character, not volume), PITCH / TONE / CRUSH / SPACE / VELOCITY / LEVEL,
DEEP EDIT per pad, plus a one-shot zip organised Pass/Sound.

Nord Drum 3P setup: global MIDI channel 10, individual channels off, notes 60/62/64/65/67/69 for channels 1–6.
Split the Nord output on the first pass to capture Clean + Low together.

Tested end-to-end on a simulated session (`tests/simulate_session.py`: real Nord hits, velocity-dependent tone,
5 drive passes, 12 ms latency, 20 ppm drift, MIDI jitter, one dropped trigger): 719/719 present hits found, max onset
error 1.25 ms, dropped hit flagged, 0.06 dB max level difference between GONK passes.

## Decent Sampler notes learned the hard way

- The background image is stretched behind DS's own header bar — never put layout in it. All boxes/labels are `<rectangle>`/`<label>` elements.
- DS scales the panel to the window WIDTH and the keyboard covers whatever does not fit, so keep the panel wide and short (812x256).
- On load DS fires every control's bindings in file order; the DEEP button must come after the pad menu.
- Wave shaper isn't transparent at minimum, so DRIVE and CRUSH are parallel buses (dry signal untouched).

## Roadmap

- [x] MIDI-map capture (sync hit + drift fit)
- [x] Velocity layers with RR inside each layer
- [x] GONK drive selector, loudness-matched across passes
- [ ] Combine several kits (Kit1+Kit2+Kit3) into one instrument
- [ ] Verify in Decent Sampler with a real recorded session
- [ ] Chromatic preset for tonal hits
