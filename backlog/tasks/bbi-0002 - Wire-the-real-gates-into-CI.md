---
id: BBI-0002
title: Wire the real gates into CI
status: To Do
assignee: []
created_date: '2026-08-14 16:35'
updated_date: '2026-08-14 16:36'
labels:
  - ci
dependencies:
  - BBI-0001
  - BBI-0004
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`.github/workflows/ci.yml` runs only the docs-manifest check. Three things that can run without credentials or network run in no gate at all:

- `python3 -m unittest discover -s src/endpoint -p 'test_*.py'` (7 tests, passing as at 2026-08-14)
- `shellcheck src/endpoint/*.sh src/intune/*.sh`
- `src/grafana/check-tier-consistency.py` — blocked, see the tier-consistency task; that must land first

So the repo's stated definition_of_done is enforced by nobody on a push. The unit test pins the rule that stops a silent total loss of detection (bumblebee refuses a catalog directory mixing schema versions), which is exactly the kind of thing that regresses unnoticed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 ci.yml runs the unittest discovery and fails the build on a test failure
- [ ] #2 ci.yml runs shellcheck over src/endpoint/*.sh and src/intune/*.sh and fails on findings
- [ ] #3 The tier-consistency check is wired in once it works, or its absence is recorded in the task's notes with the reason
- [ ] #4 ci-success still gates on every new job, so a skipped job cannot pass the branch protection check
- [ ] #5 New actions are pinned to a commit hash, not a tag (zizmor is in CI and will fail otherwise)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 docs manifest: every docs.toml nav target exists and every docs/*.md is in nav (the check in .github/workflows/ci.yml)
- [ ] #2 python3 -m unittest discover -s src/endpoint -p 'test_*.py'
- [ ] #3 shellcheck src/endpoint/*.sh src/intune/*.sh
<!-- DOD:END -->
