#!/usr/bin/env python3
"""
Recordings + hit map -> every hit cut, labelled and checked.

    python3 capture/capture.py sessions/out/Nord3P_Kit1_map.json ~/Rec/Nord3P_Kit1_*.wav --out work/kit1

Files must end in _<pass>.wav where <pass> is one of the map's passes
(clean, low, med, high, bonkers ...). Each pass must start at bar 1 so the
sync hit is in the recording.

How it finds hits (no guessing about WHAT a hit is — the map says that):
 1. sync: first sound in the file = bar 1 sync hit -> time zero
 2. coarse pass: look for each hit near where the map says it is
 3. fit a line through (scheduled, found) -> corrects interface latency and
    clock drift between the DAW and the interface
 4. fine pass: re-find each hit in a tight window around the fitted time
 5. cut from onset to just before the next scheduled hit, trim the dead tail
Writes <out>/hits/<pass>/*.wav (raw level) and <out>/capture.json, and prints
a QA report: missing hits, timing error, velocity layers that don't get louder.
"""
import argparse, json, os, re, sys
import numpy as np, soundfile as sf

EPS = 1e-12
def db(x): return 20 * np.log10(np.maximum(x, EPS))


def env_db(m, sr, win_ms=1.0):
    hop = max(1, int(sr * win_ms / 1000))
    n = len(m) // hop
    fr = np.abs(m[: n * hop]).reshape(n, hop).max(axis=1)
    return db(fr), hop


def rms_env_db(m, sr, win_ms=5.0):
    hop = max(1, int(sr * win_ms / 1000))
    n = max(1, len(m) // hop)
    fr = m[: n * hop].reshape(n, hop) if len(m) >= hop else m.reshape(1, -1)
    return db(np.sqrt(np.mean(fr ** 2, axis=1) + EPS)), hop


def noise_floor(m, sr):
    hop = int(sr * 0.05)
    n = len(m) // hop
    r = np.sqrt(np.mean(m[: n * hop].reshape(n, hop) ** 2, axis=1) + EPS)
    return float(np.percentile(db(r), 10))


def find_onset(E, hop, sr, t_lo, t_hi, floor_db, min_rise=15):
    """First envelope frame in [t_lo, t_hi) that rises into the hit.
    Threshold adapts to the hit's own peak so soft and loud hits both
    land on the start of the transient. Returns (seconds, peak_db) or None."""
    a = max(0, int(t_lo * sr / hop)); b = min(len(E), int(t_hi * sr / hop))
    if b <= a:
        return None
    look = E[a:min(len(E), b + int(0.08 * sr / hop))]
    pk = float(look.max())
    if pk < floor_db + min_rise:
        return None
    thr = max(floor_db + 10, pk - 30)
    over = E[a:b] > thr
    idx = np.where(over[:-1] & over[1:])[0]          # must stay up for 2 ms: noise spikes don't
    if not len(idx):
        return None
    return (a + idx[0]) * hop / sr, pk


def refine(m, sr, t, floor_db=-120):
    """Sample-accurate start: back up to the last quiet sample before the rise."""
    i = int(t * sr)
    a = max(0, i - int(sr * 0.004)); b = min(len(m), i + int(sr * 0.004))
    seg = np.abs(m[a:b])
    if not len(seg):
        return t
    pk = seg.max()
    j = np.argmax(seg > max(pk * 0.05, 10 ** ((floor_db + 6) / 20)))   # stay above the noise
    s = a + j - int(sr * 0.0005)              # 0.5 ms pre-roll
    # nearest zero crossing at or before s
    for k in range(max(1, s), max(1, s - 96), -1):
        if (m[k - 1] <= 0 < m[k]) or (m[k - 1] >= 0 > m[k]):
            s = k; break
    return max(0, s) / sr


def process_pass(path, M, pas, out, tail_db=-60, verbose=True):
    """tail_db: trim where the tail falls this far below the hit's peak (or 8 dB over the noise)."""
    y, sr = sf.read(path, always_2d=True, dtype="float64")
    m = y.mean(axis=1)
    E, hop = env_db(m, sr, 1.0)
    floor = float(np.percentile(E, 30))               # peak-domain floor (most of a session is silence)
    rms_floor = noise_floor(m, sr)
    # 1. sync = first thing clearly above the floor
    above = np.where(E > floor + 24)[0]
    if not len(above):
        raise SystemExit(f"{os.path.basename(path)}: no audio found")
    t0 = refine(m, sr, above[0] * hop / sr, floor)
    hits = M["hits"]
    sched = np.array([h["seconds"] for h in hits])
    # 2. coarse
    found = []
    for h in hits:
        exp = t0 + h["seconds"]
        r = find_onset(E, hop, sr, exp - 0.05, exp + 0.15, floor)
        found.append(r[0] if r else np.nan)
    found = np.array(found)
    ok = ~np.isnan(found)
    if ok.sum() < 4:
        raise SystemExit(f"{os.path.basename(path)}: only {ok.sum()} hits found — is this the right map?")
    # 3. fit latency + drift (robust: refit after dropping outliers)
    A = np.vstack([sched[ok], np.ones(ok.sum())]).T
    k, c = np.linalg.lstsq(A, found[ok], rcond=None)[0]
    res = found[ok] - (k * sched[ok] + c)
    keep = np.abs(res) < max(0.004, 4 * np.median(np.abs(res)) + 1e-4)
    k, c = np.linalg.lstsq(A[keep], found[ok][keep], rcond=None)[0]
    # 4. fine
    rows, onsets = [], []
    for h in hits:
        exp = k * h["seconds"] + c
        r = find_onset(E, hop, sr, exp - 0.012, exp + 0.03, floor)
        onsets.append(refine(m, sr, r[0], floor) if r else None)
    pdir = os.path.join(out, "hits", pas)
    os.makedirs(pdir, exist_ok=True)
    errs = []
    for i, h in enumerate(hits):
        on = onsets[i]
        row = dict(pass_=pas, **{kk: h[kk] for kk in ("index", "sound", "name", "role", "note",
                                                       "velocity", "layer", "rr")})
        if on is None:
            row.update(missing=True); rows.append(row); continue
        errs.append(on - (k * h["seconds"] + c))
        nxt = (k * hits[i + 1]["seconds"] + c) if i + 1 < len(hits) and hits[i + 1]["sound"] == h["sound"] \
            else on + h["window"] * k + 2.0                      # last hit of a sound: use the gap bars too
        end = min(len(m), int((min(nxt, on + h["window"] * k + 2.0) - 0.015) * sr))
        s = int(on * sr)
        seg = y[s:end]
        # tail trim
        e5, h5 = rms_env_db(seg.mean(axis=1), sr, 5.0)
        pk = e5.max()
        thr = max(pk + tail_db, rms_floor + 8)
        idx = np.where(e5 > thr)[0]
        cut = min(len(seg), (idx[-1] + 3) * h5) if len(idx) else len(seg)
        seg = seg[:cut]
        fn = f"{h['sound']+1:02d}_{h['name'].replace(' ', '-')}_v{h['velocity']:03d}_rr{h['rr']}.wav"
        sf.write(os.path.join(pdir, fn), seg, sr, subtype="PCM_24")
        a = seg[: int(sr * 0.15)].mean(axis=1)
        row.update(missing=False, file=os.path.join("hits", pas, fn), onset_s=round(on, 5),
                   dur=round(len(seg) / sr, 4), peak_db=round(float(db(np.abs(seg).max())), 2),
                   rms_db=round(float(db(np.sqrt(np.mean(a ** 2) + EPS))), 2),
                   clipped=bool(np.abs(seg).max() > 0.995))
        rows.append(row)
    errs = np.abs(np.array(errs)) * 1000
    stats = dict(pass_=pas, file=os.path.basename(path), sr=sr, floor_db=round(rms_floor, 1),
                 sync_s=round(t0, 4), drift_ppm=round((k - 1) * 1e6, 1),
                 found=int(sum(not r["missing"] for r in rows)), total=len(rows),
                 timing_err_ms_p95=round(float(np.percentile(errs, 95)), 2) if len(errs) else None)
    return rows, stats, sr


def qa(rows, M):
    """Warnings a human should look at before building."""
    out = []
    by = {}
    for r in rows:
        by.setdefault((r["pass_"], r["sound"]), []).append(r)
    order = {p: i for i, p in enumerate(M["passes"])}
    for (pas, si), rs in sorted(by.items(), key=lambda kv: (order.get(kv[0][0], 99), kv[0][1])):
        name = M["sounds"][si]["name"]
        miss = [r for r in rs if r["missing"]]
        for r in miss:
            out.append(f"[{pas}] {name}: MISSING hit v{r['velocity']} rr{r['rr']} (bar trigger didn't sound?)")
        live = [r for r in rs if not r["missing"]]
        clip = [r for r in live if r["clipped"]]
        if clip:
            out.append(f"[{pas}] {name}: {len(clip)} clipped hits — lower the input gain")
        lay = {}
        for r in live:
            lay.setdefault(r["layer"], []).append(r["rms_db"])
        med = [np.median(lay[l]) for l in sorted(lay)]
        for a, b, la, lb in zip(med, med[1:], sorted(lay), sorted(lay)[1:]):
            if b < a - 0.5:
                out.append(f"[{pas}] {name}: layer {lb} is quieter than layer {la} "
                           f"({b:.1f} vs {a:.1f} dB) — velocity response off or hit misfired")
        spread = {l: max(v) - min(v) for l, v in lay.items() if len(v) > 1}
        wide = {l: d for l, d in spread.items() if d > 6}
        if wide:
            out.append(f"[{pas}] {name}: round robins differ by up to {max(wide.values()):.1f} dB in "
                       f"{len(wide)} layer(s) — a weak/doubled trigger, or the sound itself varies a lot")
        if len(med) > 1 and med[-1] - med[0] < 3:
            out.append(f"[{pas}] {name}: only {med[-1]-med[0]:.1f} dB between softest and hardest layer "
                       f"— the drive may be flattening the dynamics (layers still differ in tone)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("wavs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tail-db", type=float, default=-60)
    a = ap.parse_args()
    M = json.load(open(a.map))
    passes = [p.lower() for p in M["passes"]]
    os.makedirs(a.out, exist_ok=True)
    rows, stats, srs = [], [], set()
    for w in a.wavs:
        mm = re.search(r"_([A-Za-z0-9]+)\.(wav|aif|aiff|flac)$", os.path.basename(w))
        pas = mm.group(1).lower() if mm else None
        if pas not in passes:
            sys.exit(f"{os.path.basename(w)}: name must end in _<pass>.wav, one of {passes}")
        r, s, sr = process_pass(w, M, pas, a.out, a.tail_db)
        rows += r; stats.append(s); srs.add(sr)
        print(f"  {pas:8s} found {s['found']}/{s['total']}  sync at {s['sync_s']:.3f} s  "
              f"drift {s['drift_ppm']:+.1f} ppm  timing p95 {s['timing_err_ms_p95']} ms  floor {s['floor_db']} dB")
    if len(srs) > 1:
        sys.exit(f"mixed sample rates {srs} — export every pass at the same rate")
    order = [p for p in passes if p in {s['pass_'] for s in stats}]
    warns = qa(rows, M)
    json.dump(dict(map=os.path.abspath(a.map), name=M["name"], passes=order, sounds=M["sounds"],
                   velocities=M["velocities"], round_robins=M["round_robins"], sr=srs.pop(),
                   stats=stats, warnings=warns, hits=rows),
              open(os.path.join(a.out, "capture.json"), "w"), indent=1)
    print(f"\n{len(warns)} QA warning(s)" + (":" if warns else ""))
    for w in warns:
        print("  -", w)


if __name__ == "__main__":
    main()
