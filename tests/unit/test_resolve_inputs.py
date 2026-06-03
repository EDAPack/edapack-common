"""Unit tests for resolve-inputs.py using an injected offline backend."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conftest import load_script  # noqa: E402

ri = load_script("resolve-inputs")


class FakeBackend:
    """Deterministic offline backend."""

    def __init__(self, refs_by_repo, latest_release_by_repo=None):
        self._refs = refs_by_repo
        self._latest = latest_release_by_repo or {}

    def ls_remote(self, repo):
        return dict(self._refs[repo])

    def latest_release(self, repo):
        return self._latest[repo]


REPO = "https://github.com/acme/widget"
REFS = {
    REPO: {
        "refs/heads/master": "a" * 40,
        "refs/heads/main": "b" * 40,
        "refs/tags/v1.2.0": "c" * 40,
        "refs/tags/v1.10.0": "d" * 40,
        "refs/tags/v1.9.0": "e" * 40,
    }
}


def _backend():
    return FakeBackend(REFS, {REPO: "v1.10.0"})


def test_branch_policy():
    ref, sha = ri.resolve_policy(_backend(), REPO, "branch:master")
    assert (ref, sha) == ("master", "a" * 40)


def test_tag_policy():
    ref, sha = ri.resolve_policy(_backend(), REPO, "tag:v1.2.0")
    assert (ref, sha) == ("v1.2.0", "c" * 40)


def test_latest_tag_version_sorted():
    # v1.10.0 must beat v1.9.0 (numeric, not lexical)
    ref, sha = ri.resolve_policy(_backend(), REPO, "latest-tag")
    assert ref == "v1.10.0"
    assert sha == "d" * 40


def test_latest_release_policy():
    ref, sha = ri.resolve_policy(_backend(), REPO, "latest-release")
    assert ref == "v1.10.0"
    assert sha == "d" * 40


def test_annotated_tag_peeled():
    refs = {REPO: {"refs/tags/v2.0.0": "1" * 40, "refs/tags/v2.0.0^{}": "2" * 40}}
    be = FakeBackend(refs)
    ref, sha = ri.resolve_ref(be, REPO, "v2.0.0")
    assert sha == "2" * 40  # peeled commit, not the tag object


def test_raw_sha_passthrough():
    ref, sha = ri.resolve_ref(_backend(), REPO, "deadbeef")
    assert (ref, sha) == ("deadbeef", "deadbeef")


def test_unknown_ref_raises():
    with pytest.raises(ValueError):
        ri.resolve_ref(_backend(), REPO, "nope-not-a-ref")


SPEC = {
    "schema": "edapack.build-inputs/1",
    "core": {"name": "widget", "repo": REPO, "policy": "branch:master"},
    "dependencies": [
        {"name": "gadget", "repo": REPO, "policy": "tag:v1.2.0"},
        {"name": "doodad", "repo": REPO, "policy": "latest-tag", "track": False},
    ],
}


def test_resolve_inputs_shape_and_roles():
    out = ri.resolve_inputs(SPEC, recipe_sha="r1", backend=_backend())
    names = [(i["name"], i["role"]) for i in out["inputs"]]
    assert names == [("widget", "core"), ("gadget", "dependency"), ("doodad", "dependency")]
    assert out["inputs_digest"].startswith("sha256:")


def test_core_ref_override():
    out = ri.resolve_inputs(SPEC, recipe_sha="r1", core_ref="v1.9.0", backend=_backend())
    core = out["inputs"][0]
    assert core["ref"] == "v1.9.0"
    assert core["resolved_sha"] == "e" * 40
    assert core["version"] == "1.9.0"


def test_dependency_override():
    out = ri.resolve_inputs(SPEC, recipe_sha="r1", overrides={"gadget": "v1.10.0"}, backend=_backend())
    gadget = next(i for i in out["inputs"] if i["name"] == "gadget")
    assert gadget["resolved_sha"] == "d" * 40


def test_digest_determinism_ignores_order():
    spec2 = dict(SPEC)
    spec2["dependencies"] = list(reversed(SPEC["dependencies"]))
    d1 = ri.resolve_inputs(SPEC, recipe_sha="r1", backend=_backend())["inputs_digest"]
    d2 = ri.resolve_inputs(spec2, recipe_sha="r1", backend=_backend())["inputs_digest"]
    assert d1 == d2


def test_digest_sensitive_to_recipe_sha():
    d1 = ri.resolve_inputs(SPEC, recipe_sha="r1", backend=_backend())["inputs_digest"]
    d2 = ri.resolve_inputs(SPEC, recipe_sha="r2", backend=_backend())["inputs_digest"]
    assert d1 != d2


def test_digest_sensitive_to_input_change():
    d1 = ri.resolve_inputs(SPEC, recipe_sha="r1", backend=_backend())["inputs_digest"]
    d2 = ri.resolve_inputs(SPEC, recipe_sha="r1", core_ref="v1.9.0", backend=_backend())["inputs_digest"]
    assert d1 != d2


def test_untracked_input_excluded_from_digest():
    # Changing only the untracked 'doodad' must NOT change the digest.
    base = ri.resolve_inputs(SPEC, recipe_sha="r1", backend=_backend())
    moved = ri.resolve_inputs(SPEC, recipe_sha="r1", overrides={"doodad": "v1.2.0"}, backend=_backend())
    assert base["inputs_digest"] == moved["inputs_digest"]
    # but the untracked input's resolved value did change
    d_base = next(i for i in base["inputs"] if i["name"] == "doodad")["resolved_sha"]
    d_moved = next(i for i in moved["inputs"] if i["name"] == "doodad")["resolved_sha"]
    assert d_base != d_moved


def test_github_slug():
    assert ri._github_slug("https://github.com/a/b.git") == "a/b"
    assert ri._github_slug("git@github.com:a/b") == "a/b"


def test_version_strips_nonnumeric_prefix():
    # tags come in several shapes across the tools
    assert ri._strip_v("v5.038") == "5.038"
    assert ri._strip_v("nextpnr-0.10") == "0.10"
    assert ri._strip_v("yosys-0.50") == "0.50"
    assert ri._strip_v("v13_0") == "13_0"
    assert ri._strip_v("main") == "main"  # no digits -> unchanged
