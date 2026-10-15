# Loop: bumblebee-intune
tier: routine
gate: just check
ci-required: ci-success
grafana-stack: none

Public reference deployment: the scripts are the deliverable and the tenant is not a test rig. No
real tenant, device, user or Grafana identifier in any tracked file or loop artifact. `just check`
is `fmt-check lint test docs-check`; it does not run `tier-check`. There is no `just ci` recipe.

## Credentials

- None held by the loop. The `infra` recipes (`alerts-apply`, `pppc-deploy`, `pppc-delete`) mutate
  live Grafana and Intune: never run one to verify a change, and never pass `--yes` or `JUST_YES=1`.

## Traps

- The declared-only `source_type` list is one alternation copied into four places: `DECLARED_ONLY`
  in `src/grafana/alert-rules.py`, three inline alternations in `src/grafana/recording-rules.yaml`,
  the list in `docs/alerting.md`, and the fleet dashboard JSON in a Git-Sync repo outside this
  checkout. A change to it lands all four or none; a partial edit makes alerts and dashboard
  classify the same finding differently, silently. An omission fails safe (a false critical), which
  never justifies a partial edit.
- `src/grafana/check-tier-consistency.py` is the guard for that rule and is broken: it loads
  `bumblebee_alerts.py` and `bumblebee_recording_rules.yaml`, which this repo names `alert-rules.py`
  and `recording-rules.yaml`, so it raises `FileNotFoundError`. The port from a `deploy/` layout
  left stale names behind (its docstrings and `src/endpoint/test_catalog_select.py` still say run
  from `deploy/`). When touching a ported file, grep for its old name as well as its new one.
- `docs.toml` nav is strict at the hub (`m7kni/m7kni-net-site`): a page added to `docs/` without a
  nav entry, or deleted without removing its entry, fails every sibling repo's docs build.
- Placeholders are deliberate; never substitute a real value: `logs-prod-XXX` (installer and
  samples doc), `<your-git-sync-repo>` and `<your-gcx-context>`.

## Mutexes

- The live Intune tenant and any enrolled device: no lane touches them. Work needing a real device
  parks with the boundary written down.
- The Grafana Git-Sync working copy holding the fleet dashboard JSON is single-writer, root only.
- `gcx` queries against live Loki are read-only and allowed, through one lane at a time.
- Root-owned, never edited by a feature lane: `docs.toml`, `AGENTS.md`, `CLAUDE.md`,
  `backlog/config.yml`, `.github/workflows/*`.

## Lane brief

A lane that needs a root-owned file changed returns the exact edit as a diff and does not work
around the boundary. A lane that adds a docs page returns the `docs.toml` nav entry it needs.
