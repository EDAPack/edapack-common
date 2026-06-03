#!/usr/bin/env python3
"""Stage Agent Skills from a source package into a release tree.

Canonical edapack copy (single source of truth; tool repos no longer vendor
this). Reads a `skill-manifest.yaml`, validates each listed skill (frontmatter,
referenced binaries present in the release), copies the skill directory into
`<dest>/<name>/`, and writes `<dest>/index.json` summarizing what shipped.

With --strict, an empty manifest or any missing skill/binary is a hard error
(used to enforce that every release actually ships its skills).

Manifest schema: see schemas/skill-manifest.schema.json.
SKILL.md frontmatter required keys: name, description, version.

Exit codes:
  0  success
  1  manifest / validation / copy failure
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ecyaml import parse_simple_yaml, parse_frontmatter  # noqa: E402


@dataclass
class SkillEntry:
    name: str
    path: Path
    binaries: list
    description: str
    version: str


def _validate(manifest_path: Path, source_root: Path, release_root: Path) -> list:
    manifest = parse_simple_yaml(manifest_path.read_text(encoding="utf-8"))
    skills_raw = manifest.get("skills") or []
    if not skills_raw:
        return []
    bin_dir = release_root / "bin"
    entries = []
    for s in skills_raw:
        for required in ("name", "path"):
            if required not in s:
                raise ValueError(f"manifest entry missing '{required}': {s}")
        name = s["name"]
        skill_dir = (source_root / s["path"]).resolve()
        if not skill_dir.is_dir():
            raise ValueError(f"skill '{name}': directory not found: {skill_dir}")
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            raise ValueError(f"skill '{name}': missing SKILL.md")
        try:
            fm = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"skill '{name}': {exc}")
        for required in ("name", "description", "version"):
            if not fm.get(required):
                raise ValueError(
                    f"skill '{name}': SKILL.md frontmatter missing '{required}'"
                )
        if fm["name"] != name:
            raise ValueError(
                f"skill '{name}': SKILL.md frontmatter name='{fm['name']}' "
                f"does not match manifest name"
            )
        binaries = s.get("binaries") or []
        for b in binaries:
            if not (bin_dir / b).exists() and not (bin_dir / f"{b}.exe").exists():
                raise ValueError(
                    f"skill '{name}': declared binary '{b}' not found in {bin_dir}"
                )
        entries.append(
            SkillEntry(
                name=name,
                path=skill_dir,
                binaries=list(binaries),
                description=fm["description"],
                version=str(fm["version"]),
            )
        )
    return entries


def _stage(entries: list, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for e in entries:
        target = dest / e.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(e.path, target, ignore=shutil.ignore_patterns(".research.md"))
        print(f"  staged skill: {e.name} ({len(e.binaries)} binaries)")
    index = {
        "schema": "edapack.skills/1",
        "skills": [
            {
                "name": e.name,
                "description": e.description,
                "version": e.version,
                "binaries": e.binaries,
                "path": e.name,
            }
            for e in entries
        ],
    }
    (dest / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"  wrote {dest / 'index.json'}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--source-root", required=True, type=Path,
                   help="Package source root (where skills/ lives).")
    p.add_argument("--release-root", required=True, type=Path,
                   help="Release directory (must contain bin/).")
    p.add_argument("--dest", required=True, type=Path,
                   help="Destination skills directory (usually <release-root>/skills).")
    p.add_argument("--strict", action="store_true",
                   help="Fail if the manifest lists no skills.")
    args = p.parse_args(argv)
    try:
        entries = _validate(args.manifest, args.source_root, args.release_root)
    except ValueError as exc:
        print(f"stage-skills: {exc}", file=sys.stderr)
        return 1
    if not entries:
        msg = "stage-skills: manifest lists no skills"
        if args.strict:
            print(msg + " (strict)", file=sys.stderr)
            return 1
        print(msg + "; nothing to do")
        return 0
    _stage(entries, args.dest)
    print(f"stage-skills: staged {len(entries)} skill(s) into {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
