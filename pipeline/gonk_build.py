#!/usr/bin/env python3
"""
Stage 3 — build.

Turns kit.json into a finished Decent Sampler library:
  * loudness-matched, faded 24-bit one-shots
  * a .dspreset with 16 pads, round robin (Random / Cycle / Off),
    six front-panel knobs, and a hidden DEEP page for per-pad edits
  * generated panel art + cover
  * a second, much bigger folder of every clean hit, sorted by role
    (plain WAVs for any DAW / Ableton Drum Rack / MPC)
"""
import argparse, json, os, shutil, zipfile
from xml.sax.saxutils import quoteattr as qa
import numpy as np, soundfile as sf
from PIL import Image, ImageDraw, ImageFont

EPS = 1e-12
def db(x): return 20 * np.log10(np.maximum(x, EPS))

ROLE_TARGET = {"kick": -13, "snare": -14, "tom": -14, "perc": -15, "zap": -15,
               "bell": -17, "hat": -18, "cymbal": -18}            # short-term RMS, dBFS
ROLE_COLOR = {"kick": "#E8553D", "snare": "#F2A43A", "tom": "#D9C34A", "perc": "#5FBF8F",
              "zap": "#4FB3D9", "bell": "#8C7BE0", "hat": "#C9D1D9", "cymbal": "#9AA6B2"}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
def note_name(n): return f"{NOTE_NAMES[n % 12]}{n // 12 - 2}"   # Ableton/DS: 60 = C3

FONT_DIR = "/usr/share/fonts/opentype/inter/"
def font(w, size):
    for f in (f"Inter-{w}.otf", f"InterDisplay-{w}.otf"):
        p = os.path.join(FONT_DIR, f)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ---------------------------------------------------------------- audio
def short_rms_db(y, sr, ms=150):
    seg = y[: int(sr * ms / 1000)]
    return float(db(np.sqrt(np.mean(seg ** 2) + EPS)))


def finish(y, sr):
    """Click-free start, smooth tail, DC removed."""
    n = len(y)
    fi = min(32, n // 4)
    y[:fi] *= np.linspace(0, 1, fi)[:, None]
    fo = int(min(sr * 0.03, n * 0.25))
    y[n - fo:] *= (0.5 * (1 + np.cos(np.linspace(0, np.pi, fo))))[:, None]
    return y


def process_set(paths, role, sr_expected=None, peak_ceiling=-1.0):
    """Load RR variants, match their loudness to the role target, keep the
    whole set under the peak ceiling with ONE shared trim so the variants stay
    level-matched with each other."""
    ys = []
    for p in paths:
        y, sr = sf.read(p, always_2d=True, dtype="float64")
        ys.append(finish(y, sr))
    tgt = ROLE_TARGET.get(role, -15)
    gains = [tgt - short_rms_db(y, sr) for y in ys]
    peaks = [db(np.abs(y).max()) + g for y, g in zip(ys, gains)]
    trim = min(0.0, peak_ceiling - max(peaks))
    return [y * 10 ** ((g + trim) / 20) for y, g in zip(ys, gains)], sr


# ---------------------------------------------------------------- art
def hex2rgb(h): return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def make_art(pads, title, subtitle, out_bg, out_cover):
    W, H, S = 812, 375, 2                          # draw at 2x, downsample for crisp edges
    im = Image.new("RGB", (W * S, H * S), (20, 21, 24))
    d = ImageDraw.Draw(im)
    # subtle tape-grain texture
    rng = np.random.default_rng(7)
    noise = (rng.normal(0, 5, (H * S, W * S))).astype(np.int16)
    arr = np.array(im).astype(np.int16) + noise[..., None]
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)); d = ImageDraw.Draw(im)
    # Texture only. Decent Sampler stretches the bg image behind its own header bar,
    # so nothing positional may live here — all layout is drawn with <rectangle>/<label>
    # in the preset, which share the controls' coordinate system.
    im = im.resize((W, H), Image.LANCZOS)
    im.save(out_bg, optimize=True)
    # cover: 512 square
    cv = Image.new("RGB", (1024, 1024), (20, 21, 24)); cd = ImageDraw.Draw(cv)
    for i, p in enumerate(pads):
        r, cidx = divmod(i, 4)
        c = hex2rgb(ROLE_COLOR.get(p["role"], "#888888"))
        x0, y0 = 112 + cidx * 205, 300 + (3 - r) * 150
        cd.rounded_rectangle([x0, y0, x0 + 185, y0 + 130], radius=14, fill=(32, 33, 37))
        cd.rounded_rectangle([x0, y0, x0 + 10, y0 + 130], radius=5, fill=c)
    cd.text((112, 120), title.upper(), font=font("Bold", 96), fill=(240, 236, 228))
    cd.text((116, 232), subtitle, font=font("Medium", 34), fill=(150, 150, 150))
    cv.resize((512, 512), Image.LANCZOS).save(out_cover, optimize=True)


# ---------------------------------------------------------------- preset
def knob(x, y, label, lo, hi, val, binds, tags="", w=100, h=104, vtype="float", dflt=None,
         visible=True, tip="", snap=None, pname=None):
    color = "FFE8553D"
    a = (f'<labeled-knob x="{x}" y="{y}" width="{w}" height="{h}" label={qa(label)} '
         f'parameterName={qa((pname + label) if pname else label)} valueType="{vtype}" '
         f'minValue="{lo}" maxValue="{hi}" value="{val}" defaultValue="{dflt if dflt is not None else val}" '
         f'textColor="FFE6E1D8" textSize="13" trackForegroundColor="{color}" '
         f'trackBackgroundColor="FF3A3B40"')
    if tags: a += f' tags="{tags}"'
    if not visible: a += ' visible="false"'
    if tip: a += f" tooltip={qa(tip)}"
    if snap: a += f' snapMode="{snap}"'
    return a + ">\n" + "\n".join("        " + b for b in binds) + "\n      </labeled-knob>"


def vis(tag, on):
    return (f'<binding type="control" level="ui" controlTags="{tag}" parameter="VISIBLE" '
            f'translation="fixed_value" translationValue="{"true" if on else "false"}"/>')


LENGTH_TABLE = "0,0.03;0.2,0.08;0.4,0.2;0.6,0.5;0.8,1.4;0.95,4;1,30"


def build_preset(pads, kit_name, sample_dir_rel, bg_rel, cover_rel, rr_counts):
    P = len(pads)
    L = []
    L.append('<?xml version="1.0" encoding="UTF-8"?>')
    L.append(f'<!-- {kit_name} — generated by gonk pipeline. 3 x {P} groups: '
             f'[0..{P-1}] Random RR, [{P}..{2*P-1}] Cycle RR, [{2*P}..{3*P-1}] RR off. -->')
    L.append('<DecentSampler minVersion="1.37.0">')
    L.append(f'  <ui width="812" height="375" bgColor="FF141518" bgImage="{bg_rel}" coverArt="{cover_rel}">')
    L.append('    <tab name="main">')

    # ---- panel drawing (first, so it sits underneath every control)
    def rect(x, y, w, h, fill, border="00000000", bt=0, extra=""):
        L.append(f'      <rectangle x="{x}" y="{y}" width="{w}" height="{h}" fillColor="{fill}" '
                 f'borderColor="{border}" borderThickness="{bt}"{extra}/>')
    rect(0, 0, 812, 48, "FF0E0F11")
    rect(0, 48, 812, 2, "FFE8553D")
    rect(452, 60, 344, 300, "FF1B1C1F", "FF303136", 1)
    for p in pads:
        x, y, w, h = p["cell"]
        c = "FF" + ROLE_COLOR.get(p["role"], "#888888")[1:]
        rect(x, y, w, h, "FF202125", "FF303136", 1)
        rect(x, y, 4, h, c)
    L.append(f'      <label x="18" y="3" width="330" height="30" text={qa(kit_name.upper())} '
             f'textColor="FFF0ECE4" textSize="22" hAlign="left"/>')
    L.append(f'      <label x="18" y="32" width="330" height="13" text={qa(rr_counts or "")} '
             f'textColor="FF8A8B90" textSize="10" hAlign="left"/>')
    L.append('      <label x="470" y="12" width="82" height="24" text="ROUND ROBIN" '
             'textColor="FF8A8B90" textSize="10" hAlign="right"/>')

    # ---- pad map labels (always visible)
    for p in pads:
        x, y, w, h = p["cell"]
        c = "FF" + ROLE_COLOR.get(p["role"], "#888888")[1:]
        L.append(f'      <label x="{x+w-46}" y="{y+4}" width="40" height="14" text="{"●" * len(p["files"])}" '
                 f'textColor="{c}" textSize="9" hAlign="right" '
                 f'tooltip="{len(p["files"])} round-robin hits"/>')
        L.append(f'      <label x="{x+12}" y="{y+6}" width="{w-40}" height="14" text="{note_name(p["note"])}" '
                 f'textColor="FF8A8B90" textSize="11" hAlign="left"/>')
        parts = p["name"].split()
        role_txt = parts[0].upper()
        desc = " · ".join(parts[1:3]) + (f" {chr(64 + int(parts[3]))}" if len(parts) > 3 else "")
        L.append(f'      <label x="{x+12}" y="{y+h-40}" width="{w-16}" height="18" text={qa(role_txt)} '
                 f'textColor="FFE6E1D8" textSize="14" hAlign="left"/>')
        L.append(f'      <label x="{x+12}" y="{y+h-22}" width="{w-16}" height="16" text={qa(desc)} '
                 f'textColor="FF9A9BA0" textSize="11" hAlign="left"/>')

    # ---- header: RR menu + DEEP toggle
    def rr_opt(name, en):
        b = [f'<binding type="general" level="group" groupTags="{t}" parameter="ENABLED" '
             f'translation="fixed_value" translationValue="{"true" if e else "false"}"/>'
             for t, e in zip(("rr_rand", "rr_cycle", "rr_off"), en)]
        return f'        <option name="{name}">\n' + "\n".join("          " + x for x in b) + "\n        </option>"
    L.append('      <menu x="556" y="12" width="120" height="24" value="1" textSize="13" '
             'textColor="FFE6E1D8" backgroundColor="FF232428" highlightedBackgroundColor="FFE8553D" '
             'tooltip="Random: never the same hit twice in a row. Cycle: steps 1-2-3-4. Off: always the first hit.">')
    L.append(rr_opt("Random", (1, 0, 0)))
    L.append(rr_opt("Cycle", (0, 1, 0)))
    L.append(rr_opt("Off", (0, 0, 1)))
    L.append('      </menu>')


    # ---- front panel: six knobs, Sitala-style (few controls, each musical)
    cols, rows = [466, 578, 690], [76, 212]
    tone_lp = ('<binding type="effect" level="instrument" effectTags="tone_lp" parameter="FX_FILTER_FREQUENCY" '
               'translation="table" translationTable="0,180;0.15,600;0.3,2200;0.45,9000;0.5,22000;1,22000"/>')
    tone_hp = ('<binding type="effect" level="instrument" effectTags="tone_hp" parameter="FX_FILTER_FREQUENCY" '
               'translation="table" translationTable="0,10;0.5,10;0.55,40;0.7,180;0.85,600;1,1800"/>')
    main = [
        knob(cols[0], rows[0], "PITCH", -12, 12, 0,
             ['<binding type="amp" level="instrument" position="0" parameter="GLOBAL_TUNING"/>'],
             tags="main", vtype="float", snap="whole_numbers", tip="Whole kit, in semitones"),
        knob(cols[1], rows[0], "TONE", 0, 1, 0.5, [tone_lp, tone_hp], tags="main",
             tip="Left = darker (low-pass). Right = thinner (high-pass). Centre = flat."),
        knob(cols[2], rows[0], "DRIVE", 0, 1, 0,
             ['<binding type="amp" level="group" groupTags="all" parameter="OUTPUT_2_VOLUME" '
              'translation="table" translationTable="0,0;0.5,0.35;1,1"/>'],
             tags="main", tip="Parallel saturation bus. Dry signal is never touched."),
        knob(cols[0], rows[1], "CRUSH", 0, 1, 0,
             ['<binding type="amp" level="group" groupTags="all" parameter="OUTPUT_3_VOLUME" '
              'translation="table" translationTable="0,0;0.5,0.3;1,0.9"/>'],
             tags="main", tip="Parallel bit-crush bus (6-bit, 1/5 rate)."),
        knob(cols[1], rows[1], "SPACE", 0, 1, 0,
             ['<binding type="effect" level="instrument" effectTags="space" parameter="FX_REVERB_WET_LEVEL" '
              'translation="table" translationTable="0,0;0.5,0.18;1,0.55"/>'],
             tags="main", tip="Room reverb amount"),
        knob(cols[2], rows[1], "LEVEL", 0, 1, 0.75,
             ['<binding type="amp" level="instrument" position="0" parameter="AMP_VOLUME" '
              'translation="table" translationTable="0,0;0.75,1;1,2"/>'],
             tags="main", tip="Output level (+6 dB at max)"),
    ]
    L += ["      " + k for k in main]

    # ---- DEEP page (hidden until the button is pressed)
    L.append('      <rectangle x="452" y="60" width="344" height="300" fillColor="FF1E1F23" '
             'borderColor="FFE8553D" borderThickness="1" tags="deep" visible="false"/>')
    L.append('      <label x="466" y="70" width="200" height="18" text="DEEP EDIT · PAD" textColor="FFE8553D" '
             'textSize="12" hAlign="left" tags="deep" visible="false"/>')
    L.append('      <menu x="466" y="92" width="210" height="26" value="1" textSize="13" tags="deep,padmenu" '
             'visible="false" textColor="FFE6E1D8" backgroundColor="FF232428" '
             'highlightedBackgroundColor="FFE8553D">')
    for p in pads:
        L.append(f'        <option name={qa(note_name(p["note"]) + "  " + p["name"].upper())}>')
        L.append(f'          <binding type="control" level="ui" controlTags="pk" parameter="VISIBLE" '
                 f'translation="fixed_value" translationValue="false" triggerOnLoad="false"/>')
        L.append(f'          <binding type="control" level="ui" controlTags="pk{p["pad"]:02d}" parameter="VISIBLE" '
                 f'translation="fixed_value" translationValue="true" triggerOnLoad="false"/>')
        L.append('        </option>')
    L.append('      </menu>')
    L.append("      " + knob(694, 70, "VELOCITY", 0, 1, 0.6,
             ['<binding type="amp" level="instrument" position="0" parameter="AMP_VEL_TRACK"/>'],
             tags="deep", w=90, h=96, visible=False,
             tip="0 = every hit full level. 1 = fully velocity sensitive. Whole kit."))
    L.append('      <label x="466" y="128" width="210" height="44" textColor="FF8A8B90" textSize="11" '
             'hAlign="left" vAlign="top" tags="deep" visible="false" '
             'text="Pick a pad, shape it below. Saves with your project."/>')
    px = [460, 544, 628, 712]
    for p in pads:
        t = f'pk{p["pad"]:02d}'
        tg = f'groupTags="{t}"'
        L += ["      " + k for k in [
            knob(px[0], 200, "TUNE", -24, 24, 0,
                 [f'<binding type="amp" level="group" {tg} parameter="GROUP_TUNING"/>'],
                 tags=f"pk,{t}", pname=f"{note_name(p['note'])} {p['name']} ", w=84, h=100, visible=False, snap="whole_numbers",
                 tip="Semitones. Hold Shift for fine tuning", dflt=0),
            knob(px[1], 200, "LENGTH", 0, 1, 1,
                 [f'<binding type="amp" level="group" {tg} parameter="ENV_DECAY" translation="table" translationTable="{LENGTH_TABLE}"/>',
                  f'<binding type="amp" level="group" {tg} parameter="ENV_RELEASE" translation="table" translationTable="{LENGTH_TABLE}"/>'],
                 tags=f"pk,{t}", pname=f"{note_name(p['note'])} {p['name']} ", w=84, h=100, visible=False, tip="Full right = the whole hit. Turn left to choke it shorter."),
            knob(px[2], 200, "LEVEL", 0, 1, 0.5,
                 [f'<binding type="amp" level="group" {tg} parameter="AMP_VOLUME" translation="table" translationTable="0,0;0.5,1;1,2.5"/>'],
                 tags=f"pk,{t}", pname=f"{note_name(p['note'])} {p['name']} ", w=84, h=100, visible=False, tip="Centre = as balanced. Right = up to +8 dB"),
            knob(px[3], 200, "PAN", -100, 100, 0,
                 [f'<binding type="amp" level="group" {tg} parameter="PAN"/>'],
                 tags=f"pk,{t}", pname=f"{note_name(p['note'])} {p['name']} ", w=84, h=100, visible=False, snap="whole_numbers", dflt=0),
        ]]
    # DEEP toggle goes LAST: on load Decent Sampler fires every control's bindings in
    # file order, so this one's 'hide deep page' must come after the pad menu's 'show pad'.
    L.append('      <button x="692" y="12" width="104" height="24" value="0" style="text" '
             'tooltip="Per-pad tune, length, level, pan + velocity response">')
    L.append('        <state name="DEEP EDIT  ›">')
    for b in (vis("main", 1), vis("deep", 0), vis("pk", 0)):
        L.append("          " + b)
    L.append('        </state>')
    L.append('        <state name="‹  PLAY">')
    for b in (vis("main", 0), vis("pk", 0), vis("deep", 1), vis("pk00", 1),
              '<binding type="control" level="ui" controlTags="padmenu" parameter="VALUE" '
              'translation="fixed_value" translationValue="1"/>'):
        L.append("          " + b)
    L.append('        </state>')
    L.append('      </button>')
    L.append('    </tab>')
    # keyboard colouring by role
    L.append('    <keyboard>')
    for p in pads:
        L.append(f'      <color loNote="{p["note"]}" hiNote="{p["note"]}" color="FF{ROLE_COLOR[p["role"]][1:]}"/>')
    L.append('    </keyboard>')
    L.append('  </ui>')

    # ---- groups
    env = ('attack="0" ampEnvHold="0" decay="30" sustain="0" release="30" '
           'decayCurve="0" releaseCurve="0"')
    L.append(f'  <groups {env} ampVelTrack="0.6" volume="0dB" '
             'output1Target="MAIN_OUTPUT" output1Volume="1" '
             'output2Target="BUS_1" output2Volume="0" output3Target="BUS_2" output3Volume="0">')
    for mode, tag, onoff in (("random", "rr_rand", True), ("round_robin", "rr_cycle", False),
                             ("always", "rr_off", False)):
        for p in pads:
            v = p["files"] if mode != "always" else p["files"][:1]
            choke = ' silencedByTags="hatchoke" silencingDecay="0.04"' if p["role"] == "hat" else ""
            tags = f'all,{tag},pk{p["pad"]:02d},{p["role"]}' + (",hatchoke" if p["role"] == "hat" else "")
            L.append(f'    <group name={qa(p["name"] + " · " + tag)} enabled="{"true" if onoff else "false"}" '
                     f'tags="{tags}"{choke} seqMode="{mode}" seqLength="{len(v)}">')
            for k, f in enumerate(v):
                L.append(f'      <sample path="{sample_dir_rel}/{f}" rootNote="{p["note"]}" '
                         f'loNote="{p["note"]}" hiNote="{p["note"]}" seqPosition="{k+1}"/>')
            L.append('    </group>')
    L.append('  </groups>')

    # ---- effects: instrument chain + two parallel colour buses
    L.append('  <effects>')
    L.append('    <effect type="lowpass" frequency="22000" resonance="0.7" tags="tone_lp"/>')
    L.append('    <effect type="highpass" frequency="10" resonance="0.7" tags="tone_hp"/>')
    L.append('    <effect type="reverb" roomSize="0.55" damping="0.45" wetLevel="0" tags="space"/>')
    L.append('  </effects>')
    L.append('  <buses>')
    L.append('    <bus busVolume="1" output1Target="MAIN_OUTPUT" output1Volume="1">')
    L.append('      <effects>')
    L.append('        <effect type="wave_shaper" drive="18" driveBoost="0.4" outputLevel="0.22" highQuality="true"/>')
    L.append('        <effect type="lowpass" frequency="9000" resonance="0.7"/>')
    L.append('      </effects>')
    L.append('    </bus>')
    L.append('    <bus busVolume="1" output1Target="MAIN_OUTPUT" output1Volume="1">')
    L.append('      <effects>')
    L.append('        <effect type="bit_crusher" bitDepth="6" sampleRateReduction="5" mix="1.0"/>')
    L.append('      </effects>')
    L.append('    </bus>')
    L.append('  </buses>')
    # MIDI: mod wheel sweeps TONE
    L.append('  <midi>')
    L.append(f'    <cc number="1"><binding level="ui" type="control" parameter="VALUE" controlTags="tone_knob" '
             f'translation="linear" translationOutputMin="0" translationOutputMax="1"/></cc>')
    L.append('  </midi>')
    L.append('</DecentSampler>')
    xml = "\n".join(L)
    # tag the TONE knob for the mod-wheel binding
    xml = xml.replace('tags="main" tooltip="Left = darker', 'tags="main,tone_knob" tooltip="Left = darker', 1)
    return xml


def grid_cells(n):
    """4x4 pad grid, MPC order: first pad bottom-left."""
    cells = []
    x0, y0, w, h, g = 16, 60, 103, 72, 3
    for i in range(n):
        r, c = divmod(i, 4)
        cells.append((x0 + c * (w + g), y0 + (3 - r) * (h + g), w, h))
    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--name", default="Gonk Nord 3P Dual")
    ap.add_argument("--subtitle", default="Nord Drum 3P · dual Gonkulator")
    ap.add_argument("--out", default=None)
    ap.add_argument("--pack-q", type=float, default=-8, help="quality floor for the one-shot pack")
    args = ap.parse_args()

    kit = json.load(open(os.path.join(args.run, "kit.json")))["pads"]
    D = json.load(open(os.path.join(args.run, "slices.json")))
    out = args.out or os.path.join(args.run, "dist")
    lib = os.path.join(out, args.name)
    shutil.rmtree(lib, ignore_errors=True)
    smp = os.path.join(lib, "Samples"); res = os.path.join(lib, "Resources")
    os.makedirs(smp); os.makedirs(res)
    slug = "".join(ch for ch in args.name.title() if ch.isalnum())

    cells = grid_cells(len(kit))
    for p, cell in zip(kit, cells):
        ys, sr = process_set([os.path.join(args.run, "slices", f) for f in p["variants"]], p["role"])
        p["files"], p["cell"] = [], cell
        nm = p["name"].replace(" ", "-")
        for k, y in enumerate(ys):
            fn = f"{p['pad']+1:02d}_{nm}_rr{k+1}.wav"
            sf.write(os.path.join(smp, fn), y, sr, subtype="PCM_24")
            p["files"].append(fn)

    make_art(kit, args.name.replace("Gonk ", "GONK  "), args.subtitle,
             os.path.join(res, "panel.png"), os.path.join(res, "cover.png"))
    xml = build_preset(kit, args.name, "Samples", "Resources/panel.png", "Resources/cover.png", args.subtitle)
    open(os.path.join(lib, f"{args.name}.dspreset"), "w").write(xml)
    open(os.path.join(lib, "DSLibraryInfo.xml"), "w").write(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<DecentSamplerLibraryInfo name={qa(args.name)} '
        f'version="1.0.0" coverArt="Resources/cover.png">\n</DecentSamplerLibraryInfo>\n')

    # Decent Sampler library file = zip of the library folder, renamed .dslibrary
    dsl = os.path.join(out, f"{slug}.dslibrary")
    with zipfile.ZipFile(dsl, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(lib):
            for f in files:
                fp = os.path.join(root, f)
                z.write(fp, os.path.relpath(fp, out))

    # ---- the full one-shot pack: every clean hit, sorted by role, loudness-matched
    pack = os.path.join(out, f"{args.name} - One-Shots")
    shutil.rmtree(pack, ignore_errors=True)
    count = {}
    for s in sorted(D["slices"], key=lambda s: (s["role"], s["centroid"])):
        if s.get("q", -99) < args.pack_q:
            continue
        r = s["role"]
        count[r] = count.get(r, 0) + 1
        d = os.path.join(pack, r.title() + ("s" if not r.endswith("s") else ""))
        os.makedirs(d, exist_ok=True)
        (y,), sr = process_set([os.path.join(args.run, "slices", s["file"])], r)
        c = s["centroid"]
        tone = "deep" if c < 700 else "warm" if c < 1500 else "mid" if c < 3000 else "bright" if c < 5500 else "air"
        sf.write(os.path.join(d, f"GONK_{r}_{tone}_{count[r]:03d}.wav"), y, sr, subtype="PCM_24")
    packzip = os.path.join(out, f"{slug}_OneShots.zip")
    with zipfile.ZipFile(packzip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(pack):
            for f in sorted(files):
                fp = os.path.join(root, f); z.write(fp, os.path.relpath(fp, out))
    json.dump(kit, open(os.path.join(args.run, "kit_built.json"), "w"), indent=1)
    print("library:", dsl, f"{os.path.getsize(dsl)/1e6:.1f} MB")
    print("one-shots:", packzip, f"{os.path.getsize(packzip)/1e6:.1f} MB", count)


if __name__ == "__main__":
    main()
