# bumblebee-intune

Reference Intune deployment for Bumblebee: configuration profiles, endpoint scripts, and the Grafana
alerting/recording rules that consume what they emit.

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
`infra` recipe to verify a code change.

**`docs/` is not built here.** The `m7kni/m7kni-net-site` hub builds and publishes it; `docs.toml` is
the nav manifest and `just docs-check` mirrors the hub's strict build, so a page added without a nav
entry fails the gate.

## No real identifiers, anywhere

`backlog/` is committed, so tasks and docs must never carry an email address, handle, UPN, tenant or
account ID, device name or serial, Intune object GUID, Grafana stack or tenant ID, address,
coordinate, or a real per-user home path. Write the shape, not the instance: "the second device in
the pilot ring", `<tenant>/<device>/<policy>`. Aggregate counts, timings and structural findings are
fine. Sweep before committing:

```bash
grep -rniE "[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|logs-prod-[0-9]{3}|prometheus-prod-[0-9]+|tempo-prod-[0-9]+|/Users/(?!\*)[a-z]+|[A-Z0-9]{10,12}\b.*serial|\.local\b" backlog/ && echo "IDENTIFIERS FOUND"
```

`rknightion`, `m7kni-net-site` and `github.com/rknightion/...` are deliberately not matched: they are
public repository slugs that already appear throughout tracked source and docs, and a sweep that
always fires is a sweep nobody reads.

## Tracker

Tasks are `bbi-NNNN`. Read the **Agent fan-out protocol (canonical)** doc before designing a wave,
and the **Wave operating model** doc for this project's own rules
(`backlog doc list --plain`, `backlog doc view <id> --plain`).
