#!/usr/bin/env python3
"""
Patch one capture with another: re-record a sound (or a few) without redoing the whole kit.

    python3 capture/merge.py --out work/kit1_merged work/kit1 work/kit1_hat

Later captures win per (MIDI note, pass): if the hat capture has hits for note 67
in the clean pass, those replace whatever the first capture had for note 67 /
clean (here: 24 missing hits). Everything else is kept. Pads come out ordered by
MIDI note. The result is a normal capture folder for build_layered.py.
"""
import argparse, json, os, shutil


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("captures", nargs="+", help="capture folders, base first, patches after")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    caps = [(d, json.load(open(os.path.join(d, "capture.json")))) for d in a.captures]
    base = caps[0][1]
    for d, C in caps[1:]:
        if sorted(C["velocities"]) != sorted(base["velocities"]) or C["round_robins"] != base["round_robins"]:
            raise SystemExit(f"{d}: velocities/round robins differ from {caps[0][0]} — layers wouldn't line up")
    # winner per (note, pass) = last capture that has at least one found hit there
    chosen, sounds_by_note = {}, {}
    for ci, (d, C) in enumerate(caps):
        for s in C["sounds"]:
            sounds_by_note[s["note"]] = s           # later spec names win too
        for h in C["hits"]:
            if h["missing"]:
                continue
            note = C["sounds"][h["sound"]]["note"]
            chosen[(note, h["pass_"])] = ci
    notes = sorted(sounds_by_note)
    new_index = {n: i for i, n in enumerate(notes)}
    shutil.rmtree(a.out, ignore_errors=True)
    hits, passes = [], []
    for ci, (d, C) in enumerate(caps):
        for p in C["passes"]:
            if p not in passes:
                passes.append(p)
        for h in C["hits"]:
            note = C["sounds"][h["sound"]]["note"]
            if chosen.get((note, h["pass_"])) != ci:
                continue
            si = new_index[note]
            s = sounds_by_note[note]
            h = dict(h, sound=si, name=s["name"], role=s.get("role", "perc"))
            if not h["missing"]:
                fn = f"{si+1:02d}_{s['name'].replace(' ', '-')}_v{h['velocity']:03d}_rr{h['rr']}.wav"
                rel = os.path.join("hits", h["pass_"], fn)
                os.makedirs(os.path.join(a.out, "hits", h["pass_"]), exist_ok=True)
                shutil.copy2(os.path.join(d, h["file"]), os.path.join(a.out, rel))
                h["file"] = rel
            hits.append(h)
    # passes in the base's order first
    order = base["passes"] + [p for p in passes if p not in base["passes"]]
    out = dict(base, name=base["name"], passes=order,
               sounds=[sounds_by_note[n] for n in notes], hits=hits,
               stats=[s for _, C in caps for s in C["stats"]],
               warnings=[w for _, C in caps for w in C["warnings"] if "MISSING" not in w],
               merged_from=[os.path.abspath(d) for d, _ in caps])
    json.dump(out, open(os.path.join(a.out, "capture.json"), "w"), indent=1)
    found = sum(1 for h in hits if not h["missing"])
    print(f"merged {len(caps)} captures -> {len(notes)} sounds, {found} hits, passes {order}")
    for n in notes:
        src = sorted({caps[chosen[(n, p)]][0] for p in order if (n, p) in chosen})
        print(f"  note {n:3d} {sounds_by_note[n]['name']:8s} from {', '.join(os.path.relpath(x) for x in src) or 'NOTHING'}")


if __name__ == "__main__":
    main()
