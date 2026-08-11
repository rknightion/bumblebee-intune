#!/bin/sh
# Intune macOS custom attribute (string): one token describing whether
# bumblebee's most recent scans matched a known-compromised package.
#
#   finding        -> at least one profile emitted >=1 exposure finding
#   clean_partial  -> no findings, but the newest scan for some profile TIMED
#                     OUT or ran with NO exposure catalog. "clean" would be a
#                     lie: a truncated scan emits FEWER findings and still
#                     reports status=complete, and a catalog-less scan cannot
#                     emit findings at all.
#   clean          -> every profile completed in full, with a catalog, 0 findings
#   stale          -> summaries exist but the newest is older than 48h
#   pending_scan   -> installed, but no scan has completed yet. NOT a fault:
#                     this is the normal state for the window between the
#                     installer running and the first daemon firing, which the
#                     installer's own version bump re-enters every upgrade.
#   not_installed  -> no install marker; the installer has never run here
#   parse_error    -> summaries present but unreadable
#
# pending_scan and not_installed used to be one "no_run" token. That conflated
# a freshly-upgraded healthy host with a host where the install never landed,
# and because the attribute is only re-evaluated daily, a healthy host sat
# showing the scary value for up to 24h after every version bump.
#
# Reads EVERY per-(mode,profile) summary and reports the WORST case. The
# previous version read a single shared last_summary.json that all four
# daemons overwrote, so on a host with a live finding in one profile it
# reported "clean" whenever a different profile happened to finish last.
#
# Visibility only (macOS cannot drive compliance from a script); use for
# console filtering / dynamic groups alongside the Grafana alerts.
SUMMARY_DIR="/var/db/bumblebee/summary"
if [ ! -d "$SUMMARY_DIR" ] || [ -z "$(/bin/ls -A "$SUMMARY_DIR" 2>/dev/null)" ]; then
  if [ -f "/var/db/bumblebee/install.version" ]; then
    echo "pending_scan"
  else
    echo "not_installed"
  fi
  exit 0
fi
/usr/bin/python3 - "$SUMMARY_DIR" <<'PY'
import glob, json, os, sys
from datetime import datetime, timezone

STALE_HOURS = 48

paths = sorted(glob.glob(os.path.join(sys.argv[1], "*.json")))
if not paths:
    print("pending_scan" if os.path.exists("/var/db/bumblebee/install.version")
          else "not_installed")
    raise SystemExit(0)

any_ok = False
findings = 0
degraded = False
newest = None

for p in paths:
    try:
        with open(p, encoding="utf-8") as fh:
            s = json.load(fh)
    except Exception:
        degraded = True
        continue
    any_ok = True
    findings += s.get("findings_emitted") or 0
    # Three separate ways "0 findings" can fail to mean "clean":
    #   timed_out          - scan stopped early, so it emitted FEWER findings
    #   status != complete - partial/error run
    #   catalog_effective  - no exposure catalog was loaded, so findings were
    #                        impossible; the wrapper annotates the summary with
    #                        this because the scan_summary itself looks clean.
    if s.get("timed_out") or s.get("status") != "complete" or s.get("catalog_effective") == 0:
        degraded = True
    try:
        t = datetime.fromisoformat((s.get("scan_time") or "").replace("Z", "+00:00"))
        newest = t if newest is None else max(newest, t)
    except Exception:
        pass

if not any_ok:
    print("parse_error"); raise SystemExit(0)
if findings > 0:
    print("finding"); raise SystemExit(0)
if newest is not None:
    age_h = (datetime.now(timezone.utc) - newest).total_seconds() / 3600.0
    if age_h > STALE_HOURS:
        print("stale"); raise SystemExit(0)
print("clean_partial" if degraded else "clean")
PY
