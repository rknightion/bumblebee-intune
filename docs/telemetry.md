---
title: Telemetry
description: Record shapes, shipping NDJSON to Loki, what fleet-wide inventory actually costs, and the credential problem Intune does not solve.
---

# Telemetry

## Record types

The scanner emits NDJSON. Four record types matter:

| `record_type` | Emitted | Carries |
| --- | --- | --- |
| `scan_summary` | One per scan | `status`, `timed_out`, `duration_ms`, `files_considered`, `findings_emitted`, `roots` |
| `finding` | One per catalog match | `package_name`, `version`, `ecosystem`, `catalog_id`, `source_type`, `root_kind`, `severity` |
| `package` | One per package, inventory mode only | `ecosystem`, `package_manager`, `source_type`, `root_kind`, `install_scope`, `direct_dependency`, `has_lifecycle_scripts`, `lifecycle_scripts` |
| `catalog_health` | One per scan, **written by the wrapper** | Everything on [this page](exposure-catalogs.md#emit-catalog-health-yourself) |

The last one is not upstream's. It exists because [the scanner cannot report on its own
blindness](silent-failures.md).

Ship `mode` and `profile` as **stream labels** alongside `host`. They partition cleanly, they are low
cardinality, and every useful query filters on them.

## Shipping to Loki

A small pushing helper, invoked by the wrapper:

- **Timestamp entries with ingestion-time `now()`, not `scan_time`.** A scan that started 40 minutes
  ago and finished now produces records whose `scan_time` is outside Loki's accepted window, and they
  are rejected with `greater_than_max_sample_age`. The scan time is in the record body where it
  belongs.
- **Pace the pushes.** A single inventory run can be hundreds of thousands of records; firing them
  as fast as the network allows earns rate limits.
- **Prune after a successful send**, but prune after a *failed* one too, once you have retried. Local
  disk on a laptop is not a durable queue, and filling it is a worse outcome than losing a day of
  inventory. Say so explicitly in the code so nobody "fixes" it later.

Source:
[`src/endpoint/loki-push.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/endpoint/loki-push.py).

## What this costs

Measure before you enable inventory fleet-wide. **Volume scales with dependency-cache size, not with
fleet size**, and the spread between developers is enormous.

Two laptops of the same model, same day:

| Host | `package` records/24h | Volume/day |
| --- | --- | --- |
| Busy developer | 259,835 | **242.7 MB** |
| Light user | ~1,000 | 0.92 MB |

A **264x spread** between two machines. Over 14 days: 2.56M records versus 14.7k. Extrapolating to 50
similar developer machines is roughly **360 GB/month** of log ingest, and the number is driven by how
many Go modules and npm packages your busiest engineer has cached, not by anything you control.

Options, roughly in order of how much they cost you:

1. **Recording rules for the aggregate panels.** Keeps full fidelity, removes the repeated
   full-scans. See [Dashboards](dashboards.md).
2. **Lower the inventory cadence.** Weekly instead of daily is a 7x reduction and inventory is not a
   detection control.
3. **`--findings-only` everywhere, inventory opt-in.** Findings are tiny; the volume is all
   `package`. This is the right default if you are not going to use the inventory.
4. **Drop `package` records at the pipeline** and keep only what you aggregate.

Decide deliberately. The default of "ship everything daily" is defensible on a small fleet and
indefensible on a large one, and the bill arrives a month later.

## Is the inventory worth keeping?

Yes, if you use it. `package` records answer questions that findings cannot:

- **Which packages execute code at install time.** `has_lifecycle_scripts` plus the hook names is the
  highest-signal supply-chain cut available, because a malicious release only needs an install hook
  to run.
- **What is installed versus merely declared**, which is the whole basis of [finding
  triage](alerting.md#classify-by-what-is-installed).
- Blast radius for a new advisory, without waiting for a catalog update and a scan cycle.

But split lifecycle scripts by hook, or the number is useless. Measured: **1,071 packages** carry
`has_lifecycle_scripts: true` - and **1,012 of those are `["prepare"]` only**, which runs on a local
`npm install` of the *project*, not on installing the dependency. The genuinely risky
`install`/`preinstall`/`postinstall` subset is **39**.

1,071 is a number nobody can act on. 39 is a list somebody can read.

```logql
sum by (host, ecosystem) (count_over_time({source="bumblebee", record_type="package"}
  | json has_lifecycle_scripts="has_lifecycle_scripts", lifecycle_scripts="lifecycle_scripts"
  | has_lifecycle_scripts="true" | lifecycle_scripts=~".*install.*" [5m]))
```

`lifecycle_scripts` arrives as a JSON array and LogQL extracts it in its raw string form, so a
substring match is the correct test. `.*install.*` deliberately catches all three hooks.

## `severity` is not a triage signal

Every `finding` record in one 30-day sample carried `severity: "critical"`. All 211 of them, because
severity is whatever the source catalog stamped and the OSV importer marks every `MAL-` record
critical.

All 211 were simultaneously `pnpm-lockfile` + `user_package_root` - a malicious package *declared* in
a lockfile vendored read-only inside a third-party dependency cache. Nothing installed, nothing that
would ever be installed.

So `severity: critical` means "the catalog said critical", not "this endpoint is compromised".
Anything routing on it pages on every advisory match, and after a week nobody reads the pages. Derive
the tier from `source_type` and `root_kind` instead.

## The credential problem

**Intune has no secret-vaulting for script content.** The Loki token is embedded in the script body,
which means:

- Anyone with Graph read on `deviceShellScripts` can read it.
- It lands on every managed device in `/var/db/bumblebee/env`.

There is no way around this within Intune. The script has to authenticate and Intune has nowhere to
hide the secret. So make the credential boring to steal:

- **Push-only.** It can write logs and read nothing.
- **Single-purpose and single-stack**, so revoking it affects one thing.
- **Rotatable on a schedule** you actually keep, since you must assume it is compromised eventually.
- `0600`, root-owned, and **overwritten before unlink** by the uninstaller.

State this plainly in your own documentation rather than letting a reader assume the token is
protected. A reference implementation that quietly embeds a credential teaches the wrong lesson.

If your backend supports it, prefer an ingest token scoped to a single label set. If you need
something stronger, the honest answer is that the credential has to come from somewhere other than an
Intune script - a configuration profile with a managed keychain item, or a bootstrap that exchanges a
device identity for a short-lived token. Both are more moving parts than most fleets want for a log
push.
