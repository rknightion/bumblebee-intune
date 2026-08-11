---
title: Dashboards
description: Recording rules for the aggregate panels, the two ways they silently under-report, and what to put on the dashboard.
---

# Dashboards

## Aggregate with recording rules

The `package` stream is the expensive one - [hundreds of megabytes a day from a single
host](telemetry.md#what-this-costs) - and its size tracks your busiest engineer's dependency caches,
not your fleet size. Every dashboard render that aggregates it re-scans the lot.

Move the aggregation server-side into recording rules and let the panels read cheap pre-aggregated
series.

**Keep per-package detail in the log store.** Tables that show `package_name`, `version` and
`source_file` are exactly the fields that must never become recording-rule labels; query those
directly over a narrow window.

## The window must equal the evaluation interval

```yaml
name: bumblebee-inventory
interval: 5m
rules:
  - record: bumblebee:packages_by_ecosystem:count5m
    expr: |
      sum by (host, profile, ecosystem) (count_over_time(
        {source="bumblebee", record_type="package", mode="inventory"}
        | json ecosystem="ecosystem" [5m] offset 10m))
```

A `[5m]` range on a 5m interval means each evaluation scans only the five minutes it has not seen
before.

!!! danger "A 24h window is far worse than the problem it replaces"

    The intuitive rule - a `[24h]` window "to cover the daily inventory run" - re-scans a full day of
    logs **288 times a day** at a 5m interval. That is roughly **70 GB/day of query**, against a
    handful of dashboard renders. Non-overlapping windows are the entire point.

Because inventory runs daily, most buckets are empty and only the bucket containing the push carries
values. Query from panels with an outer window:

```promql
max_over_time(bumblebee:packages_by_ecosystem:count5m[25h])
```

`max_over_time`, not `sum` - the recorded series carries one sample per push, so summing across a
range double-counts when two runs fall inside it.

## The ingest race

`offset 10m` in that rule is load-bearing, and this is the subtlest failure in the whole deployment.

A rule whose window equals its interval evaluates a window that is **still being written**. It
samples whatever fraction of the push has landed and never revisits, because the next evaluation has
moved on and the push's timestamps are behind it forever.

!!! danger "Under-reports by 5x on exactly the host the aggregation exists for"

    | host | push size | recorded | ratio |
    | --- | --- | --- | --- |
    | small host | 1,611 | 1,611 | exact |
    | busiest host | 216,443 | 39,456 | **18%** |

    Five ecosystems were **absent entirely** from the recorded sample, because they are written late
    in the stream. Replaying the identical query by hand over the same window returns the full
    216,443 - the query was never wrong, only its timing.

    Nothing errors. The ruler group stays healthy and the dashboard renders a confident number.

    With `offset 10m`: **216,442 raw, 216,442 recorded.** Exact.

The offset keeps windows non-overlapping - each evaluation still advances by the interval - so it
costs nothing. It just reads behind the write head.

## Verifying recording rules

Four checks, in order. The first three are the ones usually skipped.

1. **Compare the recorded value to the raw source**, on your **largest** host. Series existing is not
   evidence of anything.
2. **Account for every zero.** A rule that is correctly silent and a rule that is broken are
   indistinguishable from the metric. Check each empty series against the data it should be reading.
3. **Confirm the panels actually use the rules.** See below.
4. Confirm cardinality is what you expected.

!!! warning "Recording rules are not retrospective"

    A new or edited rule only sees data pushed **after** it exists, so a metric fed by a daily job
    produces nothing until the next run - and editing a rule resets it. Expect a blank dashboard for
    a full cycle, and shorten the loop by kicking the daemon rather than waiting:

    ```sh
    sudo launchctl kickstart system/com.bumblebee.inventory-baseline
    ```

## A recording rule nothing queries is not an optimisation

Writing the rules and repointing the panels is **one change**. Half of it does nothing.

!!! danger "Both halves report success independently"

    Rules can be applied, healthy and correct while every panel still queries the raw stream - so the
    full-scan the rules existed to remove carries on, and nothing indicates it. The ruler is green
    because the rules evaluate; the dashboard is green because the queries work.

    The acceptance test is a query against the **dashboard JSON**, not a healthy ruler group:

    ```sh
    jq -r '.. | .expr? // empty' dashboard.json | grep 'record_type="package"'
    ```

## Put every template variable in the `by` clause

If the dashboard exposes a `$profile` variable, the recording rule must keep the `profile` label.

A rule that drops it makes the converted panel silently ignore the filter: the panel renders, returns
numbers, and answers a different question from the one the user asked. **A recording rule that cannot
answer the dashboard's own filters does not replace the raw query, it only looks like it does.**

## Cardinality

Safe: `ecosystem`, `package_manager`, `root_kind`, `source_type`, `install_scope`,
`direct_dependency`, `profile`, `host`. Worst case is a few hundred series.

Never: `package_name`, `source_file`, `version`. A single host emits tens of thousands of distinct
packages - that is a cardinality incident, not a recording rule.

## What to show

Five groups, roughly in the order an operator needs them:

| Tab | Answers |
| --- | --- |
| **Overview** | Is the fleet covered? Hosts reporting, scan freshness, findings by tier |
| **Security** | What matched, at which tier, on which host - with `source_file` and `project_path` |
| **Scan health** | Truncation rate, duration, files considered, scanner version by host |
| **Inventory** | Packages by ecosystem / manager / root kind, install-hook packages, MCP servers, editor extensions |
| **Pipeline health** | Catalog age per source, entries, schema version and conflicts, FDA state |

Two panels earn their place more than the rest:

- **Findings by tier**, using the same expressions as the alerts, so the dashboard and the pager
  cannot disagree.
- **Install-hook packages**, filtered to `install`/`preinstall`/`postinstall` rather than all
  lifecycle scripts. [1,071 versus 39](telemetry.md#is-the-inventory-worth-keeping) is the difference
  between a number and a list.

## If your dashboards are Git-Sync managed

!!! warning "An API push is silently reverted"

    With Grafana Git Sync enabled, `resources pull dashboards` omits Git-Sync-managed dashboards
    entirely and an API push is reverted on the next reconcile. Edit the file in the Git-Sync repo
    and push; reconcile takes 15 to 50 s.

    The reverted push reports success, so the only symptom is a change that quietly disappears a
    minute later.
