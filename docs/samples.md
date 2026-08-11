---
title: Samples
description: The complete source tree - endpoint scripts, Intune deployment tooling and observability rules.
---

# Samples

Everything is in [`src/`](https://github.com/rknightion/bumblebee-intune/tree/main/src). It is the
deployment, not an illustration of it: the same scripts, with credentials, scan roots and
organisation names replaced by placeholders.

## Endpoint

Runs on the managed Mac. POSIX `sh` and stdlib Python 3 only.

| File | What it does |
| --- | --- |
| [`bumblebee-run.sh`](https://github.com/rknightion/bumblebee-intune/blob/main/src/endpoint/bumblebee-run.sh) | The wrapper the LaunchDaemons invoke. Catalog selection, FDA probe, scan, summary, health record, ship, prune. |
| [`catalog-select.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/endpoint/catalog-select.py) | Groups catalogs by `schema_version` and stages one coherent set. `--rank N` walks the fallbacks. |
| [`test_catalog_select.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/endpoint/test_catalog_select.py) | Seven tests. The one to read is `test_mixed_schemas_never_staged_together`. |
| [`loki-push.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/endpoint/loki-push.py) | Ships NDJSON. Ingestion-time timestamps, paced. |

```sh
cd src/endpoint && python3 -m unittest test_catalog_select
```

`bumblebee-run.sh` is the file to read first. It is where the difference between "a scanner runs on
this laptop" and "this laptop is covered" is actually implemented.

## Intune

| File | What it does |
| --- | --- |
| [`installer.sh`](https://github.com/rknightion/bumblebee-intune/blob/main/src/intune/installer.sh) | The `deviceShellScript`. Pinned SHA256-verified acquisition, helper staging, four LaunchDaemons. Idempotent. |
| [`attribute-exposure.sh`](https://github.com/rknightion/bumblebee-intune/blob/main/src/intune/attribute-exposure.sh) | Custom attribute: one token, worst case across all profiles. |
| [`attribute-scan-summary.sh`](https://github.com/rknightion/bumblebee-intune/blob/main/src/intune/attribute-scan-summary.sh) | Custom attribute: one line of detail. |
| [`uninstall.sh`](https://github.com/rknightion/bumblebee-intune/blob/main/src/intune/uninstall.sh) | Full removal, including legacy daemon labels. Never assign it. |
| [`deploy-pppc.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/intune/deploy-pppc.py) | Derives the cdhash from the pinned release and deploys the PPPC profile. |

### Placeholders to fill in

`installer.sh` carries three, all at the top:

```sh
PROJECT_ROOTS="/Users/*/repos"
LOKI_URL="https://logs-prod-XXX.grafana.net/loki/api/v1/push"
LOKI_TOKEN="__SET_ME__"
```

!!! danger "The token is readable at rest"

    Intune has no secret-vaulting for script content: whatever you put in `LOKI_TOKEN` is readable by
    anyone with Graph read on `deviceShellScripts`, and it lands on every managed device in
    `/var/db/bumblebee/env`.

    Use a push-only, single-purpose, rotatable credential. See [the credential
    problem](telemetry.md#the-credential-problem).

`deploy-pppc.py` and the Grafana scripts carry organisation placeholders (`com.example.*`,
`Example Org`) and a `<your-gcx-context>` marker.

## Observability

Grafana-specific, and the easiest part to swap for another backend - the endpoint emits plain NDJSON.

| File | What it does |
| --- | --- |
| [`recording-rules.yaml`](https://github.com/rknightion/bumblebee-intune/blob/main/src/grafana/recording-rules.yaml) | 14 Loki recording rules. Non-overlapping windows, `offset 10m`. |
| [`alert-rules.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/grafana/alert-rules.py) | Reconciles the seven alert rules. Dry-run by default. |
| [`check-tier-consistency.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/grafana/check-tier-consistency.py) | Asserts the declared-only list matches across alerts, recording rules and dashboard. Wire into CI. |

```sh
python3 src/grafana/alert-rules.py              # dry run, prints the diff
python3 src/grafana/alert-rules.py --apply
python3 src/grafana/check-tier-consistency.py   # exits 1 on drift
```

!!! warning "Treat a failed rule push as a build failure"

    A reconciler can print a clean diff for every rule and then fail to apply some of them. If yours
    reports a partial push, the live rules have drifted from the code that claims to own them - and
    it stays that way silently.

## Reading order

New to this, in order:

1. [`installer.sh`](https://github.com/rknightion/bumblebee-intune/blob/main/src/intune/installer.sh) - the header comment explains every design decision
2. [`bumblebee-run.sh`](https://github.com/rknightion/bumblebee-intune/blob/main/src/endpoint/bumblebee-run.sh) - where the health signal is produced
3. [`catalog-select.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/endpoint/catalog-select.py) - the failure that emits nothing at all
4. [`alert-rules.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/grafana/alert-rules.py) - the tier model, in the `DECLARED_ONLY` comment

The comments are the documentation. Where a script explains why something is the way it is, that
reason was expensive.
