---
id: doc-0003
title: Closed GitHub issues (pre-Backlog history index)
type: other
created_date: '2026-08-14 16:34'
updated_date: '2026-08-17 09:02'
---
Every issue closed on `rknightion/bumblebee-intune` before the repository moved to Backlog.md on
2026-08-14. One row per closed item, so the history is readable from the checkout alone.

**The issues still exist** — nothing was deleted, and the tracker stays enabled. Full bodies and
comments are at `gh issue view <N> --repo rknightion/bumblebee-intune --comments`, or in the browser
at the URL below. This document is a pointer, not the record; if the issues are ever deleted it must
be replaced by a redacted archive and this paragraph rewritten.

**GitHub's `#NNN` numbering is the only ID space over this history.** Backlog IDs follow creation
order and can never be made to match, which is why closed work was not imported as `Done` tasks —
`#2` is cited in the commit that closed it and stays citable as `#2`.

Counts as at 2026-08-14, from `gh issue list --state all --limit 1000`: **1 closed, 1 open.**

## Closed

| # | Title | Closed | Resulting commit |
|---|---|---|---|
| [2](https://github.com/rknightion/bumblebee-intune/issues/2) | Make the findings scan interval overridable per device | 2026-08-12 | `2294b14` |

## Open, and deliberately not a task

| # | Title | Why |
|---|---|---|
| [1](https://github.com/rknightion/bumblebee-intune/issues/1) | Dependency Dashboard | Renovate's own dashboard. Bot-maintained, recreated on the next run, and not work anyone does. |

## Work that predates the tracker entirely

The repository is four commits old and most of what it contains was never an issue. For provenance,
since the index above would otherwise imply the repo did one thing:

| Commit | Date | What |
|---|---|---|
| `8ac268b` | 2026-08-10 | Scaffold the repository |
| `b4da91c` | 2026-08-11 | The reference deployment — Intune scripts, endpoint scripts, Grafana rules, docs |
| `eea517d` | 2026-08-11 | Correct the author name |
| `2294b14` | 2026-08-12 | Closes `#2` |
