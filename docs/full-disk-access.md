---
title: Full Disk Access
description: The PPPC profile, why the code requirement has to be a cdhash, why you cannot test the grant over SSH, and how to prove it works.
---

# Full Disk Access

This is the page most likely to be got wrong, and the failure is silent in both directions: without
the grant the scanner quietly scans less, and with a *stale* grant it quietly stops scanning the same
paths again.

## Root is not enough

macOS TCC applies to root. Without a Full Disk Access grant, the scanner cannot read:

- `~/Documents`, `~/Desktop`, `~/Downloads`
- Large parts of `~/Library`, including `~/Library/Application Support` - which is where **browser
  extensions** live

The walker emits a `debug` diagnostic for each unreadable path and carries on. `files_considered`
drops. The scan completes and reports clean.

!!! info "Measured: what the missing grant actually costs"

    Without FDA, three `browser_extension_root` paths appear in `scan_summary.roots` on every scan and
    produce **zero** package records. Across 30 days, zero. The first scan after the grant lands
    produces **22** browser-extension records within seconds: password managers, an SSO plugin, an AI
    assistant.

    An entire category of supply-chain surface, and one of the more interesting ones, is invisible
    until this profile is deployed - while the scan reports success the whole time.

!!! warning "Rule out permissions before reporting an extractor as broken"

    A root that yields nothing looks identical to a root the scanner cannot read. `scan_summary.roots`
    lists what was **attempted**, not what was read, so absence of records is not evidence of a
    scanner bug. Check the FDA probe first - the two shapes are indistinguishable and only one of them
    is upstream's problem.

## The release binaries cannot be keyed by bundle ID

The macOS release builds are ad-hoc linker-signed:

```console
$ codesign -dv --verbose=4 bumblebee
Identifier=a.out
Signature=adhoc
```

`Identifier=a.out`. No Team ID, no stable bundle identifier, no Developer ID. So a PPPC payload
cannot use the usual `anchor apple generic and certificate leaf[subject.OU] = "TEAMID"` requirement.
The only stable-enough identity is the binary's **cdhash**:

```
identifier "a.out" and cdhash H"04880f3a33b79f5f5c11dedd5d42c6c711cf5c1a"
```

with `IdentifierType: path` and the identifier set to the staged binary's absolute path.

This is worth raising upstream - signed and notarised release assets would replace this whole section
with a one-line Team ID requirement. Until then, the cdhash is the mechanism.

## The cdhash couples the profile to the binary

**The cdhash changes with every build.** Bump the pinned version and the profile no longer matches the
binary that is running, so the grant silently stops applying. Scans still complete, still report
clean, over a smaller tree.

Do not paste a cdhash into a plist by hand. Derive it from the same pinned constants the installer
uses, so the two cannot drift:

```
read BUMBLEBEE_VERSION and the SHA256 out of installer.sh
download that exact tarball, verify the checksum
extract, then parse the CDHash line from:
    codesign -dv --verbose=4 <binary>
```

Then the upgrade procedure is: edit the version constants, run the PPPC deploy script, push the
installer. Three steps that always happen together.

Source:
[`src/intune/deploy-pppc.py`](https://github.com/rknightion/bumblebee-intune/blob/main/src/intune/deploy-pppc.py).

## The payload

A `macOSCustomConfiguration` profile carrying `com.apple.TCC.configuration-profile-policy`:

```xml
<key>Services</key>
<dict>
  <key>SystemPolicyAllFiles</key>
  <array>
    <dict>
      <key>Identifier</key>
      <string>/usr/local/libexec/bumblebee/bin/bumblebee</string>
      <key>IdentifierType</key>
      <string>path</string>
      <key>CodeRequirement</key>
      <string>identifier "a.out" and cdhash H"04880f3a..."</string>
      <key>Allowed</key>
      <true/>
    </dict>
  </array>
</dict>
```

`SystemPolicyAllFiles` is Full Disk Access. `IdentifierType: path` is required because there is no
bundle ID to key on.

## You cannot test a PPPC grant over SSH

This wastes an afternoon if you do not know it.

TCC attributes a file access to the **responsible process**, not the process making the syscall. Run
the scanner from an SSH session and the responsible process is `sshd-keygen-wrapper`, which has no
FDA grant. The scan fails to read `~/Documents` and you conclude the profile is broken.

The same profile works perfectly when the binary is launched by `launchd`.

Test it the way it actually runs:

```sh
sudo launchctl kickstart -k system/com.bumblebee.findings-baseline
```

then read the resulting telemetry. Never conclude anything about TCC from an interactive SSH session.

Terminal.app has the same problem in reverse: if *it* holds FDA, a manual test inherits the grant and
passes whether or not your profile works.

## `test -r` is not a Full Disk Access test

```sh
[ -r "$HOME/Documents" ] && echo "we have FDA"   # WRONG
```

Root satisfies `test -r` on mode bits alone. TCC is enforced at `open()`, not at `stat()`. This check
passes on a machine with no grant whatsoever.

**Do a real read and count what comes back.** The wrapper runs a bounded scan against a
TCC-protected directory and counts the files considered:

```sh
_CU="$(/usr/bin/stat -f '%Su' /dev/console 2>/dev/null || true)"
case "$_CU" in ""|root|loginwindow) _CU="" ;; esac
if [ -n "$_CU" ] && [ -d "/Users/$_CU/Documents" ]; then
  FDA_ROOT="/Users/$_CU/Documents"
else
  for _u in /Users/*; do [ -d "$_u/Documents" ] && FDA_ROOT="$_u/Documents" && break; done
fi

"$BIN" scan --profile deep --root "$FDA_ROOT" --max-duration 20s \
       --output file --output-file "$_PROBE"
```

Two details that matter:

- **Probe the console user's Documents, not the first one you find.** Iterating `/Users/*` typically
  hits a service or admin account first, and those folders hold a handful of files at most.
- **Emit the raw count alongside the verdict**, not just a boolean.

!!! warning "A probe that returns 1 file is not a signal"

    Probing an admin account's Documents returns **1** file; probing the console user's returns
    **1,169**. A count of 0 or 1 cannot distinguish a granted-but-empty folder from a denied one, so a
    probe that lands on the wrong account reports a false negative on a working grant - and a false
    *positive* is worse, because you will trust it.

    Shipping `fda_probe_files` alongside the `fda` verdict is what lets you tell the two apart after
    the fact, without an SSH session.

The probe costs a bounded 20 seconds per scan and is the only thing that will tell you the grant has
gone away.

## Proving it worked

Before the grant:

```json
{ "fda": 0, "fda_probe_files": 0 }
```

After:

```json
{ "fda": 1, "fda_probe_files": 1169, "fda_probe_root": "/Users/<console-user>/Documents" }
```

Corroborate with data rather than the verdict alone: browser-extension package records should appear
for the first time within minutes of the profile landing, on roots that were previously listed and
empty.

Alert on `min by (host) (fda) < 1`. The most likely cause by far is a version bump without a profile
redeploy.

## Deploying it

Assign to All Devices. A PPPC profile that is not assigned everywhere leaves you with a fleet where
coverage depends on group membership, and the resulting partial-coverage failure is invisible for
exactly the reasons on [this page](silent-failures.md).

Verify by **reading the stored payload back** and asserting on it, not by trusting the `201`. Graph
accepts a `macOSCustomConfiguration` with a malformed plist perfectly happily.

## What the profile does not fix

FDA does not grant access to everything. It does not cover other users' keychains, and it does not
make an encrypted volume readable. If a root appears in `scan_summary.roots` and yields nothing after
the grant, check whether it is genuinely a TCC path before concluding anything.
