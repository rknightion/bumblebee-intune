---
id: BBI-0005
title: Migrate the repo task surface to just and retire Makefiles and ad-hoc scripts
status: To Do
assignee: []
created_date: '2026-08-28 19:18'
labels: []
dependencies: []
priority: medium
type: chore
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
# Migrate bumblebee-intune's task surface to `just`

This task applies the frozen fleet `JUST-FLEET-STANDARD.md` (§1–§13) to this repo's actual
inventory. Do not re-litigate the standard — every rule below is a direct application of it, not a
local variant.

## 1. Outcome

A top-level `justfile` exists with all seven mandatory recipes (`default`, `setup`, `fmt`,
`fmt-check`, `lint`, `test`, `check`) plus a `docs-check` recipe that replaces the inline Python
heredoc currently living inside `.github/workflows/ci.yml`, and three optional `[group('infra')]`
recipes wrapping the Grafana/Intune deployment tools that need external credentials. `just check`
is the complete local gate and is *exactly* what `ci.yml` runs (today it is not — see §5). There is
no `Makefile` in this repo, so there is nothing to delete on that front. All five shipped shell
scripts and all six Python programs stay exactly where they are (`git mv` none of them) — every one
of them is either a shipped runtime artifact that runs on a managed device/target host, or a real
program invoked with credentials this gate cannot assume. `AGENTS.md`'s "Gate" section and
`backlog/config.yml`'s `definition_of_done` point at `just check` instead of raw commands. `README.md`'s
test-run snippet points at `just test`.

**No files may be migrated (absorbed) into the justfile.** This repo's classification comes out
100% KEEP: there is nothing that is "just a few commands sequenced" — every script is either a
target-machine artifact or a substantial program (90–492 lines) with real logic.

## 2. The complete justfile

Drop this in as `justfile` at the repo root.

```just
set shell := ["bash", "-euo", "pipefail", "-c"]

# show the task surface
default:
    @just --list

# verify the local toolchain is present -- nothing to install, no lockfile in this repo
setup:
    @command -v python3 >/dev/null || (echo "python3 not found -- install Python 3.x" && exit 1)
    @command -v shellcheck >/dev/null || (echo "shellcheck not found -- brew install shellcheck (macOS) or apt install shellcheck (Linux CI)" && exit 1)
    @echo "toolchain present: python3, shellcheck"

# format source in place -- the justfile is the only thing here with a formatter
[group('check')]
fmt:
    @just --fmt
    @echo "no formatter configured for src/ -- shell (shellcheck-only) and stdlib python, hand-formatted"

# verify formatting -- never mutates
[group('check')]
fmt-check:
    just --fmt --check

# shellcheck every shipped shell script (deviceShellScript payloads + LaunchDaemon wrapper)
[group('check')]
[no-exit-message]
lint:
    shellcheck src/endpoint/*.sh src/intune/*.sh

# run the endpoint unit test suite; filter narrows to test_<filter>*.py
[group('check')]
[no-exit-message]
test filter="":
    python3 -m unittest discover -s src/endpoint -p 'test_{{ filter }}*.py'

# validate docs.toml nav matches docs/*.md -- mirrors the m7kni.io hub's strict build
[group('check')]
[script('python3')]
docs-check:
    import pathlib
    import sys
    import tomllib

    manifest = pathlib.Path("docs.toml")
    docs = pathlib.Path("docs")
    errors = []

    with manifest.open("rb") as fh:
        cfg = tomllib.load(fh)

    def targets(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from targets(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from targets(item)

    seen = set()
    for entry in cfg.get("site", {}).get("nav", []):
        for target in targets(entry):
            seen.add(target)
            if not (docs / target).is_file():
                errors.append(f"nav points at docs/{target}, which does not exist")

    for page in sorted(docs.rglob("*.md")):
        rel = page.relative_to(docs).as_posix()
        if rel.startswith(("includes/", "superpowers/")) or rel == "404.md":
            continue
        if rel not in seen:
            errors.append(f"docs/{rel} exists but is not in the nav")

    for key in ("name", "description", "author"):
        if not cfg.get("site", {}).get(key):
            errors.append(f"[site].{key} is missing or empty")

    if errors:
        print("\n".join(f"  - {e}" for e in errors))
        sys.exit(1)
    print(f"docs.toml OK - {len(seen)} pages in nav")

# the full gate -- exactly what CI enforces
[group('check')]
check: fmt-check lint test docs-check

# assert the declared-only source_type list matches across alerts/recording-rules/dashboard.
# Pass --dashboard <path> -- the script's own default path is a placeholder and will not resolve here.
[group('check')]
tier-check *args:
    python3 src/grafana/check-tier-consistency.py {{ args }}

# dry-run diff of Grafana alert rules against the deployed state (gcx auth required; pass --context)
[group('infra')]
alerts-plan context:
    python3 src/grafana/alert-rules.py --context {{ context }}

# apply Grafana alert rule changes -- mutates the Bumblebee Alerts folder (gcx auth required)
[group('infra')]
[confirm('apply Grafana alert rule changes now?')]
alerts-apply context:
    python3 src/grafana/alert-rules.py --context {{ context }} --apply

# show current Full Disk Access PPPC profile status via Microsoft Graph (creds required)
[group('infra')]
pppc-status:
    python3 src/intune/deploy-pppc.py --status

# create/update the Full Disk Access PPPC profile and assign it (Graph creds required)
[group('infra')]
pppc-deploy:
    python3 src/intune/deploy-pppc.py

# delete the Full Disk Access PPPC profile from Intune (Graph creds required)
[group('infra')]
[confirm('delete the PPPC profile from Intune now?')]
pppc-delete:
    python3 src/intune/deploy-pppc.py --delete
```

Notes on this justfile:
- `tier-check`, `alerts-plan`, `alerts-apply`, `pppc-status`, `pppc-deploy`, `pppc-delete` are **not**
  dependencies of `check` — they need credentials/network (`gcx` auth, Microsoft Graph) or a
  `--dashboard` path that has no working default in this repo, so they cannot run in an unattended
  gate. They exist so `just --list` is still the complete answer to "what can I do here."
- `deploy-pppc.py` currently does `from graph import Graph` against a `graph.py` module that does
  **not exist anywhere in this repo** (verified: `find . -iname graph.py` returns nothing). This is
  a pre-existing gap, not something this task fixes. Wrap it as written; do not try to make
  `pppc-*` recipes pass — they cannot, today, regardless of `just`.
- `check-tier-consistency.py` loads `bumblebee_alerts.py` via `importlib` (`HERE / "bumblebee_alerts.py"`
  at `src/grafana/check-tier-consistency.py:22`) but the real file is `alert-rules.py`. Also
  pre-existing, also not this task's problem to fix — flagged again in §9 Traps.

## 3. Makefile disposition

No `Makefile` or `GNUmakefile` exists anywhere in this repo (verified:
`find . -iname Makefile -o -iname GNUmakefile` outside vendor/node_modules/third_party/.venv,
zero results). **Nothing to delete, no `git rm` step for this section.**

## 4. Script disposition

| Script | Classification | Recipe | Reason |
|---|---|---|---|
| `src/endpoint/bumblebee-run.sh` | KEEP | none (shellcheck via `lint` only) | LaunchDaemon wrapper script, embedded verbatim into `installer.sh` at deploy time (per its own header comment), executes on the managed macOS endpoint. Never invoked by a developer or CI directly. |
| `src/intune/attribute-exposure.sh` | KEEP | none (shellcheck via `lint` only) | Intune macOS custom-attribute script, runs on-device via Intune's attribute collection, not developer/CI-invoked. |
| `src/intune/attribute-scan-summary.sh` | KEEP | none (shellcheck via `lint` only) | Same as above — device-side Intune custom attribute. |
| `src/intune/installer.sh` | KEEP | none (shellcheck via `lint` only) | Intune `deviceShellScript` payload, runs as root on the managed device. Ships `@@EMBED:catalog_select.py@@` / `@@EMBED:loki_push.py@@` placeholder markers (`installer.sh:264,276`) with no embedding tool present in this repo — pre-existing, see §9. |
| `src/intune/uninstall.sh` | KEEP | none (shellcheck via `lint` only) | Intune `deviceShellScript` payload, runs as root on the managed device, assigned as a separate uninstall policy. |
| `src/endpoint/catalog-select.py` | KEEP | none (real program, 160 lines) | Schema-coherence catalog stager, embedded into `installer.sh` at deploy time (per header). Not a dev/CI task. |
| `src/endpoint/loki-push.py` | KEEP | none (real program, 106 lines) | Loki NDJSON forwarder, embedded into `installer.sh` at deploy time (per header). Not a dev/CI task. |
| `src/endpoint/test_catalog_select.py` | KEEP | covered by `test` | Real unittest module; discovered by `python3 -m unittest discover -s src/endpoint -p 'test_*.py'`, unchanged from current invocation. |
| `src/grafana/alert-rules.py` | KEEP | `alerts-plan` / `alerts-apply` | 492-line Grafana alert-rule reconciler needing `gcx` CLI auth and an explicit `--context`. Real program with substantial logic — never absorb. |
| `src/grafana/check-tier-consistency.py` | KEEP | `tier-check` | 110-line drift checker across three duplicated source lists. Real program; kept as a file, wrapped in a recipe. |
| `src/intune/deploy-pppc.py` | KEEP | `pppc-status` / `pppc-deploy` / `pppc-delete` | 258-line Microsoft Graph PPPC profile deployer. Real program needing Graph credentials — never absorb. |

No script in this repo is a thin sequencing wrapper (`plan.sh`/`setup.sh`-shaped). There is nothing
to ABSORB. `git rm` nothing under `src/`.

## 5. CI changes

**Current state, and the gap this task closes:** `.github/workflows/ci.yml` today runs *only* the
docs-manifest validation. It does **not** run `shellcheck` or the unittest suite, even though
`AGENTS.md`'s "Gate" section and `backlog/config.yml`'s `definition_of_done` both name them as
required. Verified live: `shellcheck src/endpoint/*.sh src/intune/*.sh` currently **exits 1** —
`SC2086` on `installer.sh:322` and `installer.sh:326` (`$PROJECT_ROOTS` unquoted). This has never
been caught because CI never runs shellcheck. Fixing CI to call `just check` will immediately turn
this workflow red unless the quoting is fixed first — see the required pre-step in §8 Order of work.

Edit `.github/workflows/ci.yml`:

1. Rename the `validate-docs` job (or repurpose it) to run the full gate. Insert a `setup-just` step
   after checkout and before the run step:

```yaml
      - uses: extractions/setup-just@<pin-to-current-fleet-sha> # v4
        with:
          just-version: '1.58.0'
```

   Use whatever exact pinned SHA the fleet's other repos are currently using for
   `extractions/setup-just` (check a sibling repo already migrated, or resolve the SHA for the
   current `v4` tag at migration time — do not invent a SHA).

2. Replace the entire inline `python - <<'PYEOF' ... PYEOF` heredoc step with:

```yaml
      - name: just check
        run: just check
```

   (The heredoc's exact logic is now `docs-check` inside the justfile — see §2. Delete the heredoc
   from the workflow file entirely; it is fully migrated, not duplicated.)

3. The job still needs `actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7` (with
   `python-version: '3.x'`) — `just check` needs `python3` on PATH, and `shellcheck` needs to be
   present too. `ubuntu-latest` runners ship `shellcheck` preinstalled (Actions runner image default
   tool cache) — verify this holds at migration time; if not, add
   `- run: sudo apt-get install -y shellcheck` before the `just check` step, or switch to a
   shellcheck-installing action.

4. Rename the job (e.g. `validate-docs` → `ci` or `gate`) if the job id changes meaningfully, but
   **`ci-success`'s `needs: [validate-docs]` must be updated to match the new job id** — do not leave
   it pointing at a job name that no longer exists, and do not add a second job. `ci-success` itself,
   its `permissions:`, its `if: always()`, and its pass/fail logic must not change.

5. Do not touch `harden-runner`, `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`
   with `persist-credentials: false`, or the workflow's `permissions: contents: read` block.

**Leave untouched, entirely:** `actionlint.yml`, `codeql.yml`, `scorecard.yml`, `zizmor.yml` (all
`uses: rknightion/.github/.github/workflows/...@0228d8b9f1e36ff3a1d0906574d70fa174ddc7bf # v1.5.1`
reusable calls — GitHub-native, never fold into `just`) and `trigger-docs-sync.yml` (OpenBao broker
token mint + `repository-dispatch`, GitHub-native, never fold into `just`).

## 6. Docs and agent-contract changes

**`AGENTS.md`** — replace the "Gate" section body:

Current (`AGENTS.md:16-24`):
```markdown
## Gate

Run all three before calling anything done. They need no credentials and make no network calls.

​```bash
python3 -m unittest discover -s src/endpoint -p 'test_*.py'
shellcheck src/endpoint/*.sh src/intune/*.sh
​```

Plus the docs manifest check in `.github/workflows/ci.yml` — every `docs.toml` nav target must
exist and every `docs/*.md` must appear in nav. The hub builds with `strict = true`, so a nav entry
pointing at a missing file fails the whole fleet build, not just this repo.

These are `definition_of_done` in `backlog/config.yml`, so every new task inherits them.
```

Replace with:
```markdown
## Task interface

This repo's task surface is a `justfile`. Discover it, don't guess it:

    just --list                        # human-readable
    just --dump --dump-format json     # machine-readable
    just --show <recipe>               # what a recipe actually runs

- `just check` is the full gate and is exactly what CI enforces. It must pass before you commit.
- Prefer `just <recipe>` over the underlying tool. If you are typing `pytest` or `shellcheck`, you
  want `just test` or `just lint`.
- Run `just` with stdin from /dev/null. Recipes marked `[confirm]` are destructive — stop and ask
  before running one; never pass `--yes` or `JUST_YES=1`.
- If a task you need does not exist, add a recipe with a `#` doc comment and a `[group(...)]`
  rather than running a bare command.

This is `definition_of_done` in `backlog/config.yml`, so every new task inherits it.
```

(Verbatim from fleet standard §9, plus the repo-specific closing line pointing at
`backlog/config.yml`.)

**`README.md:64-68`** — replace:
```markdown
Run the endpoint tests:

​```sh
cd src/endpoint && python3 -m unittest test_catalog_select
​```
```
with:
```markdown
Run the endpoint tests:

​```sh
just test
​```
```

**`CLAUDE.md`** — unchanged. It only does `@AGENTS.md` and carries no gate text of its own.

No other file under `docs/` references `make` or a script path as a run instruction (verified:
`grep -rn "make \|\./scripts" docs/*.md` — no hits beyond unrelated prose already checked).

## 7. `backlog/config.yml`

Current:
```yaml
definition_of_done:
  - "docs manifest: every docs.toml nav target exists and every docs/*.md is in nav (the check in .github/workflows/ci.yml)"
  - "python3 -m unittest discover -s src/endpoint -p 'test_*.py'"
  - "shellcheck src/endpoint/*.sh src/intune/*.sh"
```

New:
```yaml
definition_of_done:
  - "just check"
```

`just check` runs `fmt-check`, `lint`, `test`, and `docs-check` — a strict superset of the three
lines it replaces (it additionally verifies `justfile` formatting, which is new and harmless). Do
**not** hand-edit this file — `backlog/config.yml` is the one deliberate exception the repo's own
`AGENTS.md` names for list-valued keys that `backlog config set` cannot reach, so a direct edit here
is correct and matches existing repo convention; every other `backlog/` file must go through the CLI.

## 8. Order of work

1. **Fix the pre-existing shellcheck finding first**, before wiring CI to enforce it: quote
   `$PROJECT_ROOTS` on `src/intune/installer.sh:322` and `:326` (`"$PROJECT_ROOTS"`). Re-run
   `shellcheck src/endpoint/*.sh src/intune/*.sh` locally and confirm exit 0. This is unrelated to
   `just` mechanically, but is required so step 3 below doesn't ship broken CI.
2. Add the `justfile` from §2 at the repo root. Run `just --fmt --check` (should already pass — it
   was authored fmt-clean), then `just check` locally end to end. Confirm `test` and `lint` and
   `docs-check` all pass (they will, once step 1 is done) and `fmt-check` passes.
3. Edit `.github/workflows/ci.yml` per §5. Push and confirm the workflow run is green, including
   `ci-success`.
4. Edit `AGENTS.md`, `README.md` per §6.
5. Edit `backlog/config.yml` per §7 (direct file edit, not the CLI — see §7's note).
6. Nothing to delete (§3 — no Makefile; §4 — no ABSORB scripts). Skip the deletion step entirely;
   there is no "last" step here beyond confirming nothing still references the old raw commands
   (`grep -rn "python3 -m unittest\|shellcheck src/" --include=*.md --include=*.yml .` should return
   only this task's own history, not live docs/CI).

## 9. Traps specific to this repo

- **`shellcheck` currently fails** (`SC2086` at `installer.sh:322,326`) and nothing catches it today
  because CI never runs shellcheck. Fix it as step 1 of §8, not as an afterthought — otherwise
  wiring `ci-success` to `just check` ships a red CI on the very commit that adds it.
- **`deploy-pppc.py` imports a `graph` module that does not exist in this repo.** `pppc-status` /
  `pppc-deploy` / `pppc-delete` will fail with `ModuleNotFoundError` today regardless of `just`. Do
  not treat this as a `just`-migration bug; wrap the script as-is and leave the underlying gap alone
  unless a separate task is filed for it.
- **`check-tier-consistency.py:22` loads `bumblebee_alerts.py` via `importlib`, but the real file is
  `alert-rules.py`.** `just tier-check` will fail with `FileNotFoundError` on the module load,
  independent of any `--dashboard` argument. Same as above: pre-existing, not in scope, wrap as-is.
- **`check-tier-consistency.py`'s default `--dashboard` path is a literal placeholder**
  (`Path.home() / "repos/<your-git-sync-repo>/bumblebee/bumblebee-fleet.json"`,
  `check-tier-consistency.py:16`) — it will never resolve on any real machine. `tier-check` must stay
  out of `check`'s dependency list for this reason alone, independent of the `importlib` bug above.
- **`installer.sh` embeds `catalog-select.py` and `loki-push.py` via `@@EMBED:...@@` markers with no
  embedding script anywhere in this repo.** This looks like a manual, currently-undocumented step.
  Do not invent an ABSORB target for it and do not add a `gen`/`gen-check` recipe pretending this is
  automated — it isn't, today.
- **`docs-check` uses `[script('python3')]`, not a line-based recipe** — the original CI step has a
  `for`/nested-function body that would hit just's "extra leading whitespace" failure mode in a
  plain recipe (§10 of the fleet standard). Keep it as a script block; do not try to flatten it to
  one-liners.
- **`ubuntu-latest`'s shellcheck availability is not verified as part of this task** — GitHub-hosted
  runner images have historically preinstalled `shellcheck` in the tool cache, but confirm this at
  migration time (`shellcheck --version` as a debug step, or check the runner image manifest) before
  assuming the CI job needs no extra install step.
- **No lockfile, no `pyproject.toml`, nothing to `uv sync` or `pip install`.** `setup` is a
  toolchain-presence check, not a dependency install — do not invent a Python dependency manifest
  that doesn't exist. If a future task adds one (e.g. for `deploy-pppc.py`'s Graph SDK dependency),
  `setup` should then gain a real install step; that's out of scope here.

## 10. Out of scope

- Every script marked KEEP in §4 — do not move, rename, or absorb any of them.
- `.github/workflows/actionlint.yml`, `codeql.yml`, `scorecard.yml`, `zizmor.yml` — GitHub-native
  reusable-workflow calls into `rknightion/.github`. Never convert `uses:` into `run: just`.
- `.github/workflows/trigger-docs-sync.yml` — OpenBao broker token mint + `repository-dispatch`.
  GitHub-native, no shell logic to migrate.
- `docs/`, `docs.toml`, and the `zensical`/m7kni.io hub build pipeline itself — this repo only owns
  the manifest and the markdown; the actual site build happens in `m7kni/m7kni-net-site`.
- Fixing `deploy-pppc.py`'s missing `graph` module or `check-tier-consistency.py`'s stale
  `bumblebee_alerts.py` reference — flagged in §9 as pre-existing, left for a separate task.
- `backlog/` task/doc/decision markdown — never hand-edited; `backlog/config.yml` is the sole named
  exception (§7).
- Renovate config (`renovate.json`) and `LICENSE` — untouched, unrelated.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A top-level justfile exists defining default, setup, fmt, fmt-check, lint, test and check, each with a # doc comment and a [group(...)]
- [ ] #2 just check runs fmt-check, lint, test and docs-check and passes locally with no credentials or network access
- [ ] #3 just --fmt --check passes on the justfile
- [ ] #4 just --list shows a doc comment and group for every public recipe, including alerts-plan, alerts-apply, tier-check, pppc-status, pppc-deploy and pppc-delete
- [ ] #5 src/intune/installer.sh:322 and :326 quote $PROJECT_ROOTS so shellcheck src/endpoint/*.sh src/intune/*.sh exits 0
- [ ] #6 no Makefile or GNUmakefile exists in the repo (none exists today; this criterion confirms none was introduced)
- [ ] #7 .github/workflows/ci.yml installs just via extractions/setup-just pinned to just-version 1.58.0 and runs just check in place of the inline docs-validation heredoc, with ci-success still gating on the renamed job
- [ ] #8 AGENTS.md's Gate section is replaced with the Task interface section naming just check as the gate, and README.md's test-run snippet reads just test
- [ ] #9 backlog/config.yml's definition_of_done reads exactly just check
- [ ] #10 actionlint.yml, codeql.yml, scorecard.yml, zizmor.yml and trigger-docs-sync.yml are unchanged
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 docs manifest: every docs.toml nav target exists and every docs/*.md is in nav (the check in .github/workflows/ci.yml)
- [ ] #2 python3 -m unittest discover -s src/endpoint -p 'test_*.py'
- [ ] #3 shellcheck src/endpoint/*.sh src/intune/*.sh
<!-- DOD:END -->
