---
id: BBI-0004
title: Silence the two SC2086 hits in installer.sh — do NOT quote them
status: To Do
assignee: []
created_date: '2026-08-14 16:35'
labels:
  - chore
  - shell
dependencies: []
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`shellcheck src/endpoint/*.sh src/intune/*.sh` is clean except for two info-level SC2086 findings, at `src/intune/installer.sh:322` and `:326`, both on `$PROJECT_ROOTS`.

**The obvious fix is wrong and would break the deployment.** Line 72 sets `PROJECT_ROOTS="/Users/*/repos"` — the word splitting and glob expansion that SC2086 warns about are exactly what the variable is for. Quoting it passes the literal string `/Users/*/repos` to launchd, and every project-root scan silently finds nothing. This is a silent-total-loss-of-detection failure, the same class the catalog-select test exists to prevent.

The correct change is an explicit `# shellcheck disable=SC2086` carrying the reason, so the next reader does not reach for the quotes either. Needed before shellcheck can be a CI gate that fails on findings.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 shellcheck src/endpoint/*.sh src/intune/*.sh produces no output
- [ ] #2 The suppression is a targeted disable directive with a comment saying why the expansion is required, not a blanket disable and not added quotes
- [ ] #3 PROJECT_ROOTS still expands to the matching per-user repo directories — verified by running the emit path, not by reading it
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 docs manifest: every docs.toml nav target exists and every docs/*.md is in nav (the check in .github/workflows/ci.yml)
- [ ] #2 python3 -m unittest discover -s src/endpoint -p 'test_*.py'
- [ ] #3 shellcheck src/endpoint/*.sh src/intune/*.sh
<!-- DOD:END -->
