#!/usr/bin/env python3
"""
Combine captures into one kit, or patch a sound.

    # two Nord presets -> one 12-pad kit
    python3 capture/merge.py --out work/kit1_full work/kit1A work/kit1B
    # re-recorded hat replaces the hat from the first capture
    python3 capture/merge.py --out work/kit1_fixed work/kit1 work/kit1_hat

Sounds are matched by NAME (case-insensitive): a later capture with the same
name replaces that sound pass-by-pass (patch); a new name adds a pad. So name
sounds uniquely across the kit ("kick", "kick 2").

Pads: a slot set by hand in a spec ("slot": 46) is kept; everything else is
(re)assigned on the GM map in capture order, so the first preset gets the
primary slots (36 kick, 38 snare...) and the second fills the alternates.
"""
import argparse, json, os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from layout import assign_slots


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("captures", nargs="+", help="capture folders, in priority order")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    caps = [(d, json.load(open(os.path.join(d, "capture.json")))) for d in a.captures]
    base = caps[0][1]
    for d, C in caps[1:]:
        if sorted(C["velocities"]) != sorted(base["velocities"]) or C["round_robins"] != base["round_robins"]:
            raise SystemExit(f"{d}: velocities/round robins differ from {caps[0][0]} — layers wouldn't line up")

    # final sound list, first appearance order; later same-name entries update the definition
    sounds, order = {}, []
    for ci, (d, C) in enumerate(caps):
        for s in C["sounds"]:
            k = s["name"].strip().lower()
            if k not in sounds:
                order.append(k)
            prev = sounds.get(k, {})
            sounds[k] = dict(prev, **s)
    final = [dict(sounds[k]) for k in order]
    for s in final:
        if not s.get("slot_fixed"):
            s.pop("slot", None)
    assign_slots(final)
    final.sort(key=lambda s: s["slot"])
    idx = {s["name"].strip().lower(): i for i, s in enumerate(final)}

    # winner per (sound name, pass) = last capture with a found hit
    chosen = {}
    for ci, (d, C) in enumerate(caps):
        for h in C["hits"]:
            if not h["missing"]:
                chosen[(C["sounds"][h["sound"]]["name"].strip().lower(), h["pass_"])] = ci
    shutil.rmtree(a.out, ignore_errors=True)
    hits, passes = [], []
    for ci, (d, C) in enumerate(caps):
        passes += [p for p in C["passes"] if p not in passes]
        for h in C["hits"]:
            k = C["sounds"][h["sound"]]["name"].strip().lower()
            if chosen.get((k, h["pass_"])) != ci:
                continue
            s = final[idx[k]]
            h = dict(h, sound=idx[k], name=s["name"], role=s.get("role", "perc"))
            if not h["missing"]:
                fn = f"{s['slot']}_{s['name'].replace(' ', '-')}_v{h['velocity']:03d}_rr{h['rr']}.wav"
                rel = os.path.join("hits", h["pass_"], fn)
                os.makedirs(os.path.join(a.out, "hits", h["pass_"]), exist_ok=True)
                shutil.copy2(os.path.join(d, h["file"]), os.path.join(a.out, rel))
                h["file"] = rel
            hits.append(h)
    po = base["passes"] + [p for p in passes if p not in base["passes"]]
    out = dict(base, passes=po, sounds=final, hits=hits,
               stats=[s for _, C in caps for s in C["stats"]],
               warnings=[w for _, C in caps for w in C["warnings"] if "MISSING" not in w],
               merged_from=[os.path.abspath(d) for d, _ in caps])
    json.dump(out, open(os.path.join(a.out, "capture.json"), "w"), indent=1)
    print(f"merged {len(caps)} captures -> {len(final)} pads, "
          f"{sum(1 for h in hits if not h['missing'])} hits, passes {po}")
    for s in final:
        k = s["name"].strip().lower()
        src = sorted({os.path.relpath(caps[chosen[(k, p)]][0]) for p in po if (k, p) in chosen})
        print(f"  pad {s['slot']:3d} {s['name']:10s} {s.get('role','perc'):8s} from {', '.join(src) or 'NOTHING'}")


if __name__ == "__main__":
    main()
