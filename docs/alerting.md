---
title: Alerting
description: Classifying findings by what is actually installed rather than by catalog severity, and the seven rules that make up a working alert set.
---

# Alerting

## Classify by what is installed

The most useful judgement in the whole deployment, and the one that decides whether anyone still
reads the alerts in a month.

`source_type` distinguishes a package that is **installed on disk** from one merely **declared in a
lockfile**. Combined with `root_kind`, that gives three tiers with genuinely different responses:

| Tier | Shape | Meaning | Action |
| --- | --- | --- | --- |
| 1 | `source_type` **not** in the declared-only list | Unpacked in `node_modules`, `site-packages`, a Homebrew cellar, an editor extension. The code is present and reachable. | **Page** |
| 2 | declared-only **and** `root_kind = project_root` | Named in a lockfile in a repo you own. Not installed yet, but a build would install it. | Warn |
| 3 | declared-only **and** `root_kind != project_root` | Named in a lockfile vendored read-only inside a third-party dependency cache. | Dashboard only |

Tier 3 is the one people get wrong, and it is the bulk of the volume. A `pnpm-lock.yaml` vendored
inside a Go module under `~/go/pkg/mod` names packages that are not installed, will never be
installed by anything on the machine, and **come straight back if you delete them**, because the file
is part of a read-only module cache. Paging on those trains people to ignore the alert.

The declared-only list:

```
npm-lockfile   pnpm-lockfile   yarn-lockfile   bun-lockfile
rubygems-gemfile-lock          composer-lock
go-mod         go-sum          skill-lock      mcp-config
```

!!! tip "Make it an allowlist, not a denylist"

    Match tier 1 as "**not** in this list". Then any `source_type` a future release adds defaults to
    **critical** rather than to silence. An omission produces a false page, which someone will
    investigate; the inverse produces silence, which nobody will.

### Is a lockfile-only match ever exploitable?

Worth answering explicitly, because it decides whether tier 3 is "ignore" or "ignore for now".

A malicious package *declared* inside a read-only module cache is not exploitable: nothing is
installed, no `node_modules` exists, the tree is mode-0444, and it returns on the next dependency
fetch. Removing it achieves nothing.

But a malicious release *installed* inside a vendored tree is real exposure. The distinction that
matters is **lockfile versus installed**, not first-party versus third-party. Tier 3 is not "this
came from someone else's code, therefore fine" - it is "nothing was installed, therefore nothing runs".

### Verify the list against live data

```logql
sum by (source_type) (count_over_time({source="bumblebee", record_type="package"}
  | json source_type="source_type" [1h]))
```

Do not write this list from memory. Enumerate it from live data and re-check it after every scanner
upgrade.

!!! warning "An omission here pages critical on a whole ecosystem"

    A list missing `composer-lock` grades every PHP lockfile match as INSTALLED - on one fleet, 14
    live packages that would each have paged at critical.

    This is the direction the allowlist design chooses on purpose. An omission is loud and gets
    investigated; the inverse is silent and does not.

## Guard the list, because it lives in three places

The same alternation ends up in your alert rules (what pages), your recording rules (what the metrics
count), and your dashboard JSON (what a human reads). Those three cannot see each other.

When they drift, **the same finding sits in different tiers depending on where you look**, and
nothing errors.

!!! danger "Fixing the list in two places out of three is the normal outcome"

    Correcting the alert rules and the recording rules while missing the dashboard leaves the pager
    and the dashboard disagreeing about the tier of the same finding, indefinitely, with no signal at
    all. Both are internally consistent; neither can see the other.

Ship a check that asserts they match, run it before deploying, and **negative-test it** - a
consistency check that has never been seen to fail is indistinguishable from one that always passes.

Source:
[`src/grafana/check-tier-consistency.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/grafana/check-tier-consistency.py).

Better still, generate all three from one source. The check is the pragmatic version.

## The seven rules

| Rule | Fires when | Severity |
| --- | --- | --- |
| `BumblebeeFinding` | A tier-1 (installed) match exists | critical |
| `BumblebeeFindingDeclared` | A tier-2 match in a repo you own | warning |
| `BumblebeeCatalogIneffective` | `catalog_effective == 0` - detection is off | critical |
| `BumblebeeCatalogStale` | `catalog_age_hours > 18` - blind detector | warning |
| `BumblebeeFullDiskAccessMissing` | `fda < 1` - coverage silently shrunk | warning |
| `BumblebeeScanTimingOut` | >15% of scans truncating over 48h | warning |
| `BumblebeeHealthTelemetryMissing` | Host scanning but not reporting health | warning |

Three of those seven alert on the **monitoring**, not on findings. That ratio is not an accident: on
this deployment the likeliest bad outcome is not a missed malicious package, it is a fleet that
stopped looking and did not mention it.

Source:
[`src/grafana/alert-rules.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/grafana/alert-rules.py).

## Rules worth explaining

### `BumblebeeCatalogIneffective`

The failure mode that looks exactly like good news. A host scanning with no usable catalog emits
`scan_summary` records indistinguishable from a clean host - `status: complete`, `timed_out: false`,
`findings_emitted: 0` - so without this rule, total loss of detection reads as a quiet week.

```logql
min by (host) (last_over_time({source="bumblebee", record_type="catalog_health"}
  | json catalog_effective="catalog_effective" | unwrap catalog_effective [25h]))
```

### `BumblebeeScanTimingOut`

A **rate**, gated on having enough scans for a rate to mean anything:

```logql
(sum by (host, profile) (count_over_time({...record_type="scan_summary"}
   | json timed_out="timed_out" | timed_out="true" [48h]))
 /
 sum by (host, profile) (count_over_time({...record_type="scan_summary"} [48h])))
and
(sum by (host, profile) (count_over_time({...record_type="scan_summary"} [48h])) >= 8)
```

A count threshold only catches a profile that never finishes at all. The measured baseline before the
[daemon split](architecture.md) was ~0.23 - roughly a quarter of scans silently under-reporting  - 
which a "more than 10 in 48h" rule never tripped.

The `>= 8` floor stops a laptop that was mostly switched off from tripping this on two unlucky runs.

### `BumblebeeHealthTelemetryMissing`

The companion rule that makes the other three trustworthy. Covered in [silent
failures](silent-failures.md#alert-coverage-silently-becomes-whichever-hosts-are-current); the short
version is that any rule deriving series from a field is blind to hosts that do not emit it, and
those hosts do not even show as NoData.

## Two settings that matter on laptops

**`NoDataState: OK` on every rule.** These are laptops. A machine that is switched off, on a plane, or
in a drawer must not page. This is also why the health-telemetry rule exists - with NoData suppressed
everywhere, absence needs a rule whose *presence* condition catches it.

**A lookback longer than the scan interval.** With four-hourly scans, a 12h lookback keeps
finding-based alerts steady instead of flapping once per cycle. The cost is that resolution after
remediation takes up to 12h; say so in the alert description so nobody re-investigates a resolved
alert that has not cleared yet.

## Route on labels your policy tree actually matches

Check the live notification policy tree before choosing labels. A plausible-looking `team=security`
label that matches no route falls through to the default receiver, which is usually an inbox nobody
reads. Confirm the label you chose actually selects the route you meant.

## Write descriptions for the person woken at 3am

The alert description is the only thing a responder has. Include: what the failure actually means,
why nothing else went red, the likely causes in order of probability, the exact commands to run on
the host, and the query that verifies a fix.

That is verbose for a dashboard and correct for a pager. The most valuable line in most of them is
the one explaining *why the rest of the system still looks healthy*, because that is the fact that
otherwise costs an hour.
