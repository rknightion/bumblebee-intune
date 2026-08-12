---
title: bumblebee-intune
description: A reference deployment of the Bumblebee supply-chain scanner to macOS endpoints via Microsoft Intune, with the silent failure modes documented.
---

# Deploying Bumblebee with Microsoft Intune

[Bumblebee](https://github.com/perplexityai/bumblebee) is a read-only supply-chain scanner. It walks
an endpoint's package roots, inventories what is installed, and matches it against catalogs of
known-malicious releases. Pointing it at a laptop is easy. Running it across a managed fleet so that
a clean result actually means something is not.

This is a working reference for the second part: the deployment shape, the endpoint wrapper, the TCC
grant, the catalog handling, the telemetry, and the alerts. Every trap documented here was hit on a
live fleet, and most of them are silent.

## The problem this solves

The scanner is well behaved. It is the *deployment* that lies to you, because almost every way this
breaks produces a result that looks like good news:

- A scan that hits its deadline stops early and still reports `status: "complete"`.
- A scan with no usable exposure catalog reports `findings_emitted: 0`, exactly like a clean host.
- A scan without Full Disk Access silently skips `~/Documents`, `~/Desktop`, `~/Downloads` and every
  browser extension, and reports success over the smaller tree.
- A host running a six-week-old copy of your install script keeps reporting healthy scans while
  quietly falling out of the alerts that were supposed to watch it.

None of these produce an error. None turn a dashboard red. A fleet in any of those states looks
identical to a fleet that is genuinely clean, which is the worst property a security control can
have.

[The silent failure catalogue](silent-failures.md) is the page to read if you read only one.

## What is here

| | |
| --- | --- |
| [Getting started](getting-started.md) | Prerequisites and the shortest path to a working deployment |
| [Scan architecture](architecture.md) | Profiles, the four-daemon split, and why one scan schedule does not work |
| [Installation and upgrades](installation.md) | Pinned, checksum-verified acquisition; idempotent upgrades; uninstall |
| [Full Disk Access](full-disk-access.md) | The PPPC profile, the cdhash problem, and why you cannot test it over SSH |
| [Exposure catalogs](exposure-catalogs.md) | Schema coupling, coherent selection, and the failure that emits nothing at all |
| [Telemetry](telemetry.md) | Record shapes, shipping to Loki, and what this costs |
| [Alerting](alerting.md) | Classifying findings by what is actually installed, and seven rules |
| [Dashboards](dashboards.md) | Recording rules, and the two ways they silently under-report |
| [Deployment](deployment.md) | Intune mechanics: scripts, custom attributes, assignment, rollout |
| [Silent failure modes](silent-failures.md) | Everything that breaks without erroring, in one table |
| [Samples](samples.md) | The complete source tree in `src/` |

## The shape of the deployment

```
Intune deviceShellScript (root, daily)
  └── downloads pinned bumblebee release, verifies SHA256
      writes /usr/local/bin/bumblebee-run + helpers
      loads four LaunchDaemons
                │
                ├── findings/baseline    every 4h   fast, bounded, no dependency caches
                ├── findings/project     every 4h   your repo roots
                │                        (4h is the default; per-device override
                │                         to daily for build machines - see
                │                         architecture.md)
                ├── inventory/baseline   daily      everything, including the caches
                └── inventory/project    daily      your repo roots, full inventory
                                │
                                ├── NDJSON ──► Grafana Loki ──► alerts + dashboards
                                └── summary ──► Intune custom attribute
```

Two independent reporting paths, deliberately. The Intune custom attribute gives you device-level
state in the console without any external dependency; Loki gives you fleet-level detection, history
and alerting. Either one alone leaves a gap.

## Scope and assumptions

macOS endpoints managed by Microsoft Intune, running the scanner as root via LaunchDaemons, shipping
NDJSON to Grafana Loki. Grafana specifics live in their own pages and are straightforward to swap for
another backend; the endpoint and Intune halves are not backend-specific.

Everything here was measured against **bumblebee v0.1.2**. Behaviour that a later release may change
is flagged where it matters.

!!! warning "This is a reference, not a product"

    Copy it, read the comments, and change the parts that do not fit. The credentials, scan roots and
    thresholds are all deliberately placeholders. In particular, read
    [the note on the Loki token](telemetry.md#the-credential-problem) before you deploy: Intune has no
    secret-vaulting for script content.

## Source

[rknightion/bumblebee-intune](https://github.com/rknightion/bumblebee-intune). Apache-2.0.
