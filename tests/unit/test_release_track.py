"""Tests for the release track: tag filtering, version probe, track selection,
previous-manifest track detection, and changelog extraction.

All offline — the resolve backend is injected, and the changelog extractor is a
pure text function.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conftest import load_script  # noqa: E402

ri = load_script("resolve-inputs")
gm = load_script("gen-manifest")
rn = load_script("release-notes")


class FakeBackend:
    """Deterministic offline backend, with file reads for the version probe."""

    def __init__(self, refs_by_repo, files=None):
        self._refs = refs_by_repo
        self._files = files or {}

    def ls_remote(self, repo):
        return dict(self._refs[repo])

    def latest_release(self, repo):
        raise AssertionError("latest-release must not be used for a tags-only repo")

    def read_file(self, repo, ref, path):
        try:
            return self._files[(repo, ref, path)]
        except KeyError:
            raise ValueError("404 {} {} {}".format(repo, ref, path))


REPO = "https://github.com/verilator/verilator"
CONFIGURE_AC = 'AC_INIT([Verilator],[5.051 devel],[verilator@veripool.org])\n'

REFS = {
    REPO: {
        "refs/heads/master": "a" * 40,
        "refs/tags/v5.048": "c" * 40,
        "refs/tags/v5.050": "d" * 40,
        "refs/tags/v5.046": "e" * 40,
    }
}


def _backend(extra_refs=None, files=None):
    refs = {REPO: dict(REFS[REPO])}
    if extra_refs:
        refs[REPO].update(extra_refs)
    if files is None:  # not `or` — an explicit {} means "no files readable"
        files = {(REPO, "a" * 40, "configure.ac"): CONFIGURE_AC}
    return FakeBackend(refs, files)


SPEC = {
    "schema": "edapack.build-inputs/1",
    "core": {
        "name": "verilator",
        "repo": REPO,
        "policy": "branch:master",
        "release_policy": r"latest-tag:^v\d+\.\d+$",
        "version_probe": "configure.ac:AC_INIT",
        "release_notes": "Changes",
    },
}


# --------------------------------------------------------------------------- #
# latest-tag regex filtering
# --------------------------------------------------------------------------- #
def test_latest_tag_picks_newest_numerically():
    ref, sha = ri.resolve_policy(_backend(), REPO, "latest-tag")
    assert (ref, sha) == ("v5.050", "d" * 40)


def test_latest_tag_regex_excludes_prerelease_tags():
    # An rc tag sorts highest numerically (5,52,1) but must not be selected.
    be = _backend({"refs/tags/v5.052-rc1": "f" * 40})
    assert ri.resolve_policy(be, REPO, "latest-tag")[0] == "v5.052-rc1"
    ref, _ = ri.resolve_policy(be, REPO, r"latest-tag:^v\d+\.\d+$")
    assert ref == "v5.050"


def test_latest_tag_regex_no_match_raises():
    with pytest.raises(ValueError):
        ri.resolve_policy(_backend(), REPO, "latest-tag:^nope-")


def test_latest_tag_bad_regex_raises():
    with pytest.raises(ValueError):
        ri.resolve_policy(_backend(), REPO, "latest-tag:^v(")


# --------------------------------------------------------------------------- #
# version_probe (P2): a branch policy must still yield a version
# --------------------------------------------------------------------------- #
def test_probe_version_from_ac_init():
    assert ri.probe_version(CONFIGURE_AC, "AC_INIT") == "5.051"


def test_probe_version_absent_key():
    assert ri.probe_version(CONFIGURE_AC, "NOT_THERE") is None


def test_branch_policy_version_comes_from_probe():
    out = ri.resolve_inputs(SPEC, recipe_sha="r1", backend=_backend())
    core = out["inputs"][0]
    assert core["ref"] == "master"
    assert core["version"] == "5.051"  # not None -> no more "0.<build-id>"


def test_probe_failure_is_non_fatal():
    # No configure.ac available at the resolved ref.
    out = ri.resolve_inputs(SPEC, recipe_sha="r1", backend=_backend(files={}))
    assert out["inputs"][0]["version"] is None


def test_version_probe_requires_path_and_key():
    with pytest.raises(ValueError):
        ri.resolve_version_probe(_backend(), REPO, "master", "configure.ac")


# --------------------------------------------------------------------------- #
# track selection
# --------------------------------------------------------------------------- #
def test_release_track_resolves_core_to_newest_tag():
    out = ri.resolve_inputs(SPEC, recipe_sha="r1", backend=_backend(), track="release")
    core = out["inputs"][0]
    assert (core["ref"], core["version"]) == ("v5.050", "5.050")
    assert out["track"] == "release"


def test_release_candidate_present_on_both_tracks():
    # The dev track needs it too, to know when to stand down.
    for track in ("dev", "release"):
        out = ri.resolve_inputs(SPEC, recipe_sha="r1", backend=_backend(), track=track)
        assert out["release_candidate"]["tag"] == "v5.050"


def test_release_candidate_none_without_release_policy():
    spec = {"schema": SPEC["schema"], "core": dict(SPEC["core"])}
    del spec["core"]["release_policy"]
    out = ri.resolve_inputs(spec, recipe_sha="r1", backend=_backend())
    assert out["release_candidate"] is None


def test_release_track_without_release_policy_raises():
    spec = {"schema": SPEC["schema"], "core": dict(SPEC["core"])}
    del spec["core"]["release_policy"]
    with pytest.raises(ValueError):
        ri.resolve_inputs(spec, recipe_sha="r1", backend=_backend(), track="release")


def test_core_ref_override_wins_on_release_track():
    out = ri.resolve_inputs(SPEC, recipe_sha="r1", core_ref="v5.048",
                            backend=_backend(), track="release")
    assert out["inputs"][0]["ref"] == "v5.048"


def test_unknown_track_raises():
    with pytest.raises(ValueError):
        ri.resolve_inputs(SPEC, recipe_sha="r1", backend=_backend(), track="nightly")


def test_release_notes_path_carried_into_candidate():
    out = ri.resolve_inputs(SPEC, recipe_sha="r1", backend=_backend())
    assert out["release_notes"] == "Changes"


# --------------------------------------------------------------------------- #
# previous-manifest track detection (P3)
# --------------------------------------------------------------------------- #
def test_track_of_prefers_recorded_field():
    m = {"release": {"tag": "v5.050", "track": "dev"}}
    assert gm.track_of(m) == "dev"  # recorded field beats the tag shape


def test_track_of_falls_back_to_tag_shape():
    # Manifests published before `release.track` existed.
    assert gm.track_of({"release": {"tag": "v5.051.32639969514"}}) == "dev"
    assert gm.track_of({"release": {"tag": "v5.050"}}) == "release"


def test_track_of_tag_argument_without_manifest():
    assert gm.track_of(None, "v5.051.32639969514") == "dev"
    assert gm.track_of(None, "v5.050") == "release"


def test_track_of_unknown_without_anything():
    assert gm.track_of(None, None) is None


# --------------------------------------------------------------------------- #
# changelog extraction
# --------------------------------------------------------------------------- #
CHANGES = """\
Revision History
================

Verilator 5.050 2026-07-01
==========================

**Important:**

* Support covergroups (#784). [Matthew Ballance]

Verilator 5.048 2026-04-26
==========================

* Older thing.
"""


def test_extract_section_bounded_by_next_heading():
    body = rn.extract_section(CHANGES, "5.050")
    assert body.startswith("Verilator 5.050 2026-07-01")
    assert "covergroups" in body
    assert "Older thing" not in body
    assert "5.048" not in body


def test_extract_section_drops_rst_underline():
    # A '===' line would promote the heading to an H1 in the Markdown body.
    body = rn.extract_section(CHANGES, "5.050")
    assert "====" not in body


def test_extract_section_missing_version():
    assert rn.extract_section(CHANGES, "9.999") is None


def test_extract_section_markdown_headings():
    md = "# Changelog\n\n## 5.050 - 2026-07-01\n\n* New.\n\n## 5.048\n\n* Old.\n"
    body = rn.extract_section(md, "5.050")
    assert "New." in body and "Old." not in body


def test_render_includes_upstream_link():
    out = rn.render("verilator-bin", "5.050", "* thing", "https://x/tag/v5.050")
    assert "verilator-bin 5.050" in out
    assert "https://x/tag/v5.050" in out
    assert "## Upstream changes" in out
