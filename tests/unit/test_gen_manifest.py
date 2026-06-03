"""Tests for gen-manifest.py assemble + merge."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conftest import load_script  # noqa: E402

gm = load_script("gen-manifest")

CANDIDATE = {
    "inputs_digest": "sha256:" + "0" * 64,
    "inputs": [
        {"name": "verilator", "role": "core", "repo": "r", "ref": "v5.038",
         "resolved_sha": "a" * 40, "version": "5.038", "tracked": True},
    ],
}


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return p


def _args(**kw):
    class A:
        pass
    a = A()
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_assemble_basic(tmp_path):
    cand = _write(tmp_path, "cand.json", CANDIDATE)
    out = tmp_path / "manifest.json"
    rc = gm.assemble(_args(
        candidate=cand, package="verilator-bin", version="5.038.20260607",
        tag="v5.038.20260607", built_at="2026-06-07T12:00:00Z", trigger="schedule",
        recipe_sha="r" * 40, platform=None, skills_index=None, output=out,
    ))
    assert rc == 0
    m = json.loads(out.read_text())
    assert m["schema"] == "edapack.manifest/1"
    assert m["package"] == "verilator-bin"
    assert m["release"]["version"] == "5.038.20260607"
    assert m["inputs_digest"] == CANDIDATE["inputs_digest"]
    assert m["inputs"] == CANDIDATE["inputs"]


def test_assemble_with_platform_and_skills(tmp_path):
    cand = _write(tmp_path, "cand.json", CANDIDATE)
    plat = _write(tmp_path, "plat.json", {"os": "linux", "arch": "x86_64", "libc": "glibc_2.28"})
    idx = _write(tmp_path, "index.json", {
        "schema": "edapack.skills/1",
        "skills": [{"name": "verilator", "version": "5.038", "binaries": ["verilator"], "path": "verilator"}],
    })
    out = tmp_path / "m.json"
    gm.assemble(_args(
        candidate=cand, package="verilator-bin", version="v", tag="t",
        built_at="2026-06-07T12:00:00Z", trigger="schedule", recipe_sha="s",
        platform=plat, skills_index=idx, output=out,
    ))
    m = json.loads(out.read_text())
    assert m["platform"]["arch"] == "x86_64"
    assert m["skills"][0]["name"] == "verilator"
    assert m["skills"][0]["binaries"] == ["verilator"]


def test_merge_collects_platforms(tmp_path):
    base = {
        "schema": "edapack.manifest/1", "package": "p",
        "release": {"version": "v", "tag": "t", "built_at": "2026-06-07T12:00:00Z",
                    "trigger": "schedule", "recipe_sha": "s"},
        "inputs_digest": "sha256:" + "1" * 64, "inputs": CANDIDATE["inputs"],
    }
    m1 = dict(base, platform={"os": "linux", "arch": "x86_64"})
    m2 = dict(base, platform={"os": "linux", "arch": "aarch64"})
    p1 = _write(tmp_path, "m1.json", m1)
    p2 = _write(tmp_path, "m2.json", m2)
    out = tmp_path / "top.json"
    rc = gm.merge(_args(manifest=[p1, p2], output=out))
    assert rc == 0
    top = json.loads(out.read_text())
    assert "platform" not in top
    arches = sorted(p["arch"] for p in top["platforms"])
    assert arches == ["aarch64", "x86_64"]


def test_merge_rejects_mismatched_digests(tmp_path):
    base = {"schema": "edapack.manifest/1", "package": "p",
            "release": {"version": "v", "tag": "t", "built_at": "x",
                        "trigger": "schedule", "recipe_sha": "s"},
            "inputs": CANDIDATE["inputs"]}
    p1 = _write(tmp_path, "m1.json", dict(base, inputs_digest="sha256:" + "1" * 64,
                                          platform={"os": "linux", "arch": "x86_64"}))
    p2 = _write(tmp_path, "m2.json", dict(base, inputs_digest="sha256:" + "2" * 64,
                                          platform={"os": "linux", "arch": "aarch64"}))
    out = tmp_path / "top.json"
    rc = gm.merge(_args(manifest=[p1, p2], output=out))
    assert rc == 1
    assert not out.exists()
