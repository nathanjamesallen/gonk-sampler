#!/usr/bin/env python3
"""
capture.json -> velocity-layered, multi-drive Decent Sampler instrument.

    python3 capture/build_layered.py work/kit1 --name "Gonk Nord Kit 1" --subtitle "Nord Drum 3P · Gonkulator"

Instrument shape:
  * one pad per sound, on C1 upward
  * every pad: N velocity layers (ranges split halfway between recorded
    velocities) x M round robins (random, never the same hit twice in a row)
  * GONK menu switches between the recorded passes (Clean ... Bonkers)
  * front strip: PITCH  TONE  CRUSH  SPACE  VELOCITY  LEVEL
  * DEEP EDIT: per-pad TUNE / LENGTH / LEVEL / PAN

Loudness policy:
  * clean pass, loudest layer of each sound -> role target level
  * softer layers keep 70% of their real dB distance below it (dynamics
    preserved but the softest layer doesn't vanish)
  * every other pass is matched to the clean pass layer-for-layer, so
    switching GONK changes character, not volume
  * one shared trim per sound keeps every peak under -1 dBFS
"""
import argparse, json, os, shutil, sys, zipfile
from xml.sax.saxutils import quoteattr as qa
import numpy as np, soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline"))
from gonk_build import (ROLE_TARGET, ROLE_COLOR, note_name, finish, make_art, knob, vis,
                        LENGTH_TABLE, db)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from layout import assign_slots, GM_NAMES, GRID
for _r, _t, _c in (("rim", -16, "#E8A23D"), ("openhat", -18, "#AEB8C2"), ("pedalhat", -19, "#B9C2CB"),
                   ("ride", -18, "#8C99A6")):
    ROLE_TARGET.setdefault(_r, _t); ROLE_COLOR.setdefault(_r, _c)

KEEP_DYNAMICS = 0.7
PEAK_CEILING = -1.0
BASE_NOTE = 36            # C1


def vel_ranges(vels):
    vs = sorted(vels)
    edges = [0] + [int((a + b) // 2) + 1 for a, b in zip(vs, vs[1:])] + [128]
    return {v: (edges[i], edges[i + 1] - 1) for i, v in enumerate(vs)}


def slot_cell(note):
    """2 x 8 grid of the 16 GM pads: bottom row 36-43, top row 44-51 (pad order of a 4x4, read left to right)."""
    i = note - 36
    r, c = divmod(i, 8)
    g, x0, w, h = 3, 16, 95, 58
    return (x0 + c * (w + g), 48 + (1 - r) * (h + g), w, h)


def cells_for(n):
    cols = n if n <= 8 else -(-n // 2)
    rows = -(-n // cols)
    g, x0, total_w = 3, 16, 781
    w = (total_w - (cols - 1) * g) / cols
    h = 58 if rows <= 2 else 38
    cells = []
    for i in range(n):
        r, c = divmod(i, cols)
        y = 48 + (rows - 1 - r) * (h + g) if rows > 1 else 48 + (58 + g) // 2
        cells.append((int(round(x0 + c * (w + g))), int(y), int(w), h))
    return cells


def process(C, cap_dir, lib_dir):
    """Apply the loudness policy, write finished WAVs, return pads."""
    passes = C["passes"]; ref = passes[0]
    hits = [h for h in C["hits"] if not h["missing"]]
    pads = []
    assign_slots(C["sounds"])          # older captures have no slot yet: fill from roles
    for si, s in enumerate(C["sounds"]):
        hs = [h for h in hits if h["sound"] == si]
        if not hs:
            continue
        role = s.get("role", "perc")
        # clean-pass layer levels
        refl = {}
        for h in hs:
            if h["pass_"] == ref:
                refl.setdefault(h["layer"], []).append(h["rms_db"])
        if not refl:                                          # no clean pass: use first available
            for h in hs:
                refl.setdefault(h["layer"], []).append(h["rms_db"])
        L = {l: float(np.median(v)) for l, v in refl.items()}
        top = L[max(L)]
        T = ROLE_TARGET.get(role, -15)
        out_level = {l: max(T - 36, T + KEEP_DYNAMICS * (L[l] - top)) for l in L}
        gains = {}
        for h in hs:
            gains[(h["pass_"], h["layer"], h["rr"])] = min(36.0, out_level.get(h["layer"], T) - h["rms_db"])
        worst = max(h["peak_db"] + gains[(h["pass_"], h["layer"], h["rr"])] for h in hs)
        trim = min(0.0, PEAK_CEILING - worst)
        files = {}
        for h in hs:
            y, sr = sf.read(os.path.join(cap_dir, h["file"]), always_2d=True, dtype="float64")
            y = finish(y, sr) * 10 ** ((gains[(h["pass_"], h["layer"], h["rr"])] + trim) / 20)
            rel = os.path.join("Samples", h["pass_"], os.path.basename(h["file"]))
            os.makedirs(os.path.join(lib_dir, "Samples", h["pass_"]), exist_ok=True)
            sf.write(os.path.join(lib_dir, rel), y, sr, subtype="PCM_24")
            files[(h["pass_"], h["velocity"], h["rr"])] = rel
        pads.append(dict(pad=len(pads), sound=si, name=s["name"], role=role, note=int(s["slot"]),
                         files=files, trim_db=round(trim, 1),
                         layer_db={int(l): round(v + trim, 1) for l, v in out_level.items()}))
    pads.sort(key=lambda p: p["note"])
    for i, p in enumerate(pads):
        p["pad"] = i
    return pads


def build_xml(C, pads, name, subtitle, default_pass):
    passes, vels, R = C["passes"], sorted(C["velocities"]), C["round_robins"]
    VR = vel_ranges(vels)
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         f'<!-- {name}: {len(pads)} pads x {len(vels)} velocity layers x {R} round robins x '
         f'{len(passes)} drive passes ({", ".join(passes)}). Generated by gonk-sampler. -->',
         '<DecentSampler minVersion="1.37.0">',
         '  <ui width="812" height="256" bgColor="FF141518" bgImage="Resources/panel.png" coverArt="Resources/cover.png">',
         '    <tab name="main">']

    def rect(x, y, w, h, fill, border="00000000", bt=0, extra=""):
        L.append(f'      <rectangle x="{x}" y="{y}" width="{w}" height="{h}" fillColor="{fill}" '
                 f'borderColor="{border}" borderThickness="{bt}"{extra}/>')
    rect(0, 0, 812, 40, "FF0E0F11"); rect(0, 40, 812, 2, "FFE8553D")
    rect(16, 172, 781, 80, "FF1B1C1F", "FF303136", 1)
    on_grid = all(36 <= p["note"] <= 51 for p in pads)
    cells = [slot_cell(p["note"]) for p in pads] if on_grid else cells_for(len(pads))
    if on_grid:                                   # empty GM slots: dim, labelled, ready to fill
        used = {p["note"] for p in pads}
        for n in GRID:
            if n in used: continue
            x, y, w, h = slot_cell(n)
            rect(x, y, w, h, "FF17181B", "FF26272B", 1)
    for p, (x, y, w, h) in zip(pads, cells):
        c = "FF" + ROLE_COLOR.get(p["role"], "#888888")[1:]
        rect(x, y, w, h, "FF202125", "FF303136", 1); rect(x, y, 4, h, c)
    L.append(f'      <label x="18" y="2" width="330" height="24" text={qa(name.upper())} textColor="FFF0ECE4" textSize="19" hAlign="left"/>')
    L.append(f'      <label x="18" y="25" width="330" height="13" text={qa(subtitle)} textColor="FF8A8B90" textSize="10" hAlign="left"/>')
    if on_grid:
        for n in GRID:
            if n in {p["note"] for p in pads}: continue
            x, y, w, h = slot_cell(n)
            L.append(f'      <label x="{x+12}" y="{y+5}" width="40" height="14" text="{note_name(n)}" textColor="FF4A4B50" textSize="11" hAlign="left"/>')
            L.append(f'      <label x="{x+12}" y="{y+38}" width="{w-16}" height="14" text={qa(GM_NAMES[n])} textColor="FF4A4B50" textSize="11" hAlign="left"/>')
    for p, (x, y, w, h) in zip(pads, cells):
        c = "FF" + ROLE_COLOR.get(p["role"], "#888888")[1:]
        L.append(f'      <label x="{x+12}" y="{y+5}" width="40" height="14" text="{note_name(p["note"])}" textColor="FF8A8B90" textSize="11" hAlign="left"/>')
        L.append(f'      <label x="{x+w-70}" y="{y+5}" width="62" height="14" text="{len(vels)}V·{R}RR" textColor="{c}" textSize="10" hAlign="right" tooltip="{len(vels)} velocity layers, {R} round robins each"/>')
        L.append(f'      <label x="{x+12}" y="{y+20}" width="{w-16}" height="18" text={qa(p["name"].upper())} textColor="FFE6E1D8" textSize="14" hAlign="left"/>')
        L.append(f'      <label x="{x+12}" y="{y+38}" width="{w-16}" height="14" text={qa(p["role"])} textColor="FF9A9BA0" textSize="11" hAlign="left"/>')

    # GONK selector: one group set per pass, switched with ENABLED
    L.append('      <label x="400" y="8" width="56" height="24" text="GONK" textColor="FFE8553D" textSize="12" hAlign="right"/>')
    L.append(f'      <menu x="462" y="8" width="130" height="24" value="{passes.index(default_pass)+1}" textSize="13" '
             'textColor="FFE6E1D8" backgroundColor="FF232428" highlightedBackgroundColor="FFE8553D" '
             'tooltip="Which drive pass plays. Levels are matched, so this changes character, not volume.">')
    for p in passes:
        L.append(f'        <option name={qa(p.title())}>')
        for q in passes:
            L.append(f'          <binding type="general" level="group" groupTags="pass_{q}" parameter="ENABLED" '
                     f'translation="fixed_value" translationValue="{"true" if q == p else "false"}"/>')
        L.append('        </option>')
    L.append('      </menu>')

    # front strip
    MX, MY = [36, 166, 296, 426, 556, 686], 173
    tone_lp = ('<binding type="effect" level="instrument" effectTags="tone_lp" parameter="FX_FILTER_FREQUENCY" '
               'translation="table" translationTable="0,180;0.15,600;0.3,2200;0.45,9000;0.5,22000;1,22000"/>')
    tone_hp = ('<binding type="effect" level="instrument" effectTags="tone_hp" parameter="FX_FILTER_FREQUENCY" '
               'translation="table" translationTable="0,10;0.5,10;0.55,40;0.7,180;0.85,600;1,1800"/>')
    for k in [
        knob(MX[0], MY, "PITCH", -12, 12, 0, ['<binding type="amp" level="instrument" position="0" parameter="GLOBAL_TUNING"/>'],
             tags="main", snap="whole_numbers", tip="Whole kit, in semitones"),
        knob(MX[1], MY, "TONE", 0, 1, 0.5, [tone_lp, tone_hp], tags="main,tone_knob",
             tip="Left = darker. Right = thinner. Centre = flat. Mod wheel controls this too."),
        knob(MX[2], MY, "CRUSH", 0, 1, 0, ['<binding type="amp" level="group" groupTags="all" parameter="OUTPUT_2_VOLUME" '
             'translation="table" translationTable="0,0;0.5,0.3;1,0.9"/>'], tags="main", tip="Parallel bit-crush (6-bit, 1/5 rate)"),
        knob(MX[3], MY, "SPACE", 0, 1, 0, ['<binding type="effect" level="instrument" effectTags="space" parameter="FX_REVERB_WET_LEVEL" '
             'translation="table" translationTable="0,0;0.5,0.18;1,0.55"/>'], tags="main", tip="Room reverb"),
        knob(MX[4], MY, "VELOCITY", 0, 1, 0.3, ['<binding type="amp" level="instrument" position="0" parameter="AMP_VEL_TRACK"/>'],
             tags="main", tip="Extra velocity-to-volume on top of the recorded layers. 0 = layers only."),
        knob(MX[5], MY, "LEVEL", 0, 1, 0.75, ['<binding type="amp" level="instrument" position="0" parameter="AMP_VOLUME" '
             'translation="table" translationTable="0,0;0.75,1;1,2"/>'], tags="main", tip="Output (+6 dB at max)"),
    ]:
        L.append("      " + k)

    # deep page
    L.append('      <rectangle x="16" y="172" width="781" height="80" fillColor="FF1E1F23" borderColor="FFE8553D" borderThickness="1" tags="deep" visible="false"/>')
    L.append('      <label x="30" y="180" width="220" height="16" text="DEEP EDIT · PAD" textColor="FFE8553D" textSize="12" hAlign="left" tags="deep" visible="false"/>')
    L.append('      <menu x="30" y="200" width="220" height="26" value="1" textSize="13" tags="deep,padmenu" visible="false" '
             'textColor="FFE6E1D8" backgroundColor="FF232428" highlightedBackgroundColor="FFE8553D">')
    for p in pads:
        L.append(f'        <option name={qa(note_name(p["note"]) + "  " + p["name"].upper())}>')
        L.append('          <binding type="control" level="ui" controlTags="pk" parameter="VISIBLE" translation="fixed_value" translationValue="false" triggerOnLoad="false"/>')
        L.append(f'          <binding type="control" level="ui" controlTags="pk{p["pad"]:02d}" parameter="VISIBLE" translation="fixed_value" translationValue="true" triggerOnLoad="false"/>')
        L.append('        </option>')
    L.append('      </menu>')
    L.append('      <label x="30" y="230" width="220" height="16" textColor="FF8A8B90" textSize="11" hAlign="left" tags="deep" visible="false" text="Pick a pad, shape it. Applies to every GONK pass."/>')
    px = [300, 420, 540, 660]
    for p in pads:
        t = f'pk{p["pad"]:02d}'; tg = f'groupTags="{t}"'; pn = f'{note_name(p["note"])} {p["name"]} '
        for k in [
            knob(px[0], MY, "TUNE", -24, 24, 0, [f'<binding type="amp" level="group" {tg} parameter="GROUP_TUNING"/>'],
                 tags=f"pk,{t}", pname=pn, visible=False, snap="whole_numbers", dflt=0),
            knob(px[1], MY, "LENGTH", 0, 1, 1,
                 [f'<binding type="amp" level="group" {tg} parameter="ENV_DECAY" translation="table" translationTable="{LENGTH_TABLE}"/>',
                  f'<binding type="amp" level="group" {tg} parameter="ENV_RELEASE" translation="table" translationTable="{LENGTH_TABLE}"/>'],
                 tags=f"pk,{t}", pname=pn, visible=False, tip="Full right = whole hit. Left = choke it shorter."),
            knob(px[2], MY, "LEVEL", 0, 1, 0.5, [f'<binding type="amp" level="group" {tg} parameter="AMP_VOLUME" translation="table" translationTable="0,0;0.5,1;1,2.5"/>'],
                 tags=f"pk,{t}", pname=pn, visible=False, tip="Centre = as balanced"),
            knob(px[3], MY, "PAN", -100, 100, 0, [f'<binding type="amp" level="group" {tg} parameter="PAN"/>'],
                 tags=f"pk,{t}", pname=pn, visible=False, snap="whole_numbers", dflt=0),
        ]:
            L.append("      " + k)
    # DEEP toggle last (load-order: its 'hide' must win)
    L.append('      <button x="692" y="8" width="104" height="24" value="0" style="text" tooltip="Per-pad tune, length, level, pan">')
    L.append('        <state name="DEEP EDIT  ›">')
    for b in (vis("main", 1), vis("deep", 0), vis("pk", 0)):
        L.append("          " + b)
    L.append('        </state>')
    L.append('        <state name="‹  PLAY">')
    for b in (vis("main", 0), vis("pk", 0), vis("deep", 1), vis("pk00", 1),
              '<binding type="control" level="ui" controlTags="padmenu" parameter="VALUE" translation="fixed_value" translationValue="1"/>'):
        L.append("          " + b)
    L.append('        </state>')
    L.append('      </button>')
    L.append('    </tab>')
    L.append('    <keyboard>')
    for p in pads:
        L.append(f'      <color loNote="{p["note"]}" hiNote="{p["note"]}" color="FF{ROLE_COLOR.get(p["role"], "#888888")[1:]}"/>')
    L.append('    </keyboard>')
    L.append('  </ui>')

    L.append('  <groups attack="0" ampEnvHold="0" decay="30" sustain="0" release="30" decayCurve="0" releaseCurve="0" '
             'ampVelTrack="0.3" volume="0dB" output1Target="MAIN_OUTPUT" output1Volume="1" '
             'output2Target="BUS_1" output2Volume="0">')
    for pas in passes:
        for p in pads:
            hat = p["role"] == "hat"
            tags = f'all,pass_{pas},pk{p["pad"]:02d},{p["role"]}' + (",hatchoke" if hat else "")
            choke = ' silencedByTags="hatchoke" silencingDecay="0.04"' if hat else ""
            L.append(f'    <group name={qa(p["name"] + " · " + pas)} enabled="{"true" if pas == default_pass else "false"}" '
                     f'tags="{tags}"{choke} seqMode="random" seqLength="{R}">')
            for v in vels:
                lo, hi = VR[v]
                have = [k for k in range(1, R + 1) if (pas, v, k) in p["files"]]
                if not have:
                    continue
                for k in range(1, R + 1):
                    src = p["files"][(pas, v, k if k in have else have[(k - 1) % len(have)])]   # fill a missing RR
                    L.append(f'      <sample path="{src}" rootNote="{p["note"]}" loNote="{p["note"]}" hiNote="{p["note"]}" '
                             f'loVel="{lo}" hiVel="{hi}" seqPosition="{k}"/>')
            L.append('    </group>')
    L.append('  </groups>')
    L += ['  <effects>',
          '    <effect type="lowpass" frequency="22000" resonance="0.7" tags="tone_lp"/>',
          '    <effect type="highpass" frequency="10" resonance="0.7" tags="tone_hp"/>',
          '    <effect type="reverb" roomSize="0.55" damping="0.45" wetLevel="0" tags="space"/>',
          '  </effects>',
          '  <buses>',
          '    <bus busVolume="1" output1Target="MAIN_OUTPUT" output1Volume="1">',
          '      <effects><effect type="bit_crusher" bitDepth="6" sampleRateReduction="5" mix="1.0"/></effects>',
          '    </bus>',
          '  </buses>',
          '  <midi>',
          '    <cc number="1"><binding level="ui" type="control" parameter="VALUE" controlTags="tone_knob" translation="linear" translationOutputMin="0" translationOutputMax="1"/></cc>',
          '  </midi>',
          '</DecentSampler>']
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture_dir")
    ap.add_argument("--name", default=None)
    ap.add_argument("--subtitle", default="Gonkulator · velocity layered")
    ap.add_argument("--out", default=None)
    ap.add_argument("--default-pass", default=None, help="pass selected on load (default: first)")
    a = ap.parse_args()
    C = json.load(open(os.path.join(a.capture_dir, "capture.json")))
    name = a.name or C["name"]
    out = a.out or os.path.join(a.capture_dir, "dist")
    lib = os.path.join(out, name)
    shutil.rmtree(lib, ignore_errors=True); os.makedirs(os.path.join(lib, "Resources"))
    pads = process(C, a.capture_dir, lib)
    dp = a.default_pass or C["passes"][0]
    make_art(pads, name, a.subtitle, os.path.join(lib, "Resources", "panel.png"), os.path.join(lib, "Resources", "cover.png"))
    open(os.path.join(lib, f"{name}.dspreset"), "w").write(build_xml(C, pads, name, a.subtitle, dp))
    open(os.path.join(lib, "DSLibraryInfo.xml"), "w").write(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<DecentSamplerLibraryInfo name={qa(name)} version="1.0.0" '
        f'coverArt="Resources/cover.png">\n</DecentSamplerLibraryInfo>\n')
    slug = "".join(ch for ch in name.title() if ch.isalnum())
    dsl = os.path.join(out, f"{slug}.dslibrary")
    with zipfile.ZipFile(dsl, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(lib):
            for f in sorted(files):
                fp = os.path.join(root, f); z.write(fp, os.path.relpath(fp, out))
    # one-shot pack = the finished samples, organised Pass/Sound
    pz = os.path.join(out, f"{slug}_OneShots.zip")
    with zipfile.ZipFile(pz, "w", zipfile.ZIP_DEFLATED) as z:
        for p in pads:
            for (pas, v, k), rel in sorted(p["files"].items()):
                z.write(os.path.join(lib, rel), os.path.join(f"{name} - One-Shots", pas.title(),
                                                             f"{p['pad']+1:02d} {p['name']}", os.path.basename(rel)))
    n = sum(len(p["files"]) for p in pads)
    print(f"{name}: {len(pads)} pads, {n} samples, passes {C['passes']} (default {dp})")
    for p in pads:
        print(f"  {note_name(p['note']):4s} {p['name']:10s} {p['role']:7s} layers dB {p['layer_db']}  trim {p['trim_db']}")
    print("library:", dsl, f"{os.path.getsize(dsl)/1e6:.1f} MB")
    print("one-shots:", pz, f"{os.path.getsize(pz)/1e6:.1f} MB")


if __name__ == "__main__":
    main()
