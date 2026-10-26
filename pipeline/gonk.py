#!/usr/bin/env python3
"""
GONK pipeline — long recording in, Decent Sampler instrument out.

    python3 gonk.py NORD_3P_DUAL_GONK.wav
    python3 gonk.py take1.wav take2.wav --out ~/Desktop/GonkLibs
    python3 gonk.py take.wav --name "Gonk Volca Beats" --subtitle "Volca Beats · Gonkulator"

Each input becomes its own library: <Name>.dslibrary (drag onto Decent Sampler)
plus <Name>_OneShots.zip (every clean hit, sorted by role, for any DAW).

Tuning knobs, if a take behaves differently:
  --delta 0.05     more sensitive hit detection (busy / quiet material)
  --delta 0.12     less sensitive (washy material firing false hits)
  --max-len 6      allow longer tails (cymbals, drones)
  --rr 6           more round-robin variants per pad
  --pick-q -8      accept rougher hits into RR sets (sparse takes)
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(*a):
    print("  $", " ".join(os.path.basename(x) if x.endswith(".py") else x for x in a))
    subprocess.run([sys.executable, *a], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", default="gonk_out")
    ap.add_argument("--name", default=None, help="library name (default: from file name)")
    ap.add_argument("--subtitle", default="Gonkulator one-shots")
    ap.add_argument("--delta", default="0.07")
    ap.add_argument("--max-len", default="3.0")
    ap.add_argument("--rr", default="4")
    ap.add_argument("--pads", default="16")
    ap.add_argument("--pick-q", default="-5")
    a = ap.parse_args()
    for inp in a.inputs:
        stem = os.path.splitext(os.path.basename(inp))[0]
        name = a.name if (a.name and len(a.inputs) == 1) else "Gonk " + stem.replace("_", " ").title()
        work = os.path.join(a.out, "_work", stem)
        print(f"\n== {name}")
        run(os.path.join(HERE, "gonk_slice.py"), inp, work, "--delta", a.delta, "--max-len", a.max_len)
        run(os.path.join(HERE, "gonk_curate.py"), work, "--rr", a.rr, "--pads", a.pads, "--pick-q", a.pick_q)
        run(os.path.join(HERE, "gonk_build.py"), work, "--name", name, "--subtitle", a.subtitle,
            "--out", a.out)
        run(os.path.join(HERE, "contact_sheet.py"), work, os.path.join(work, "slices"))
        print(f"   review sheet: {os.path.join(work, 'contact.png')}")


if __name__ == "__main__":
    main()
