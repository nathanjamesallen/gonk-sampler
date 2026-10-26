#!/usr/bin/env python3
"""
One command: recorded passes -> finished layered instrument.

    python3 capture/run.py sessions/out/Nord3P_Kit1_map.json ~/Rec/Nord3P_Kit1_*.wav \
        --name "Gonk Nord Kit 1" --subtitle "Nord Drum 3P · Gonkulator"

Stops before building if any pass is missing hits, so you can re-record that
pass instead of shipping a hole (use --force to build anyway; holes are filled
from a neighbouring round robin).
"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map"); ap.add_argument("wavs", nargs="+")
    ap.add_argument("--name", required=True)
    ap.add_argument("--subtitle", default="Gonkulator · velocity layered")
    ap.add_argument("--out", default=None)
    ap.add_argument("--default-pass", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    slug = "".join(ch for ch in a.name.title() if ch.isalnum())
    out = a.out or os.path.join("gonk_out", slug)
    work = os.path.join(out, "_capture")
    subprocess.run([sys.executable, os.path.join(HERE, "capture.py"), a.map, *a.wavs, "--out", work], check=True)
    C = json.load(open(os.path.join(work, "capture.json")))
    missing = [s for s in C["stats"] if s["found"] < s["total"]]
    if missing and not a.force:
        for s in missing:
            print(f"!! {s['file']}: {s['total'] - s['found']} hit(s) missing — re-record that pass, or rerun with --force")
        sys.exit(1)
    cmd = [sys.executable, os.path.join(HERE, "build_layered.py"), work, "--name", a.name,
           "--subtitle", a.subtitle, "--out", out]
    if a.default_pass:
        cmd += ["--default-pass", a.default_pass]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
