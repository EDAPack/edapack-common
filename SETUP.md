# Bootstrapping `edapack-common` on GitHub

This repo is implemented and tested locally. To make it usable by the tool repos
(Phase 1+), it needs to be published and its builder images built. These steps
require your GitHub credentials, so run them yourself.

## 1. Create the repo and push

```sh
cd edapack-common
gh repo create edapack/edapack-common --public --source=. --remote=origin --push
# or, if the repo already exists:
#   git remote add origin git@github.com:edapack/edapack-common.git
#   git push -u origin main
```

The default branch is `main`. The `selftest` workflow runs on push and should go
green (46 pytest + 7 shell tests + lint).

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

## 3. Build & publish the builder images

The `builder-images` workflow builds one rootless manylinux image per variant and
pushes to GHCR. Trigger it once:

```sh
gh workflow run builder-images.yml --repo edapack/edapack-common
```

Then **make the resulting packages public** (Org → Packages → each
`manylinux_*` package → Package settings → Change visibility → Public), or the
tool builds must authenticate to pull them. The tool `build` jobs do `docker
login ghcr.io` with `GITHUB_TOKEN` and request `packages: read`, so private also
works within the org — public is simplest.

Images land at:

```
ghcr.io/edapack/manylinux_2_28_x86_64:latest
ghcr.io/edapack/manylinux_2_34_x86_64:latest
ghcr.io/edapack/manylinux2014_x86_64:latest
```

## 4. Verify before Phase 1

- `selftest` green on `main`.
- `v1` tag exists.
- The three `ghcr.io/edapack/manylinux_*` images exist and are pullable.

Once these hold, the tool repos can adopt the reusable workflow (Phase 1).

## Org / naming notes

- The GitHub org is **`edapack`** (lowercase — matches the tool repos'
  `git@github.com:edapack/<tool>-bin` remotes and GHCR's lowercase requirement).
- All reusable-workflow `uses:` and `ghcr.io/edapack/...` references use that
  lowercase path.
