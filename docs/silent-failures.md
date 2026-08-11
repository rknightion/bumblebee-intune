---
title: Silent failure modes
description: Every way this deployment breaks without erroring, what it looks like from the outside, and how to detect it.
---

# Silent failure modes

The failure modes worth designing against are the ones that produce a green result. This page is the
checklist: what breaks quietly, and the specific control that catches each one.

Every entry has been observed on a live fleet rather than derived from reading the source.

## The table

| Failure | What it looks like | How to detect it |
| --- | --- | --- |
| Scan hits `--max-duration` | `status: "complete"`, fewer findings | Join on `timed_out`; alert on the truncation *rate* |
| Catalog directory mixes schema versions | Exit 2, **no records at all** | Alert on absence of a scan; emit `catalog_effective` yourself |
| `--findings-only` with no catalog | Exit 2, no records | Same |
| No usable catalog | `findings_emitted: 0`, `status: complete` | `catalog_effective == 0` from the wrapper |
| Full Disk Access lost | Fewer `files_considered`, no error | Explicit read probe against a TCC-protected path |
| Binary version bumped, PPPC not redeployed | FDA silently revoked | Same probe; alert on it |
| Host running an old install script | Healthy scans, missing telemetry fields | Set difference: scanning hosts minus hosts reporting health |
| Recording rule races the ingest | Metric under-reports by ~5x | Compare recorded value to raw on your *biggest* host |
| Recording rule nothing queries | Rules healthy, dashboard still expensive | Grep the dashboard JSON for the raw stream |
| Tier allowlist drifts between systems | Same finding, two different severities | Assert the lists match, in CI |
| Alert rule created on the wrong API path | Reconciler reports success, changes never land | Treat a failed push as a build failure |
| Intune `runState` | Reports `unknown` even on success | Ignore it; use the custom attribute |

The rest of this page explains the non-obvious ones.

## A truncated scan reports success

When a scan hits `--max-duration`, it stops walking and emits its summary. That summary says:

```json
{ "status": "complete", "timed_out": true }
```

A truncated scan emits **fewer findings** than a full one, because it did not finish looking. Every
other signal stays green: the record arrives on time, the status says complete, the finding count is
merely lower. A host with a real finding beyond the cut-off is indistinguishable from a clean host
unless you specifically join on `timed_out`.

**Treat `status` as "the scan ended", never as "the scan finished".** Join on `timed_out` in every
query that draws a conclusion from a finding count, and alert on the truncation **rate**, not a
count.

!!! info "Why a count threshold does not work"

    On an un-split scan schedule, **23% of scans truncate** (0.23 baseline profile, 0.21 project)  - 
    roughly a quarter of scans silently under-reporting. A "more than 10 timeouts in 48h" rule never
    fires on that, because it is designed to catch a profile that never finishes at all.

    A rate threshold of 0.15 catches it. See [the daemon split](architecture.md) for the fix that
    takes the steady-state rate to ~0 and turns this alert into a regression detector.

## The catalog failures produce nothing at all

Bumblebee refuses a catalog directory whose files disagree on `schema_version`, and it exits **2
before scanning**. Not a warning, not a partial load. No package records, no findings, and no
`scan_summary` either.

That last part is what makes it dangerous. Every other failure at least leaves a record saying the
scan happened. This one leaves nothing, so a dashboard filtered to "scans that ran" simply shows one
fewer host, and a laptop that is switched off looks the same.

`--findings-only` with no catalog at all behaves the same way.

The mitigation is two-part, and both halves are needed:

1. Assemble a schema-coherent catalog set on the endpoint, and prove the binary can load it with a
   throwaway probe scan before any real scan depends on it.
2. Have the wrapper emit its own health record - `catalog_effective`, file count, entry count,
   schema version, whether a conflict was seen - because the scanner's own output cannot tell you
   this.

See [Exposure catalogs](exposure-catalogs.md).

## Missing Full Disk Access shrinks the scan without erroring

The walker emits a `debug` diagnostic for paths it cannot read and carries on. `files_considered`
drops. Nothing goes red.

Two rules follow, and both are load-bearing:

- **A root that enumerates is not a root that was read.** `scan_summary.roots` lists what was
  *attempted*. Nothing in the record separates "walked it, found nothing" from "could not open a
  single file".
- **Rule out permissions before reporting an extractor as broken.** Absent data and a broken
  extractor are the same shape from the outside, and only one of them is upstream's problem.

!!! info "Measured: browser extensions are entirely invisible without the grant"

    Three `browser_extension_root` paths appear in `scan_summary.roots` on every scan and produce
    **zero** package records for 30 days - then **22** within seconds of the FDA grant landing.
    Browser extension directories live under `~/Library/Application Support`, which is TCC-protected.

    Read as a scanner bug, this is a plausible upstream issue. It is a permissions problem, and the
    record shape gives you nothing to tell them apart.

See [Full Disk Access](full-disk-access.md) for the probe that detects this.

## Alert coverage silently becomes "whichever hosts are current"

Any alert that derives its series from a field is blind to hosts that do not emit that field. Those
hosts produce no series, so they are not evaluated. They do not alert. They do not show as NoData
either, because the healthy hosts keep the rule green.

The cause is ordinary and will recur anywhere: Intune shell scripts re-run on their **own schedule**
(`P1D` here), so a host can be a day behind any script content change with nothing reporting the
skew, and a laptop asleep at its slot is longer still.

!!! danger "A stale host drops out of your alerts without appearing anywhere"

    A host running a **six-week-old installer** reports completely normal scans: `status=complete`,
    findings reported, catalog age fresh. But its wrapper predates the health fields, so it emits no
    series for the catalog and FDA rules - and those rules stay green on the strength of the other
    hosts.

    Fleet coverage silently becomes "whichever hosts happen to be current". There is no view in
    Intune or Grafana that shows this; you have to ask the question directly.

`unless` is a set difference - hosts that are scanning, minus hosts reporting usable health:

```logql
count by (host) (count_over_time({source="bumblebee", record_type="scan_summary"} [25h]))
unless
count by (host) (count_over_time({source="bumblebee", record_type="catalog_health"}
                 | json ce="catalog_effective" | ce=~".+" [25h]))
```

**Absence has to be alerted on separately, by a query the healthy hosts cannot satisfy.**

## Verify aggregations against your biggest producer

When you check a recording rule, an export, or any other aggregation, **compare the recorded value to
the raw source on your highest-volume host** - not on a convenient one, and not by confirming that
series merely exist.

!!! danger "The same rule is exact on a small host and wrong by 5x on a large one"

    | host | push size | recorded | ratio |
    | --- | --- | --- | --- |
    | small host | 1,611 | 1,611 | exact |
    | busiest host | 216,443 | 39,456 | **18%** |

    Same rule, same evaluation, same five-minute window. Verifying against the small host passes
    cleanly and proves nothing, because volume is exactly the variable that breaks it.

    Mechanism and fix in [Dashboards](dashboards.md#the-ingest-race).

Account for every zero as well. A rule that is **correctly** silent and a rule that is broken look
identical from the metric alone, so check each empty series against the data it should be reading.

## Design rules that fall out of this

1. **Emit your own health record.** The scanner tells you what it found. It cannot tell you what it
   was prevented from looking at, or whether it had a catalog to look with. That is the wrapper's
   job, and it is where most of the value in this deployment sits.
2. **Prove capability, do not infer it.** Do not test Full Disk Access with `test -r` - root passes
   that on mode bits alone. Read a real file and count it. Do not assume a catalog set loads; run a
   throwaway scan against it first.
3. **Alert on absence separately.** Every field-derived alert needs a companion that fires when the
   field stops arriving.
4. **A 2xx proves nothing.** This applies to Graph, to Loki, and to the scanner's own exit code.
   Read back what actually landed and count it.
5. **Carry the deployed version in the telemetry.** Version skew is invisible from the Intune side
   and explains a surprising share of "impossible" results.
