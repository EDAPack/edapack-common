"""Tests for the shared _ecyaml parser, incl. inline-comment handling."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import _ecyaml as y  # noqa: E402


def test_strip_inline_comment_basic():
    assert y.strip_inline_comment("value  # note") == "value"
    assert y.strip_inline_comment("branch:master   # top-of-trunk") == "branch:master"


def test_hash_without_space_kept():
    # not a comment unless preceded by whitespace (so URLs/fragments survive)
    assert y.strip_inline_comment("a#b") == "a#b"
    assert y.strip_inline_comment("https://x/y#frag") == "https://x/y#frag"


def test_hash_inside_quotes_kept():
    assert y.strip_inline_comment("'a # b'") == "'a # b'"


def test_build_inputs_with_inline_comments():
    text = """schema: edapack.build-inputs/1
core:
  name: nextpnr            # the core source
  repo: https://github.com/YosysHQ/nextpnr
  policy: latest-tag       # pin with core_ref
dependencies:
  - name: icestorm         # chipdb data
    repo: https://github.com/YosysHQ/icestorm
    policy: branch:master
"""
    d = y.parse_simple_yaml(text)
    assert d["core"]["name"] == "nextpnr"
    assert d["core"]["policy"] == "latest-tag"
    assert d["core"]["repo"] == "https://github.com/YosysHQ/nextpnr"
    assert d["dependencies"][0]["name"] == "icestorm"
    assert d["dependencies"][0]["repo"] == "https://github.com/YosysHQ/icestorm"


def test_flow_list_with_comment():
    d = y.parse_simple_yaml("binaries: [a, b, c]   # tools\n")
    assert d["binaries"] == ["a", "b", "c"]


def test_comment_line_before_list_items():
    # a comment line between a key and its list must not make the list a dict
    text = """dependencies:
  # the deps
  - name: a
    policy: branch:main
  # another
  - name: b
    policy: tag:v1
"""
    d = y.parse_simple_yaml(text)
    assert isinstance(d["dependencies"], list)
    assert [x["name"] for x in d["dependencies"]] == ["a", "b"]


def test_inline_and_line_comments_full_build_inputs():
    text = """schema: edapack.build-inputs/1
core:
  name: nextpnr            # core
  repo: https://github.com/YosysHQ/nextpnr
  policy: latest-tag
dependencies:               # the deps
  # icestorm provides chipdb
  - name: icestorm
    repo: https://github.com/YosysHQ/icestorm
    policy: branch:main
"""
    d = y.parse_simple_yaml(text)
    assert d["core"]["name"] == "nextpnr"
    assert isinstance(d["dependencies"], list)
    assert d["dependencies"][0]["name"] == "icestorm"
