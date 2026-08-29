#!/bin/sh
# bumblebee Intune installer (macOS, deviceShellScript). Runs as root.
#
# ACQUISITION: pinned upstream release tarball, SHA256-verified, root-owned.
#   The previous design acquired the binary via Workbrew into the console
#   user's /opt/homebrew and then staged it root-owned for root LaunchDaemons
#   to execute. That is a local privilege-escalation path: anyone who can
#   write /opt/homebrew/bin/bumblebee owns root on the next installer cycle.
#   It was also an unpinned `brew upgrade` on every run, i.e. an unreviewed
#   third-party binary rolling onto the fleet automatically.
#   We now fetch a pinned version straight from the upstream release, verify
#   it against a pinned SHA256, and never read a user-writable path.
#
#   UPGRADES are a two-line edit (BUMBLEBEE_VERSION + the SHA256 pair) plus a
#   bump of INSTALL_VERSION. The installer is idempotent: it re-downloads only
#   when the staged version marker does not match, and swaps the binary
#   atomically, so an upgrade is the same code path as a fresh install and can
#   be ringed with an Intune assignment filter.
#
# SCAN ARCHITECTURE (two shapes, deliberately split):
#   - findings  (every 4h by default, RunAtLoad): bounded fast roots,
#     --findings-only. Excludes the big read-only dependency caches (Go module
#     cache, Cargo registry) so it finishes in seconds and detection stays
#     frequent on the roots that carry executable, actually-installed code.
#     The interval is overridable per-device by a managed preference, so build
#     machines and CI hosts can drop to daily -- see the FINDINGS_INTERVAL block
#     below, which also explains why this cannot be decided on the device.
#   - inventory (daily): the SAME roots with NO exclusions plus full package
#     inventory, on a generous --max-duration. This is the pass that covers
#     the dependency caches, so cache exposure is still detected -- at a daily
#     cadence rather than 4-hourly, and without starving the fast pass.
#
#   Rationale: on this fleet the frequent baseline was spending ~440s of a
#   600s budget walking ~/go/pkg/mod and timing out ~29% of the time. A
#   timed-out scan emits FEWER findings while still reporting status=complete,
#   so a quarter of scans were silently under-reporting.
#
# CATALOGS: assembled per (mode,profile) by catalog_select.py into a
#   SCHEMA-COHERENT set. bumblebee refuses a catalog directory that mixes
#   schema_versions and exits 2 BEFORE scanning -- no records at all, not even
#   a scan_summary. Upstream has already bumped the bundled threat_intel
#   catalogs to 0.2.0 while our own published catalogs are 0.1.0, so a naive
#   "copy every valid file into one dir" step is a live outage waiting for the
#   next release. Selection is followed by a probe scan that proves THIS
#   binary can load THIS set before any real scan depends on it.
#
# loki-push timestamps entries with ingestion-time now() (NOT scan_time) to
# avoid greater_than_max_sample_age drops, and paces to a compressed-
# throughput target.
set -eu

INSTALL_VERSION="2026-08-12-servermode-004"

# --- pinned scanner release ---------------------------------------------
# Bump these three together. SHA256s come from the release's checksums.txt:
#   https://github.com/perplexityai/bumblebee/releases/download/v<V>/checksums.txt
BUMBLEBEE_VERSION="0.1.2"
BUMBLEBEE_SHA256_ARM64="0535aefeb6d1bdc2b4f44e393c5da385c95ac63c7c8f0bcee01b054d688bdab5"
BUMBLEBEE_SHA256_AMD64="ea7f0ea303f712f3073ddb0f9fc0b368692ec1eee581b9a5d069ed986db2b433"
BUMBLEBEE_BASE_URL="https://github.com/perplexityai/bumblebee/releases/download"

# --- project scan roots ---------------------------------------------------
# Directories where developers keep working repositories. The `project` profile
# scans these; the `baseline` profile finds the standard per-user roots on its
# own. Space-separated, and they may legitimately not exist on a given host --
# the wrapper drops missing roots rather than failing.
#
# There is no good fleet-wide answer here. A single shared convention
# (~/repos, ~/src, ~/dev) is worth enforcing precisely because it makes this
# line possible; without one, either scan the home directory and accept the
# cost, or drive this from an Intune assignment filter per team.
PROJECT_ROOTS="/Users/*/repos"

# --- telemetry credentials -------------------------------------------------
# Grafana Cloud Loki push endpoint and token.
#
# READ THIS BEFORE FILLING IT IN. Intune has no secret-vaulting for script
# content: whatever you put here is readable by anyone with Graph read access
# to deviceShellScripts, and it lands on every managed device in
# /var/db/bumblebee/env. That is not a reason to avoid shipping a credential --
# the scanner has to authenticate somehow -- it is a reason to make this one
# boring to steal. Use a push-only token, scoped to a single stack, that you
# are willing to rotate on a schedule and that grants no read access to
# anything.
LOKI_URL="https://logs-prod-XXX.grafana.net/loki/api/v1/push"
LOKI_TOKEN="__SET_ME__"          # format: "<numeric-id>:<token>"

# --- rolling catalogs (public, generated by rknightion/bumblebee-catalog) ---
# All four assets live on the same moving `catalog-latest` release. The extra
# two were being built, validated and published by CI but never fetched by any
# endpoint, so their coverage (editor extensions via DataDog, CWE-506 malware
# advisories via GHSA that OSSF's MAL- ids do not alias) was not deployed.
CATALOG_BASE_URL="https://github.com/rknightion/bumblebee-catalog/releases/latest/download"
CATALOG_ASSETS="osv-malicious.json datadog-malicious.json ghsa-malicious.json"
CATALOG_METAS="catalog-meta.json datadog-meta.json ghsa-meta.json"

# --- staged (root-owned) paths the daemons use ---
STAGE_DIR=/usr/local/libexec/bumblebee
STAGED_BIN="$STAGE_DIR/bin/bumblebee"
STAGED_CURATED="$STAGE_DIR/threat_intel"
BIN_VERSION_FILE="$STAGE_DIR/.bumblebee.version"

# --- runtime paths ---
RUN=/usr/local/bin/bumblebee-run
LOKI_PY="$STAGE_DIR/loki-push.py"
CAT_SELECT_PY="$STAGE_DIR/catalog-select.py"
DB_DIR=/var/db/bumblebee
CATALOG_BASE="$DB_DIR/catalog"
OSV_DIR="$DB_DIR/osv"
PROBE_DIR="$DB_DIR/probe"
ENV_FILE="$DB_DIR/env"
VERSION_FILE="$DB_DIR/install.version"
LOG_DIR=/var/log/bumblebee
DAEMON_DIR=/Library/LaunchDaemons

LABELS="com.bumblebee.findings-baseline com.bumblebee.findings-project com.bumblebee.inventory-baseline com.bumblebee.inventory-project"
# Legacy labels cleaned up on migration (pre-split and pre-brew shapes).
OLD_LABELS="com.bumblebee.baseline com.bumblebee.project"

log() { /bin/echo "$(date -u +%FT%TZ) bumblebee-installer: $*" >&2; }

# --- findings cadence: 4h default, overridable per-device by managed preference ---
# Build machines, CI runners and anything else where a 4-hourly scan competes with real
# work can be dropped to daily without a second copy of this script.
#
# WHY A MANAGED PREFERENCE AND NOT ON-DEVICE DETECTION: you cannot ask a Mac what Intune
# thinks it is. `profiles show -type enrollment` returns (null) on an ADE-enrolled device,
# so the enrolment profile name -- the obvious thing to branch on -- is simply not
# readable from a script. The role has to be DELIVERED to the device. Ship a custom
# settings profile (a macOSCustomAppConfiguration with the bundleId below) carrying
# BumblebeeFindingsIntervalSeconds, and scope it with an assignment filter on
# enrollmentProfileName. Intune evaluates the filter server-side and the device just
# reads the answer out of /Library/Managed Preferences.
#
# Absent, unreadable or non-numeric => the default below. A freshly enrolled machine does
# a cycle or two at the default before the profile lands; the next daily installer run
# picks up the override.
SERVERMODE_DOMAIN="/Library/Managed Preferences/com.example.servermode"   # __SET_ME__
FINDINGS_INTERVAL=14400
if [ -f "${SERVERMODE_DOMAIN}.plist" ]; then
  _sm_val=$(/usr/bin/defaults read "$SERVERMODE_DOMAIN" BumblebeeFindingsIntervalSeconds 2>/dev/null || true)
  case "$_sm_val" in
    ''|*[!0-9]*) log "server-mode pref present but BumblebeeFindingsIntervalSeconds unusable ('$_sm_val'); keeping ${FINDINGS_INTERVAL}s" ;;
    *) FINDINGS_INTERVAL="$_sm_val"; log "server mode: findings interval ${FINDINGS_INTERVAL}s" ;;
  esac
fi

# --- preflight ---
if [ "$(id -u)" -ne 0 ]; then
  log "ERROR: must run as root (got uid=$(id -u))"; exit 1
fi
if ! /usr/bin/command -v /usr/bin/python3 >/dev/null 2>&1; then
  log "WARN: /usr/bin/python3 not found; install Xcode CLT. Skipping."; exit 0
fi

# --- directories ---
/bin/mkdir -p "$DB_DIR" "$LOG_DIR" "$STAGE_DIR/bin" "$CATALOG_BASE" "$OSV_DIR" "$PROBE_DIR"
/usr/sbin/chown root:wheel "$DB_DIR" "$LOG_DIR" "$STAGE_DIR" "$STAGE_DIR/bin" "$CATALOG_BASE" "$OSV_DIR" "$PROBE_DIR"
/bin/chmod 755 "$LOG_DIR" "$STAGE_DIR" "$STAGE_DIR/bin"
/bin/chmod 700 "$DB_DIR"
/bin/chmod 755 "$CATALOG_BASE" "$OSV_DIR" "$PROBE_DIR"

# --- acquire the pinned binary (SHA256-verified, root-owned throughout) ---
# Skip the download entirely when the staged binary is already the pinned
# version AND still runnable. The marker alone is not trusted: a truncated or
# clobbered binary must re-install rather than be assumed good.
need_install=1
if [ -x "$STAGED_BIN" ] && [ -f "$BIN_VERSION_FILE" ] &&
   [ "$(cat "$BIN_VERSION_FILE" 2>/dev/null)" = "$BUMBLEBEE_VERSION" ] &&
   "$STAGED_BIN" version >/dev/null 2>&1; then
  need_install=0
fi

if [ "$need_install" -eq 1 ]; then
  case "$(/usr/bin/uname -m)" in
    arm64)  GOARCH=arm64; EXPECT_SHA="$BUMBLEBEE_SHA256_ARM64" ;;
    x86_64) GOARCH=amd64; EXPECT_SHA="$BUMBLEBEE_SHA256_AMD64" ;;
    *)      log "ERROR: unsupported arch $(/usr/bin/uname -m)"; exit 1 ;;
  esac
  TARBALL="bumblebee_${BUMBLEBEE_VERSION}_darwin_${GOARCH}.tar.gz"
  URL="$BUMBLEBEE_BASE_URL/v${BUMBLEBEE_VERSION}/$TARBALL"

  WORK="$DB_DIR/.install.$$"
  /bin/rm -rf "$WORK"; /bin/mkdir -p "$WORK"; /bin/chmod 700 "$WORK"
  trap '/bin/rm -rf "$WORK"' EXIT

  log "downloading $TARBALL"
  if ! /usr/bin/curl -fsSL --max-time 180 "$URL" -o "$WORK/$TARBALL"; then
    log "ERROR: download failed; keeping any existing staged binary, will retry next cycle"
    exit 0
  fi

  ACTUAL_SHA="$(/usr/bin/shasum -a 256 "$WORK/$TARBALL" | /usr/bin/awk '{print $1}')"
  if [ "$ACTUAL_SHA" != "$EXPECT_SHA" ]; then
    # Fail CLOSED. A checksum mismatch is either a corrupted download or a
    # tampered artifact; neither may be staged and executed as root.
    log "ERROR: SHA256 mismatch for $TARBALL (expected $EXPECT_SHA, got $ACTUAL_SHA); refusing to install"
    exit 1
  fi
  log "SHA256 verified"

  /usr/bin/tar -xzf "$WORK/$TARBALL" -C "$WORK"
  if [ ! -f "$WORK/bumblebee" ]; then
    log "ERROR: tarball did not contain a bumblebee binary"; exit 1
  fi
  # Prove it runs on this host before it becomes the staged binary.
  /bin/chmod 0755 "$WORK/bumblebee"
  if ! "$WORK/bumblebee" version >/dev/null 2>&1; then
    log "ERROR: extracted binary failed to execute; refusing to stage"; exit 1
  fi

  /usr/bin/install -m 0755 -o root -g wheel "$WORK/bumblebee" "$STAGED_BIN.tmp"
  /bin/mv -f "$STAGED_BIN.tmp" "$STAGED_BIN"

  # The tarball ships threat_intel/** alongside the binary, so the curated
  # catalogs always match the binary's schema expectations. Swap atomically.
  if [ -d "$WORK/threat_intel" ]; then
    TMP_CUR="$STAGE_DIR/.threat_intel.tmp.$$"
    /bin/rm -rf "$TMP_CUR"
    /bin/cp -R "$WORK/threat_intel" "$TMP_CUR"
    /usr/sbin/chown -R root:wheel "$TMP_CUR"
    /bin/rm -rf "$STAGED_CURATED.old"
    [ -d "$STAGED_CURATED" ] && /bin/mv "$STAGED_CURATED" "$STAGED_CURATED.old" || true
    /bin/mv "$TMP_CUR" "$STAGED_CURATED"
    /bin/rm -rf "$STAGED_CURATED.old"
  fi

  /bin/echo "$BUMBLEBEE_VERSION" > "$BIN_VERSION_FILE"
  /usr/sbin/chown root:wheel "$BIN_VERSION_FILE"; /bin/chmod 644 "$BIN_VERSION_FILE"
  /bin/rm -rf "$WORK"; trap - EXIT
  log "staged bumblebee $BUMBLEBEE_VERSION ($GOARCH)"
else
  log "bumblebee $BUMBLEBEE_VERSION already staged; skipping download"
fi

# --- if no usable binary, don't bootstrap daemons ---
if [ ! -x "$STAGED_BIN" ]; then
  log "WARN: no bumblebee binary staged yet; skipping daemon load, will retry"; exit 0
fi

# --- env file (Loki creds + catalog URLs) ---
umask 077
/bin/cat > "$ENV_FILE" <<EOF
LOKI_URL='$LOKI_URL'
LOKI_TOKEN='$LOKI_TOKEN'
CATALOG_BASE_URL='$CATALOG_BASE_URL'
CATALOG_ASSETS='$CATALOG_ASSETS'
CATALOG_METAS='$CATALOG_METAS'
EOF
/usr/sbin/chown root:wheel "$ENV_FILE"; /bin/chmod 600 "$ENV_FILE"
umask 022

# --- idempotency gate ---
daemons_loaded() {
  for l in $LABELS; do /bin/launchctl print "system/$l" >/dev/null 2>&1 || return 1; done
  return 0
}
if [ -f "$VERSION_FILE" ] && [ "$(cat "$VERSION_FILE" 2>/dev/null)" = "$INSTALL_VERSION" ] && daemons_loaded; then
  log "artifacts already at $INSTALL_VERSION and daemons loaded; binary/catalogs refreshed, done"; exit 0
fi

# --- catalog-select.py (schema-coherent catalog staging) ---
/bin/cat > "$CAT_SELECT_PY" <<'PYTHON_EOF_BUMBLEBEE_CATSELECT'
@@EMBED:catalog_select.py@@
PYTHON_EOF_BUMBLEBEE_CATSELECT
/usr/sbin/chown root:wheel "$CAT_SELECT_PY"; /bin/chmod 755 "$CAT_SELECT_PY"

# --- bumblebee-run wrapper ---
/bin/cat > "$RUN" <<'WRAPPER_EOF_BUMBLEBEE_v4'
@@EMBED:bumblebee-run.sh@@
WRAPPER_EOF_BUMBLEBEE_v4
/usr/sbin/chown root:wheel "$RUN"; /bin/chmod 755 "$RUN"

# --- loki-push.py ---
/bin/cat > "$LOKI_PY" <<'PYTHON_EOF_BUMBLEBEE_LOKIPUSH'
@@EMBED:loki_push.py@@
PYTHON_EOF_BUMBLEBEE_LOKIPUSH
/usr/sbin/chown root:wheel "$LOKI_PY"; /bin/chmod 755 "$LOKI_PY"

# --- emit the four LaunchDaemons ---
# emit_plist <label> <mode> <profile> <max> <interval> <runatload> [extra args...]
emit_plist() {
  label="$1"; mode="$2"; profile="$3"; max="$4"; interval="$5"; ral="$6"
  shift 6
  plist="$DAEMON_DIR/$label.plist"
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0"><dict>'
    echo "  <key>Label</key><string>$label</string>"
    echo '  <key>ProgramArguments</key><array>'
    echo '    <string>/usr/local/bin/bumblebee-run</string>'
    echo "    <string>$mode</string><string>$profile</string><string>$max</string>"
    for a in "$@"; do echo "    <string>$a</string>"; done
    echo '  </array>'
    echo "  <key>StartInterval</key><integer>$interval</integer>"
    [ "$ral" = "yes" ] && echo '  <key>RunAtLoad</key><true/>'
    echo '  <key>UserName</key><string>root</string>'
    echo "  <key>StandardOutPath</key><string>$LOG_DIR/$label.out</string>"
    echo "  <key>StandardErrorPath</key><string>$LOG_DIR/$label.err</string>"
    echo '  <key>ProcessType</key><string>Background</string>'
    echo '  <key>LowPriorityIO</key><true/><key>Nice</key><integer>10</integer>'
    echo '</dict></plist>'
  } > "$plist"
  /usr/sbin/chown root:wheel "$plist"; /bin/chmod 644 "$plist"
}

# findings: every $FINDINGS_INTERVAL (4h default), RunAtLoad. Fast + bounded --
# the dependency caches are excluded here and covered by the daily inventory pass
# instead. `--` marks the end of scan roots so the wrapper can tell roots from
# excludes.
#
# RunAtLoad MUST stay `yes`, and it matters more once the interval can be raised:
# this installer boots out and re-bootstraps all four daemons on EVERY run, and
# Intune runs it daily, so each reload resets the StartInterval countdown. With
# RunAtLoad=no, a daemon whose interval is >= the installer cadence can be starved
# indefinitely and simply never scan. RunAtLoad is what guarantees roughly one
# findings scan per installer cycle whatever the interval is.
emit_plist com.bumblebee.findings-baseline  findings  baseline 10m "$FINDINGS_INTERVAL" yes \
  -- --exclude pkg/mod --exclude .cargo/registry
emit_plist com.bumblebee.findings-project   findings  project  15m "$FINDINGS_INTERVAL" yes \
  "$PROJECT_ROOTS"
# inventory: daily, full sweep including the caches, generous budget.
emit_plist com.bumblebee.inventory-baseline inventory baseline 45m 86400 no
emit_plist com.bumblebee.inventory-project  inventory project  30m 86400 no \
  "$PROJECT_ROOTS"

# --- clean up legacy daemons, then (re)load the four ---
for l in $OLD_LABELS; do
  /bin/launchctl print "system/$l" >/dev/null 2>&1 && /bin/launchctl bootout "system/$l" 2>/dev/null || true
  /bin/rm -f "$DAEMON_DIR/$l.plist"
done
for l in $LABELS; do
  /bin/launchctl print "system/$l" >/dev/null 2>&1 && /bin/launchctl bootout "system/$l" 2>/dev/null || true
  /bin/launchctl bootstrap system "$DAEMON_DIR/$l.plist"
  log "loaded $l"
done

# Retire the single shared summary file the pre-split wrapper wrote. All four
# daemons used to overwrite it, so the Intune exposure attribute reported
# whichever daemon happened to finish last -- on a host with a live finding in
# one profile it read "clean" whenever another profile finished after it.
/bin/rm -f "$DB_DIR/last_summary.json"

/bin/echo "$INSTALL_VERSION" > "$VERSION_FILE"
/usr/sbin/chown root:wheel "$VERSION_FILE"; /bin/chmod 644 "$VERSION_FILE"
log "install complete at version $INSTALL_VERSION (bumblebee $BUMBLEBEE_VERSION at $STAGED_BIN)"
exit 0
