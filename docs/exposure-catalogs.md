---
title: Exposure catalogs
description: Schema coupling between the scanner and its catalogs, how to assemble a coherent set on the endpoint, and the failure that emits no records at all.
---

# Exposure catalogs

Findings come from matching installed packages against catalogs of known-malicious releases. Getting
the catalogs onto the endpoint is the easy part. Getting a **coherent** set onto the endpoint is
where this bites.

## The failure: mixed schema versions emit nothing

Bumblebee's catalog loader requires every file in the directory to declare the **same**
`schema_version`. Mix them and it exits **2 before scanning**.

Live-proved on v0.1.2:

| Catalog directory | Result |
| --- | --- |
| All `0.1.0` | scans normally |
| All `0.2.0` | `exit 2` - this release hard-rejects `0.2.0` |
| Mixed `0.1.0` + `0.2.0` | `exit 2`, **no output file created at all** |

That third row is the dangerous one. There are no package records, no findings, and **no
`scan_summary`** either. Every other failure in this deployment at least leaves a record saying the
scan happened. This one leaves nothing, so a dashboard filtered to "scans that ran" just shows one
fewer host - the same shape as a laptop that is switched off.

`--findings-only` with no catalog at all fails identically.

## Why a fleet drifts into it

The release tarball ships its own `threat_intel/` catalogs, matched to that binary. You will also want
external catalogs on a faster refresh cycle, because the whole point is to learn about a malicious
release quickly.

Those two move independently:

- **Upstream** bumps the bundled catalogs when it changes the schema, in lockstep with the binary.
- **Your published catalogs** are on their own cadence, at whatever schema you last generated.

So an upstream release that bumps `threat_intel/` to a new schema turns "copy every valid catalog
file into one directory" into an outage, on the next installer cycle, on every host at once. Nothing
you did changed; the version you pinned changed underneath the assumption.

!!! danger "Ordering matters when you bump your own catalog schema"

    Publish a new schema **only after** a scanner release supporting it exists *and* your pinned
    `BUMBLEBEE_VERSION` has rolled out to the fleet. Doing it in the other order kills detection
    everywhere, silently. Enforce it in your catalog repo's CI: assemble the real directory and run
    the actual pinned binary against it.

## Coherent selection on the endpoint

Rather than trusting that the sources agree, group the candidates by `schema_version` and stage
exactly one group:

```
for each candidate catalog file:
    parse it, read schema_version, count entries
    discard anything unparseable or unsupported

group by schema_version
rank groups: newest schema first, then most entries
stage group[rank] into catalog/<mode>_<profile>/ via atomic rename
```

Then **prove it loads** before anything depends on it:

```sh
"$BIN" scan --profile baseline --root "$PROBE_DIR" --max-duration 5s \
       --exposure-catalog "$STAGED" --output file --output-file /dev/null \
  || try_next_rank
```

A throwaway scan against an empty directory. It costs a few seconds and it is the difference between
finding out now and finding out from an absence of findings later.

If every rank fails, run **without** a catalog and say so, rather than not running at all. Drop both
the catalog argument and `--findings-only`, because `--findings-only` with no catalog is itself an
exit 2.

Source:
[`src/endpoint/catalog-select.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/endpoint/catalog-select.py),
with [tests](https://github.com/rknightion/bumblebee-intune/blob/main/src/endpoint/test_catalog_select.py).
The test worth reading is `test_mixed_schemas_never_staged_together`.

## Emit catalog health yourself

The scanner cannot tell you it had nothing to look with. A catalog-less scan reports
`status: complete`, `timed_out: false`, `findings_emitted: 0` - which is character-for-character what
a clean host reports.

So the wrapper emits its own record:

```json
{
  "record_type": "catalog_health",
  "catalog_effective": 1,
  "catalog_files": 14,
  "catalog_entries": 33789,
  "catalog_schema_version": "0.1.0",
  "catalog_files_rejected": 0,
  "catalog_schema_conflict": false,
  "catalog_age_hours": 2.9,
  "catalog_age_by_source": { "osv": 2.9, "datadog": 5.5, "ghsa": 5.5 },
  "catalog_sources": 3,
  "fda": 1,
  "fda_probe_files": 1169
}
```

`catalog_effective: 0` is the page-worthy one: detection is entirely off on that host while every
other signal stays green. `catalog_schema_conflict` tells you *why* without an SSH session.

Per-source ages matter because sources fail independently. One stale feed among three is a partial
loss of coverage that a single aggregate age would hide.

## Staleness is its own alert

A catalog that stops refreshing is a **blind detector**: scans keep succeeding and keep reporting
clean against a frozen list, and nothing else in the pipeline goes red.

Alert on `max by (host) (catalog_age_hours) > 18` when your generation cadence is 4-hourly. Two
causes dominate:

1. Your catalog generation stopped. If it runs on GitHub Actions, note that **scheduled workflows are
   auto-disabled after 60 days of repository inactivity** - a catalog repo that is working correctly
   is exactly the repo nobody commits to.
2. Endpoints are failing to fetch and falling back to their last-good cache. Which is the correct
   behaviour, and is why it is silent.

## Fetching, and last-good caching

Refresh before each scan, but never let a fetch failure stop a scan:

- Download to a temp file, validate it parses, then atomically `rename` into place.
- On any failure, keep the previous file and carry on. A network blip must not disable detection.
- Track age per source and emit it, so "we have been running on cache for six hours" is visible.

The corollary: **a host can run indefinitely on a stale cache and look perfectly healthy.** The
staleness alert above is the only thing that catches it.

## Check that you deploy every catalog you generate

Cross-check the asset list your CI publishes against the list your endpoint fetches. There is no
error when they disagree - the scan runs happily against whatever subset arrived.

!!! warning "Published and deployed are different lists"

    A catalog pipeline that builds, validates and publishes three catalogs while the endpoint fetches
    one produces a scan that works, reports clean, and has two-thirds less coverage than you think.
    The gap is invisible from both ends: CI is green, and the endpoint's scan succeeds.

    Emitting `catalog_files` and `catalog_entries` is what makes it checkable at a glance. Fixing one
    such gap moved a fleet from 12 files / 30,511 entries to 14 / 33,789 - a number nobody could see
    beforehand.

The catalogs most easily missed are the supplementary ones: editor-extension advisories, and malware
advisories whose identifiers the primary source does not alias.
