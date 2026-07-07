#!/usr/bin/env python3
"""Materialize Rerun web viewer assets into git-ignored static dirs (dev only).

Copies the @rerun-io/web-viewer ESM + wasm and the exported .rrd into
backend/static/ so the no-build /rerun page can serve them. The 47 MB wasm and
the .rrd are NOT committed; run this script to (re)create them locally.

One patch is applied: index.js dynamically imports "./re_viewer" WITHOUT a file
extension, which a browser (unlike a bundler) cannot resolve and an import map
cannot rewrite (it is a relative specifier). We rewrite it to "./re_viewer.js"
so the module loads via a plain import map — no bundler needed.

Usage: python tools/prepare_rerun_web_assets.py [--source <@rerun-io/web-viewer dir>]
"""
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.33.1"
DEFAULT_SOURCE = ROOT / "experiments" / "rerun_web_control" / "node_modules" / "@rerun-io" / "web-viewer"
VENDOR = ROOT / "backend" / "static" / "vendor" / "rerun-web-viewer" / VERSION
RRD_SRC = ROOT / "rerun_build" / "demo_obstacle_stop_01.rrd"
RRD_DST = ROOT / "backend" / "static" / "rerun" / "demo_obstacle_stop_01.rrd"

PATCH_FROM = 'await import("./re_viewer")'
PATCH_TO = 'await import("./re_viewer.js")'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                    help="path to the installed @rerun-io/web-viewer package")
    args = ap.parse_args()
    src = args.source

    for f in ("index.js", "re_viewer.js", "re_viewer_bg.wasm"):
        if not (src / f).exists():
            raise SystemExit(f"missing {f} in {src}\n"
                             f"run `npm install` in experiments/rerun_web_control first, "
                             f"or pass --source")

    VENDOR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "re_viewer.js", VENDOR / "re_viewer.js")
    shutil.copy2(src / "re_viewer_bg.wasm", VENDOR / "re_viewer_bg.wasm")

    text = (src / "index.js").read_text()
    patched = text.replace(PATCH_FROM, PATCH_TO)
    if patched == text:
        print("[warn] expected import patch site not found; index.js copied unchanged")
    (VENDOR / "index.js").write_text(patched)

    RRD_DST.parent.mkdir(parents=True, exist_ok=True)
    if RRD_SRC.exists():
        shutil.copy2(RRD_SRC, RRD_DST)
    else:
        print(f"[warn] {RRD_SRC} missing — run tools/export_to_rerun.py first")

    print(f"[prepare] viewer -> {VENDOR}")
    print(f"[prepare] rrd    -> {RRD_DST}")


if __name__ == "__main__":
    main()
