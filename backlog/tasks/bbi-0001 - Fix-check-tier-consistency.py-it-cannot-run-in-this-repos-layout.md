---
id: BBI-0001
title: 'Fix check-tier-consistency.py: it cannot run in this repo''s layout'
status: To Do
assignee: []
created_date: '2026-08-14 16:35'
labels:
  - bug
  - grafana
dependencies: []
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`src/grafana/check-tier-consistency.py` loads `bumblebee_alerts.py` and `bumblebee_recording_rules.yaml` from its own directory. This repo ships `alert-rules.py` and `recording-rules.yaml`, so it raises FileNotFoundError on every invocation and has never run here.

Residue from a port off a `deploy/` layout with underscored filenames — the files were renamed, the references were not.

This matters more than a broken utility usually would: the script is the only guard on the declared-only source_type alternation, which is duplicated across four places that cannot see each other (alert-rules.py, three inline copies in recording-rules.yaml, docs/alerting.md prose, and the fleet dashboard JSON in an external Git-Sync repo). Missing composer-lock in one of them on 2026-08-11 put 14 live packagist packages in two different tiers for a fortnight, silently. See the Wave operating model doc.

Verified 2026-08-14: the four in-repo copies currently agree at ten entries, so this is fixing the guard, not chasing a live drift.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 python3 src/grafana/check-tier-consistency.py runs to completion against this repo's real filenames
- [ ] #2 It reads the alternation from alert-rules.py, recording-rules.yaml AND docs/alerting.md, and exits non-zero if any pair disagrees
- [ ] #3 Deliberately reports the fleet dashboard JSON as unchecked when --dashboard is not supplied, rather than silently skipping it
- [ ] #4 Negative-tested: a deliberately removed entry in one copy makes it exit non-zero
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 docs manifest: every docs.toml nav target exists and every docs/*.md is in nav (the check in .github/workflows/ci.yml)
- [ ] #2 python3 -m unittest discover -s src/endpoint -p 'test_*.py'
- [ ] #3 shellcheck src/endpoint/*.sh src/intune/*.sh
<!-- DOD:END -->
