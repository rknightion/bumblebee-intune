#!/bin/sh
# Intune macOS custom attribute (string). One line, inside the 5000-char
# attribute limit, summarising bumblebee's most recent scans.
#
# Reports the WORST case across every per-(mode,profile) summary rather than
# whichever daemon wrote last: findings are summed, timed_out/degraded is
# sticky, and the oldest scan_time is reported so a profile that has quietly
# stopped running is visible here rather than being hidden by a fresh sibling.
# The attribute value is always defined. With no summary yet it distinguishes
# status=pending_scan (installed, first daemon has not fired, the normal state
# for a few minutes after every install and every version bump) from
# status=not_installed (the installer has never run here). Collapsing both into
# one "no_run" made a healthy just-upgraded host look identical to a failed
# deployment for up to 24h, since the attribute only re-evaluates daily.
SUMMARY_DIR="/var/db/bumblebee/summary"
if [ ! -d "$SUMMARY_DIR" ] || [ -z "$(/bin/ls -A "$SUMMARY_DIR" 2>/dev/null)" ]; then
  if [ -f "/var/db/bumblebee/install.version" ]; then
    echo "status=pending_scan version=$(cat /var/db/bumblebee/install.version 2>/dev/null)"
  else
    echo "status=not_installed"
  fi
  exit 0
fi

/usr/bin/python3 - "$SUMMARY_DIR" <<'PY'
import glob, json, os, sys

paths = sorted(glob.glob(os.path.join(sys.argv[1], "*.json")))
if not paths:
    print("status=pending_scan" if os.path.exists("/var/db/bumblebee/install.version")
          else "status=not_installed")
    raise SystemExit(0)

profiles, bad = [], 0
agg = {"pkgs": 0, "findings": 0, "dup": 0, "supp": 0, "diag": 0, "files": 0}
worst_status, any_timeout = "complete", False
oldest_scan, newest_scan = None, None
scanner, host, errs = "?", "?", []

for p in paths:
    name = os.path.basename(p).removesuffix(".json")
    try:
        with open(p, encoding="utf-8") as fh:
            s = json.load(fh)
    except Exception:
        bad += 1
        profiles.append(f"{name}:unreadable")
        continue

    agg["pkgs"] += s.get("package_records_emitted", 0) or 0
    agg["findings"] += s.get("findings_emitted", 0) or 0
    agg["dup"] += s.get("duplicates", 0) or 0
    agg["supp"] += s.get("package_records_suppressed", 0) or 0
    agg["diag"] += s.get("diagnostics_count", 0) or 0
    agg["files"] += s.get("files_considered", 0) or 0

    st = s.get("status", "?")
    to = bool(s.get("timed_out"))
    any_timeout = any_timeout or to
    if st != "complete":
        worst_status = st
    profiles.append(f"{name}:{st}{'/timeout' if to else ''}:{s.get('findings_emitted', 0)}")

    t = s.get("scan_time")
    if t:
        oldest_scan = t if oldest_scan is None else min(oldest_scan, t)
        newest_scan = t if newest_scan is None else max(newest_scan, t)
    scanner = s.get("scanner_version", scanner)
    host = (s.get("endpoint") or {}).get("hostname", host)
    if s.get("error"):
        errs.append(str(s["error"]).replace(" ", "_")[:80])

# timed_out is called out separately from status because bumblebee reports
# status=complete on a timeout (a deadline is not an error upstream), so
# status alone hides a truncated, under-reporting scan.
fields = [
    ("status", worst_status),
    ("timed_out", str(any_timeout).lower()),
    ("profiles", len(paths)),
    ("unreadable", bad),
    ("pkgs", agg["pkgs"]),
    ("findings", agg["findings"]),
    ("dup", agg["dup"]),
    ("supp", agg["supp"]),
    ("diag", agg["diag"]),
    ("files", agg["files"]),
    ("scanner", scanner),
    ("host", host),
    ("oldest_scan", oldest_scan or "?"),
    ("newest_scan", newest_scan or "?"),
    ("detail", ",".join(profiles)),
]
if errs:
    fields.append(("err", "|".join(errs)[:200]))

print(" ".join(f"{k}={v}" for k, v in fields)[:4990])
PY
