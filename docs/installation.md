---
title: Installation and upgrades
description: Pinned, checksum-verified acquisition of the scanner binary; idempotent upgrades; and a real uninstall path.
---

# Installation and upgrades

## Do not install it with a package manager

Acquire the binary directly from the pinned upstream release. Do not use Homebrew, Workbrew, or any
package manager whose prefix is user-writable, and do not use an "install the latest" step anywhere
in a root context.

- **A user-writable prefix is a local privilege-escalation path.** Anyone who can write
  `/opt/homebrew/bin/bumblebee` owns root on the next installer cycle, because a root LaunchDaemon
  executes whatever is at that path. On a single-user Mac the Homebrew prefix is user-writable by
  design. A root daemon must never read its executable from a path a user can write.
- **`brew upgrade` on every run ships unreviewed binaries.** Whatever upstream published this
  morning rolls onto the fleet automatically, at a time nobody chose and in a version nobody
  checked.

!!! danger "This is the easy mistake to make"

    Installing via a package manager and then copying the binary to a root-owned location does **not**
    fix it. The privilege escalation is in the read, not the copy: the installer reads a
    user-writable path on every cycle, so a user who replaces that file owns root the next time the
    installer runs. Root-owning the destination just means the attacker's binary is also root-owned.

## Pinned tarball, verified, root-owned throughout

```sh
BUMBLEBEE_VERSION="0.1.2"
BUMBLEBEE_SHA256_ARM64="0535aefeb6d1bdc2b4f44e393c5da385c95ac63c7c8f0bcee01b054d688bdab5"
BUMBLEBEE_SHA256_AMD64="ea7f0ea303f712f3073ddb0f9fc0b368692ec1eee581b9a5d069ed986db2b433"
BUMBLEBEE_BASE_URL="https://github.com/perplexityai/bumblebee/releases/download"
```

Checksums come from the release's own `checksums.txt`. The install sequence:

1. Pick the arch, build the tarball URL from the pinned version
2. Download into a `0700` working directory under `/var/db/bumblebee`
3. `shasum -a 256` and compare against the pinned value
4. **Fail closed on mismatch** - `exit 1`, stage nothing
5. Extract, `chmod 0755`, and run `bumblebee version` to prove it executes on this host
6. `install -m 0755 -o root -g wheel` then `mv -f` for an atomic swap

Step 4 is the one to get right. A checksum mismatch is either a corrupted download or a tampered
artifact. Neither may be staged and executed as root, and "carry on with the old binary" is not an
acceptable default when you cannot explain the mismatch.

Step 5 catches the case a checksum cannot: an architecture mismatch, a missing dynamic library, a
binary that is fine but unrunnable here.

A **failed download**, by contrast, exits 0 and keeps the existing staged binary. A flaky network is
not a reason to leave a fleet unscanned, and the next cycle retries.

## Upgrades are the same code path

An upgrade is a three-line edit:

```diff
-BUMBLEBEE_VERSION="0.1.2"
-BUMBLEBEE_SHA256_ARM64="0535aefe..."
-BUMBLEBEE_SHA256_AMD64="ea7f0ea3..."
+BUMBLEBEE_VERSION="0.1.3"
+BUMBLEBEE_SHA256_ARM64="<from checksums.txt>"
+BUMBLEBEE_SHA256_AMD64="<from checksums.txt>"
```

plus a bump of `INSTALL_VERSION`, which is the marker that decides whether the wrapper, helpers and
daemon plists get rewritten.

The installer is idempotent. It skips the download entirely when the staged binary is already the
pinned version **and still runs**:

```sh
if [ -x "$STAGED_BIN" ] && [ -f "$BIN_VERSION_FILE" ] &&
   [ "$(cat "$BIN_VERSION_FILE")" = "$BUMBLEBEE_VERSION" ] &&
   "$STAGED_BIN" version >/dev/null 2>&1; then
  need_install=0
fi
```

The version marker alone is not trusted. A truncated or clobbered binary must reinstall rather than
be assumed good on the strength of a text file next to it.

!!! danger "A version bump silently revokes Full Disk Access"

    The PPPC profile's code requirement is keyed on the binary's **cdhash**, because the release
    builds are ad-hoc signed with no Team ID. The cdhash changes with every build, so bumping
    `BUMBLEBEE_VERSION` without redeploying the PPPC profile revokes FDA on every managed device  - 
    and scans keep completing and keep reporting clean, over a smaller tree.

    Treat the version constant and the profile as **one change**. See [Full Disk
    Access](full-disk-access.md#the-cdhash-couples-the-profile-to-the-binary).

!!! warning "Bump `INSTALL_VERSION` whenever you edit the wrapper"

    Edit the wrapper and leave `INSTALL_VERSION` alone, and the idempotency gate short-circuits: your
    change never reaches any endpoint, and the install reports success because it did exactly what it
    was told.

    The symptom is that endpoints keep emitting the **old record shape**. If a field you just added
    comes back `null` on every host, check this before investigating anything else.

## Two version markers, on purpose

| File | Tracks | Bumped by |
| --- | --- | --- |
| `/usr/local/libexec/bumblebee/.bumblebee.version` | The scanner binary | `BUMBLEBEE_VERSION` |
| `/var/db/bumblebee/install.version` | The wrapper, helpers and plists | `INSTALL_VERSION` |

They move independently. A scanner upgrade with no wrapper change should not rewrite the daemons, and
a wrapper fix should not force a redownload.

`install.version` is also the marker the [custom attribute](deployment.md#custom-attributes) uses to
distinguish "installed, no scan yet" from "never installed", and the string worth carrying into your
telemetry so [version skew is visible](silent-failures.md#alert-coverage-silently-becomes-whichever-hosts-are-current).

## Layout on disk

```
/usr/local/libexec/bumblebee/
    bin/bumblebee               the pinned binary, root:wheel 0755
    threat_intel/               curated catalogs shipped in the tarball
    catalog-select.py           schema-coherent catalog assembly
    loki-push.py                NDJSON shipper
    .bumblebee.version
/usr/local/bin/bumblebee-run    the wrapper the daemons invoke
/var/db/bumblebee/              0700  env file, catalogs, summaries, probe
    env                         0600  credentials
    summary/${MODE}_${PROFILE}.json
    catalog/${MODE}_${PROFILE}/
    osv/                        shared upstream catalog cache
    install.version
/var/log/bumblebee/
/Library/LaunchDaemons/com.bumblebee.*.plist
```

`threat_intel/` ships **inside the release tarball**, so the curated catalogs always match the
binary's schema expectations. It is swapped atomically alongside the binary. This matters more than
it looks - see [Exposure catalogs](exposure-catalogs.md).

## There must be an uninstall path

Ship one from the start, as a separate, **unassigned** Intune script. It exists so that removal is a
one-click operation when you need it, not an improvisation during an incident.

It must:

- Boot out every daemon label, **including the legacy ones** from earlier versions of your own
  deployment. Labels you have renamed are still loaded on machines that have not run the new
  installer.
- Kill any in-flight scan.
- Remove the staged tree, the wrapper, the daemon plists and the logs.
- **Overwrite the env file before unlinking it.** It contains a credential.

!!! warning "Leave the uninstaller unassigned"

    Assigning it to All Devices uninstalls the fleet. Assign it deliberately, to the devices you mean,
    and unassign it afterwards. This is one place where a tool that defaults to "no assignment change"
    earns its keep - see [Deployment](deployment.md#assignment).

Source: [`src/intune/uninstall.sh`](https://github.com/rknightion/bumblebee-intune/blob/main/src/intune/uninstall.sh).
