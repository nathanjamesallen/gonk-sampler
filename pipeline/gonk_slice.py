#!/usr/bin/env python3
"""
Stage 1 — slice + analyze.

Takes one long recording, finds every hit, cuts it into one-shots with
clean starts and natural tails, and measures each one so later stages
can sort, rank and group them.

Output: <out>/slices/*.wav (raw cuts, 24-bit) + <out>/slices.json
"""
import argparse, json, os
import numpy as np, soundfile as sf, librosa

EPS = 1e-12
def db(x): return 20 * np.log10(np.maximum(x, EPS))


def envelope(x, sr, win_ms=5):
    """RMS envelope (one value per hop) of a mono signal."""
    hop = int(sr * win_ms / 1000)
    n = len(x) // hop
    if n == 0:
        return np.array([np.sqrt(np.mean(x ** 2) + EPS)]), hop
    fr = x[: n * hop].reshape(n, hop)
    return np.sqrt(np.mean(fr ** 2, axis=1) + EPS), hop


def detect_onsets(mono, sr, delta):
    """Onset sample positions, backtracked to the true start of each hit."""
    sr_d = 22050
    y = librosa.resample(mono, orig_sr=sr, target_sr=sr_d)
    hop = 128  # ~5.8 ms resolution
    env = librosa.onset.onset_strength(y=y, sr=sr_d, hop_length=hop, aggregate=np.median)
    fr = librosa.onset.onset_detect(onset_envelope=env, sr=sr_d, hop_length=hop,
                                    backtrack=True, delta=delta, wait=4)
    return (librosa.frames_to_samples(fr, hop_length=hop) * sr / sr_d).astype(int), env


def refine_start(mono, sr, s, search_ms=15):
    """Snap the cut to just before the first sample that rises out of the
    pre-hit floor, so every slice starts on its transient with no lead-in."""
    a = max(0, s - int(sr * search_ms / 1000))
    b = min(len(mono), s + int(sr * search_ms / 1000))
    seg = np.abs(mono[a:b])
    if len(seg) == 0:
        return s
    pk = seg.max()
    idx = np.argmax(seg > pk * 0.1)
    return max(0, a + idx - int(sr * 0.0008))   # keep 0.8 ms of pre-roll


def zero_cross_back(x, i, max_back=96):
    """Move index back to the nearest zero crossing (avoids start clicks)."""
    lo = max(1, i - max_back)
    for j in range(i, lo, -1):
        if x[j - 1] <= 0 < x[j] or x[j - 1] >= 0 > x[j]:
            return j
    return i


def features(seg, sr):
    mono = seg.mean(axis=1)
    peak = float(np.abs(seg).max())
    n_an = min(len(mono), int(sr * 0.3))
    a = mono[:n_an]
    S = np.abs(librosa.stft(a, n_fft=2048, hop_length=256)) + EPS
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    P = S ** 2
    e = P.sum(axis=0)
    w = e / e.sum()
    tot = P.sum()
    cent = float((librosa.feature.spectral_centroid(S=S, sr=sr)[0] * w).sum())
    flat = float((librosa.feature.spectral_flatness(S=S)[0] * w).sum())
    low = float(P[freqs < 150].sum() / tot)
    mid = float(P[(freqs >= 150) & (freqs < 2000)].sum() / tot)
    high = float(P[freqs >= 6000].sum() / tot)
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(
        librosa.feature.melspectrogram(S=P, sr=sr, n_mels=64)), n_mfcc=20)
    # fundamental-ish: strongest spectral peak in the body (after the click)
    body = P[:, min(4, P.shape[1] - 1):].sum(axis=1)
    body[freqs < 25] = 0
    f0 = float(freqs[np.argmax(body)])
    # tonality: how peaky the body spectrum is (1 = pure tone)
    tonal = float(body.max() / (body.sum() + EPS)) * 50
    # pitch glide: centroid motion over the first 150 ms (zaps / sweeps)
    c_tr = librosa.feature.spectral_centroid(S=S, sr=sr)[0]
    k = max(2, min(len(c_tr), int(0.15 * sr / 256)))
    glide = float(np.log2((c_tr[k - 1] + 1) / (c_tr[0] + 1)))
    env, hop = envelope(mono, sr, 2)
    attack_ms = float(np.argmax(env) * hop / sr * 1000)
    # decay time to -30 dB from peak
    pk_i = int(np.argmax(env))
    below = np.where(db(env[pk_i:]) < db(env[pk_i]) - 30)[0]
    decay_ms = float((below[0] if len(below) else len(env) - pk_i) * hop / sr * 1000)
    width = 0.0
    if seg.shape[1] == 2:
        mid_s = (seg[:, 0] + seg[:, 1]) / 2; side = (seg[:, 0] - seg[:, 1]) / 2
        width = float(np.sqrt(np.mean(side ** 2)) / (np.sqrt(np.mean(mid_s ** 2)) + EPS))
    return dict(peak_db=float(db(peak)), centroid=cent, flatness=flat, low=low, mid=mid,
                high=high, f0=f0, tonal=tonal, glide=glide, attack_ms=attack_ms,
                decay_ms=decay_ms, width=width,
                mfcc=[float(v) for v in mfcc.mean(axis=1)] + [float(v) for v in mfcc.std(axis=1)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("out")
    ap.add_argument("--max-len", type=float, default=3.0, help="longest one-shot, seconds")
    ap.add_argument("--min-len", type=float, default=0.04)
    ap.add_argument("--gate-db", type=float, default=-42, help="ignore hits quieter than this peak")
    ap.add_argument("--delta", type=float, default=0.07, help="onset sensitivity (lower = more hits)")
    ap.add_argument("--tail-db", type=float, default=-54, help="trim tail where it falls this far below peak")
    args = ap.parse_args()

    y, sr = sf.read(args.input, always_2d=True, dtype="float64")
    mono = y.mean(axis=1)
    src = os.path.splitext(os.path.basename(args.input))[0]
    on, _ = detect_onsets(mono, sr, args.delta)
    on = sorted(set(refine_start(mono, sr, int(s)) for s in on))
    # drop doubles created by refinement
    clean = []
    for s in on:
        if not clean or s - clean[-1] > int(sr * 0.025):
            clean.append(s)
    on = clean

    # global noise floor = 10th percentile of 50 ms RMS over the take
    env50, _ = envelope(mono, sr, 50)
    floor_db = float(np.percentile(db(env50), 10))

    os.makedirs(os.path.join(args.out, "slices"), exist_ok=True)
    meta = []
    for i, s in enumerate(on):
        nxt = on[i + 1] if i + 1 < len(on) else len(mono)
        e = min(nxt, s + int(sr * args.max_len))
        s0 = zero_cross_back(mono, s)
        seg = y[s0:e].copy()
        if len(seg) < sr * args.min_len:
            continue
        m = seg.mean(axis=1)
        pk = np.abs(seg).max()
        if db(pk) < args.gate_db:
            continue
        env, hop = envelope(m, sr, 5)
        env_db = db(env)
        pk_db = env_db.max()
        # missed re-trigger: after the peak, the level dips then jumps back up
        # by >8 dB (within 24 dB of peak) -> cut there, it's the next hit.
        # 20 ms overlapping window: longer than a 50 Hz cycle so kick ripple
        # isn't mistaken for a new hit
        r_hop = int(sr * 0.005)
        renv = db(librosa.feature.rms(y=m, frame_length=int(sr * 0.02), hop_length=r_hop,
                                      center=False)[0])
        retrig = False
        if len(renv) > 4:
            pk_i = int(np.argmax(renv))
            run_min = np.minimum.accumulate(renv[pk_i:])
            rise = renv[pk_i:] - run_min
            hits = np.where((rise > 9) & (renv[pk_i:] > renv[pk_i] - 24) &
                            (np.arange(len(rise)) * r_hop > sr * 0.03))[0]
        else:
            hits = []
        if len(hits):
            j = pk_i + hits[0]
            while j > pk_i and renv[j - 1] < renv[j]:   # walk back to where the rise began
                j -= 1
            cut = j * r_hop + int(sr * 0.01)            # frame is 20 ms; rise starts mid-frame
            if cut > sr * args.min_len:
                seg = seg[:cut]; m = m[:cut]; retrig = True
                env, hop = envelope(m, sr, 5); env_db = db(env)
        # tail trim: last frame above max(peak - tail_db, floor + 6)
        thresh = max(pk_db + args.tail_db, floor_db + 6)
        above = np.where(env_db > thresh)[0]
        end = min(len(seg), (above[-1] + 2) * hop) if len(above) else len(seg)
        seg = seg[:end]
        if len(seg) < sr * args.min_len:
            continue
        # quality measures
        pre = mono[max(0, s0 - int(sr * 0.03)):s0]
        pre_db = float(db(np.sqrt(np.mean(pre ** 2) + EPS)) - db(pk)) if len(pre) else -120.0
        tl = seg[-int(sr * 0.02):].mean(axis=1)
        tail_db = float(db(np.sqrt(np.mean(tl ** 2) + EPS)) - pk_db)
        choked = bool(retrig or (end >= len(y[s0:e]) - hop and e == nxt and tail_db > -30))
        f = features(seg, sr)
        name = f"{src}_{i:04d}.wav"
        sf.write(os.path.join(args.out, "slices", name), seg, sr, subtype="PCM_24")
        meta.append(dict(file=name, src=src, start=int(s0), start_s=round(s0 / sr, 3),
                         dur=round(len(seg) / sr, 4), pre_db=pre_db, tail_db=tail_db,
                         choked=choked, retrig=retrig, clipped=bool(pk > 0.995), **f))
    json.dump(dict(source=args.input, sr=sr, floor_db=floor_db, slices=meta),
              open(os.path.join(args.out, "slices.json"), "w"), indent=1)
    print(f"{len(on)} onsets -> {len(meta)} slices kept  (floor {floor_db:.1f} dB)")


if __name__ == "__main__":
    main()
