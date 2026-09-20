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
| `.github/workflows/build-release.yml` | Reusable workflow: resolve → change-gate → build matrix → publish, in stock `quay.io/pypa/manylinux*` images. Tool repos call this from a thin `ci.yml`, once per track (see below). |
| `scripts/resolve-inputs.py` | Resolve `build-inputs.yaml` (+ overrides) to commit SHAs and an `inputs_digest`, per track. |
| `scripts/gen-manifest.py` | Assemble per-tarball `manifest.json`; merge into a top-level release manifest; report a manifest's track. |
| `scripts/manifest-diff.py` | The change-gate: decide `build_needed` by diffing input digests. |
| `scripts/release-notes.py` | Build the release-track body: slice the upstream changelog section for the version being released, or (`gh-compare`) list the pull requests merged since the last release. |
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
- **`scripts/release-ivpm.yaml`** (optional, in each tool repo) is staged into
  the release as `ivpm.yaml`. See below.

## Shipping an ivpm manifest with a release

`ivpm` reads `packages/<name>/ivpm.yaml` for every installed package, so an
`ivpm.yaml` *inside* the tarball is what lets an installed release prepend its
`bin/` to `PATH` and pull the runtime packages it needs.

This is opt-in per package. Put a consumer-facing manifest at
`scripts/release-ivpm.yaml` and `ec_finalize_release` stages it automatically;
omit the file and the release ships without one. `verilator-bin` needs nothing
at install time and omits it; `yosys-bin` needs a venv with `click` for `sby`.

It must **not** be a copy of the project's own `ivpm.yaml`. That file describes
how to *build* the package — tool sources, `edapack-common`, test deps — and
shipping it makes a consumer fetch all of it. `ec_stage_release_ivpm` rejects a
byte-identical copy and warns if the manifest mentions a `default-dev` dep-set.

```yaml
# scripts/release-ivpm.yaml — what a consumer needs, nothing more
package:
  name: yosys-bin
  env:
  - name: PATH
    path-prepend: "${IVPM_PACKAGES}/yosys-bin/bin"
  dep-sets:
  - name: default
    deps:
    - name: click
      src: pypi
```

## Release notes

The release track's body comes from `core.release_notes` in
`build-inputs.yaml`, which takes one of two forms:

| Value | Body |
|---|---|
| a path, e.g. `Changes` | The upstream changelog's section for the version being built, read at the resolved SHA. |
| `gh-compare` | The pull requests merged between the previous release's upstream ref and this one. |
| *(absent)* | Default notes naming the upstream release. |

`gh-compare` exists for upstreams that have no changelog *and* publish empty
release bodies (Verible: 40 consecutive releases, every body zero-length). It
lists merged PR titles rather than commit subjects, because a repo that does
not squash-merge has a commit log that is mostly `Merge branch 'x' into master`
and `Fixed formatting`. The previous upstream ref is read out of the previous
release's `manifest.json` — not from this repo's previous git tag, which need
not match upstream's (`yosys-0.50` upstream vs `v0.50` here).

Both forms soft-fail: a changelog reformat or a GitHub API hiccup drops back to
the default notes rather than blocking a release.

## Release tracks

`build-release.yml` takes a `track` input. A tool repo calls it once per track.

| | `dev` (default) | `release` |
|---|---|---|
| Core ref | `core.policy`, e.g. `branch:master` | `core.release_policy`, e.g. `latest-tag:^v\d+\.\d+$` |
| Version | `<core version>.<run id>` | `<upstream tag version>` |
| Pre-release | yes | no |
| Moves `latest` | no | yes |
| Body | input changes since last release | upstream changelog section (`core.release_notes`) |
| Gate | `inputs_digest` vs. the last *dev* release | upstream tag we have not published |

A package with no `core.release_policy` has no release track and behaves as
before. Where a branch policy yields no version from the ref alone, set
`core.version_probe` (e.g. `configure.ac:AC_INIT`) to read it out of the tree at
the resolved SHA.

Both tracks resolve the newest upstream release and branch opposite ways on
whether it is already published, so at most one of them builds in a given run:
a week that ships a release does not also ship a redundant snapshot. Neither
job depends on the other — the test is a pure function of upstream tags and
published releases.

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
