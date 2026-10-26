#!/usr/bin/env python3
"""
Stage 2 — curate.

Scores every slice for cleanliness, groups slices into sound families
(similar timbre = believable round-robin), names each family by what it
sounds like, then picks the best N families for the kit, spread across
drum roles so the kit is playable out of the box.

Output: <run>/kit.json  (pads -> ordered RR variants) + contact sheet PNG
"""
import argparse, json, os
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import AgglomerativeClustering


def quality(s):
    """Higher = cleaner, more usable one-shot. Units are roughly dB."""
    q = 0.0
    q -= max(0.0, s["pre_db"] + 45) * 0.4          # previous hit bleeding into the start
    q += min(0.0, -s["pre_db"] - 45) * 0.0
    if s["choked"]:
        q -= max(0.0, s["tail_db"] + 40) * 0.25    # tail chopped by the next hit
    q -= 3.0 if s.get("retrig") else 0.0           # had a re-trigger cut off
    q -= max(0.0, -20 - s["peak_db"]) * 0.5        # too quiet (noise floor creeps up)
    q -= 6.0 if s["dur"] < 0.06 else 0.0
    q -= 4.0 if s["attack_ms"] > 60 else 0.0       # smeared/blurred start
    return q


def role(s):
    """Plain-language drum role from spectral shape."""
    c, low, high, dec, tonal, glide = (s["centroid"], s["low"], s["high"], s["decay_ms"],
                                       s["tonal"], s["glide"])
    if low > 0.45 and c < 900:
        return "kick"
    if low > 0.2 and c < 1600:
        return "tom" if tonal > 12 else "kick"
    if high > 0.25 or c > 5500:
        return "hat" if s["dur"] < 0.25 or dec < 120 else "cymbal"
    if glide < -1.6 and tonal > 8:
        return "zap"
    if s["flatness"] > 0.008 or (c > 2200 and tonal < 8):
        return "snare"
    if tonal > 18:
        return "bell" if c > 1500 else "tom"
    return "perc"


# what a playable 16-pad kit wants, in pad order (GM-ish: kicks low, hats up top)
ROLE_PLAN = [("kick", 3), ("snare", 3), ("tom", 2), ("perc", 3), ("zap", 1),
             ("bell", 1), ("hat", 2), ("cymbal", 1)]
ROLE_FALLBACK = {"kick": ["tom"], "tom": ["perc", "kick"], "snare": ["perc"], "perc": ["snare", "tom", "bell"],
                 "zap": ["perc", "bell"], "bell": ["perc", "tom"], "hat": ["cymbal", "snare"],
                 "cymbal": ["hat", "snare"]}

TONE_WORDS = [(700, "deep"), (1500, "warm"), (3000, "mid"), (5500, "bright"), (1e9, "air")]
def describe(fam):
    c = np.median([s["centroid"] for s in fam])
    dec = np.median([s["decay_ms"] for s in fam])
    tone = next(w for lim, w in TONE_WORDS if c < lim)
    length = "snap" if dec < 90 else ("short" if dec < 180 else ("long" if dec < 400 else "ring"))
    return tone, length


def feat_matrix(sl):
    rows = []
    for s in sl:
        m = s["mfcc"]
        rows.append(m[1:13] + m[21:27] + [np.log(s["centroid"] + 1), s["low"], s["high"],
                    np.log(s["decay_ms"] + 1), s["glide"], s["tonal"] / 10, s["flatness"] * 50])
    X = StandardScaler().fit_transform(np.array(rows))
    X[:, :12] *= 1.4      # timbre (MFCC) matters most for "same sound, new hit"
    return X


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--pads", type=int, default=16)
    ap.add_argument("--rr", type=int, default=4, help="round-robin variants per pad")
    ap.add_argument("--families", type=int, default=40)
    ap.add_argument("--min-q", type=float, default=-14)
    ap.add_argument("--pick-q", type=float, default=-5, help="worst quality allowed in a RR set")
    args = ap.parse_args()

    D = json.load(open(os.path.join(args.run, "slices.json")))
    sl = D["slices"]
    for s in sl:
        s["q"] = quality(s)
        s["role"] = role(s)
    good = [s for s in sl if s["q"] > args.min_q]
    X = feat_matrix(good)
    lab = AgglomerativeClustering(n_clusters=min(args.families, len(good)), linkage="ward").fit_predict(X)

    fams = []
    for k in set(lab):
        idx = np.where(lab == k)[0]
        members = [good[i] for i in idx]
        cen = X[idx].mean(axis=0)
        dist = np.linalg.norm(X[idx] - cen, axis=1)
        # RR pick: closest-to-centre AND clean. Score = quality - spread penalty
        spread = dist / (np.median(dist) + 1e-9)
        # RR variants must feel like the same drum: similar length + similar level
        ld = np.log([m["dur"] for m in members]); ld_med = np.median(ld)
        rank = sorted(range(len(members)), key=lambda j: -(members[j]["q"] - 3 * spread[j]
                                                          - 9 * abs(ld[j] - ld_med)))
        rank = [j for j in rank if members[j]["q"] > args.pick_q]
        picks = []
        for j in rank:                                 # keep lengths within ~2x of the lead hit
            if picks and abs(np.log(members[j]["dur"] / picks[0]["dur"])) > 0.7:
                continue
            picks.append(members[j])
            if len(picks) == args.rr: break
        roles = [m["role"] for m in members]
        r = max(set(roles), key=roles.count)
        if not picks:
            continue
        fams.append(dict(id=int(k), size=len(members), role=r, picks=picks,
                         score=float(np.mean([p["q"] for p in picks])) + min(len(members), 12) * 0.25,
                         tightness=float(np.mean(dist[[rank[j] for j in range(len(picks))]]))))

    # choose pads per role plan, best-scoring families first, avoid near-duplicates
    chosen, used = [], set()
    cen_of = {f["id"]: X[np.where(lab == f["id"])[0]].mean(axis=0) for f in fams}
    def too_close(f):
        return any(np.linalg.norm(cen_of[f["id"]] - cen_of[c["id"]]) < 1.2 for c in chosen)
    for r, n in ROLE_PLAN:
        pool = sorted([f for f in fams if f["role"] == r and len(f["picks"]) >= 2], key=lambda f: -f["score"])
        for fb in ROLE_FALLBACK[r]:
            pool += sorted([f for f in fams if f["role"] == fb and len(f["picks"]) >= 2], key=lambda f: -f["score"])
        got = 0
        for f in pool:
            if got == n: break
            if f["id"] in used or too_close(f): continue
            f["pad_role"] = r; chosen.append(f); used.add(f["id"]); got += 1
    for f in sorted(fams, key=lambda f: -f["score"]):     # top up to N
        if len(chosen) >= args.pads: break
        if f["id"] not in used and len(f["picks"]) >= 2:
            f["pad_role"] = f["role"]; chosen.append(f); used.add(f["id"])

    order = {r: i for i, (r, _) in enumerate(ROLE_PLAN)}
    chosen.sort(key=lambda f: (order.get(f["pad_role"], 99), np.median([p["centroid"] for p in f["picks"]])))
    names, pads = {}, []
    for i, f in enumerate(chosen[: args.pads]):
        tone, length = describe(f["picks"])
        base = f"{f['pad_role']} {tone} {length}"
        names[base] = names.get(base, 0) + 1
        label = base if names[base] == 1 else f"{base} {names[base]}"
        pads.append(dict(pad=i, note=36 + i, name=label, role=f["pad_role"], family=f["id"],
                         family_size=f["size"], variants=[p["file"] for p in f["picks"]],
                         q=[round(p["q"], 1) for p in f["picks"]]))
    json.dump(dict(pads=pads), open(os.path.join(args.run, "kit.json"), "w"), indent=1)
    json.dump(D, open(os.path.join(args.run, "slices.json"), "w"), indent=1)   # now with q + role
    for p in pads:
        print(f"{p['note']:3d} {p['name']:24s} fam#{p['family']:<3d} n={p['family_size']:<4d} q={p['q']}")
    from collections import Counter
    print("roles across clean slices:", Counter(s["role"] for s in good), "clean:", len(good), "/", len(sl))


if __name__ == "__main__":
    main()
