# bumblebee-intune

A working reference for deploying [Bumblebee](https://github.com/perplexityai/bumblebee), a read-only
supply-chain scanner, to macOS endpoints with Microsoft Intune.

Pointing the scanner at a laptop is easy. Running it across a managed fleet so that a clean result
actually means something is not, because almost every way this breaks produces a result that looks
like good news:

- A scan that hits its deadline stops early and still reports `status: "complete"`.
- A scan with no usable exposure catalog reports zero findings, exactly like a clean host.
- A scan without Full Disk Access silently skips `~/Documents`, `~/Desktop` and every browser
  extension, and reports success over the smaller tree.
- A host running an old copy of your install script keeps reporting healthy scans while quietly
  falling out of the alerts meant to watch it.

None of those produce an error. This repository is the deployment shape that catches them.

**Documentation: <https://m7kni.io/bumblebee-intune/>**

## What is here

```
docs/     the published guide
src/
  endpoint/   the wrapper, catalog selection and log shipper that run on the Mac
  intune/     installer, custom attributes, PPPC profile deployment, uninstaller
  grafana/    recording rules, alert rules, and a drift check for the tier model
```

`src/` is the deployment, not an illustration of it - the same scripts, with credentials, scan roots
and organisation names replaced by placeholders. POSIX `sh` and stdlib Python 3 on the endpoint; no
third-party runtime dependencies.

## Start here

| | |
| --- | --- |
| [Silent failure modes](https://m7kni.io/bumblebee-intune/silent-failures/) | Every way this breaks without erroring, and the control that catches each |
| [Getting started](https://m7kni.io/bumblebee-intune/getting-started/) | Prerequisites, deployment order, and how to prove it works |
| [Full Disk Access](https://m7kni.io/bumblebee-intune/full-disk-access/) | The PPPC profile, the cdhash trap, and why you cannot test it over SSH |
| [Scan architecture](https://m7kni.io/bumblebee-intune/architecture/) | Why one scan schedule cannot serve both detection and inventory |

## Scope

macOS endpoints managed by Microsoft Intune, scanning as root via LaunchDaemons, shipping NDJSON to
Grafana Loki. The Grafana half is straightforward to swap for another backend; the endpoint and Intune
halves are not backend-specific.

Measured against **bumblebee v0.1.2**. Behaviour a later release may change is flagged where it
matters.

## Using this

Copy it, read the comments, and change what does not fit. Credentials, scan roots and thresholds are
deliberately placeholders.

> [!IMPORTANT]
> Intune has no secret-vaulting for script content. The Loki token you put in `installer.sh` is
> readable by anyone with Graph read on `deviceShellScripts` and lands on every managed device. Use a
> push-only, single-purpose, rotatable credential, and read
> [Telemetry](https://m7kni.io/bumblebee-intune/telemetry/#the-credential-problem) first.

Run the endpoint tests:

```sh
just test
```

## Layout notes

The published site is built centrally by the [m7kni.io hub](https://github.com/m7kni/m7kni-net-site).
This repo owns `docs/**/*.md` and `docs.toml` and nothing else about the site - `zensical.toml`, the
brand stylesheet, the SEO template, the fonts and the social card are injected at build time and are
gitignored here. A tracked copy of any of them is drift.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
