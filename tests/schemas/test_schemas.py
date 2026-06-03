"""Validate good/bad fixtures against the JSON Schemas."""

import json
import sys
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMAS = ROOT / "schemas"
sys.path.insert(0, str(ROOT / "tests"))
from conftest import load_script  # noqa: E402


def _schema(name):
    return json.loads((SCHEMAS / name).read_text())


GOOD_MANIFEST = {
    "schema": "edapack.manifest/1",
    "package": "verilator-bin",
    "release": {
        "version": "5.038.20260607", "tag": "v5.038.20260607",
        "built_at": "2026-06-07T12:00:00Z", "trigger": "schedule",
        "recipe_sha": "a" * 40,
    },
    "inputs_digest": "sha256:" + "0" * 64,
    "inputs": [
        {"name": "verilator", "role": "core", "repo": "r", "ref": "v5.038",
         "resolved_sha": "b" * 40, "version": "5.038", "tracked": True},
    ],
}

GOOD_BUILD_INPUTS = {
    "schema": "edapack.build-inputs/1",
    "core": {"name": "verilator", "repo": "https://github.com/verilator/verilator",
             "policy": "branch:master"},
    "dependencies": [
        {"name": "bitwuzla", "repo": "https://github.com/bitwuzla/bitwuzla",
         "policy": "branch:main"},
    ],
}

GOOD_SKILL_MANIFEST = {
    "skills": [{"name": "verilator", "path": "skills/verilator", "binaries": ["verilator"]}]
}


def test_good_manifest_validates():
    jsonschema.validate(GOOD_MANIFEST, _schema("manifest.schema.json"))


def test_good_build_inputs_validates():
    jsonschema.validate(GOOD_BUILD_INPUTS, _schema("build-inputs.schema.json"))


def test_good_skill_manifest_validates():
    jsonschema.validate(GOOD_SKILL_MANIFEST, _schema("skill-manifest.schema.json"))


@pytest.mark.parametrize("mutate", [
    lambda m: m.pop("inputs_digest"),
    lambda m: m.__setitem__("inputs_digest", "notasha"),
    lambda m: m.__setitem__("inputs", []),
    lambda m: m["release"].pop("recipe_sha"),
    lambda m: m["release"].__setitem__("trigger", "bogus"),
])
def test_bad_manifest_rejected(mutate):
    bad = json.loads(json.dumps(GOOD_MANIFEST))
    mutate(bad)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _schema("manifest.schema.json"))


@pytest.mark.parametrize("policy", ["weird", "branch:", "release"])
def test_bad_policy_rejected(policy):
    bad = json.loads(json.dumps(GOOD_BUILD_INPUTS))
    bad["core"]["policy"] = policy
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _schema("build-inputs.schema.json"))


def test_gen_manifest_output_roundtrips_schema(tmp_path):
    """gen-manifest assemble output must validate against the schema."""
    gm = load_script("gen-manifest")
    cand = tmp_path / "cand.json"
    cand.write_text(json.dumps({
        "inputs_digest": "sha256:" + "0" * 64,
        "inputs": GOOD_MANIFEST["inputs"],
    }))
    out = tmp_path / "m.json"

    class A:
        pass
    a = A()
    a.candidate = cand
    a.package = "verilator-bin"
    a.version = "5.038.20260607"
    a.tag = "v5.038.20260607"
    a.built_at = "2026-06-07T12:00:00Z"
    a.trigger = "schedule"
    a.recipe_sha = "a" * 40
    a.platform = None
    a.skills_index = None
    a.output = out
    gm.assemble(a)
    jsonschema.validate(json.loads(out.read_text()), _schema("manifest.schema.json"))
