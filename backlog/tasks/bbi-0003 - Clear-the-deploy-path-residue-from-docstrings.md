---
id: BBI-0003
title: Clear the deploy/ path residue from docstrings
status: To Do
assignee: []
created_date: '2026-08-14 16:35'
labels:
  - chore
dependencies: []
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Cosmetic residue from the same port. Both tell a reader to run the code from a directory that does not exist in this repo:

- `src/endpoint/test_catalog_select.py` — "Run: python3 -m unittest test_catalog_select (from src/endpoint/) or: python3 -m unittest discover -s deploy"
- `src/grafana/check-tier-consistency.py` — the module docstring's Run: block and its WHY THIS EXISTS section both say `deploy/…`

Low stakes on its own. Worth doing as part of whichever wave touches these files, because a stale instruction that exits 0 is how the tier-consistency breakage went unnoticed in the first place.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 No docstring or comment in src/ refers to a deploy/ path or an underscored filename that this repo does not have
- [ ] #2 The documented run command for each script is the one that actually works, verified by running it
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 docs manifest: every docs.toml nav target exists and every docs/*.md is in nav (the check in .github/workflows/ci.yml)
- [ ] #2 python3 -m unittest discover -s src/endpoint -p 'test_*.py'
- [ ] #3 shellcheck src/endpoint/*.sh src/intune/*.sh
<!-- DOD:END -->
