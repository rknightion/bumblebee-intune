---
title: Deployment
description: Intune mechanics - shell scripts, custom attributes, assignment, rollout and the Graph traps that report success while doing nothing.
---

# Deployment

## What you create in Intune

| Object | Type | Assignment |
| --- | --- | --- |
| Bumblebee installer | `deviceShellScript`, run as root, daily | All Devices |
| Bumblebee scan summary | `deviceCustomAttributeShellScript` | All Devices |
| Bumblebee exposure | `deviceCustomAttributeShellScript` | All Devices |
| Bumblebee FDA (PPPC) | `deviceConfiguration` (macOSCustomConfiguration) | All Devices |
| Bumblebee uninstall | `deviceShellScript` | **none - deliberately** |

All beta Graph endpoints. `deviceShellScripts` and `deviceCustomAttributeShellScripts` are macOS-only
and beta-only.

## Script settings

```json
{
  "runAsAccount": "system",
  "executionFrequency": "P1D",
  "retryCount": 3,
  "blockExecutionNotifications": true
}
```

`retryCount: 3` matters on laptops: a machine that is asleep or off the network at its slot gets
another attempt rather than waiting a full day.

!!! warning "Script content reaches hosts on the script's own schedule, not on save"

    With `executionFrequency: P1D`, a host can run script content that is a **day or more** out of
    date, and a laptop asleep at its slot is longer still. Nothing in Intune reports the skew.

    Carry the installed version in your endpoint telemetry and [alert on its
    absence](silent-failures.md#alert-coverage-silently-becomes-whichever-hosts-are-current). This is
    the mechanism behind hosts silently dropping out of your alert coverage.

    To force a pickup during testing:

    ```sh
    sudo launchctl kickstart -k system/com.microsoft.intuneMDMAgent.daemon
    ```

## Custom attributes

Two, because they answer different questions:

**Exposure** - a single token for console filtering and dynamic groups:

```
finding | clean_partial | clean | stale | pending_scan | not_installed | parse_error
```

**Scan summary** - one line of detail inside the 5,000-character attribute limit: status, profile,
counts, duration, scanner version, and the oldest scan time across profiles.

Both read **every** per-`(mode, profile)` summary and report the **worst** case. Findings are summed,
degraded state is sticky, and the *oldest* scan time is reported so a profile that has quietly
stopped running is visible rather than masked by a fresh sibling.

`clean_partial` is the one that earns its keep: no findings, but the newest scan for some profile
**timed out** or ran with **no catalog**. Reporting that as `clean` is a lie - a truncated scan emits
fewer findings, and a catalog-less scan cannot emit any.

!!! warning "Distinguish 'installed, not scanned yet' from 'not installed'"

    Collapsing both into one `no_run` token makes a **healthy, just-upgraded** host indistinguishable
    from a deployment that never landed. Because attributes are only re-evaluated daily, that wrong
    reading sticks for up to 24 hours after every version bump.

    Key `pending_scan` off the presence of the install marker, and carry the installed version in the
    string.

macOS custom attributes are **visibility only** - you cannot drive compliance from one. Use them for
console filtering and dynamic groups, and put the real detection in your alerts.

## Assignment

Assign the installer, both attributes and the PPPC profile to **All Devices**.

Partial assignment is the failure this whole deployment is built to avoid: coverage becomes a
function of group membership, nothing reports the gap, and the fleet that looks clean is the fleet
that was never scanned.

!!! danger "`/assign` replaces the entire assignment set"

    It is not additive. A deploy tool that defaults to assigning a canary group will **silently narrow
    a fleet-wide script to one device** on a routine content push, and the script keeps reporting
    success on that one device.

    Default your tooling to making **no assignment change**, and make assignment an explicit,
    separate action.

!!! warning "Never assign the uninstaller"

    Assigning it to All Devices uninstalls the fleet. Assign it deliberately, to the devices you mean,
    then unassign it.

## Rollout

Ring it with an assignment filter on the installer if you want one - the installer is idempotent, so
an upgrade is the same code path as a fresh install and safe to re-run.

The one ordering constraint is the PPPC profile:

1. Deploy the PPPC profile for the **new** binary version
2. Then push the installer that stages it

The other order leaves a window where the new binary is running without a matching grant, and [that
window is silent](full-disk-access.md#the-cdhash-couples-the-profile-to-the-binary).

## Graph traps

!!! danger "`deviceShellScripts` has no `/assignments` route"

    `GET /deviceManagement/deviceShellScripts/{id}/assignments` returns **400 `No method match route
    template`**. Read assignments with `?$expand=assignments` on the entity instead.

    A preflight that swallows the error reports `[]` for a script assigned to All Devices - so your
    tooling concludes the script is unassigned and "helpfully" assigns it, which is where the
    `/assign` replacement trap above bites.

!!! warning "`deviceRunStates.runState` reports `unknown` on success"

    Devices report `unknown` for scripts that demonstrably ran and produced output. Do not build
    monitoring on it.

    `lastStateUpdateDateTime` on the same object **is** trustworthy, and is how you catch a host
    running stale script content.

Other Graph notes:

- Never include `@odata.type` in `$select` - 400. It returns automatically.
- URL-encode `$filter` values.
- `appRoleAssignment` reads are eventually consistent. A count that has not moved is not evidence a
  grant failed; re-read before concluding anything, and never re-POST on the strength of the first
  read.

## Verify by reading back

A `2xx` proves the request was accepted, not that it did what you meant. This applies to script
content, assignments and especially the PPPC payload - Graph will happily accept a
`macOSCustomConfiguration` containing a malformed plist and return `201`.

Read the object back and assert on the stored value.

## Confirming a deployment landed

Check all three, because each catches something the others miss:

```sh
# 1. the endpoint agrees which version it is running
sudo cat /var/db/bumblebee/install.version

# 2. the daemons are loaded
sudo launchctl list | grep bumblebee

# 3. telemetry carries the new fields
#    (a field that is null everywhere means INSTALL_VERSION was not bumped)
```

Then confirm in your log store that both the `scan_summary` and `catalog_health` records have arrived
from the host, with `catalog_effective: 1` and `fda: 1`.
