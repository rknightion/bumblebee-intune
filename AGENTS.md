# bumblebee-intune

Reference Intune deployment for Bumblebee: configuration profiles, endpoint scripts, and the Grafana
alerting/recording rules that consume what they emit.

## Task interface

`just check` (fmt-check, lint, test, docs-check) is the gate and must pass before you commit. There
is no `just ci` recipe. Note it does not run `tier-check` - see the trap below.

## Traps

**`just check` does not run `tier-check`.** The installed-vs-declared `source_type` split is a regex
alternation duplicated across three systems that cannot see each other - `src/grafana/alert-rules.py`
(what pages), `src/grafana/recording-rules.yaml` (what the metrics count), and the fleet dashboard
JSON (what a human reads). An omission fails in the safe direction, so nothing goes red; the three
answers simply disagree. Anything touching that list must end with:

```bash
just tier-check --dashboard <git-sync-checkout>/bumblebee/bumblebee-fleet.json
```

The script's own default `--dashboard` path is a placeholder and will not resolve. It takes no
credentials and makes no network calls.

**The `infra` group mutates live systems** - `alerts-apply` writes the Grafana Bumblebee Alerts
folder, `pppc-deploy` / `pppc-delete` write Intune. `alerts-plan` is the dry-run diff. Never run an
`infra` recipe to verify a code change. `alerts-apply` and `pppc-delete` are `[confirm]`-marked;
stop and ask before running one, and never pass `--yes` or `JUST_YES=1` to skip the prompt.

**`docs/` is not built here.** The `m7kni/m7kni-net-site` hub builds and publishes it; `docs.toml` is
the nav manifest and `just docs-check` mirrors the hub's strict build, so a page added without a nav
entry fails the gate.

## No real identifiers, anywhere

Issues, pull request text and everything under `.github/tracker/` are public, so none of them may
carry an email address, handle, UPN, tenant or account ID, device name or serial, Intune object
GUID, Grafana stack or tenant ID, address, coordinate, or a real per-user home path. Write the
shape, not the instance: "the second device in the pilot ring", `<tenant>/<device>/<policy>`.
Aggregate counts, timings and structural findings are fine.

`rknightion`, `m7kni-net-site` and `github.com/rknightion/...` are public repository slugs that
already appear throughout tracked source and docs; they are fine.

## Tracking work

Work is tracked in this repository's GitHub issues; they are the only queue. Do not add a checklist
file beside them.

- A pull request body says how it relates to an issue, on a line of its own: `Closes #N` with the
  `Acceptance evidence` table from `.github/pull_request_template.md` (one row for each acceptance
  criterion of that issue), `Refs #N` when it only relates to one, or `Task: none, <reason>`.
- A pull request that changes `.github/workflows/` or `.github/actions/` carries no closing keyword:
  write `Task: none, <reason>`, with `Refs #N` beside it if you like.
- Contributors never need the project board or the tracker tooling; a maintainer handles both.
- Older commits, docs and tests cite legacy board ids such as `BBI-0001`. `just issue <ID>` prints
  the issue each one became; `.github/tracker/id-map.json` is the map.
