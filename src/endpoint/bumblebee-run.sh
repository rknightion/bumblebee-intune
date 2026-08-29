#!/bin/sh
# bumblebee-run <mode> <profile> <max_duration> [root ...] [-- <extra scan args>...]
#
#   mode=findings  -> --findings-only (findings + summary + diagnostics only)
#   mode=inventory -> full package inventory
#
# Roots before `--` are passed as --root; when none are given the profile's
# curated defaults are expanded with --all-users. Args after `--` go through
# to `bumblebee scan` verbatim (used for --exclude).
#
# Embedded into installer.sh at deploy time; edit deploy/bumblebee-run.sh.
set -eu

MODE="${1:?mode required (findings|inventory)}"
PROFILE="${2:?profile required (baseline|project)}"
MAX_DURATION="${3:?max-duration required}"
shift 3

# Split "<roots...> -- <extra...>" into two lists.
ROOT_ARGS=""
EXTRA_ARGS=""
seen_sep=0
for a in "$@"; do
  if [ "$a" = "--" ]; then seen_sep=1; continue; fi
  if [ "$seen_sep" -eq 1 ]; then EXTRA_ARGS="$EXTRA_ARGS $a"
  else ROOT_ARGS="$ROOT_ARGS --root $a"; fi
done
# No explicit roots -> use the profile defaults across every user home.
# --all-users and --root are mutually exclusive upstream, so this is either/or.
[ -n "$ROOT_ARGS" ] || ROOT_ARGS="--all-users"

ENV_FILE="/var/db/bumblebee/env"
DB_DIR="/var/db/bumblebee"
# Per-(mode,profile) summary. A single shared last_summary.json let whichever
# daemon finished last define the Intune exposure attribute for the whole
# host, so a live finding in one profile was masked by a clean run in another.
SUMMARY_FILE="$DB_DIR/summary/${MODE}_${PROFILE}.json"
LOG_DIR="/var/log/bumblebee"
LOG="$LOG_DIR/run-${MODE}-${PROFILE}.err"   # per-daemon: no concurrent writers
BIN="/usr/local/libexec/bumblebee/bin/bumblebee"
CURATED="/usr/local/libexec/bumblebee/threat_intel"
LOKI_PUSH="/usr/local/libexec/bumblebee/loki-push.py"
CAT_SELECT="/usr/local/libexec/bumblebee/catalog-select.py"
OSV_DIR="$DB_DIR/osv"
PROBE_DIR="$DB_DIR/probe"
CAT_DIR="$DB_DIR/catalog/${MODE}_${PROFILE}"
LOG_MAX_BYTES=5242880

mkdir -p "$LOG_DIR" "$OSV_DIR" "$DB_DIR/catalog" "$DB_DIR/summary" "$PROBE_DIR"

# Rotate this daemon's own log. Safe without locking now that each daemon
# writes its own file.
if [ -f "$LOG" ]; then
  size=$(/usr/bin/stat -f '%z' "$LOG" 2>/dev/null || echo 0)
  [ "$size" -gt "$LOG_MAX_BYTES" ] && { /bin/mv -f "$LOG" "$LOG.1"; : > "$LOG"; }
fi

# Sweep leftovers from a previous crash (kill -9 skips the EXIT trap). Without
# this, aborted runs accumulate multi-hundred-MB NDJSON in /var/folders.
find /var/folders -maxdepth 3 -name 'bumblebee.*' -type f -mtime +1 -delete 2>/dev/null || true
rm -rf "$DB_DIR/catalog/.stage.${MODE}_${PROFILE}."* 2>/dev/null || true

[ -r "$ENV_FILE" ] || { echo "$(date -u +%FT%TZ) ERROR: $ENV_FILE missing" >> "$LOG"; exit 1; }
# shellcheck source=/dev/null
. "$ENV_FILE"
if [ -z "${LOKI_URL:-}" ] || [ -z "${LOKI_TOKEN:-}" ]; then
  echo "$(date -u +%FT%TZ) ERROR: LOKI creds unset" >> "$LOG"
  exit 1
fi

DEVICE_ID="$(/usr/sbin/ioreg -d2 -c IOPlatformExpertDevice 2>/dev/null | /usr/bin/awk -F'"' '/IOPlatformUUID/{print $4; exit}')"
export INTUNE_DEVICE_ID="${DEVICE_ID:-unknown}"
HOSTNAME_SHORT="$(/bin/hostname -s)"

# Full Disk Access probe -- MUST be performed by the bumblebee binary itself.
#
# Running as root is NOT sufficient for TCC-protected paths, and a missing
# grant fails SILENTLY: the walker emits a debug diagnostic for unreadable
# paths and the scan still reports status=complete with a smaller
# files_considered. The result rides on catalog_health as `fda`, so a PPPC
# profile that stopped matching -- e.g. after a BUMBLEBEE_VERSION bump changed
# the binary's cdhash -- shows up in Loki instead of quietly shrinking coverage.
#
# Two traps make the obvious probes wrong:
#   1. `test -r` answers from the mode bits, and root can read anything by mode,
#      so it reports success on paths TCC then refuses to open.
#   2. TCC decides on the RESPONSIBLE process, not the one calling open().
#      Probing from this shell asks "does /bin/sh have FDA", which it never
#      will -- the PPPC grant names the bumblebee binary. Worse, running the
#      binary over SSH makes sshd the responsible process, so a probe that
#      looks correct still measures the wrong thing (live-proven 2026-08-11:
#      `responsible=com.apple.sshd-keygen-wrapper, accessing=bumblebee`).
#
# So: run the real binary, from this launchd context, against a TCC-protected
# root, and see whether it can actually see anything. fda_probe_files is
# emitted alongside the verdict because 0 is ambiguous -- it means "denied" OR
# "that directory genuinely holds nothing the scanner recognises".
FDA=0
FDA_FILES=-1
FDA_ROOT=""
# Prefer the CONSOLE user's Documents. Upstream's deployment guide recommends
# deriving the real user from /dev/console ownership rather than trusting any
# tool-supplied variable, and it matters here: picking the first /Users entry
# alphabetically lands on a service account like IntuneAdmin whose Documents
# holds one file, so a pass looks indistinguishable from a near-empty read.
_CU="$(/usr/bin/stat -f '%Su' /dev/console 2>/dev/null || true)"
case "$_CU" in
  ""|root|loginwindow) _CU="" ;;
esac
if [ -n "$_CU" ] && [ -d "/Users/$_CU/Documents" ]; then
  FDA_ROOT="/Users/$_CU/Documents"
else
  for _u in /Users/*; do
    case "$(basename "$_u")" in Shared|Guest|.*) continue ;; esac
    [ -d "$_u/Documents" ] && { FDA_ROOT="$_u/Documents"; break; }
  done
fi
if [ -n "$FDA_ROOT" ]; then
  _P="$(/usr/bin/mktemp -t bbfda.XXXXXX)"
  if "$BIN" scan --profile deep --root "$FDA_ROOT" --max-duration 20s \
       --output file --output-file "$_P" >/dev/null 2>&1; then
    FDA_FILES=$(/usr/bin/tail -n 1 "$_P" 2>/dev/null | /usr/bin/python3 -c \
      'import json,sys
try: print(json.load(sys.stdin).get("files_considered", -1))
except Exception: print(-1)' 2>/dev/null || echo -1)
    [ "${FDA_FILES:--1}" -gt 0 ] 2>/dev/null && FDA=1
  fi
  rm -f "$_P"
fi

push_one() {
  if [ -x "$LOKI_PUSH" ]; then
    /usr/bin/python3 "$LOKI_PUSH" --url "$LOKI_URL" --token "$LOKI_TOKEN" \
      --profile "$PROFILE" --mode "$MODE" --hostname "$HOSTNAME_SHORT" "$1" >>"$LOG" 2>&1 || true
  fi
}

emit_health() {  # <status> [detail]
  H="$OSV_DIR/.health.$$.json"
  /usr/bin/python3 - "$1" "${2:-}" "$PROFILE" "$MODE" "$HOSTNAME_SHORT" > "$H" <<'PY'
import json, sys
from datetime import datetime, timezone
print(json.dumps({"record_type": "agent_health",
                  "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "status": sys.argv[1], "detail": sys.argv[2],
                  "profile": sys.argv[3], "mode": sys.argv[4], "host": sys.argv[5]}))
PY
  push_one "$H"; rm -f "$H"
}

if [ ! -x "$BIN" ]; then
  emit_health binary_absent "$BIN"
  echo "$(date -u +%FT%TZ) ERROR: $BIN absent; emitted agent_health" >> "$LOG"; exit 0
fi

# --- refresh the rolling catalogs (atomic per file, last-good on failure) ---
if [ -n "${CATALOG_BASE_URL:-}" ]; then
  for asset in ${CATALOG_ASSETS:-} ${CATALOG_METAS:-}; do
    T="$OSV_DIR/.dl.$$.tmp"
    if /usr/bin/curl -fsSL --max-time 90 "$CATALOG_BASE_URL/$asset" -o "$T" 2>>"$LOG"; then
      # Metas are small provenance blobs; catalogs are validated properly by
      # catalog-select.py below. Here we only insist on parseable JSON so a
      # 404 HTML body can never overwrite a good last-known catalog.
      if /usr/bin/python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$T" >/dev/null 2>&1; then
        /bin/mv -f "$T" "$OSV_DIR/$asset"
      else
        rm -f "$T"; echo "$(date -u +%FT%TZ) WARN: $asset not valid JSON; keeping last-good" >> "$LOG"
      fi
    else
      rm -f "$T"; echo "$(date -u +%FT%TZ) WARN: fetch failed for $asset; keeping last-good" >> "$LOG"
    fi
  done
fi

# --- assemble a SCHEMA-COHERENT catalog set, then prove the binary loads it ---
#
# bumblebee's loadDir refuses a directory mixing schema_versions and exits 2
# before scanning: no records, no scan_summary, complete silence. Per-file
# validation cannot see that because each file is individually valid. So we
# group by schema_version, stage one group, and PROBE it with a real (empty
# root) scan. If the binary rejects the preferred group we fall back to the
# next one rather than discovering the problem via missing data.
CANDIDATES=""
[ -d "$CURATED" ] && for f in "$CURATED"/*.json; do [ -e "$f" ] && CANDIDATES="$CANDIDATES $f"; done
for a in ${CATALOG_ASSETS:-}; do [ -s "$OSV_DIR/$a" ] && CANDIDATES="$CANDIDATES $OSV_DIR/$a"; done

CAT_SUMMARY='{"staged":0,"entries":0,"schema_version":"","rejected":0,"ranks":0,"schema_conflict":false}'
CAT_OK=0
rank=0
while [ "$rank" -lt 3 ]; do
  STAGING="$DB_DIR/catalog/.stage.${MODE}_${PROFILE}.$$"
  rm -rf "$STAGING"
  # shellcheck disable=SC2086
  if out=$(/usr/bin/python3 "$CAT_SELECT" --out "$STAGING" --rank "$rank" $CANDIDATES 2>>"$LOG"); then
    if "$BIN" scan --profile baseline --root "$PROBE_DIR" --exposure-catalog "$STAGING" \
         --max-duration 30s --output file --output-file /dev/null >/dev/null 2>>"$LOG"; then
      CAT_SUMMARY="$out"; CAT_OK=1
      rm -rf "$CAT_DIR.old"
      if [ -d "$CAT_DIR" ]; then
        /bin/mv "$CAT_DIR" "$CAT_DIR.old" || true
      fi
      /bin/mv "$STAGING" "$CAT_DIR"; rm -rf "$CAT_DIR.old"
      break
    fi
    echo "$(date -u +%FT%TZ) WARN: binary rejected catalog rank=$rank ($out); trying next group" >> "$LOG"
    rm -rf "$STAGING"
  else
    CAT_SUMMARY="$out"
    rm -rf "$STAGING"
    break
  fi
  rank=$((rank + 1))
done

CAT_ARGS=""
FINDINGS_ARG=""
if [ "$CAT_OK" -eq 1 ]; then
  CAT_ARGS="--exposure-catalog $CAT_DIR"
  [ "$MODE" = "findings" ] && FINDINGS_ARG="--findings-only"
else
  # No usable catalog. `--findings-only` REQUIRES a catalog (bumblebee exits 2
  # with no output), so a findings run must degrade to an inventory run rather
  # than die silently. Detection is UNAVAILABLE here -- emphatically not
  # "clean" -- so say so loudly enough for an alert to fire.
  emit_health catalog_unavailable "$CAT_SUMMARY"
  echo "$(date -u +%FT%TZ) ERROR: no usable exposure catalog ($CAT_SUMMARY); scanning inventory-only" >> "$LOG"
fi

TMP="$(/usr/bin/mktemp -t bumblebee.XXXXXX)"; trap 'rm -f "$TMP"' EXIT
{ echo "----"; echo "$(date -u +%FT%TZ) bumblebee-run mode=$MODE profile=$PROFILE max=$MAX_DURATION catalog=$CAT_SUMMARY device_id=$INTUNE_DEVICE_ID"; } >> "$LOG"

set +e
# shellcheck disable=SC2086
"$BIN" scan --profile "$PROFILE" $ROOT_ARGS $EXTRA_ARGS $CAT_ARGS $FINDINGS_ARG \
  --max-duration "$MAX_DURATION" --output file --output-file "$TMP" \
  --device-id-env INTUNE_DEVICE_ID 2>>"$LOG"
SCAN_RC=$?
set -e

# Persist this profile's summary line (the trailing scan_summary record),
# annotated with whether an exposure catalog was actually in play. Without
# that annotation a catalog-less run writes a summary that is indistinguishable
# from a genuinely clean one -- status=complete, timed_out=false,
# findings_emitted=0 -- and the Intune attribute would report "clean" for a
# host on which detection was not running at all.
if [ -s "$TMP" ]; then
  if /usr/bin/tail -n 1 "$TMP" | /usr/bin/python3 -c '
import json, sys
try:
    rec = json.loads(sys.stdin.read())
except Exception:
    sys.exit(1)
rec["catalog_effective"] = int(sys.argv[1])
rec["mode"] = sys.argv[2]
json.dump(rec, open(sys.argv[3], "w"))
' "$CAT_OK" "$MODE" "${SUMMARY_FILE}.tmp"; then
    /bin/mv "${SUMMARY_FILE}.tmp" "$SUMMARY_FILE"
  else
    rm -f "${SUMMARY_FILE}.tmp"
    echo "$(date -u +%FT%TZ) WARN: could not persist summary for $MODE/$PROFILE" >> "$LOG"
  fi
  /usr/bin/python3 "$LOKI_PUSH" --url "$LOKI_URL" --token "$LOKI_TOKEN" --profile "$PROFILE" \
    --mode "$MODE" --hostname "$HOSTNAME_SHORT" "$TMP" >>"$LOG" 2>&1 \
    || echo "$(date -u +%FT%TZ) WARN: loki-push failed (rc=$SCAN_RC)" >> "$LOG"
else
  # Exit 2 with no output is how a rejected catalog or a bad root manifests.
  emit_health scan_no_output "rc=$SCAN_RC"
fi

# --- catalog freshness + effectiveness diagnostic -----------------------
# catalog_effective is the field that matters: it is 0 whenever this host is
# scanning with NO exposure catalog, which looks identical to "clean" in every
# other signal. Alert on it.
/usr/bin/python3 - "$OSV_DIR" "$PROFILE" "$HOSTNAME_SHORT" "$MODE" "$CAT_OK" "$CAT_SUMMARY" "${CATALOG_METAS:-}" "$FDA" "$FDA_FILES" "$FDA_ROOT" \
  > "$OSV_DIR/.fresh.$$.json" 2>>"$LOG" <<'PY' || true
import json, os, sys
from datetime import datetime, timezone

osv_dir, profile, host, mode, cat_ok, cat_summary_raw, metas, fda, fda_files, fda_root = sys.argv[1:11]
try:
    cat = json.loads(cat_summary_raw)
except Exception:
    cat = {}

now = datetime.now(timezone.utc)
ages, oldest, sources = {}, None, 0
for meta_name in metas.split():
    path = os.path.join(osv_dir, meta_name)
    try:
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
        gen = m.get("generated_at", "")
        age = round((now - datetime.fromisoformat(gen.replace("Z", "+00:00"))).total_seconds() / 3600.0, 2)
    except Exception:
        continue
    label = meta_name.replace("-meta.json", "").replace("catalog", "osv")
    ages[label] = age
    sources += 1
    oldest = age if oldest is None else max(oldest, age)

print(json.dumps({
    "record_type": "catalog_health",
    "time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "profile": profile, "mode": mode, "host": host,
    # Worst-case age across every catalog source, so one dead feed cannot hide
    # behind a fresh one.
    "catalog_age_hours": oldest if oldest is not None else -1,
    "catalog_age_by_source": ages,
    "catalog_sources": sources,
    "catalog_effective": int(cat_ok),
    "catalog_files": cat.get("staged", 0),
    "catalog_entries": cat.get("entries", 0),
    "catalog_schema_version": cat.get("schema_version", ""),
    "catalog_files_rejected": cat.get("rejected", 0),
    "catalog_schema_conflict": bool(cat.get("schema_conflict", False)),
    # 1 when the PPPC Full Disk Access grant is in effect. 0 means TCC-protected
    # paths are being skipped silently and coverage is smaller than it looks.
    "fda": int(fda),
    "fda_probe_files": int(fda_files),
    "fda_probe_root": fda_root,
}))
PY
[ -s "$OSV_DIR/.fresh.$$.json" ] && push_one "$OSV_DIR/.fresh.$$.json"
rm -f "$OSV_DIR/.fresh.$$.json"

echo "$(date -u +%FT%TZ) bumblebee-run done mode=$MODE profile=$PROFILE rc=$SCAN_RC catalog_effective=$CAT_OK" >> "$LOG"
exit "$SCAN_RC"
