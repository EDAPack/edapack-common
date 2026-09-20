"""Unit tests for release-notes.py — both modes, fully offline.

Compare-mode fixtures are shaped after real chipsalliance/verible history,
which is the upstream this mode was written for: no changelog file, empty
release bodies, no squash-merge, and therefore a commit log dominated by
`Merge branch ... into master` and `Fixed formatting`.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conftest import load_script  # noqa: E402

rn = load_script("release-notes")


# --------------------------------------------------------------------------- #
# Changelog mode (pre-existing behavior — these lock it down)
# --------------------------------------------------------------------------- #
RST_CHANGELOG = """\
Verilator 5.051 devel
=====================

**Under development**

Verilator 5.050 2026-07-01
==========================

**Major:**

* Added a thing.
* Fixed another thing.

Verilator 5.048 2026-05-01
==========================

* Older news.
"""

MD_CHANGELOG = """\
# Changelog

## 5.050 - 2026-07-01

- Added a thing.

## 5.048 - 2026-05-01

- Older news.
"""


def test_extract_section_rst():
    section = rn.extract_section(RST_CHANGELOG, "5.050")
    assert "Added a thing." in section
    assert "Older news." not in section
    assert "Under development" not in section
    # The === underline must not survive: it would promote the line above it
    # to a heading when GitHub renders the body as Markdown.
    assert "====" not in section


def test_extract_section_markdown():
    section = rn.extract_section(MD_CHANGELOG, "5.050")
    assert "Added a thing." in section
    assert "Older news." not in section


def test_extract_section_absent_version():
    assert rn.extract_section(MD_CHANGELOG, "9.999") is None


# --------------------------------------------------------------------------- #
# Compare mode
# --------------------------------------------------------------------------- #
def _commit(message):
    return {"commit": {"message": message}}


MERGE_COMMITS = [
    _commit("Merge branch 'chipsalliance:master' into master"),
    _commit("Fixed formatting"),
    _commit("Merge pull request #2586 from acme/labels\n\n"
            "verilog: make generate-label-prefix configurable via style_regex"),
    _commit("Merge origin/master into fix/2544"),
    _commit("Merge pull request #2595 from acme/space\n\nAdd --class_parameter_space"),
    _commit("Merge pull request #2586 from acme/labels\n\nduplicate, same PR"),
]


def test_extract_prs_from_merge_commits():
    prs = rn.extract_prs(MERGE_COMMITS)
    assert prs == [
        ("2586", "verilog: make generate-label-prefix configurable via style_regex"),
        ("2595", "Add --class_parameter_space"),
    ]


def test_extract_prs_dedupes_by_number():
    # The fixture merges #2586 twice; the second must not produce a second line.
    assert [n for n, _ in rn.extract_prs(MERGE_COMMITS)].count("2586") == 1


def test_extract_prs_from_squash_commits():
    commits = [
        _commit("Fix the parser (#101)"),
        _commit("Bump deps (#102)\n\nbody text"),
        _commit("A commit with no PR reference"),
    ]
    assert rn.extract_prs(commits) == [
        ("101", "Fix the parser"),
        ("102", "Bump deps"),
    ]


def test_merge_commits_win_over_squash_shaped_subjects():
    """The bug a live run against Verible caught.

    In a repo that does not squash-merge, the PR's own branch commits are in
    the range too, and one ending in an issue reference looks exactly like a
    squash merge. Blending the two heuristics listed the same change twice,
    under the PR number and under the referenced issue number.
    """
    commits = [
        _commit("Fix indentation of `let` (#868) (#2001) (#2348)"),
        _commit("Merge pull request #2596 from acme/let\n\n"
                "Fix indentation of `let` (#868) (#2001) (#2348)"),
    ]
    assert rn.extract_prs(commits) == [
        ("2596", "Fix indentation of `let` (#868) (#2001) (#2348)")
    ]


def test_squash_shape_used_when_no_merge_commits():
    # A genuinely squash-merging repo must still get PR-based notes.
    commits = [_commit("Fix the parser (#101)"), _commit("Chore (#102)")]
    assert [n for n, _ in rn.extract_prs(commits)] == ["101", "102"]


def test_extract_prs_merge_commit_without_a_title():
    # A stripped merge body leaves only the number. Keep the PR rather than
    # silently dropping it from the notes.
    prs = rn.extract_prs([_commit("Merge pull request #7 from acme/x")])
    assert prs == [("7", "(no title)")]


def test_extract_commit_subjects_filters_noise():
    subjects = rn.extract_commit_subjects(MERGE_COMMITS)
    assert not any(s.startswith("Merge ") for s in subjects)
    assert "Fixed formatting" not in subjects


def test_extract_commit_subjects_dedupes():
    commits = [_commit("Same subject"), _commit("Same subject"), _commit("Other")]
    assert rn.extract_commit_subjects(commits) == ["Same subject", "Other"]


COMPARE = {
    "total_commits": 131,
    "html_url": "https://github.com/acme/verible/compare/v0.0-4163...v0.0-4294",
    "commits": MERGE_COMMITS,
}


def test_render_compare_lists_prs_not_commits():
    body = rn.render_compare(
        "verible-bin", "0.0-4294-gc1d8f5e8",
        "https://github.com/acme/verible/releases/tag/v0.0-4294-gc1d8f5e8",
        COMPARE, "v0.0-4163-g6cce8f19")
    assert "#2586 verilog: make generate-label-prefix configurable" in body
    assert "#2595 Add --class_parameter_space" in body
    # The noise the PR titles exist to replace must not appear.
    assert "Merge branch" not in body
    assert "Fixed formatting" not in body
    assert "131 commits since `v0.0-4163-g6cce8f19`" in body
    assert "2 merged pull requests" in body
    assert "[Full diff](https://github.com/acme/verible/compare/" in body


def test_render_compare_reports_api_truncation():
    # 6 commits returned for a 131-commit range: the compare API capped it.
    body = rn.render_compare("p", "1", "u", COMPARE, "prev")
    assert "GitHub compare API limit" in body


def test_render_compare_no_truncation_note_when_complete():
    complete = dict(COMPARE, total_commits=len(MERGE_COMMITS))
    body = rn.render_compare("p", "1", "u", complete, "prev")
    assert "compare API limit" not in body


def test_render_compare_caps_entries():
    commits = [_commit("Merge pull request #%d from a/b\n\ntitle %d" % (i, i))
               for i in range(50)]
    body = rn.render_compare("p", "1", "u",
                             {"total_commits": 50, "commits": commits,
                              "html_url": "u"},
                             "prev", max_entries=10)
    assert "…and 40 more." in body
    assert body.count("\n- #") == 10


def test_render_compare_falls_back_to_subjects():
    commits = [_commit("Fix a thing"), _commit("Merge branch 'x' into master")]
    body = rn.render_compare("p", "1", "u",
                             {"total_commits": 2, "commits": commits, "html_url": "u"},
                             "prev")
    assert "- Fix a thing" in body
    assert "1 change:" in body
    assert "pull request" not in body


def test_render_compare_singular_plural():
    one = [_commit("Merge pull request #1 from a/b\n\nonly one")]
    body = rn.render_compare("p", "1", "u",
                             {"total_commits": 1, "commits": one, "html_url": "u"},
                             "prev")
    assert "1 commit since" in body
    assert "1 merged pull request:" in body


# --------------------------------------------------------------------------- #
# main() wiring
# --------------------------------------------------------------------------- #
CANDIDATE = {
    "track": "release",
    "release_notes": rn.COMPARE_SENTINEL,
    "inputs": [{
        "name": "verible", "role": "core",
        "repo": "https://github.com/acme/verible",
        "ref": "v0.0-4294-gc1d8f5e8",
        "resolved_sha": "c" * 40, "version": "0.0-4294-gc1d8f5e8",
    }],
    "inputs_digest": "sha256:" + "0" * 64,
}


def _write_candidate(tmp_path, **overrides):
    doc = dict(CANDIDATE)
    doc.update(overrides)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(doc))
    return path


def test_main_first_release_needs_no_network(tmp_path):
    out = tmp_path / "notes.md"
    rc = rn.main(["--candidate", str(_write_candidate(tmp_path)),
                  "--package", "verible-bin", "--version", "0.0-4294-gc1d8f5e8",
                  "--output", str(out)])
    assert rc == 0
    assert "First release of verible-bin" in out.read_text()


def test_main_same_ref_is_soft_failure(tmp_path):
    out = tmp_path / "notes.md"
    rc = rn.main(["--candidate", str(_write_candidate(tmp_path)),
                  "--package", "verible-bin", "--version", "0.0-4294-gc1d8f5e8",
                  "--previous-ref", "v0.0-4294-gc1d8f5e8",
                  "--output", str(out)])
    assert rc == 2
    assert not out.exists()


def test_main_without_release_notes_declared(tmp_path):
    out = tmp_path / "notes.md"
    rc = rn.main(["--candidate", str(_write_candidate(tmp_path, release_notes=None)),
                  "--package", "p", "--version", "1", "--output", str(out)])
    assert rc == 2


class FakeBackend:
    """Offline stand-in for GitBackend. Never touches the network."""

    def __init__(self, compare_result=None, file_text=None, exc=None):
        self._compare = compare_result
        self._text = file_text
        self._exc = exc
        self.calls = []

    def compare(self, repo, base, head):
        self.calls.append(("compare", repo, base, head))
        if self._exc:
            raise self._exc
        return self._compare

    def read_file(self, repo, ref, path):
        self.calls.append(("read_file", repo, ref, path))
        if self._exc:
            raise self._exc
        return self._text


def test_main_compare_failure_is_soft(tmp_path):
    """A GitHub API hiccup must not block a release."""
    out = tmp_path / "notes.md"
    rc = rn.main(["--candidate", str(_write_candidate(tmp_path)),
                  "--package", "p", "--version", "1",
                  "--previous-ref", "v0.0-4163-g6cce8f19",
                  "--output", str(out)],
                 backend=FakeBackend(exc=RuntimeError("502 Bad Gateway")))
    assert rc == 2
    assert not out.exists()


def test_main_compare_end_to_end(tmp_path):
    out = tmp_path / "notes.md"
    backend = FakeBackend(compare_result=COMPARE)
    rc = rn.main(["--candidate", str(_write_candidate(tmp_path)),
                  "--package", "verible-bin", "--version", "0.0-4294-gc1d8f5e8",
                  "--previous-ref", "v0.0-4163-g6cce8f19",
                  "--output", str(out)],
                 backend=backend)
    assert rc == 0
    assert backend.calls == [
        ("compare", "https://github.com/acme/verible",
         "v0.0-4163-g6cce8f19", "v0.0-4294-gc1d8f5e8")
    ]
    body = out.read_text()
    assert "#2586 verilog: make generate-label-prefix" in body
    assert "Upstream: https://github.com/acme/verible/releases/tag/v0.0-4294" in body


def test_main_changelog_mode_still_works(tmp_path):
    """The pre-existing mode must be untouched by the compare-mode addition."""
    out = tmp_path / "notes.md"
    backend = FakeBackend(file_text=RST_CHANGELOG)
    rc = rn.main(["--candidate", str(_write_candidate(tmp_path,
                                                      release_notes="Changes")),
                  "--package", "verilator-bin", "--version", "5.050",
                  "--output", str(out)],
                 backend=backend)
    assert rc == 0
    assert backend.calls[0][0] == "read_file"
    assert backend.calls[0][3] == "Changes"
    body = out.read_text()
    assert "Added a thing." in body
    assert "Older news." not in body


def test_main_changelog_mode_missing_section_is_soft(tmp_path):
    out = tmp_path / "notes.md"
    rc = rn.main(["--candidate", str(_write_candidate(tmp_path,
                                                      release_notes="Changes")),
                  "--package", "verilator-bin", "--version", "9.999",
                  "--output", str(out)],
                 backend=FakeBackend(file_text=RST_CHANGELOG))
    assert rc == 2
    assert not out.exists()
