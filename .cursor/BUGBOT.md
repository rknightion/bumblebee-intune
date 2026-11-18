# Review managed macOS deployment changes

## Software and operators

Endpoint operators use the reference Intune deployment to install Bumblebee scans and report
exposure observations. Review the install, uninstall and inventory scripts under `src/intune/`,
the shell runner and Python catalog selector under `src/endpoint/`, and alert and recording rules
under `src/grafana/`.

## Invariants

Keep the catalog selector's deterministic candidate order and schema validation. Review coverage
and health claims against actual observation. A deadline, an unusable catalog or missing Full Disk
Access can make an incomplete scan look clean. Keep telemetry field names and the installed versus
declared classification consistent across alerts, recording rules, operator docs and dashboard queries.

## Sensitive changes

Treat installation, removal, Full Disk Access policy, and Grafana or Intune writes as operator
actions. Check assignment and credential scope against the documented deployment. Use placeholder
identifiers and offline fixtures for verification. Test protected-path reads when reviewing a Full
Disk Access probe, since a permission predicate alone can pass without the intended access.

## Checks already in CI

The repository gate checks the Justfile, ShellCheck, endpoint unit tests and the docs navigation
contract. Keep the required `ci-success` check and applicable PR metadata checks. Report meaningful
behavior or coverage gaps beyond findings already reported by those checks.

## Verification

Run `just check </dev/null`. Exercise catalog and scan failure cases with synthetic inputs.
Review tier changes across the alert, recording, docs and dashboard copies, and consult `LOOP.md`
for the separate parity checker and its current limitations.
