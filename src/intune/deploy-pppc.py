#!/usr/bin/env python3
"""Deploy a PPPC profile granting bumblebee Full Disk Access.

WHY
---
Upstream is explicit (docs/deployment-macos.md): "Grant the bumblebee binary Full
Disk Access via MDM (Privacy Preferences Policy Control payload). A LaunchDaemon
running as root still needs FDA for TCC-protected paths." Root is not enough --
TCC gates ~/Library, ~/Documents, ~/Desktop and ~/Downloads regardless of uid.

Without this profile the scanner silently skips those trees. It does not error;
the walker emits a debug diagnostic for unreadable paths and carries on, so the
scan still reports status=complete with a smaller files_considered. That is a
quiet coverage gap, which is exactly the class of failure this deployment keeps
finding.

THE CODE REQUIREMENT IS A cdhash, AND THAT MATTERS
--------------------------------------------------
bumblebee's release binaries are NOT Developer ID signed. The darwin/arm64 build
is ad-hoc *linker-signed* (`Identifier=a.out`, no TeamIdentifier, no Info.plist),
so the only usable designated requirement is its cdhash:

    codesign -d -r- bumblebee  ->  cdhash H"04880f3a..."

Consequences you must not forget:

1. **The cdhash changes on every bumblebee version bump.** Bump
   BUMBLEBEE_VERSION in installer.sh without re-running this script and the PPPC
   entry stops matching -- FDA is silently lost and coverage quietly shrinks
   again. This script therefore DERIVES the cdhash from the pinned tarball
   rather than taking a hand-copied constant, and refuses to run if it cannot.
2. **The darwin/amd64 build is not signed at all** ("code object is not signed"),
   so no cdhash exists for it and no PPPC entry can be written for an Intel Mac.
   This fleet is entirely arm64 (verified 2026-08-11), so that is academic here,
   but a reference implementation must say so out loud.
3. The wrapper emits an `fda` field on its `catalog_health` record by probing a
   TCC-protected path, so a broken grant shows up in Loki instead of being
   invisible. That is the check that proves this profile actually worked.

Usage:
    ./.venv/bin/python deploy/deploy_bumblebee_pppc.py            # create/update + assign All Devices
    ./.venv/bin/python deploy/deploy_bumblebee_pppc.py --status
    ./.venv/bin/python deploy/deploy_bumblebee_pppc.py --delete
"""
from __future__ import annotations

import base64
import hashlib
import os
import plistlib
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graph import Graph  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER = os.path.join(HERE, "installer.sh")

DISPLAY_NAME = "MacOS Bumblebee Full Disk Access (PPPC)"
STAGED_PATH = "/usr/local/libexec/bumblebee/bin/bumblebee"

# Stable UUIDs so an update replaces cleanly on-device rather than duplicating.
PROFILE_UUID = "D4A1C7E3-9B62-4F18-A5D0-7E3C1B9F2A44"
TCC_UUID = "E5B2D8F4-A073-4029-B6E1-8F4D2CA03B55"

ALL_DEVICES = {"@odata.type": "#microsoft.graph.allDevicesAssignmentTarget"}


def pinned_from_installer():
    """Read the version + arm64 SHA256 the installer actually deploys.

    Single source of truth: if these were duplicated here they would drift, and
    a drifted cdhash means FDA silently stops applying.
    """
    src = open(INSTALLER, encoding="utf-8").read()
    ver = re.search(r'^BUMBLEBEE_VERSION="([^"]+)"', src, re.M)
    sha = re.search(r'^BUMBLEBEE_SHA256_ARM64="([^"]+)"', src, re.M)
    base = re.search(r'^BUMBLEBEE_BASE_URL="([^"]+)"', src, re.M)
    if not (ver and sha and base):
        sys.exit("could not read BUMBLEBEE_VERSION / SHA256_ARM64 / BASE_URL from installer.sh")
    return ver.group(1), sha.group(1), base.group(1)


def derive_cdhash(version, expect_sha, base_url):
    """Download the pinned arm64 tarball, verify it, and read the binary's cdhash."""
    url = f"{base_url}/v{version}/bumblebee_{version}_darwin_arm64.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        tgz = os.path.join(tmp, "bb.tgz")
        print(f"fetching {url}")
        with urllib.request.urlopen(url, timeout=180) as r, open(tgz, "wb") as fh:
            fh.write(r.read())
        actual = hashlib.sha256(open(tgz, "rb").read()).hexdigest()
        if actual != expect_sha:
            sys.exit(f"SHA256 mismatch: expected {expect_sha}, got {actual}. "
                     "Refusing to derive a code requirement from an unverified binary.")
        print(f"SHA256 verified ({actual[:16]}...)")
        with tarfile.open(tgz) as tf:
            tf.extract("bumblebee", path=tmp, filter="data")
        binary = os.path.join(tmp, "bumblebee")
        out = subprocess.run(["codesign", "-d", "-r-", binary],
                             capture_output=True, text=True)
        blob = out.stdout + out.stderr
        m = re.search(r'designated =>\s*(.+)', blob)
        if not m:
            sys.exit(f"could not read a designated requirement from the binary:\n{blob.strip()}")
        req = m.group(1).strip()
        if "cdhash" not in req:
            sys.exit(f"unexpected designated requirement {req!r}; refusing to guess")
        return req


def app_entry(code_req):
    return {
        # Path-based, because the binary has no usable bundle identifier: it is
        # linker-signed with Identifier=a.out and no Team ID.
        "Identifier": STAGED_PATH,
        "IdentifierType": "path",
        "CodeRequirement": code_req,
        "Authorization": "Allow",
        "Comment": "bumblebee supply-chain scanner, staged root-owned by the Intune installer",
    }


def mobileconfig_b64(code_req, version):
    profile = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadScope": "System",
        "PayloadIdentifier": "com.example.bumblebee.pppc.profile",
        "PayloadUUID": PROFILE_UUID,
        "PayloadDisplayName": "Example Org - Bumblebee Full Disk Access PPPC",
        "PayloadDescription": (
            f"Grants Full Disk Access to the bumblebee scanner v{version} at {STAGED_PATH}. "
            "A root LaunchDaemon still needs FDA for TCC-protected paths; without this the "
            "scan silently skips them. The code requirement is a cdhash and is therefore "
            "version-specific - re-run deploy_bumblebee_pppc.py whenever BUMBLEBEE_VERSION "
            "changes."),
        "PayloadOrganization": "Example Org",
        "PayloadEnabled": True,
        "PayloadContent": [{
            "PayloadType": "com.apple.TCC.configuration-profile-policy",
            "PayloadVersion": 1,
            "PayloadIdentifier": "com.example.bumblebee.pppc",
            "PayloadUUID": TCC_UUID,
            "PayloadEnabled": True,
            "PayloadDisplayName": "Privacy Preferences Policy Control",
            "PayloadOrganization": "Example Org",
            "Services": {
                # Full Disk Access. SystemPolicyAllFiles is the only service
                # needed: the walker is read-only and never drives the GUI.
                "SystemPolicyAllFiles": [app_entry(code_req)],
            },
        }],
    }
    return base64.b64encode(plistlib.dumps(profile)).decode()


def find(g):
    profs = g.get("deviceManagement/deviceConfigurations?$select=id,displayName")
    return [p for p in profs if p.get("displayName") == DISPLAY_NAME]


def assign_all_devices(g, pid):
    g.post(f"deviceManagement/deviceConfigurations/{pid}/assign",
           {"assignments": [{"target": dict(ALL_DEVICES)}]})
    print("ASSIGNED -> All Devices")


def create(g):
    version, sha, base_url = pinned_from_installer()
    print(f"installer pins bumblebee {version}")
    code_req = derive_cdhash(version, sha, base_url)
    print(f"derived code requirement: {code_req}")
    payload = mobileconfig_b64(code_req, version)

    desc = (f"Grants Full Disk Access to bumblebee v{version} at {STAGED_PATH} so the root "
            "LaunchDaemons can read TCC-protected paths. Code requirement is a cdhash and is "
            "VERSION-SPECIFIC: re-run deploy/deploy_bumblebee_pppc.py after any "
            "BUMBLEBEE_VERSION bump or FDA is silently lost. Verify with the `fda` field on "
            "bumblebee's catalog_health records in Loki.")

    existing = find(g)
    if existing:
        pid = existing[0]["id"]
        g.patch(f"deviceManagement/deviceConfigurations/{pid}", {
            "@odata.type": "#microsoft.graph.macOSCustomConfiguration",
            "displayName": DISPLAY_NAME,
            "description": desc,
            "payloadName": "Example Org - Bumblebee Full Disk Access PPPC",
            "payloadFileName": "bumblebee-pppc.mobileconfig",
            "payload": payload,
        })
        print(f"PATCHed existing profile id={pid}")
    else:
        prof = g.post("deviceManagement/deviceConfigurations", {
            "@odata.type": "#microsoft.graph.macOSCustomConfiguration",
            "displayName": DISPLAY_NAME,
            "description": desc,
            "deploymentChannel": "deviceChannel",
            "payloadName": "Example Org - Bumblebee Full Disk Access PPPC",
            "payloadFileName": "bumblebee-pppc.mobileconfig",
            "payload": payload,
        })
        pid = prof["id"]
        print(f"CREATED profile id={pid}")
    assign_all_devices(g, pid)

    # A 2xx proves the request parsed, not that the bytes landed. Read back and
    # confirm the stored payload is well-formed and carries our cdhash -- Intune
    # stores exactly what you send and validates nothing.
    back = g.get_one(f"deviceManagement/deviceConfigurations/{pid}")
    stored = plistlib.loads(base64.b64decode(back["payload"]))
    entry = stored["PayloadContent"][0]["Services"]["SystemPolicyAllFiles"][0]
    assert entry["Identifier"] == STAGED_PATH, entry
    assert entry["CodeRequirement"] == code_req, entry
    print(f"VERIFIED stored payload: {entry['IdentifierType']} {entry['Identifier']}")
    print(f"                          {entry['CodeRequirement']}")
    status(g)


def status(g):
    profs = find(g)
    if not profs:
        print("not present")
        return
    pid = profs[0]["id"]
    print(f"\nprofile id={pid}  name={DISPLAY_NAME}")
    back = g.get_one(f"deviceManagement/deviceConfigurations/{pid}")
    try:
        stored = plistlib.loads(base64.b64decode(back["payload"]))
        e = stored["PayloadContent"][0]["Services"]["SystemPolicyAllFiles"][0]
        print(f"  grants FDA to: {e['Identifier']} ({e['IdentifierType']})")
        print(f"  requirement:   {e['CodeRequirement']}")
    except Exception as exc:
        print(f"  could not decode stored payload: {exc}")
    for a in g.get(f"deviceManagement/deviceConfigurations/{pid}/assignments"):
        t = a.get("target", {})
        print(f"  assigned -> {t.get('@odata.type','').split('.')[-1]}")


def delete(g):
    profs = find(g)
    if not profs:
        print("nothing to delete")
        return
    g.delete(f"deviceManagement/deviceConfigurations/{profs[0]['id']}")
    print("DELETED")


if __name__ == "__main__":
    g = Graph()
    arg = sys.argv[1] if len(sys.argv) > 1 else "create"
    {"create": create, "--status": status, "--delete": delete}.get(arg, create)(g)
