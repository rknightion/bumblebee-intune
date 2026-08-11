---
title: Getting started
description: Prerequisites, the order to deploy things in, and how to prove the deployment is actually working.
---

# Getting started

## Prerequisites

| | |
| --- | --- |
| macOS endpoints enrolled in Intune | Supervised, with the MDM agent installed |
| Graph permissions | `DeviceManagementConfiguration.ReadWrite.All`, `DeviceManagementManagedDevices.Read.All` |
| A log store | Grafana Cloud Loki here; anything that accepts NDJSON over HTTP works |
| A push credential | Push-only, single-stack, rotatable - [read this first](telemetry.md#the-credential-problem) |
| `/usr/bin/python3` on endpoints | Ships with the Xcode Command Line Tools |

The endpoint scripts are POSIX `sh` and stdlib Python 3. No third-party runtime dependencies on the
endpoint, deliberately: anything you have to install first is another thing that can silently not be
installed.

## Decide these before you deploy

**Where do developers keep repositories?** The `project` profile needs explicit roots, and there is no
good fleet-wide default. A single shared convention (`~/repos`, `~/src`) is worth enforcing precisely
because it makes this line possible. Without one, either scan home directories and accept the cost, or
drive the roots from an assignment filter per team.

**Do you want package inventory at all?** It is the bulk of the volume by a wide margin. Findings
alone are tiny. See [what this costs](telemetry.md#what-this-costs) and decide deliberately rather
than by default.

**Which scan cadence?** The [four-daemon split](architecture.md) is a starting point, not a law. The
principle that matters is separating frequent-and-bounded detection from slow-and-complete inventory.

## Order of operations

The order is not arbitrary - the PPPC profile must exist before the binary it authorises.

1. **Fill in the placeholders** in `src/intune/installer.sh`: `LOKI_URL`, `LOKI_TOKEN`,
   `PROJECT_ROOTS`, and the catalog source.
2. **Deploy the PPPC profile** with `src/intune/deploy-pppc.py`. It derives the cdhash from the
   pinned version in the installer, so run it *after* step 1 and *before* step 3.
3. **Create the installer script** in Intune. Assign to All Devices.
4. **Create both custom attributes.** Assign to All Devices.
5. **Create the uninstaller.** Leave it unassigned.
6. **Apply the recording rules and alert rules** to your observability stack.
7. **Build the dashboard**, pointing the aggregate panels at the recording rules.

Steps 2 and 3 recur together on every scanner version bump. Wire them into one script if you can.

## Proving it works

Do not stop at "the script ran". Confirm each of these:

**The binary is staged and runs**

```sh
sudo /usr/local/libexec/bumblebee/bin/bumblebee version
sudo cat /var/db/bumblebee/install.version
```

**The daemons are loaded**

```sh
sudo launchctl list | grep bumblebee
```

**Full Disk Access is actually granted** - kick a daemon and read the resulting `catalog_health`
record. You want `fda: 1` with a **large** `fda_probe_files`.

```sh
sudo launchctl kickstart -k system/com.bumblebee.findings-baseline
```

!!! danger "Do not test this over SSH"

    TCC attributes access to the *responsible process*. Over SSH that is `sshd-keygen-wrapper`, which
    has no grant, so a working profile reports failure. Kick the daemon and read the telemetry - never
    run the binary by hand from an SSH session and draw a conclusion from it.

    Full explanation in [Full Disk Access](full-disk-access.md#you-cannot-test-a-pppc-grant-over-ssh).

**Detection is live, not just the scan** - `catalog_effective: 1`, with a plausible
`catalog_entries`. A scan with `catalog_effective: 0` reports exactly like a clean host.

**Both reporting paths work** - the Intune custom attribute shows a real value (not `pending_scan`,
once a scan has run), *and* records are arriving in your log store.

## First-week checklist

- [ ] Every enrolled Mac appears in the telemetry. Chase the ones that do not.
- [ ] `catalog_effective: 1` and `fda: 1` on **every** host, not just the ones you tested.
- [ ] Truncation rate is near zero. If not, raise `--max-duration` or narrow the roots.
- [ ] Ingest volume matches your estimate. Check this before the month ends.
- [ ] All seven alerts evaluate green, and each has at least one host instance where it should.
- [ ] The tier consistency check passes and has been seen to **fail** on a deliberate change.
- [ ] Recording rules match the raw stream on your **busiest** host.

The second-to-last item is the one people skip. A check that has never failed is indistinguishable
from one that always passes.

## Where things live

Read [Silent failure modes](silent-failures.md) before you go to production. It is the shortest path
to understanding why this deployment has the shape it does.
