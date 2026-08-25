#!/usr/bin/env python3
"""Assemble a release manifest.json (schema edapack.manifest/1).

Two modes:

  assemble  Combine a resolve-inputs candidate fragment with the release/platform
            blocks and the staged skills index into a single manifest. Used by
            each build to emit the per-tarball manifest.

  merge     Combine several per-tarball manifests (same inputs_digest) into one
            top-level manifest whose `platforms[]` lists every built platform.
            Used by the publish step.

  track     Print which track produced a manifest ('dev' / 'release'). Used to
            find the previous release *on the same track* when change-gating.

Exit codes: 0 success; 1 error.
"""

# NOTE: no `from __future__ import annotations` — must run under the
# manylinux2014 / manylinux_2_28 system Python 3.6 (that feature is 3.7+).
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# A release-track tag is the bare upstream version (v5.050); a dev-track tag
# carries a build-id third component (v5.051.32639969514).
_RELEASE_TAG_RE = re.compile(r"^v?\d+(?:\.\d+)?$")


def track_of(manifest: Optional[dict], tag: Optional[str] = None) -> Optional[str]:
    """Determine which track produced a release: 'dev', 'release', or None.

    Prefers the recorded `release.track`. Manifests written before that field
    existed fall back to the tag's shape, so the track-aware previous-manifest
    lookup still works against the existing published history.
    """
    if manifest:
        recorded = (manifest.get("release") or {}).get("track")
        if recorded in ("dev", "release"):
            return recorded
        tag = tag or (manifest.get("release") or {}).get("tag")
    if not tag:
        return None
    return "release" if _RELEASE_TAG_RE.match(tag) else "dev"


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
            "track": getattr(args, "track", None) or "dev",
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


def track(args) -> int:
    """Print the track that produced a manifest ('dev' / 'release' / 'unknown').

    Used by the previous-manifest lookup to walk releases newest-first and stop
    at the first one belonging to the track being built.
    """
    manifest = _load(args.manifest) if args.manifest and args.manifest.is_file() else None
    print(track_of(manifest, args.tag) or "unknown")
    return 0


def _emit(manifest: dict, output: Optional[Path]) -> None:
    text = json.dumps(manifest, indent=2) + "\n"
    if output:
        output.write_text(text)
    else:
        sys.stdout.write(text)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # add_subparsers(required=...) is 3.7+; set the attribute for 3.6 compat.
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    a = sub.add_parser("assemble")
    a.add_argument("--candidate", required=True, type=Path)
    a.add_argument("--package", required=True)
    a.add_argument("--version", required=True)
    a.add_argument("--tag", required=True)
    a.add_argument("--track", default="dev", choices=["dev", "release"])
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

    t = sub.add_parser("track")
    t.add_argument("--manifest", type=Path, default=None)
    t.add_argument("--tag", default=None, help="Fallback when the manifest omits release.track.")
    t.set_defaults(func=track)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
