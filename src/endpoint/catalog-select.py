#!/usr/bin/env python3
"""Stage a SCHEMA-COHERENT exposure-catalog set for one bumblebee run.

Why this exists
---------------
bumblebee's directory-mode catalog loader (`internal/exposure/exposure.go`,
`loadDir`) requires that *every* catalog in the directory declare the SAME
`schema_version`. A mismatch is a hard error:

    exposure catalog <f> declares schema_version "0.2.0" which conflicts
    with "0.1.0" from <g>

and `cmd/bumblebee/main.go` turns that into **exit 2 before the scan starts**.
No packages, no findings, and no `scan_summary` -- the run is completely
silent. Per-file validation cannot catch this, because each file is
individually valid; only the *combination* is illegal.

That matters right now: upstream has already bumped the brew-shipped
`threat_intel/*.json` catalogs to schema 0.2.0 (commit ea98c1e) while our
own rknightion/bumblebee-catalog assets are still 0.1.0. The moment a
release carrying 0.2.0 lands on an endpoint, a naive "copy every valid
catalog into one directory" staging step produces exactly the illegal mix
above and detection stops dead with nothing to alert on.

Selection rule
--------------
Group candidates by declared `schema_version`, then rank groups by TOTAL
ENTRY COUNT, descending. `--rank 0` stages the largest corpus, `--rank 1`
the next, and so on, so the caller can fall back if the binary turns out
not to support the preferred group.

Ranking by entry count rather than by "newest schema wins" is deliberate.
The newest group is often the *smallest* -- 11 curated campaign files at
0.2.0 versus one 30k-entry OSV catalog at 0.1.0 -- so preferring the newest
would silently discard almost the whole corpus. Keeping the larger corpus
and reporting the conflict loudly (`schema_conflict` in the emitted summary,
which becomes a `catalog_health` field and an alert) is the safer trade.

Output
------
A JSON object on stdout describing what was staged, for the caller to fold
into its `catalog_health` record:

    {"staged": 1, "entries": 30511, "schema_version": "0.1.0",
     "rejected": 0, "groups": {"0.1.0": {...}, "0.2.0": {...}},
     "schema_conflict": true, "ranks": 2}

Exit status is 0 when a group was staged, 1 when nothing usable was found
(the caller must then treat detection as UNAVAILABLE, not as "clean").
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

# Catalog schema versions bumblebee's loader accepts (exposure.go,
# supportedSchemaVersions). A file declaring anything else is dropped
# rather than staged: it would fail the load for the whole directory.
SUPPORTED_SCHEMA_VERSIONS = ("0.1.0", "0.2.0")


def inspect(path: str) -> tuple[str, int] | None:
    """Return (schema_version, entry_count) for a usable catalog, else None.

    A catalog is usable only if it is a JSON object carrying both
    `schema_version` and a non-empty `entries` list -- the same shape
    LoadFile insists on. Anything else is dropped here so it can never
    reach the staging directory.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    version = doc.get("schema_version")
    entries = doc.get("entries")
    if not isinstance(version, str) or version not in SUPPORTED_SCHEMA_VERSIONS:
        return None
    if not isinstance(entries, list) or not entries:
        return None
    return version, len(entries)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="staging directory to populate (created, must not pre-exist)")
    ap.add_argument("--rank", type=int, default=0, help="0 = largest schema group, 1 = next, ...")
    ap.add_argument("candidates", nargs="*", help="candidate catalog file paths")
    args = ap.parse_args()

    groups: dict[str, dict] = {}
    rejected = 0
    for path in args.candidates:
        if not os.path.isfile(path):
            continue
        info = inspect(path)
        if info is None:
            rejected += 1
            continue
        version, count = info
        g = groups.setdefault(version, {"files": [], "entries": 0})
        g["files"].append(path)
        g["entries"] += count

    summary = {
        "staged": 0,
        "entries": 0,
        "schema_version": "",
        "rejected": rejected,
        "ranks": len(groups),
        "schema_conflict": len(groups) > 1,
        "groups": {v: {"files": len(g["files"]), "entries": g["entries"]} for v, g in groups.items()},
    }

    if not groups:
        print(json.dumps(summary))
        return 1

    # Rank by total entries desc, breaking ties on the newer schema_version
    # so the choice is deterministic. Two passes exploiting sort stability:
    # order by version desc first, then by entry count desc.
    ranked = sorted(groups.items(), key=lambda kv: kv[0], reverse=True)
    ranked.sort(key=lambda kv: kv[1]["entries"], reverse=True)
    if args.rank >= len(ranked):
        print(json.dumps(summary))
        return 1
    version, chosen = ranked[args.rank]

    os.makedirs(args.out, mode=0o755, exist_ok=False)
    staged = 0
    for src in chosen["files"]:
        # Flatten into the staging dir. Names collide only if two sources
        # ship the same basename, in which case a numeric suffix keeps both
        # rather than silently dropping one.
        base = os.path.basename(src)
        dst = os.path.join(args.out, base)
        n = 1
        while os.path.exists(dst):
            stem, ext = os.path.splitext(base)
            dst = os.path.join(args.out, f"{stem}.{n}{ext}")
            n += 1
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o644)
        staged += 1

    summary["staged"] = staged
    summary["entries"] = chosen["entries"]
    summary["schema_version"] = version
    print(json.dumps(summary))
    return 0 if staged else 1


if __name__ == "__main__":
    sys.exit(main())
