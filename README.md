# edapack-common

Shared build & release infrastructure for the [edapack](https://edapack.github.io)
ecosystem of pre-built open-source EDA tool binaries. This repo is the single
source of truth for the logic every `*-bin` tool repo shares, so that build
behavior stays identical by construction instead of by copy-paste.

See **[design](../BUILD_CENTRALIZATION_DESIGN.md)** and
**[plan](../BUILD_CENTRALIZATION_PLAN.md)** for the full rationale, and the
[Developer Guide](https://edapack.github.io) for how-tos.

## What's here

| Path | Purpose |
|---|---|
| `.github/workflows/build-release.yml` | Reusable workflow: resolve → change-gate → build matrix → publish, in stock `quay.io/pypa/manylinux*` images. Tool repos call this from a thin `ci.yml`. |
| `scripts/resolve-inputs.py` | Resolve `build-inputs.yaml` (+ overrides) to commit SHAs and an `inputs_digest`. |
| `scripts/gen-manifest.py` | Assemble per-tarball `manifest.json`; merge into a top-level release manifest. |
| `scripts/manifest-diff.py` | The change-gate: decide `build_needed` by diffing input digests. |
| `scripts/stage-skills.py` | Validate + stage Agent Skills into a release (canonical copy). |
| `scripts/build-common.sh` | Shell library sourced by each tool's `build.sh` (`ec_*` helpers). |
| `scripts/local-build.sh` | Rootless local manylinux build wrapper (+ `clean`). |
| `scripts/reset-root-owned.sh` | One-shot, sudo-free removal of legacy root-owned build dirs. |
| `schemas/` | JSON Schemas for the manifest, build-inputs, and skill-manifest. |

## The contracts

- **`build-inputs.yaml`** (in each tool repo) declares the core source + every
  tracked dependency and how to resolve its version. Schema:
  `schemas/build-inputs.schema.json`.
- **`manifest.json`** (in every release) records exactly what went into the
  build, with an `inputs_digest` used to gate weekly releases. Schema:
  `schemas/manifest.schema.json`.

## Local build

First fetch the shared scripts into the tool repo (one time / when they change):

```sh
cd ../verilator-bin && ivpm update -a     # populates packages/edapack-common
```

Then build in the stock manylinux image:

```sh
scripts/local-build.sh ../verilator-bin          # build -> ../verilator-bin/dist/
scripts/local-build.sh ../verilator-bin clean    # remove work volume + dist (no sudo)
```

The build runs in `quay.io/pypa/manylinux*` (installing deps at build time),
writes scratch to a named docker volume (never the workspace), and lands the
tarball + manifest in the tool's `dist/`. Cleanup is always sudo-free.

## Tests

```sh
make test        # lint + python (pytest) + shell tests
```

## Versioning

Tool repos pin `@v1`; the floating `v1` tag moves forward as fixes land.
Breaking changes cut `@v2`.
