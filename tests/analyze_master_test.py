#!/usr/bin/env python3
"""
Score a recorded master test (sessions/nord3p_master_test.json) after capture.py.

    python3 capture/capture.py sessions/out/Nord3P_Master_Test_map.json rec.wav --out work/mt
    python3 tests/analyze_master_test.py work/mt

A/B tests play A,B,A,B. A setting WORKED when the A hits match each other, the B
hits match each other, and A differs from B: effect = distance(A,B) / spread
inside A and inside B. Above 3 = clear, 1.5-3 = weak, below = no effect.
Step tests (fine pitch) look for a steady trend. Program tests: 0 and 0-again
should match each other and differ from 1.
"""
import json, os, sys
import numpy as np, soundfile as sf, librosa


def load(cap, f):
    y, sr = sf.read(os.path.join(cap, f), always_2d=True, dtype="float64")
    return y.mean(axis=1), sr


def features(m, sr):
    a = m[: int(sr * 0.3)]
    if len(a) < 2048:
        a = np.pad(a, (0, 2048 - len(a)))
    S = np.abs(librosa.stft(a, n_fft=2048, hop_length=256)) + 1e-9
    mel = librosa.power_to_db(librosa.feature.melspectrogram(S=S ** 2, sr=sr, n_mels=40)).mean(axis=1)
    hop = int(sr * 0.005)
    n = max(1, len(m) // hop)
    e = 20 * np.log10(np.sqrt(np.mean(m[: n * hop].reshape(n, hop) ** 2, axis=1)) + 1e-12)
    pk = int(e.argmax())
    below = np.where(e[pk:] < e[pk] - 30)[0]
    decay = (below[0] if len(below) else n - pk) * 5.0
    # pitch: strongest low partial after the attack (tonal body)
    body = m[int(sr * 0.02): int(sr * 0.25)]
    B = np.abs(np.fft.rfft(body * np.hanning(len(body)), n=1 << 16)) if len(body) > 256 else np.zeros(2)
    fr = np.fft.rfftfreq(1 << 16, 1 / sr)
    B[(fr < 25) | (fr > 2000)] = 0
    f0 = float(fr[B.argmax()]) if B.any() else 0.0
    click = m[: int(sr * 0.008)]
    C = np.abs(np.fft.rfft(click, n=4096)); cf = np.fft.rfftfreq(4096, 1 / sr)
    click_bright = float((C * cf).sum() / (C.sum() + 1e-12))
    return dict(mel=mel, decay_ms=decay, f0=f0, click_hz=click_bright, level=float(e.max()))


def dist(a, b):
    return float(np.linalg.norm(a["mel"] - b["mel"]) / np.sqrt(len(a["mel"])))


def ab_score(F):
    A, B = [F[0], F[2]], [F[1], F[3]]
    within = (dist(A[0], A[1]) + dist(B[0], B[1])) / 2 + 0.15
    between = np.mean([dist(x, y) for x in A for y in B])
    return between / within


def describe(F):
    keys = [("f0", "pitch Hz", 1), ("decay_ms", "decay ms", 0), ("click_hz", "attack brightness Hz", 0), ("level", "peak dB", 1)]
    return "; ".join(f"{lab}: " + " / ".join(f"{f[k]:.{d}f}" for f in F) for k, lab, d in keys)


def main():
    cap = sys.argv[1]
    C = json.load(open(os.path.join(cap, "capture.json")))
    by = {}
    for h in C["hits"]:
        if not h["missing"]:
            m, sr = load(cap, h["file"])
            by.setdefault(h["name"], []).append((h["rr"], features(m, sr)))
    F = {k: [f for _, f in sorted(v, key=lambda x: x[0])] for k, v in by.items()}
    print(f"{'test':34s} result        effect")
    report = []
    for name, fs in F.items():
        tag = name.split()[0]
        if len(fs) < 4:
            print(f"{name:34s} MISSING HITS ({len(fs)}/4)"); continue
        if "A-B" in name or "via global" in name:
            sc = ab_score(fs)
            res = "WORKED" if sc > 3 else ("weak" if sc > 1.5 else "no effect")
        elif "fine steps" in name:
            x = np.arange(4); y = np.array([f["f0"] for f in fs])
            slope = np.polyfit(x, y, 1)[0] if y.any() else 0
            spread = np.ptp(y)
            sc = spread
            res = "WORKED" if spread > 0.5 and np.all(np.diff(y) * np.sign(slope) >= -0.2) else "no effect"
        else:
            sc = np.mean([dist(fs[i], fs[j]) for i in range(4) for j in range(i + 1, 4)])
            res = "reference"
        print(f"{name:34s} {res:12s} {sc:6.2f}")
        print(f"{'':34s} {describe(fs)}")
        report.append((name, res, sc))
    # program change
    p0, p1, p0b = F.get("T11 kick program 0"), F.get("T12 kick program 1"), F.get("T13 kick program 0 again")
    if p0 and p1 and p0b:
        d01 = np.mean([dist(a, b) for a in p0 for b in p1]); d00 = np.mean([dist(a, b) for a in p0 for b in p0b])
        res = "WORKED" if d01 > 3 * (d00 + 0.15) else "no effect"
        print(f"\nprogram change: 0 vs 1 distance {d01:.2f}, 0 vs 0-again {d00:.2f} -> {res}")
        base = F.get("T01 kick baseline")
        if base:
            print(f"  baseline kick vs program 0: {np.mean([dist(a, b) for a in base for b in p0]):.2f} "
                  f"(small = program 0 is the preset you started on)")


if __name__ == "__main__":
    main()
