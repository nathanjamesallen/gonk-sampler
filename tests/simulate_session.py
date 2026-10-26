#!/usr/bin/env python3
"""
Fake a recorded session from a hit map, for testing capture.py without hardware.

Each sound uses real one-shots (from a slice folder) per round robin; velocity
scales level AND darkens the tone, each pass adds more saturation. The
"recording" gets pre-roll, interface latency, clock drift, per-hit MIDI jitter,
a noise floor, and one dropped hit, so the aligner has real problems to solve.

    python3 tests/simulate_session.py map.json kit.json slices_dir out_dir
Writes <out>/<Name>_<pass>.wav and <out>/truth.json (true onset per hit per pass).
"""
import json, os, sys
import numpy as np, soundfile as sf
from scipy.signal import lfilter

SR = 48000
DRIVE = {"clean": None, "low": 2.5, "med": 6, "high": 15, "bonkers": 45}


def one_pole_lp(x, fc):
    a = np.exp(-2 * np.pi * fc / SR)
    return lfilter([1 - a], [1, -a], x, axis=0)


def render_hit(base, vel):
    g = (vel / 127) ** 1.6
    fc = 1500 * (20000 / 1500) ** (vel / 127)        # softer = darker
    y = one_pole_lp(base, fc) if fc < 19000 else base.copy()
    return y * g


def drive(x, amt):
    if amt is None:
        return x
    return np.tanh(amt * x) / np.tanh(amt) * 0.9


def main():
    mp, kit, sdir, out = sys.argv[1:5]
    M = json.load(open(mp)); K = json.load(open(kit))["pads"]
    os.makedirs(out, exist_ok=True)
    rng = np.random.default_rng(3)
    by_role = {}
    for p in K:
        by_role.setdefault(p["role"], []).append(p)
    # pick source variants per sound by role
    src = []
    for si, s in enumerate(M["sounds"]):
        cands = by_role.get(s["role"]) or by_role.get("perc")
        p = cands[min(si, len(cands) - 1) % len(cands)] if s["role"] == "tom" else cands[0]
        if s["role"] == "tom" and len(cands) > 1:
            p = cands[[x["name"] for x in M["sounds"] if x["role"] == "tom"].index(s["name"]) % len(cands)]
        vs = [sf.read(os.path.join(sdir, f), always_2d=True)[0] for f in p["variants"]]
        src.append(vs)
    total = int((M["total_seconds"] + 2) * SR)
    pre = 0.35; lat = 0.0123; drift = 1.00002
    truth = {}
    for pi, pas in enumerate(M["passes"]):
        y = rng.normal(0, 10 ** (-82 / 20), (total, 2))
        t_all = [(None, M["sync"]["seconds"], 0, 127)] + [(h["index"], h["seconds"], h["sound"], h["velocity"]) for h in M["hits"]]
        rows = {}
        for idx, t, si, v in t_all:
            if pas == "high" and idx == 77:      # dropped trigger
                rows[idx] = None; continue
            jit = rng.normal(0, 0.0008)
            on = pre + lat + t * drift + jit
            rr = 0 if idx is None else (M["hits"][idx]["rr"] - 1)
            base = src[si][rr % len(src[si])]
            hit = drive(render_hit(base, v) * 0.5, DRIVE.get(pas))
            a = int(on * SR); b = min(total, a + len(hit))
            y[a:b] += hit[: b - a]
            rows[idx] = round(on, 5)
        fn = os.path.join(out, f"{M['name'].replace(' ', '_')}_{pas}.wav")
        sf.write(fn, np.clip(y, -1, 1), SR, subtype="PCM_24")
        truth[pas] = {str(k): v for k, v in rows.items()}
        print("wrote", os.path.basename(fn))
    json.dump(truth, open(os.path.join(out, "truth.json"), "w"))


if __name__ == "__main__":
    main()
