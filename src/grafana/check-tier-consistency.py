#!/usr/bin/env python3
"""Assert the declared-only source_type list is identical everywhere it appears.

Run:
    python3 deploy/check_tier_consistency.py            # exits 1 on drift
    python3 deploy/check_tier_consistency.py --dashboard /path/to/dash.json

WHY THIS EXISTS
---------------
The installed-vs-declared split is the single most load-bearing judgement in the
deployment, and it is expressed as a regex alternation duplicated across three
systems that cannot see each other:

    deploy/bumblebee_alerts.py            what pages
    deploy/bumblebee_recording_rules.yaml what the metrics count
    the Grafana dashboard JSON            what a human reads

`composer-lock` was added to the first two on 2026-08-11 and missed in the
third. For a fortnight the dashboard classified a composer.lock entry as
INSTALLED while the alerts classified it as declared-only -- the same finding in
two different tiers depending on where you looked, with 14 live packagist
packages behind it. Nothing failed; the two answers simply disagreed.

An omission fails in the safe direction (a false critical, not silence), which
is deliberate -- see the DECLARED_ONLY comment in bumblebee_alerts.py -- but
"safe" is not "correct", and a disagreement between the dashboard and the pager
is its own bug.

Wire this into CI, or into whatever runs before a deploy. It takes no
credentials and makes no network calls.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DASHBOARD = Path.home() / "repos/<your-git-sync-repo>/bumblebee/bumblebee-fleet.json"

# Any alternation that starts with npm-lockfile is one of these lists. Anchoring
# on the first element rather than trying to parse LogQL keeps this robust to
# the surrounding query changing shape.
ALTERNATION = re.compile(r"npm-lockfile[|a-z0-9_-]*")


def from_alerts() -> set[str]:
    spec = importlib.util.spec_from_file_location("ba", HERE / "bumblebee_alerts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.DECLARED_ONLY.split("|"))


def alternations(text: str) -> list[set[str]]:
    return [set(m.split("|")) for m in ALTERNATION.findall(text)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD,
                    help="path to the fleet dashboard JSON (Git-Sync working copy)")
    args = ap.parse_args()

    expected = from_alerts()
    print(f"declared-only list ({len(expected)}): {'|'.join(sorted(expected))}\n")

    problems = []

    sources: list[tuple[str, Path]] = [
        ("recording rules", HERE / "bumblebee_recording_rules.yaml"),
        ("dashboard", args.dashboard),
    ]
    for label, path in sources:
        if not path.is_file():
            # A missing dashboard checkout is not drift -- say so and move on
            # rather than passing silently, which would make the check useless
            # in exactly the environment where nobody notices.
            print(f"SKIP  {label}: {path} not found")
            problems.append(f"{label}: could not be checked ({path} missing)")
            continue
        found = alternations(path.read_text())
        if not found:
            print(f"SKIP  {label}: no declared-only list found in {path.name}")
            problems.append(f"{label}: no declared-only list found")
            continue
        bad = [s for s in found if s != expected]
        if bad:
            for s in bad:
                missing = sorted(expected - s)
                extra = sorted(s - expected)
                print(f"DRIFT {label}: missing={missing} extra={extra}")
            problems.append(f"{label}: {len(bad)}/{len(found)} occurrences differ")
        else:
            print(f"OK    {label}: {len(found)} occurrence(s) match")

    if problems:
        print("\nFAIL")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nPASS: every declared-only list agrees")
    return 0


if __name__ == "__main__":
    sys.exit(main())
