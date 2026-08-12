---
title: Scan architecture
description: Profiles, the four-daemon split, and why a single scan schedule cannot serve both detection and inventory.
---

# Scan architecture

## Profiles are not interchangeable

Bumblebee ships three profiles. Take the upstream semantics literally rather than treating them as
speed settings:

| Profile | Covers | Use for |
| --- | --- | --- |
| `baseline` | Standard per-user package roots, dependency caches, system package managers | The default fleet-wide scan |
| `project` | Repository working trees you point it at | Developer machines with a known repo root |
| `deep` | Everything reachable | Investigation, on demand |

`deep` is deliberately not scheduled here. It is the right tool when you are chasing something and
the wrong tool to run on a laptop four times a day.

`--all-users` cannot be combined with `--root`. That single constraint shapes the whole design: the
baseline daemons use `--all-users` and find their own roots, while the project daemons pass explicit
roots and therefore cannot use it.

## Why one schedule does not work

The obvious deployment is one scan on a timer. It fails in a specific way.

Detection wants to be **frequent**: a malicious package installed at 09:00 should not wait until
tomorrow. Inventory wants to be **complete**: the point is to know everything that is present,
including the large read-only dependency caches.

Those two goals fight over the same time budget, and on real developer machines the caches win.

!!! danger "A single schedule silently under-reports, it does not visibly fail"

    On a combined schedule, the frequent scan spends **~440s of a 600s budget** walking
    `~/go/pkg/mod` and times out roughly **29%** of the time. Because [a truncated scan still reports
    `status: "complete"`](silent-failures.md#a-truncated-scan-reports-success), that is a quarter of
    scans quietly emitting fewer findings, with nothing to indicate it.

    Raising `--max-duration` does not fix this. It moves the cliff, and the caches grow.

## The split

Four daemons, two shapes:

```
findings/baseline    every 4h*  --findings-only, RunAtLoad
                                --exclude pkg/mod --exclude .cargo/registry
                                max-duration 10m

findings/project     every 4h*  --findings-only, RunAtLoad
                                explicit repo roots
                                max-duration 15m

inventory/baseline   daily      full package inventory, NO exclusions
                                max-duration 45m

inventory/project    daily      full package inventory, explicit repo roots
                                max-duration 30m
```

The findings pass is fast and bounded. It excludes the big read-only dependency caches, so it
finishes in seconds and detection stays frequent on the roots that carry executable, actually
installed code.

The inventory pass covers the **same roots with no exclusions**, on a generous budget, once a day. So
cache exposure is still detected - at a daily cadence rather than four-hourly, without starving the
fast pass.

The trade is explicit and worth stating in your own runbook: **a malicious package that exists only
inside a read-only dependency cache is detected within 24 hours, not within 4.** That is the right
call, because a package sitting in a Go module cache is not installed, is not executed, and cannot be
removed persistently anyway - see [the finding tiers](alerting.md#classify-by-what-is-installed).

After the split, the expected steady-state truncation rate is ~0, which turns the timeout alert from
noise into a regression detector with real headroom.

### \* The findings interval is per-device

4h is the default, not a constant. Build machines and CI runners compete with the scanner for CPU and
disk, so they can be dropped to daily by shipping a `macOSCustomAppConfiguration` carrying
`BumblebeeFindingsIntervalSeconds` and scoping it with an assignment filter. The installer reads it
from `/Library/Managed Preferences/<your-domain>` and falls back to 14400 when it is absent or
non-numeric.

It has to arrive as a *managed preference* because **you cannot ask a Mac what Intune thinks it is**:
`profiles show -type enrollment` returns `(null)` on an ADE-enrolled device, so the enrolment profile
name - the obvious thing to branch on - is not readable from a script at all. Intune evaluates the
filter server-side and the device just reads the answer.

## LaunchDaemons, not agents

Run as root LaunchDaemons in the system domain:

```xml
<key>UserName</key><string>root</string>
<key>StartInterval</key><integer>14400</integer>
<key>RunAtLoad</key><true/>
<key>ProcessType</key><string>Background</string>
<key>LowPriorityIO</key><true/>
<key>Nice</key><integer>10</integer>
```

**`RunAtLoad` is load-bearing, and more so once you raise the interval.** The installer boots out and
re-bootstraps all four daemons on every run, and Intune runs it daily - so each reload resets the
`StartInterval` countdown. With `RunAtLoad=no`, a daemon whose interval is at or above the installer
cadence can be starved indefinitely and simply never scan. `RunAtLoad` is what guarantees roughly one
scan per installer cycle whatever the interval is.

- **root + `--all-users`** is what makes this a fleet control rather than a per-user tool. A LaunchAgent
  only runs when someone is logged in and only sees that user.
- **`RunAtLoad` on the findings daemons** means a laptop that has been shut for a week scans on wake
  rather than waiting up to four hours.
- **`ProcessType Background`, `LowPriorityIO`, `Nice 10`** matter more than they look. This is
  somebody's laptop, and a scanner that makes the machine feel slow gets uninstalled by whatever
  means the user can find.
- **`StartInterval`, not `StartCalendarInterval`.** Calendar scheduling on laptops fires a thundering
  herd at 09:00 and misses machines that were asleep. Interval scheduling spreads naturally.

Root is necessary but **not sufficient** for the TCC-protected paths. See [Full Disk
Access](full-disk-access.md).

## The wrapper

The daemons do not invoke the scanner directly. They call `bumblebee-run`, which:

1. Selects a schema-coherent catalog set and proves it loads ([catalogs](exposure-catalogs.md))
2. Probes Full Disk Access with a real read ([FDA](full-disk-access.md))
3. Runs the scan
4. Writes a per-`(mode, profile)` summary for the Intune custom attribute
5. Emits a `catalog_health` record carrying everything the scanner cannot report about itself
6. Ships the NDJSON to Loki and prunes it

Step 5 is where most of the value is. The scanner reports what it found; the wrapper reports whether
it was in a position to find anything.

**Give every daemon its own summary file**, keyed `${MODE}_${PROFILE}.json`, and have the Intune
attribute read all of them and report the worst case.

!!! danger "One shared summary file reports whichever daemon finished last"

    A single `last_summary.json` written by all four daemons makes the device attribute a race. On a
    host with a live finding in one profile, it reads **clean** whenever a different profile happens
    to finish after it - and the attribute is only re-evaluated daily, so the wrong answer sticks.

Source: [`src/endpoint/bumblebee-run.sh`](https://github.com/rknightion/bumblebee-intune/blob/main/src/endpoint/bumblebee-run.sh).

## Concurrency

No locking between daemons, deliberately. Each stages its catalog set into its own
`catalog/<mode>_<profile>/` directory via an atomic `rename`, so concurrent runs cannot interfere. The
shared upstream catalog cache is refreshed by atomic rename too, so the worst case is
last-writer-wins with a valid file.

The daemons can and do overlap - a daily inventory pass runs long enough that a four-hourly findings
scan will start underneath it. That is fine, and the I/O settings above are what keep it from being
noticeable.
