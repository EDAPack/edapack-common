"""Table-driven tests for manifest-diff.py decision logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conftest import load_script  # noqa: E402

md = load_script("manifest-diff")


def mk(digest, inputs):
    return {"inputs_digest": digest, "inputs": inputs}


CORE_A = [{"name": "widget", "resolved_sha": "a" * 40, "version": "1.0"}]
CORE_B = [{"name": "widget", "resolved_sha": "b" * 40, "version": "1.1"}]


def test_no_prior_release_builds():
    d = md.decide(mk("sha256:x", CORE_A), None, "schedule", force=False, pinned=False)
    assert d["build_needed"] is True
    assert d["reason"] == "no prior release"


def test_schedule_equal_digest_skips():
    cand = mk("sha256:same", CORE_A)
    prev = mk("sha256:same", CORE_A)
    d = md.decide(cand, prev, "schedule", force=False, pinned=False)
    assert d["build_needed"] is False
    assert d["reason"] == "no input change"


def test_schedule_changed_digest_builds():
    cand = mk("sha256:new", CORE_B)
    prev = mk("sha256:old", CORE_A)
    d = md.decide(cand, prev, "schedule", force=False, pinned=False)
    assert d["build_needed"] is True
    assert d["reason"] == "inputs changed"
    assert any(c["name"] == "widget" and c["change"] == "updated" for c in d["changed_inputs"])


def test_force_overrides_equal_digest():
    cand = mk("sha256:same", CORE_A)
    prev = mk("sha256:same", CORE_A)
    d = md.decide(cand, prev, "schedule", force=True, pinned=False)
    assert d["build_needed"] is True
    assert d["reason"] == "force requested"


def test_push_always_builds():
    cand = mk("sha256:same", CORE_A)
    prev = mk("sha256:same", CORE_A)
    d = md.decide(cand, prev, "push", force=False, pinned=False)
    assert d["build_needed"] is True
    assert d["reason"] == "push build"


def test_dispatch_pinned_builds():
    cand = mk("sha256:same", CORE_A)
    prev = mk("sha256:same", CORE_A)
    d = md.decide(cand, prev, "workflow_dispatch", force=False, pinned=True)
    assert d["build_needed"] is True
    assert d["reason"] == "manual pinned build"


def test_dispatch_unpinned_equal_digest_skips():
    cand = mk("sha256:same", CORE_A)
    prev = mk("sha256:same", CORE_A)
    d = md.decide(cand, prev, "workflow_dispatch", force=False, pinned=False)
    assert d["build_needed"] is False


def test_added_and_removed_inputs_reported():
    prev = mk("sha256:old", CORE_A + [{"name": "old-dep", "resolved_sha": "c" * 40, "version": "0.1"}])
    cand = mk("sha256:new", CORE_A + [{"name": "new-dep", "resolved_sha": "d" * 40, "version": "0.2"}])
    d = md.decide(cand, prev, "schedule", force=False, pinned=False)
    changes = {c["name"]: c["change"] for c in d["changed_inputs"]}
    assert changes["new-dep"] == "added"
    assert changes["old-dep"] == "removed"


def test_summary_text_format():
    cand = mk("sha256:new", CORE_B)
    prev = mk("sha256:old", CORE_A)
    d = md.decide(cand, prev, "schedule", force=False, pinned=False)
    assert "widget: 1.0 -> 1.1" in d["changed_summary"]
