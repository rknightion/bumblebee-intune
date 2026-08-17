---
id: doc-0002
title: Wave operating model
type: guide
created_date: '2026-08-14 16:33'
updated_date: '2026-08-14 16:34'
---
This repository's own campaign rules. The model itself — run contract, run modes, routing,
authority, lane briefs, contract freezing, the goal-file template, the pre-flight checklist — is the
**Agent fan-out protocol (canonical)** doc, and nothing here restates it. If a section below could be
pasted into another project unchanged, it is in the wrong document.

## The one rule this project adds: the declared-only list is duplicated four ways

The installed-vs-declared split is the most load-bearing judgement in the deployment, and it is
expressed as a `source_type` alternation copied into four places that cannot see each other:

- `src/grafana/alert-rules.py` — `DECLARED_ONLY` (what pages)
- `src/grafana/recording-rules.yaml` — three separate inline alternations, lines 133, 137, 141
  (what the metrics count)
- `docs/alerting.md` — the prose list around line 30 (what a human reads)
- the Grafana fleet dashboard JSON, which lives in a **Git-Sync repo outside this checkout**

**The failure this caused:** `composer-lock` was added to the first two on 2026-08-11 and missed in
the dashboard. For a fortnight the dashboard classified a `composer.lock` entry as INSTALLED while
the alerts classified it as declared-only — the same finding in two different tiers depending on
where you looked, with 14 live packagist packages behind it. Nothing failed. The two answers simply
disagreed, silently.

**The rule: any wave that touches the alternation must land all four edits, or none.** A lane that
changes one and defers the others has not produced a partial improvement; it has produced a
disagreement. The dashboard edit is outside this repo, so it cannot be a lane's silent assumption —
it is either done by the root agent in the Git-Sync working copy in the same wave, or the whole
change is deferred.

Verified 2026-08-14: all four in-repo copies currently agree, at ten entries. `src/grafana/check-tier-consistency.py`
exists to assert exactly this, and **is currently broken** — see below.

**An omission fails in the safe direction** (a false critical, not silence) and that is deliberate;
see the `DECLARED_ONLY` comment. Safe is not correct. Do not use it to justify a partial edit.

## Recurring defect in this codebase: residue from a `deploy/` layout

The Grafana and endpoint code was ported from a repo whose files sat in `deploy/` with underscored
names. The port renamed the files and did not update what referred to them. Two concrete instances,
both live on 2026-08-14:

- `src/grafana/check-tier-consistency.py` loads `bumblebee_alerts.py` and
  `bumblebee_recording_rules.yaml` from its own directory. This repo ships `alert-rules.py` and
  `recording-rules.yaml`. It `FileNotFoundError`s on every invocation — the guard for the rule above
  has never run here.
- `src/endpoint/test_catalog_select.py` and the tier script's docstrings both still tell you to run
  from `deploy/`.

**So: when a lane touches a ported file, grep the repo for the file's *old* name as well as its new
one.** A rename that leaves a stale reference behind exits 0 everywhere except the moment someone
runs the thing.

## Exclusive resources — a lane may not touch these

- **The live Intune tenant and any enrolled device.** No lane assigns a policy, deploys a PPPC
  profile, or triggers a scan. This is a reference deployment; the scripts are the deliverable and
  the tenant is not a test rig. Anything needing a real device is a `Parked` task with the boundary
  written down, not a lane that "verifies in prod".
- **The Grafana Git-Sync working copy** holding the fleet dashboard JSON. Outside this checkout,
  single-writer, and see the four-way rule above. Root agent only.
- **`gcx` queries against live Loki** are read-only and allowed, but they are the only way to check
  what `source_type` values actually occur, so two lanes racing them wastes quota rather than
  corrupting anything. Route them through one lane.

## Ownership

One file, one owner, as usual. The wiring files below are **root-agent only**, never edited inside a
parallel wave, because every lane has a reason to want a line in them:

- `docs.toml` — the nav manifest
- `AGENTS.md` and `CLAUDE.md`
- `backlog/config.yml`
- `.github/workflows/*`

**The escape hatch:** a lane that believes it must edit a root-owned file stops and returns the exact
edit it wants, as a diff, in its final message. It does not make the edit and it does not work around
it. A boundary with no escape hatch is a stop condition wearing a safety label.

## The docs manifest has cross-repo blast radius

The published site is built by the `m7kni/m7kni-net-site` hub, **with `strict = true`**, from
`docs.toml` plus `docs/`. A nav entry pointing at a file that does not exist fails the *whole fleet
build* — every sibling repo's docs, not just this one. `.github/workflows/ci.yml` catches it here so
it never reaches the hub.

Practical consequence for a wave: **adding a page to `docs/` without adding it to `docs.toml` nav is
a break, and so is deleting one without removing its nav entry.** Since `docs.toml` is root-owned, a
lane that adds a doc page returns the nav entry it needs.

## Deliberate placeholders — do not "fix" them

These look like unfinished work and are not. A lane that helpfully substitutes a real value is
committing an identifier to a public repo.

- `logs-prod-XXX` in `src/intune/installer.sh` and `docs/samples.md` — the Loki push endpoint is
  per-tenant and the reader supplies it.
- `<your-git-sync-repo>` in `check-tier-consistency.py`'s `DEFAULT_DASHBOARD`.
- `<your-gcx-context>` in the `alert-rules.py` verification comment.

## Run-end against this tracker

Task state is the record; nothing durable may live only in the closing terminal message.

- Landed work is `Done` with the commit SHA in its final summary, finalized in **one** call
  (`--check-ac N ... -s Done`).
- Blocked work is `Parked` with a concrete resume boundary — what was tried, what it needs, and who
  or what can supply it. "Needs a real device" is a boundary; "blocked" is not.
- Untouched work stays `To Do` and needs no action.
- Work discovered mid-run becomes a new task labelled `needs-triage`, not a note in someone else's
  implementation notes.

The closing message to the terminal carries only what no single task can: what this run learned
about the repo as a whole. Writing it is the last unit of work, not a reply to a request.
