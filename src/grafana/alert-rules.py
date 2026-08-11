#!/usr/bin/env python3
"""Reconcile the Grafana alert rules for the bumblebee deployment.

Run:
    python3 deploy/bumblebee_alerts.py            # dry run, prints the diff
    python3 deploy/bumblebee_alerts.py --apply

Auth/routing come from the `gcx` CLI's current context. Pass --context
explicitly on every call: the current-context is global mutable state shared by
every process on the machine, and an empty result from the wrong stack is
indistinguishable from an empty result from the right one.
Rules live in the API-managed `Bumblebee Alerts` folder (uid bumblebee-alerts),
NOT the `Bumblebee` folder -- that one is GitSync-managed and Grafana refuses
API rules there ("cannot store rules in folder managed by Git Sync").

WHAT THIS ENCODES
-----------------
1. Findings are split by whether the package is actually INSTALLED or merely
   DECLARED in a lockfile, because those are different security events:

   - installed on disk (node_modules, site-packages, a Homebrew cellar, an
     editor extension) -- the malicious code is present and reachable.
     Critical, pages.
   - declared in a lockfile inside a repo you own (root_kind=project_root) --
     nothing is installed yet, but a build would install it. Warning: real,
     fixable, not a 3am page.
   - declared in a lockfile inside a third-party dependency cache
     (root_kind != project_root, e.g. a pnpm-lock.yaml vendored inside a Go
     module under ~/go/pkg/mod) -- read-only, never installed, and it comes
     straight back if deleted. NO alert at all; the fleet dashboard's
     "Exposure findings (detail)" panel is where these belong.

   The declared-only set is an explicit allowlist, so any source_type upstream
   adds in future defaults to CRITICAL rather than to silence.

2. `catalog_effective == 0` pages. A host scanning with no exposure catalog
   emits scan_summary records that are indistinguishable from a clean host --
   status=complete, timed_out=false, findings_emitted=0 -- so without this rule
   a total loss of detection looks exactly like good news.

3. Scan timeouts alert on a RATE, not a count. The old threshold (>10 timeouts
   per host+profile in 48h) only caught "never finishing"; the measured state
   was ~3 in 48h, i.e. roughly a quarter of scans silently truncated and
   under-reporting, which never tripped it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

FOLDER = "bumblebee-alerts"
DS = "grafanacloud-logs"

# source_type values that mean "a manifest/lockfile NAMES this package",
# not "this package is installed on disk". Everything else is treated as
# installed, so an omission here fails LOUD (a false critical) rather than
# silent -- which is how composer-lock was caught on 2026-08-11 after being
# missed in the first cut, with 14 live packagist packages behind it.
# Verify against live data before editing:
#   gcx --context <your-gcx-context> logs query 'sum by (source_type) (count_over_time(
#     {source="bumblebee", record_type="package"} | json source_type="source_type" [1h]))'
DECLARED_ONLY = "|".join([
    "npm-lockfile", "pnpm-lockfile", "yarn-lockfile", "bun-lockfile",
    "rubygems-gemfile-lock", "composer-lock", "go-mod", "go-sum", "skill-lock",
    "mcp-config",
])

FINDING_FIELDS = ('| json ecosystem="ecosystem", package_name="package_name", version="version", '
                  'catalog_id="catalog_id", root_kind="root_kind", source_type="source_type" ')
FINDING_BY = "sum by (host, ecosystem, package_name, version, catalog_id, root_kind)"


def logql(expr, ref="A"):
    return {
        "refId": ref,
        "datasourceUid": DS,
        "relativeTimeRange": {"from": 172800, "to": 0},
        "model": {"refId": ref, "datasource": {"type": "loki", "uid": DS},
                  "expr": expr, "queryType": "instant", "intervalMs": 1000, "maxDataPoints": 43200},
    }


def threshold(expression):
    return {
        "refId": "condition",
        "datasourceUid": "__expr__",
        "relativeTimeRange": {"from": 172800, "to": 0},
        "model": {"refId": "condition", "type": "math", "expression": expression,
                  "datasource": {"type": "__expr__", "uid": "__expr__"}},
    }


def rule(uid, title, expr, condition, severity, summary, description, lookback, for_="5m",
         extra_labels=None, panel="50", interval="5m"):
    labels = {"domain": "security", "severity": severity, "source": "bumblebee"}
    labels.update(extra_labels or {})
    return {
        "uid": uid,
        "title": title,
        "condition": "condition",
        "folderUID": FOLDER,
        "ruleGroup": f"no_group_for_rule_{uid}",
        "orgID": 1,
        "for": for_,
        # NoData is deliberately OK on every rule: these are laptops, and one
        # that is switched off must never page.
        "noDataState": "OK",
        "execErrState": "Error",
        "isPaused": False,
        "labels": labels,
        # __panelId__ decides where "View panel" lands from the alert. Point each
        # rule at the panel that shows the thing it fired on: a link to the
        # findings table from a catalog-health alert sends the responder to a
        # panel that is (correctly) empty, which reads as "nothing wrong".
        "annotations": {"__dashboardUid__": "bumblebee-fleet", "__panelId__": panel,
                        "summary": summary, "description": description},
        "data": [logql(expr), threshold(condition)],
        "lookback": lookback,
        "interval": interval,
    }


RULES = [
    rule(
        uid="bumblebee-finding",
        title="BumblebeeFinding",
        lookback="12h0m0s",
        expr=(f'{FINDING_BY} (count_over_time({{source="bumblebee", record_type="finding"}} '
              f'{FINDING_FIELDS}| source_type!~"{DECLARED_ONLY}" [12h]))'),
        condition="${A} > 0",
        severity="critical",
        summary=("bumblebee: INSTALLED malicious package on {{ $labels.host }} - "
                 "{{ $labels.ecosystem }} {{ $labels.package_name }}@{{ $labels.version }} "
                 "({{ $labels.catalog_id }})"),
        description=(
            "bumblebee found a known-malicious package that is INSTALLED ON DISK -- unpacked in "
            "node_modules / site-packages / a Homebrew cellar / an editor extension, not merely "
            "named in a lockfile. The code is present on the endpoint. Treat as real until proven "
            "otherwise.\n\n"
            "Package: {{ $labels.ecosystem }} {{ $labels.package_name }}@{{ $labels.version }}\n"
            "Catalog id: {{ $labels.catalog_id }} (https://osv.dev/vulnerability/{{ $labels.catalog_id }})\n"
            "Host: {{ $labels.host }}\nRoot kind: {{ $labels.root_kind }}\n\n"
            "Lockfile-only matches are deliberately NOT in this rule -- they fire "
            "BumblebeeFindingDeclared (your own repos) or appear on the dashboard only "
            "(third-party caches). See the 'Exposure findings (detail)' panel on the "
            "Bumblebee - Fleet Inventory dashboard for source_file and project_path, which are "
            "not labels because one package can match dozens of paths.\n\n"
            "The 12h lookback keeps this steady rather than flapping per 4h scan; expect up to "
            "12h to resolve after removal."),
    ),
    rule(
        uid="bumblebee-finding-declared",
        title="BumblebeeFindingDeclared",
        lookback="12h0m0s",
        expr=(f'{FINDING_BY} (count_over_time({{source="bumblebee", record_type="finding"}} '
              f'{FINDING_FIELDS}| source_type=~"{DECLARED_ONLY}" | root_kind="project_root" [12h]))'),
        condition="${A} > 0",
        severity="warning",
        summary=("bumblebee: malicious package DECLARED in your own repo on {{ $labels.host }} - "
                 "{{ $labels.ecosystem }} {{ $labels.package_name }}@{{ $labels.version }}"),
        description=(
            "A lockfile in a repo you own pins a known-malicious release. Nothing is installed "
            "yet, so this is not an active compromise -- but the next `npm ci` / `pnpm install` "
            "in that tree installs it, and it is yours to fix.\n\n"
            "Package: {{ $labels.ecosystem }} {{ $labels.package_name }}@{{ $labels.version }}\n"
            "Catalog id: {{ $labels.catalog_id }} (https://osv.dev/vulnerability/{{ $labels.catalog_id }})\n"
            "Host: {{ $labels.host }}\n\n"
            "Fix by bumping/removing the pin and regenerating the lockfile. Warning rather than "
            "critical on purpose: the exposure is prospective, not present.\n\n"
            "Scoped to root_kind=project_root. The same declaration inside a third-party "
            "dependency cache is not alertable -- it is read-only, was never installed, and "
            "returns as soon as the cache is repopulated."),
    ),
    rule(
        uid="bumblebee-catalog-ineffective",
        title="BumblebeeCatalogIneffective",
        lookback="25h0m0s",
        expr=('min by (host) (last_over_time({source="bumblebee", record_type="catalog_health"} '
              '| json catalog_effective="catalog_effective" | unwrap catalog_effective [25h]))'),
        condition="${A} < 1",
        severity="critical",
        summary="bumblebee on {{ $labels.host }} is scanning with NO exposure catalog - detection is OFF",
        description=(
            "A scan on this host ran with no usable exposure catalog, so it CANNOT emit findings. "
            "This is the failure mode that looks exactly like good news: the scan still completes, "
            "still reports status=complete and findings_emitted=0, and every other signal stays "
            "green while detection is entirely absent.\n\n"
            "Most likely causes, in order:\n"
            "1. Schema conflict. bumblebee refuses a catalog directory that mixes schema_versions "
            "and exits 2 BEFORE scanning. Upstream has bumped the bundled threat_intel catalogs to "
            "0.2.0 while rknightion/bumblebee-catalog still publishes 0.1.0. The endpoint's "
            "catalog-select.py is supposed to pick one coherent group -- check "
            "catalog_schema_conflict and catalog_schema_version on the catalog_health record.\n"
            "2. Every catalog fetch failed AND no last-good cache exists (a freshly imaged host).\n"
            "3. The staged threat_intel directory is empty or unreadable.\n\n"
            "Check on the host: /var/db/bumblebee/catalog/<mode>_<profile>/ and "
            "/var/log/bumblebee/run-<mode>-<profile>.err.\n\n"
            "NoData is OK here (a switched-off laptop must not page), so this fires only when a "
            "host actively reports a catalog-less scan."),
        for_="15m",
        panel="501",
    ),
    rule(
        uid="bumblebee-fda-missing",
        title="BumblebeeFullDiskAccessMissing",
        lookback="25h0m0s",
        expr=('min by (host) (last_over_time({source="bumblebee", record_type="catalog_health"} '
              '| json fda="fda" | unwrap fda [25h]))'),
        condition="${A} < 1",
        severity="warning",
        summary="bumblebee on {{ $labels.host }} has lost Full Disk Access - TCC-protected paths are being skipped",
        description=(
            "The scanner could not read a TCC-protected directory, so ~/Documents, ~/Desktop, "
            "~/Downloads and parts of ~/Library are silently out of scope on this host. Nothing "
            "else goes red: the scan still completes and still reports 0 findings, just over a "
            "smaller tree.\n\n"
            "Root is NOT sufficient for these paths -- the grant comes from the "
            "'MacOS Bumblebee Full Disk Access (PPPC)' profile.\n\n"
            "Most likely cause by far: BUMBLEBEE_VERSION was bumped in the installer without "
            "re-running deploy/deploy_bumblebee_pppc.py. The PPPC code requirement is the "
            "binary's cdhash (the release builds are ad-hoc linker-signed with Identifier=a.out, "
            "so there is no bundle ID or Team ID to key on), and the cdhash changes with every "
            "build. A version bump therefore invalidates the grant silently.\n\n"
            "Also check: the profile is still assigned; the staged binary is at the expected "
            "path; and fda_probe_files on the same record -- 0 can legitimately mean the probed "
            "Documents folder is empty rather than unreadable, which is why the raw count is "
            "emitted alongside the verdict.\n\n"
            "Verify a fix with: gcx --context <your-gcx-context> logs query "
            "'{source=\"bumblebee\", record_type=\"catalog_health\"}' -d grafanacloud-logs"),
        for_="30m",
        panel="511",
    ),
    rule(
        uid="bumblebee-scan-timeout",
        title="BumblebeeScanTimingOut",
        lookback="48h0m0s",
        # Ratio of timed-out scans to all scans over 48h, gated on having
        # enough scans for the ratio to mean anything.
        expr=(
            '(sum by (host, profile) (count_over_time({source="bumblebee", record_type="scan_summary"} '
            '| json timed_out="timed_out" | timed_out="true" [48h])) '
            '/ '
            'sum by (host, profile) (count_over_time({source="bumblebee", record_type="scan_summary"} [48h]))) '
            'and '
            '(sum by (host, profile) (count_over_time({source="bumblebee", record_type="scan_summary"} [48h])) >= 8)'
        ),
        condition="${A} > 0.15",
        severity="warning",
        summary=("bumblebee {{ $labels.profile }} scans on {{ $labels.host }} are truncating - "
                 "{{ $values.A.Value | printf \"%.2f\" }} of the last 48h of scans hit --max-duration"),
        description=(
            "More than 15% of this host+profile's scans in the last 48h hit --max-duration and "
            "stopped early. A truncated scan emits FEWER findings while still reporting "
            "status=complete, so a 'clean' result from a timing-out profile is not trustworthy -- "
            "this is a silent, partial loss of coverage rather than an outage.\n\n"
            "This is a RATE, not a count. The previous count threshold (>10 in 48h) only caught a "
            "profile that never finished at all; the fleet was measured at ~3 timeouts in 48h, "
            "i.e. a 0.23 baseline / 0.21 project truncation rate on LAPTOP-01, and never tripped it.\n\n"
            "Threshold calibration: those measured rates are what this replaces, so 0.15 fires on "
            "today's real coverage loss. Once the split daemons are in place (4-hourly findings "
            "excludes the big dependency caches; the daily inventory pass gets 45m) the expected "
            "steady state is ~0, leaving this as a regression detector with real headroom.\n\n"
            "The >= 8 scan floor means a laptop that was mostly switched off cannot trip this on a "
            "couple of unlucky runs.\n\n"
            "Fix by raising --max-duration in the daemon plist, or by narrowing that profile's "
            "roots with --exclude. The 4-hourly findings daemons deliberately exclude the big "
            "read-only dependency caches (pkg/mod, .cargo/registry); those are covered by the "
            "daily inventory pass instead."),
        for_="2h",
        panel="103",
    ),
    rule(
        uid="bumblebee-catalog-stale",
        title="BumblebeeCatalogStale",
        lookback="25h0m0s",
        expr=('max by (host) (last_over_time({source="bumblebee", record_type="catalog_health"} '
              '| json catalog_age_hours="catalog_age_hours" | unwrap catalog_age_hours [25h]))'),
        condition="${A} > 18",
        severity="warning",
        summary=("bumblebee exposure catalog on {{ $labels.host }} is "
                 "{{ $values.A.Value | printf \"%.1f\" }}h old (>18h - detection may be stale)"),
        description=(
            "The exposure catalog staged on {{ $labels.host }} is older than 18h. The OSV catalog "
            "is regenerated by the rknightion/bumblebee-catalog GitHub Action every 4h and each "
            "scan refreshes its local copy before running, so 18h means either the Action has died "
            "(GitHub auto-disables scheduled workflows after 60 days of repo inactivity) or the "
            "endpoint has been falling back to its last-good cache.\n\n"
            "This is a blind-detector alert: scans keep succeeding and keep reporting clean "
            "against a frozen catalog, so nothing else in the pipeline goes red. Check the Action "
            "first, then /var/db/bumblebee/osv/osv-malicious.json on the host.\n\n"
            "NoData is deliberately OK -- a laptop that is switched off must not page."),
        for_="30m",
        panel="51",
        interval="10m",
    ),
    rule(
        uid="bumblebee-health-telemetry-missing",
        title="BumblebeeHealthTelemetryMissing",
        lookback="48h0m0s",
        # Hosts that are scanning but NOT emitting the health fields the other
        # alerts unwrap. `unless` is a set difference: scanning hosts minus
        # hosts that reported a usable catalog_effective.
        expr=(
            'count by (host) (count_over_time({source="bumblebee", record_type="scan_summary"} [25h])) '
            'unless '
            'count by (host) (count_over_time({source="bumblebee", record_type="catalog_health"} '
            '| json ce="catalog_effective" | ce=~".+" [25h]))'
        ),
        condition="${A} > 0",
        severity="warning",
        summary=("bumblebee on {{ $labels.host }} is scanning but reporting NO health telemetry - "
                 "the catalog and FDA alerts cannot see this host"),
        description=(
            "This host is pushing scan_summary records but no catalog_health record carrying "
            "catalog_effective. Almost always it means the host is still running an OLD installer "
            "version whose wrapper predates the health fields.\n\n"
            "This alert exists because the failure is otherwise INVISIBLE, and invisible in the "
            "worst direction. BumblebeeCatalogIneffective and BumblebeeFullDiskAccessMissing both "
            "unwrap a field out of catalog_health. A host that never emits that field produces no "
            "series at all, so it is not evaluated -- it does not alert, and it does not show as "
            "NoData either, because the other hosts keep the rule healthy. Fleet coverage silently "
            "becomes 'whichever hosts happen to be on the current version', which is exactly the "
            "class of silent partial coverage the rest of this design exists to prevent.\n\n"
            "Check the host's installed version against the installer's INSTALL_VERSION:\n"
            "  ssh <host> sudo cat /var/db/bumblebee/install.version\n"
            "and force a pickup with:\n"
            "  ssh <host> sudo launchctl kickstart -k system/com.microsoft.intuneMDMAgent.daemon\n\n"
            "Intune shell scripts re-run on their own schedule (P1D here), so a host can sit up to "
            "a day behind a script content change with nothing reporting it."),
        for_="1h",
        panel="505",
    ),
]


def gcx_api(path, method=None, body=None):
    cmd = ["gcx", "api", path, "-o", "json"]
    if method:
        cmd += ["-X", method]
    tmp = None
    if body is not None:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(body, tmp)
        tmp.close()
        cmd += ["-d", f"@{tmp.name}"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


RESOURCE_SUBDIR = "alertrules.v0alpha1.rules.alerting.grafana.app"


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def to_spec(r, existing_spec=None):
    """Translate our rule dict into the k8s-style AlertRule `spec`.

    These rules live behind Grafana's RESOURCE api (stored provenance
    `kubectl`), not the classic provisioning api. A PUT to
    /api/v1/provisioning/alert-rules/{uid} fails 409
    alerting.provenanceMismatch, and reports the baffling "provided 'api',
    needs 'api'" because the classic api translates the stored `kubectl`
    provenance to `api` on READ while the write path compares the
    untranslated value. DELETE fails the same way, showing the real stored
    value (`kubectl`). Pull -> patch -> push via `gcx resources` is the
    supported route and the only one that works.
    """
    spec = dict(existing_spec or {})
    spec.update({
        "title": r["title"],
        "labels": r["labels"],
        # Keep the __-prefixed annotations. They are stored verbatim by the
        # resource API and are what the UI reads for the dashboard/panel link;
        # dropping them left panelRef and the annotation disagreeing.
        "annotations": dict(r["annotations"]),
        "for": r["for"],
        "noDataState": "Ok",
        "execErrState": "Error",
        # panelRef, not the __panelId__ annotation, is what the resource API
        # actually stores -- the annotation is stripped just above with the
        # other __-prefixed keys. Setting only the annotation leaves every rule
        # linked to whatever panelID happened to be here.
        "panelRef": {"dashboardUID": "bumblebee-fleet",
                     "panelID": int(r["annotations"]["__panelId__"])},
        "trigger": {"interval": r.get("interval", "5m")},
        "expressions": {
            "A": {
                "datasourceUID": DS,
                "model": {
                    "datasource": {"type": "loki", "uid": DS},
                    "expr": r["data"][0]["model"]["expr"],
                    "instant": True,
                    "intervalMs": 1000,
                    "maxDataPoints": 43200,
                    "queryType": "instant",
                    "refId": "A",
                },
                "queryType": "instant",
                # v0alpha1 wants Go duration STRINGS here. Passing the
                # provisioning API's integer seconds gets a 400:
                # "cannot unmarshal number into ... AlertRulePromDurationWMillis".
                "relativeTimeRange": {"from": r["lookback"], "to": "0s"},
            },
            "condition": {
                # No datasourceUID, and `source: true` marks this as the
                # output node -- the resource schema differs from the classic
                # provisioning shape, where `source` sits on the query.
                "model": {
                    "datasource": {"name": "Expression", "type": "__expr__", "uid": "__expr__"},
                    "expression": r["data"][1]["model"]["expression"],
                    "intervalMs": 1000,
                    "maxDataPoints": 43200,
                    "refId": "condition",
                    "type": "math",
                },
                "source": True,
            },
        },
    })
    return spec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the rules (default is a dry run)")
    a = ap.parse_args()

    workdir = tempfile.mkdtemp(prefix="bumblebee-rules-")
    rc, out, err = run(["gcx", "resources", "pull", "alertrules", "-p", workdir])
    if rc != 0:
        print(f"pull failed: {err or out}", file=sys.stderr)
        sys.exit(1)
    rules_dir = Path(workdir) / RESOURCE_SUBDIR
    print(f"pulled {len(list(rules_dir.glob('*.json')))} rules -> {workdir}")

    touched = []
    for r in RULES:
        path = rules_dir / f"{r['uid']}.json"
        if path.is_file():
            doc = json.loads(path.read_text())
        else:
            # Create it as a RESOURCE. Do NOT create it with the classic
            # provisioning POST first: rules born that way are stored
            # managedBy=classic-api-provisioning and then refuse every resource
            # push, so they can only be updated by delete-and-recreate. Keeping
            # every rule on one path is the whole point.
            print(f"\n=== CREATE {r['uid']} (not present in Grafana) ===")
            doc = {
                "apiVersion": "rules.alerting.grafana.app/v0alpha1",
                "kind": "AlertRule",
                "metadata": {
                    "annotations": {"grafana.app/folder": FOLDER},
                    "labels": {"grafana.app/folder": FOLDER},
                    "name": r["uid"],
                },
                "spec": {},
            }
        doc["spec"] = to_spec(r, doc.get("spec"))
        print(f"\n=== {r['uid']} ({r['title']}) severity={r['labels']['severity']} ===")
        print("  expr:", doc["spec"]["expressions"]["A"]["model"]["expr"][:180])
        print("  cond:", doc["spec"]["expressions"]["condition"]["model"]["expression"],
              "| for:", doc["spec"]["for"])
        if a.apply:
            path.write_text(json.dumps(doc, indent=1))
            touched.append(path)

    if not a.apply:
        print("\n[dry run] pass --apply to write")
        return

    # Push only our rules; pushing the whole pulled tree would rewrite all 379.
    keep = {p.name for p in touched}
    for p in rules_dir.glob("*.json"):
        if p.name not in keep:
            p.unlink()
    rc, out, err = run(["gcx", "resources", "push", "-p", workdir])
    if rc != 0:
        print(f"push failed: {err or out}", file=sys.stderr)
        sys.exit(1)
    print(f"\npushed {len(keep)} rules: {sorted(keep)}")


if __name__ == "__main__":
    main()
