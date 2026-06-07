#!/usr/bin/env python3
"""Resolve a tool's build-inputs.yaml into concrete commits + an inputs_digest.

Reads `build-inputs.yaml` (schema edapack.build-inputs/1), applies any
command-line overrides, resolves every input's `policy` (or override) to a
concrete commit SHA without cloning, and emits a candidate manifest fragment:

    {
      "inputs": [ {name, role, repo, ref, resolved_sha, version, tracked}, ... ],
      "inputs_digest": "sha256:<hex>"
    }

The digest is a stable hash over every *tracked* input's resolved_sha plus the
recipe SHA (this tool-bin repo's own commit), so a change to any tracked input
OR to the build recipe flips the digest and triggers a rebuild.

Network access is confined to an injectable backend so the resolution logic is
unit-testable offline. The default backend shells out to `git ls-remote` and
the GitHub releases API.

Exit codes: 0 success; 1 resolution/validation error.
"""

# NOTE: no `from __future__ import annotations` — must run under the
# manylinux2014 / manylinux_2_28 system Python 3.6 (that feature is 3.7+).
# Annotations below therefore use typing.Tuple/List rather than the PEP 585
# builtin generics (tuple[...]/list[...]), which 3.6 cannot evaluate.
import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ecyaml import parse_simple_yaml  # noqa: E402

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
class GitBackend:
    """Default backend: real `git ls-remote` + GitHub releases API."""

    def ls_remote(self, repo: str) -> dict:
        """Return {ref: sha} for all refs (heads + tags) of `repo`."""
        # capture_output= and text= are 3.7+; use the 3.6-compatible spellings
        # (stdout=PIPE + universal_newlines=True) so this works in-container.
        out = subprocess.run(
            ["git", "ls-remote", repo],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        ).stdout
        refs: dict = {}
        for line in out.splitlines():
            if not line.strip():
                continue
            sha, ref = line.split("\t", 1)
            refs[ref] = sha
        return refs

    def latest_release(self, repo: str) -> str:
        """Return the tag name of the repo's latest GitHub release."""
        slug = _github_slug(repo)
        url = f"https://api.github.com/repos/{slug}/releases/latest"
        req = urllib.request.Request(url, headers=_gh_headers())
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        return data["tag_name"]


def _github_slug(repo: str) -> str:
    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", repo)
    if not m:
        raise ValueError(f"not a GitHub repo URL: {repo}")
    return m.group(1)


def _gh_headers() -> dict:
    import os

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "edapack"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def _version_sort_key(tag: str):
    """Sort key approximating `sort -V` over a version tag."""
    nums = re.findall(r"\d+", tag)
    return [int(n) for n in nums] if nums else [-1]


def _strip_v(s: str) -> str:
    """Derive a clean version from a tag/ref.

    Strips a leading non-numeric prefix up to the first digit, so `v5.038`,
    `nextpnr-0.10`, and `yosys-0.50` become `5.038`, `0.10`, `0.50`. A ref with
    no digits (e.g. a branch name) is returned unchanged.
    """
    m = re.search(r"\d.*$", s)
    return m.group(0) if m else s


def resolve_ref(backend, repo: str, ref: str) -> Tuple[str, str]:
    """Resolve an explicit ref (tag, branch, or raw sha) to (ref, sha).

    Tries tag, then branch, then accepts a raw commit SHA as-is.
    """
    refs = backend.ls_remote(repo)
    # annotated tags expose a peeled ^{} entry pointing at the commit
    for candidate in (f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}"):
        if candidate in refs:
            return ref, refs[candidate]
    if f"refs/heads/{ref}" in refs:
        return ref, refs[f"refs/heads/{ref}"]
    if _SHA_RE.match(ref):
        return ref, ref
    raise ValueError(f"{repo}: cannot resolve ref '{ref}'")


def resolve_policy(backend, repo: str, policy: str) -> Tuple[str, str]:
    """Resolve a policy string to (ref, sha)."""
    if policy.startswith("branch:"):
        return resolve_ref(backend, repo, policy[len("branch:"):])
    if policy.startswith("tag:"):
        return resolve_ref(backend, repo, policy[len("tag:"):])
    if policy == "latest-release":
        tag = backend.latest_release(repo)
        return resolve_ref(backend, repo, tag)
    if policy == "latest-tag":
        refs = backend.ls_remote(repo)
        # str.removesuffix is 3.9+; slice manually to stay 3.6-compatible.
        def _strip_peel(t):
            return t[:-3] if t.endswith("^{}") else t

        tags = sorted(
            (
                _strip_peel(r[len("refs/tags/"):])
                for r in refs
                if r.startswith("refs/tags/")
            ),
            key=_version_sort_key,
        )
        if not tags:
            raise ValueError(f"{repo}: no tags for latest-tag policy")
        return resolve_ref(backend, repo, tags[-1])
    raise ValueError(f"unknown policy: {policy}")


def _derive_version(ref: str, policy: str) -> Optional[str]:
    """Best-effort human version string from the resolved ref."""
    if policy.startswith("branch:"):
        return None
    return _strip_v(ref)


def resolve_one(backend, spec: dict, role: str, override_ref: Optional[str]) -> dict:
    repo = spec["repo"]
    if override_ref:
        ref, sha = resolve_ref(backend, repo, override_ref)
        version = _strip_v(ref)
    else:
        ref, sha = resolve_policy(backend, repo, spec["policy"])
        version = _derive_version(ref, spec["policy"])
    return {
        "name": spec["name"],
        "role": role,
        "repo": repo,
        "ref": ref,
        "resolved_sha": sha,
        "version": version,
        "tracked": bool(spec.get("track", True)),
    }


def compute_digest(inputs: List[dict], recipe_sha: str) -> str:
    """Stable sha256 over tracked inputs' resolved_sha + recipe_sha."""
    payload = {
        "recipe_sha": recipe_sha,
        "inputs": sorted(
            (
                {"name": i["name"], "resolved_sha": i["resolved_sha"]}
                for i in inputs
                if i.get("tracked", True)
            ),
            key=lambda x: x["name"],
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def resolve_inputs(
    spec: dict,
    recipe_sha: str,
    core_ref: Optional[str] = None,
    overrides: Optional[dict] = None,
    backend=None,
) -> dict:
    backend = backend or GitBackend()
    overrides = overrides or {}
    inputs = [resolve_one(backend, spec["core"], "core", core_ref)]
    for dep in spec.get("dependencies", []) or []:
        inputs.append(
            resolve_one(backend, dep, "dependency", overrides.get(dep["name"]))
        )
    return {"inputs": inputs, "inputs_digest": compute_digest(inputs, recipe_sha)}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_override(items: List[str]) -> dict:
    out: dict = {}
    for it in items:
        if "=" not in it:
            raise ValueError(f"--override expects name=ref, got: {it}")
        name, ref = it.split("=", 1)
        out[name.strip()] = ref.strip()
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--build-inputs", required=True, type=Path)
    p.add_argument("--recipe-sha", required=True)
    p.add_argument("--core-ref", default=None, help="Override the core input ref.")
    p.add_argument(
        "--override",
        action="append",
        default=[],
        metavar="NAME=REF",
        help="Override a dependency input ref (repeatable).",
    )
    p.add_argument(
        "--overrides-json",
        default=None,
        help="JSON object of {name: ref} dependency overrides.",
    )
    p.add_argument("--output", type=Path, default=None, help="Write JSON here (else stdout).")
    args = p.parse_args(argv)

    spec = parse_simple_yaml(args.build_inputs.read_text(encoding="utf-8"))
    if spec.get("schema") != "edapack.build-inputs/1":
        print("resolve-inputs: bad or missing schema in build-inputs.yaml", file=sys.stderr)
        return 1

    overrides = _parse_override(args.override)
    if args.overrides_json:
        blob = json.loads(args.overrides_json)
        if blob:
            overrides.update({str(k): str(v) for k, v in blob.items()})

    try:
        result = resolve_inputs(
            spec,
            recipe_sha=args.recipe_sha,
            core_ref=args.core_ref,
            overrides=overrides,
        )
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"resolve-inputs: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
