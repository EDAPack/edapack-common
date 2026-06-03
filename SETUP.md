# Bootstrapping `edapack-common` on GitHub

This repo is implemented and tested locally. To make it usable by the tool repos
(Phase 1+), publish it and tag a `v1`. There are **no custom container images to
build** — tool builds run in the stock `quay.io/pypa/manylinux*` images and the
shared scripts are delivered to each tool via ivpm (`edapack-common` is an ivpm
dependency, fetched into `packages/edapack-common`).

## 1. Create the repo and push

```sh
cd edapack-common
gh repo create edapack/edapack-common --public --source=. --remote=origin --push
# or, if the repo already exists:
#   git remote add origin git@github.com:edapack/edapack-common.git
#   git push -u origin main
```

The default branch is `main`. The `selftest` workflow runs on push and should go
green (52 pytest + 9 shell tests + lint).

## 2. Tag `v1`

Tool repos pin `@v1`. Create the floating major tag and push it:

```sh
git tag -f v1
git push -f origin v1
```

Re-point `v1` forward (`git tag -f v1 && git push -f origin v1`) whenever you land
a non-breaking change you want the tools to pick up. Cut `v2` for breaking
changes to the reusable workflow inputs, the `ec_*` shell API, or the manifest
schema.

## 3. Verify a tool build (optional)

Trigger any tool's CI (e.g. push to `verilator-bin`, or run its workflow
manually). The reusable workflow will:

1. `pip install ivpm && ivpm update -a --skip-py-install` → fetches
   `edapack-common` into `packages/`;
2. resolve inputs + change-gate;
3. build each manylinux image via `docker run quay.io/pypa/<image>` (deps
   installed at build time);
4. publish a release with `manifest.json`.

## Org / naming notes

- The GitHub org is **`edapack`** (lowercase — matches the tool repos'
  `git@github.com:edapack/<tool>-bin` remotes).
- Each tool's `ci.yml` references `edapack/edapack-common@v1`, and its
  `ivpm.yaml` lists `edapack-common` so `ivpm update` fetches it locally.
