#!/usr/bin/env python3
"""Build the release body describing what changed upstream.

Two modes, selected by `core.release_notes` in build-inputs.yaml.

CHANGELOG MODE (`release_notes: <path>`)
Some upstream projects (Verilator among them) publish git tags but no GitHub
Releases, so there is no release body to copy. The equivalent content is the
changelog file in the repo, whose newest section at a tag describes that tag.
`core.release_notes` names that file; this script pulls it at the resolved SHA
and slices out the section for `--version`.

Recognized section headings (the leading line of a section):

    Verilator 5.050 2026-07-01      <- reStructuredText, underlined with ===
    ## 5.050 - 2026-07-01           <- Markdown ATX
    # 5.050                         <- Markdown ATX

A section ends at the next heading of the same shape. The `===`/`---` underline
is dropped: GitHub renders release bodies as Markdown, where an underline would
promote the preceding line to a heading.

COMPARE MODE (`release_notes: gh-compare`)
Other upstreams have no changelog at all AND publish empty release bodies —
Verible is the case this was written for: 40 consecutive releases, every one
with a zero-length body and no changelog file in the tree. The only description
of what changed is the commit range itself, so this mode asks GitHub to compare
the previously released upstream ref with this one and renders the MERGED PULL
REQUESTS in that range.

Pull requests, not commits, and the distinction is the whole point: a repo that
does not squash-merge has a commit log that is mostly `Merge branch 'x' into
master` and `Fixed formatting`. The PR titles underneath are written for humans.
Commit subjects are used only as a fallback when a range contains no
recognizable PR merges at all.

The previous upstream ref comes from the previous release's manifest.json
(`inputs[core].ref`), which is what manifest provenance is for — it is NOT the
previous git tag of this repo, because a tool repo's tag and its upstream's tag
need not agree (`yosys-0.50` upstream vs `v0.50` here).

Failure is deliberately soft in both modes — an upstream changelog reformat, or
a GitHub API hiccup, must not block a release. Exit code 2 means "no notes
available, publish default notes"; the caller treats that as non-fatal. Exit 1
is a usage error.
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
    # An ATX section also ends at any heading ABOVE its own level, versioned
    # or not. xezim's NOTES.md groups releases under minor-version banners --
    #     # What's new in 0.11 / ### 0.11.0 ... / # What's new in 0.10
    # -- and without this the 0.11.0 notes ended with the next banner.
    # Strictly above, not at: a same-level unversioned heading may be a
    # sub-part of the section in some changelog styles.
    m = re.match(r"^(#{1,6})\s", lines[start].strip())
    start_level = len(m.group(1)) if m else None
    i = start + 1
    # Drop an RST underline directly beneath the heading.
    if i < len(lines) and _UNDERLINE_RE.match(lines[i]):
        i += 1
    while i < len(lines):
        line = lines[i]
        if start_level:
            m = re.match(r"^(#{1,6})\s", line.strip())
            if m and len(m.group(1)) < start_level:
                break
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


# --------------------------------------------------------------------------- #
# Compare mode
# --------------------------------------------------------------------------- #
COMPARE_SENTINEL = "gh-compare"

# `Merge pull request #2592 from fangism/verible-cl-972730734` — GitHub's own
# merge-commit subject. The PR title is the first non-empty line below it.
_MERGE_PR_RE = re.compile(r"^Merge pull request #(\d+) from ")
# `Fix the thing (#2592)` — the squash-merge subject shape.
_SQUASH_PR_RE = re.compile(r"^(.*?)\s*\(#(\d+)\)\s*$")
# A git trailer line: `PiperOrigin-RevId: 980085178`, `Signed-off-by: …`,
# `Change-Id: I…`. The key must be capitalized AND hyphenated -- that is what
# separates a trailer from a conventional title like `verilog: make x
# configurable`, which is also `word: text`.
_TRAILER_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+:\s")
# https://github.com/<owner>/<repo>/... -> owner/repo
_GITHUB_SLUG_RE = re.compile(r"^https?://github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?/")
# A trailing `(#123)` squash reference inside a commit subject.
_SQUASH_REF_RE = re.compile(r"\(#(\d+)\)\s*$")
# Commit subjects that carry no information for a reader of release notes.
_NOISE_RE = re.compile(
    r"^(Merge (branch|remote-tracking branch|origin/|pull request)"
    r"|Fixed? formatting"
    r"|Update from upstream"
    r"|Bump \S+ from)",
    re.I,
)


def extract_prs(commits):
    """Return [(number, title)] for the pull requests merged in `commits`.

    Two shapes are recognized, and merge commits WIN WHOLESALE rather than
    being blended with squash subjects. A repo either squash-merges or it does
    not, and mixing the two heuristics actively produces garbage: in a
    non-squash repo every branch commit is present in the range, and a branch
    commit whose subject ends in an issue reference —

        Fix indentation of `let` (#868) (#2001) (#2348)

    — is indistinguishable from a squash merge of PR #2348. Verible's real
    history produced exactly that, listing one change twice under two numbers.
    So: if any merge-commit PRs were found, they are the answer.

    A merge whose body opens with a git trailer instead of a title is NOT a
    PR entry. That is the shape of an IMPORTED merge -- Copybara bringing a
    google/xls pull request into the xlsynth fork writes

        Merge pull request #4881 from mag-mga:mag-mga/select-to-xor
        <blank>
        PiperOrigin-RevId: 980085178

        -- and the number belongs to another repository, the title does not
    exist, and the merged branch's own commits are in the range anyway. Letting
    such merges "win wholesale" turned a 95-commit xlsynth window into seven
    `PiperOrigin-RevId:` lines and dropped everything else. Skipped, they leave
    the commit-subject fallback to do its job. A merge whose body is simply
    EMPTY is still kept as "(no title)": that is a stripped merge of this
    repo's own PR, not an import.

    Dedupes by number, preserving the order GitHub returned (oldest first).
    """
    merged, squashed = [], []
    for c in commits:
        message = ((c.get("commit") or {}).get("message") or "")
        lines = message.splitlines()
        if not lines:
            continue
        subject = lines[0].strip()

        m = _MERGE_PR_RE.match(subject)
        if m:
            title = next((ln.strip() for ln in lines[1:] if ln.strip()), "")
            if _TRAILER_RE.match(title):
                continue
            if not title:
                # A merge commit whose body was stripped tells us nothing more
                # than the number; keep it rather than dropping the PR.
                title = "(no title)"
            merged.append((m.group(1), title))
            continue

        m = _SQUASH_PR_RE.match(subject)
        if m and m.group(1).strip():
            squashed.append((m.group(2), m.group(1).strip()))

    # Squash shapes describe the range only if the range IS squash merges. A
    # fork that squash-merges its own PRs but takes most changes as plain
    # cherry-picks (xlsynth: 12 `(#N)` subjects among 88 commits, one of them
    # the only commit adding a new CLI flag) would otherwise lose most of its
    # history to the dozen PRs that happen to carry a number. Below half, the
    # commit-subject fallback describes the range better.
    if not merged and squashed:
        meaningful = [
            c for c in commits
            if not _NOISE_RE.match(
                (((c.get("commit") or {}).get("message") or "").splitlines() or [""])[0])
        ]
        if len(squashed) * 2 < len(meaningful):
            squashed = []

    out, seen = [], set()
    for number, title in (merged or squashed):
        if number in seen:
            continue
        seen.add(number)
        out.append((number, title))
    return out


def extract_commit_subjects(commits):
    """Fallback for a range with no recognizable PR merges: useful subjects."""
    out, seen = [], set()
    for c in commits:
        message = ((c.get("commit") or {}).get("message") or "")
        subject = message.splitlines()[0].strip() if message.splitlines() else ""
        if not subject or _NOISE_RE.match(subject) or subject in seen:
            continue
        seen.add(subject)
        out.append(subject)
    return out


def render_compare(package, version, upstream_url, compare, previous_ref,
                   max_entries=40):
    """Render the body for a range comparison.

    `compare` is the GitHub compare API payload. Returns markdown.
    """
    commits = compare.get("commits") or []
    total = compare.get("total_commits", len(commits))
    compare_url = compare.get("html_url") or ""

    # `#N` must name the UPSTREAM repository: a bare `#2586` in this package's
    # release body is autolinked by GitHub to this package's own issue #2586.
    m = _GITHUB_SLUG_RE.match(upstream_url or "")
    ref = "{}#".format(m.group(1)) if m else "#"

    prs = extract_prs(commits)
    if prs:
        kind = "merged pull request" + ("s" if len(prs) != 1 else "")
        entries = ["- {}{} {}".format(ref, n, t) for n, t in prs]
    else:
        subjects = extract_commit_subjects(commits)
        kind = "change" + ("s" if len(subjects) != 1 else "")
        entries = ["- {}".format(_SQUASH_REF_RE.sub(r"(" + ref + r"\1)", s))
                   for s in subjects]

    shown = entries[:max_entries]
    hidden = len(entries) - len(shown)

    lines = [
        "Automated build of {} {}.".format(package, version),
        "",
        "Upstream: {}".format(upstream_url),
        "",
        "## Upstream changes",
        "",
        "{} commit{} since `{}` — {} {}:".format(
            total, "" if total == 1 else "s", previous_ref, len(entries), kind),
        "",
    ]
    lines.extend(shown)
    if hidden > 0:
        lines.append("- …and {} more.".format(hidden))
    # The compare API returns at most 250 commits; say so rather than letting a
    # big range look complete when it is not.
    if len(commits) < total:
        lines.append("")
        lines.append(
            "_Listing derived from the first {} of {} commits (GitHub compare "
            "API limit)._".format(len(commits), total))
    if compare_url:
        lines.extend(["", "[Full diff]({})".format(compare_url)])
    return "\n".join(lines) + "\n"


def render_first_release(package, version, upstream_url):
    """No previous release to compare against — still say what this is."""
    return (
        "Automated build of {pkg} {ver}.\n\n"
        "Upstream: {url}\n\n"
        "## Upstream changes\n\n"
        "First release of {pkg}; there is no previous build to compare "
        "against.\n"
    ).format(pkg=package, ver=version, url=upstream_url)


def _default_backend():
    """The real network backend, imported late so --help needs no network."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "resolve_inputs", str(Path(__file__).resolve().parent / "resolve-inputs.py")
    )
    ri = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ri)
    return ri.GitBackend()


def main(argv=None, backend=None) -> int:
    """`backend` is injectable so the wiring below is testable offline."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--candidate", required=True, type=Path,
                   help="resolve-inputs.py output: core repo/ref + release_notes path.")
    p.add_argument("--notes-path", default=None,
                   help="Changelog path in the upstream repo; defaults to the "
                        "candidate's release_notes (from core.release_notes).")
    p.add_argument("--package", required=True)
    p.add_argument("--version", required=True, help="Version being released.")
    p.add_argument("--previous-ref", default=None,
                   help="Compare mode: the upstream ref the PREVIOUS release "
                        "was built from (from its manifest's inputs[core].ref). "
                        "Omit for a first release.")
    p.add_argument("--max-entries", type=int, default=40,
                   help="Compare mode: cap the listed pull requests.")
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

    upstream_url = _upstream_url(core["repo"], core["ref"])

    if notes_path == COMPARE_SENTINEL:
        if not args.previous_ref:
            print("release-notes: no --previous-ref; rendering first-release notes",
                  file=sys.stderr)
            args.output.write_text(
                render_first_release(args.package, args.version, upstream_url),
                encoding="utf-8",
            )
            return 0
        if args.previous_ref == core["ref"]:
            print("release-notes: previous ref equals this one; nothing to compare",
                  file=sys.stderr)
            return 2
        try:
            compare = (backend or _default_backend()).compare(
                core["repo"], args.previous_ref, core["ref"])
        except Exception as exc:
            print("release-notes: compare {}...{} failed: {}".format(
                args.previous_ref, core["ref"], exc), file=sys.stderr)
            return 2
        args.output.write_text(
            render_compare(args.package, args.version, upstream_url, compare,
                           args.previous_ref, args.max_entries),
            encoding="utf-8",
        )
        return 0

    try:
        text = (backend or _default_backend()).read_file(
            core["repo"], core["resolved_sha"], notes_path)
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
        render(args.package, args.version, section, upstream_url),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
