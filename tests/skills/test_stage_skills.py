"""Tests for the canonical stage-skills.py (incl. strict mode)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conftest import load_script  # noqa: E402

ss = load_script("stage-skills")

SKILL_MD = """---
name: widget
description: Does widget things.
version: "1.2"
---

# widget
body
"""


def _make_pkg(tmp_path, with_binary=True, name="widget", fm=SKILL_MD):
    src = tmp_path / "src"
    skill = src / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(fm)
    (skill / "references").mkdir()
    (skill / "references" / "cli.md").write_text("ref")
    rel = tmp_path / "release"
    (rel / "bin").mkdir(parents=True)
    if with_binary:
        (rel / "bin" / name).write_text("#!/bin/sh\n")
    manifest = tmp_path / "skill-manifest.yaml"
    manifest.write_text(
        f"skills:\n  - name: {name}\n    path: skills/{name}\n    binaries: [{name}]\n"
    )
    return src, rel, manifest


def _run(manifest, src, rel, strict=False):
    args = ["--manifest", str(manifest), "--source-root", str(src),
            "--release-root", str(rel), "--dest", str(rel / "skills")]
    if strict:
        args.append("--strict")
    return ss.main(args)


def test_happy_path_stages_and_indexes(tmp_path):
    src, rel, manifest = _make_pkg(tmp_path)
    assert _run(manifest, src, rel) == 0
    staged = rel / "skills" / "widget"
    assert (staged / "SKILL.md").is_file()
    assert (staged / "references" / "cli.md").is_file()
    idx = json.loads((rel / "skills" / "index.json").read_text())
    assert idx["schema"] == "edapack.skills/1"
    assert idx["skills"][0]["name"] == "widget"
    assert idx["skills"][0]["version"] == "1.2"
    assert idx["skills"][0]["binaries"] == ["widget"]


def test_missing_binary_fails(tmp_path):
    src, rel, manifest = _make_pkg(tmp_path, with_binary=False)
    assert _run(manifest, src, rel) == 1
    assert not (rel / "skills" / "index.json").exists()


def test_windows_exe_binary_accepted(tmp_path):
    src, rel, manifest = _make_pkg(tmp_path, with_binary=False)
    (rel / "bin" / "widget.exe").write_text("MZ")
    assert _run(manifest, src, rel) == 0


def test_frontmatter_name_mismatch_fails(tmp_path):
    bad = SKILL_MD.replace("name: widget", "name: gadget")
    src, rel, manifest = _make_pkg(tmp_path, fm=bad)
    assert _run(manifest, src, rel) == 1


def test_missing_frontmatter_key_fails(tmp_path):
    bad = "---\nname: widget\ndescription: x\n---\nbody\n"  # no version
    src, rel, manifest = _make_pkg(tmp_path, fm=bad)
    assert _run(manifest, src, rel) == 1


def test_empty_manifest_lenient_ok_strict_fails(tmp_path):
    _src, rel, _m = _make_pkg(tmp_path)
    empty = tmp_path / "empty.yaml"
    empty.write_text("skills:\n")
    src = tmp_path / "src"
    assert _run(empty, src, rel, strict=False) == 0
    assert _run(empty, src, rel, strict=True) == 1
