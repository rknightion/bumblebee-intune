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
[confirm('apply Grafana alert rule changes now?')]
[group('infra')]
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
[confirm('delete the PPPC profile from Intune now?')]
[group('infra')]
pppc-delete:
    python3 src/intune/deploy-pppc.py --delete
