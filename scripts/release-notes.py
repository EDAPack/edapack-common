#!/usr/bin/env python3
"""Extract an upstream changelog section for the version being released.

Some upstream projects (Verilator among them) publish git tags but no GitHub
Releases, so there is no release body to copy. The equivalent content is the
changelog file in the repo, whose newest section at a tag describes that tag.
`core.release_notes` in build-inputs.yaml names that file; this script pulls it
at the resolved SHA and slices out the section for `--version`.

Recognized section headings (the leading line of a section):

    Verilator 5.050 2026-07-01      <- reStructuredText, underlined with ===
    ## 5.050 - 2026-07-01           <- Markdown ATX
    # 5.050                         <- Markdown ATX

A section ends at the next heading of the same shape. The `===`/`---` underline
is dropped: GitHub renders release bodies as Markdown, where an underline would
promote the preceding line to a heading.

Failure is deliberately soft — an upstream changelog reformat must not block a
release. Exit code 2 means "no section found, publish default notes"; the caller
treats that as non-fatal. Exit 1 is a usage error.
"""

# NOTE: no `from __future__ import annotations` — must run under the
# manylinux2014 / manylinux_2_28 system Python 3.6 (that feature is 3.7+).
import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

_UNDERLINE_RE = re.compile(r"^[=\-~^\"']{3,}\s*$")


def _heading_version(line: str) -> Optional[str]:
    """Return the version a changelog heading names, else None."""
    stripped = line.strip()
    if not stripped:
        return None
    # Markdown ATX: '## 5.050 - 2026-07-01' / '# v5.050'
    m = re.match(r"^#{1,6}\s+v?(\d+(?:\.\d+)+)\b", stripped)
    if m:
        return m.group(1)
    # Bare/prefixed: 'Verilator 5.050 2026-07-01' / '5.050 2026-07-01'
    m = re.match(r"^(?:[A-Za-z][\w.+-]*\s+)?v?(\d+(?:\.\d+)+)\b", stripped)
    if m:
        return m.group(1)
    return None


def extract_section(text: str, version: str) -> Optional[str]:
    """Return the changelog body for `version`, or None if absent.

    Matching is on the version token, so `Verilator 5.050 2026-07-01` and
    `## 5.050` both match version `5.050`.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _heading_version(line) == version:
            start = i
            break
    if start is None:
        return None

    body: List[str] = [lines[start].strip()]
    i = start + 1
    # Drop an RST underline directly beneath the heading.
    if i < len(lines) and _UNDERLINE_RE.match(lines[i]):
        i += 1
    while i < len(lines):
        line = lines[i]
        nxt = _heading_version(line)
        # A new section starts here — but only if it is a real heading, i.e. a
        # different version. Underlined or ATX both count.
        if nxt and nxt != version:
            underlined = i + 1 < len(lines) and _UNDERLINE_RE.match(lines[i + 1])
            if underlined or line.strip().startswith("#"):
                break
        body.append(line.rstrip())
        i += 1

    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body) if len(body) > 1 else None


def render(package: str, version: str, section: str, upstream_url: str) -> str:
    return (
        "Automated build of {pkg} {ver}.\n\n"
        "Upstream: {url}\n\n"
        "## Upstream changes\n\n"
        "{section}\n"
    ).format(pkg=package, ver=version, url=upstream_url, section=section)


def _upstream_url(repo: str, ref: str) -> str:
    return "{}/releases/tag/{}".format(repo.rstrip("/").replace(".git", ""), ref)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--candidate", required=True, type=Path,
                   help="resolve-inputs.py output: core repo/ref + release_notes path.")
    p.add_argument("--notes-path", default=None,
                   help="Changelog path in the upstream repo; defaults to the "
                        "candidate's release_notes (from core.release_notes).")
    p.add_argument("--package", required=True)
    p.add_argument("--version", required=True, help="Version being released.")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)

    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    core = next((i for i in candidate["inputs"] if i["role"] == "core"), None)
    if core is None:
        print("release-notes: candidate has no core input", file=sys.stderr)
        return 1

    notes_path = args.notes_path or candidate.get("release_notes")
    if not notes_path:
        print("release-notes: no release_notes declared", file=sys.stderr)
        return 2

    # Imported late so a missing network backend can't break --help.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "resolve_inputs", str(Path(__file__).resolve().parent / "resolve-inputs.py")
    )
    ri = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ri)

    try:
        text = ri.GitBackend().read_file(core["repo"], core["resolved_sha"], notes_path)
    except Exception as exc:
        print("release-notes: cannot read {}: {}".format(notes_path, exc), file=sys.stderr)
        return 2

    section = extract_section(text, args.version)
    if not section:
        print(
            "release-notes: no section for {} in {}".format(args.version, notes_path),
            file=sys.stderr,
        )
        return 2

    args.output.write_text(
        render(args.package, args.version, section, _upstream_url(core["repo"], core["ref"])),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
