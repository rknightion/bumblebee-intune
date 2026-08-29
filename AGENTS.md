# Bumblebee Intune — contributor and agent instructions

Reference Microsoft Intune deployment for Bumblebee: configuration profiles, policies, endpoint
scripts and the Grafana alerting/recording rules that consume what they emit. `CLAUDE.md` imports
this file, so this is the one canonical copy — edit here, never there.

## Layout

- `src/intune/` — what Intune ships to a device: installer, uninstaller, PPPC deployment, the
  attribute-exposure and scan-summary scripts.
- `src/endpoint/` — what runs on the device: the run wrapper, catalog selection, the Loki push.
- `src/grafana/` — alert rules, recording rules, and the tier-consistency checker that keeps the
  declared-only list identical across all three systems that duplicate it.
- `docs/` — the published site. `docs.toml` is the nav manifest; the site itself is built by the
  `m7kni/m7kni-net-site` hub, not here.

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

## Tracker

Open work is a query, not a file: `backlog task list --plain`. Durable reference is
`backlog doc list --plain`.

Read the **Agent fan-out protocol (canonical)** doc before designing a wave, and the **Wave
operating model** doc for this project's own rules. `backlog doc list --plain` shows both.

## Non-negotiable rules

**`backlog/` is committed to git, so tasks, docs and decisions must never contain real account
identifiers or personal data** — no email addresses, handles, usernames, tenant or account IDs,
device names or serials, UPNs, Intune object GUIDs, Grafana stack or tenant IDs, addresses, or
coordinates. Write the shape, not the instance: "the second device in the pilot ring",
`<tenant>/<device>/<policy>`. Aggregate counts, timings and structural findings are fine. This is
easy to break by accident precisely because a tracker feels private. Sweep before committing:

```bash
grep -rniE "[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|logs-prod-[0-9]{3}|prometheus-prod-[0-9]+|tempo-prod-[0-9]+|/Users/(?!\*)[a-z]+|[A-Z0-9]{10,12}\b.*serial|\.local\b" backlog/ && echo "IDENTIFIERS FOUND"
```

Deliberately **not** matched: `rknightion`, `m7kni-net-site` and `github.com/rknightion/...` are
public repository slugs that already appear throughout tracked source and docs. Matching them makes
the sweep cry wolf, and a sweep that always fires is a sweep nobody reads. What must never appear is
a tenant ID, a device name or serial, a UPN or email address, an Intune object GUID, a Grafana stack
or tenant ID, or a real per-user home directory path.

**Never use `--notes` or `--plan` bare.** They *silently replace* the whole section. Use
`--append-notes` and `--append-plan`. This is an open upstream bug, not a misunderstanding, and it
destroys another session's writes with no warning. A global `PreToolUse` hook in the agent config denies the bare
forms; do not work around it.

**Finalize in one call**, so an interrupted agent cannot leave finished work looking unfinished:

```bash
backlog task edit bbi-0007 --check-ac 1 --check-ac 2 -s Done
```

The shipped guides check criteria at one step and set status several steps later; anything
interrupting between them — a context limit, a session ending — leaves the task inconsistent.

**Never hand-edit task markdown.** Section boundaries are HTML-comment markers; break one and the
section is *silently dropped*, exit code 0, with the data still in the file but invisible until the
next write destroys it for real. There is no repair command — `backlog doctor` only fixes duplicate
IDs. `backlog/config.yml` is the one deliberate exception: list-valued keys cannot be set through
`backlog config set` and the tool itself directs you to the file.

**Never let two agents edit the same task.** v1.50.x fixed the concurrent-edit race in the edit
funnel but not in reorder, draft saves, the TUI path, `doc update` or decision updates.

<!-- BACKLOG.MD GUIDELINES START -->
<!-- backlog.md-instructions-version: 1.50.1 -->
<CRITICAL_INSTRUCTION>

## Backlog.md Workflow

This project uses Backlog.md for task and project management.

**For every user request in this project, run `backlog instructions overview` before answering or taking action.**

Use the overview to decide whether to search, read, create, or update Backlog tasks.

Before task lifecycle actions, read the matching detailed guide:
- `backlog instructions task-creation` before creating or splitting tasks
- `backlog instructions task-execution` before planning, changing status or assignee, adding a plan or implementation notes, or implementing task work
- `backlog instructions task-finalization` before checking acceptance criteria, writing final summaries, or moving tasks to terminal statuses

Use `backlog <command> --help` before running unfamiliar commands. Help shows options, fields, and examples.

Do not edit Backlog task, draft, document, decision, or milestone markdown files directly. Use the `backlog` CLI so metadata, relationships, and history stay consistent.

</CRITICAL_INSTRUCTION>
<!-- BACKLOG.MD GUIDELINES END -->
