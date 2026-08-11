#!/bin/sh
# bumblebee Intune UNINSTALL (macOS, deviceShellScript). Runs as root.
#
# Unassigning the installer does NOT remove anything: the four LaunchDaemons
# keep running forever, the staged binary stays, and /var/db/bumblebee/env
# keeps a live Loki push token on disk. Intune has no uninstall semantics for
# a deviceShellScript, so removal has to be its own assigned script.
#
# HOW TO USE: assign this to the devices you want bumblebee removed from, and
# make sure the "Bumblebee installer" script is NOT also assigned to them --
# it runs daily and would reinstall. Exclude them from the installer with an
# assignment filter first, then assign this.
#
# Idempotent and safe to leave assigned: once everything is gone it exits 0
# without doing anything. It removes only paths this deployment created.
set -eu

LABELS="com.bumblebee.findings-baseline com.bumblebee.findings-project com.bumblebee.inventory-baseline com.bumblebee.inventory-project com.bumblebee.baseline com.bumblebee.project"
DAEMON_DIR=/Library/LaunchDaemons
STAGE_DIR=/usr/local/libexec/bumblebee
DB_DIR=/var/db/bumblebee
LOG_DIR=/var/log/bumblebee
RUN=/usr/local/bin/bumblebee-run

log() { /bin/echo "$(date -u +%FT%TZ) bumblebee-uninstall: $*" >&2; }

if [ "$(id -u)" -ne 0 ]; then
  log "ERROR: must run as root (got uid=$(id -u))"; exit 1
fi

# 1. Stop and unload the daemons first, so nothing is mid-scan while we delete
#    the binary underneath it.
for l in $LABELS; do
  if /bin/launchctl print "system/$l" >/dev/null 2>&1; then
    /bin/launchctl bootout "system/$l" 2>/dev/null || true
    log "booted out $l"
  fi
  /bin/rm -f "$DAEMON_DIR/$l.plist"
done

# 2. Give any in-flight scan a moment to notice, then stop it.
if /usr/bin/pgrep -f "$STAGE_DIR/bin/bumblebee" >/dev/null 2>&1; then
  /usr/bin/pkill -f "$STAGE_DIR/bin/bumblebee" 2>/dev/null || true
  sleep 2
  /usr/bin/pkill -9 -f "$STAGE_DIR/bin/bumblebee" 2>/dev/null || true
  log "terminated running scans"
fi

# 3. Remove the staged payload and the wrapper.
/bin/rm -rf "$STAGE_DIR"
/bin/rm -f "$RUN"

# 4. Remove state. $DB_DIR holds the Loki push token in `env`, so this is the
#    part that actually matters for credential hygiene -- overwrite before
#    unlinking so the bytes are not left in free space.
if [ -f "$DB_DIR/env" ]; then
  /usr/bin/perl -e 'open(F,">","/var/db/bumblebee/env") or exit 0; print F "\0" x 4096; close F;' 2>/dev/null || true
fi
/bin/rm -rf "$DB_DIR"

# 5. Logs last, so the steps above are still recorded if someone is watching.
/bin/rm -rf "$LOG_DIR"

log "uninstall complete; removed daemons, staged binary, state and logs"
exit 0
