#!/usr/bin/env python3
"""Assemble a release manifest.json (schema edapack.manifest/1).

Two modes:

  assemble  Combine a resolve-inputs candidate fragment with the release/platform
            blocks and the staged skills index into a single manifest. Used by
            each build to emit the per-tarball manifest.

  merge     Combine several per-tarball manifests (same inputs_digest) into one
            top-level manifest whose `platforms[]` lists every built platform.
            Used by the publish step.

Exit codes: 0 success; 1 error.
"""

# NOTE: no `from __future__ import annotations` — must run under the
# manylinux2014 / manylinux_2_28 system Python 3.6 (that feature is 3.7+).
import argparse
import json
import sys
from pathlib import Path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _skills_from_index(index_path: Path) -> list:
    if not index_path.is_file():
        return []
    idx = _load(index_path)
    out = []
    for s in idx.get("skills", []):
        out.append(
            {
                "name": s["name"],
                "version": s.get("version", ""),
                "binaries": s.get("binaries", []),
            }
        )
    return out


def assemble(args) -> int:
    candidate = _load(args.candidate)
    manifest = {
        "schema": "edapack.manifest/1",
        "package": args.package,
        "release": {
            "version": args.version,
            "tag": args.tag,
            "built_at": args.built_at,
            "trigger": args.trigger,
            "recipe_sha": args.recipe_sha,
        },
        "inputs_digest": candidate["inputs_digest"],
        "inputs": candidate["inputs"],
    }
    if args.platform:
        manifest["platform"] = _load(args.platform)
    if args.skills_index:
        skills = _skills_from_index(args.skills_index)
        if skills:
            manifest["skills"] = skills
    _emit(manifest, args.output)
    return 0


def merge(args) -> int:
    manifests = [_load(p) for p in args.manifest]
    if not manifests:
        print("gen-manifest: merge needs at least one manifest", file=sys.stderr)
        return 1
    digests = {m["inputs_digest"] for m in manifests}
    if len(digests) != 1:
        print(
            f"gen-manifest: refusing to merge manifests with differing "
            f"inputs_digest: {sorted(digests)}",
            file=sys.stderr,
        )
        return 1
    base = dict(manifests[0])
    platforms = []
    for m in manifests:
        if "platform" in m:
            platforms.append(m["platform"])
        platforms.extend(m.get("platforms", []))
    base.pop("platform", None)
    base["platforms"] = platforms
    _emit(base, args.output)
    return 0


def _emit(manifest: dict, output: Path | None) -> None:
    text = json.dumps(manifest, indent=2) + "\n"
    if output:
        output.write_text(text)
    else:
        sys.stdout.write(text)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("assemble")
    a.add_argument("--candidate", required=True, type=Path)
    a.add_argument("--package", required=True)
    a.add_argument("--version", required=True)
    a.add_argument("--tag", required=True)
    a.add_argument("--built-at", required=True)
    a.add_argument("--trigger", required=True, choices=["schedule", "workflow_dispatch", "push"])
    a.add_argument("--recipe-sha", required=True)
    a.add_argument("--platform", type=Path, default=None)
    a.add_argument("--skills-index", type=Path, default=None)
    a.add_argument("--output", type=Path, default=None)
    a.set_defaults(func=assemble)

    m = sub.add_parser("merge")
    m.add_argument("--manifest", required=True, action="append", type=Path)
    m.add_argument("--output", type=Path, default=None)
    m.set_defaults(func=merge)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
