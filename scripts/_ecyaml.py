"""Tiny dependency-free YAML subset parser shared by the edapack build tools.

The manylinux build containers don't have PyYAML, and we don't want to add a
third-party dependency to the release path. We only consume YAML we author
ourselves (`build-inputs.yaml`, `skill-manifest.yaml`, SKILL.md frontmatter),
so a small, predictable subset is enough.

Supported:
    key: value
    key: [a, b, c]            # one-line flow list of scalars
    block:                    # nested dict or list, one level deeper
      - name: x
        repo: y
    # comments and blank lines

Scalars are coerced: ints, floats, true/false, null/~, quoted strings.
Anything else stays a string.
"""

# NOTE: no `from __future__ import annotations` — must import on the
# manylinux2014 image's system Python 3.6 (that feature is 3.7+). All
# annotations below are bare builtins, so eager evaluation is fine.
import re

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def strip_inline_comment(v: str) -> str:
    """Remove a trailing ` # ...` inline comment, honoring quotes.

    YAML only treats `#` as a comment when preceded by whitespace, so `a:b#c`
    and URLs are safe, but `value  # note` and `[a, b] # note` are trimmed.
    A value that is *only* a comment (e.g. `key:   # note`) trims to empty.
    """
    if v.lstrip().startswith("#"):
        return ""
    in_s = in_d = False
    for i, ch in enumerate(v):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d and i > 0 and v[i - 1] in " \t":
            return v[:i].rstrip()
    return v


def parse_scalar(v: str):
    v = strip_inline_comment(v.strip())
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        body = v[1:-1].strip()
        if not body:
            return []
        return [parse_scalar(p) for p in body.split(",")]
    if (v.startswith('"') and v.endswith('"')) or (
        v.startswith("'") and v.endswith("'")
    ):
        return v[1:-1]
    if v.lower() in {"true", "false"}:
        return v.lower() == "true"
    if v.lower() in {"null", "~", ""}:
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def parse_simple_yaml(text: str) -> dict:
    """Parse the supported YAML subset into nested dict/list structures."""
    root: dict = {}
    stack: list = [(0, root)]  # (indent, container)
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        while stack and indent < stack[-1][0]:
            stack.pop()
        container = stack[-1][1]

        if stripped.startswith("- "):
            item_body = stripped[2:]
            if isinstance(container, list):
                if ":" in item_body:
                    k, v = item_body.split(":", 1)
                    item: dict = {}
                    item[k.strip()] = parse_scalar(v.strip())
                    container.append(item)
                    stack.append((indent + 2, item))
                else:
                    container.append(parse_scalar(item_body))
            else:
                raise ValueError(f"unexpected list item at line {i + 1}: {raw!r}")
        elif ":" in stripped:
            k, v = stripped.split(":", 1)
            k = k.strip()
            v = strip_inline_comment(v.strip()).strip()
            if v == "":
                j = i + 1
                # skip blank AND comment lines when deciding the block's type,
                # so a comment between a key and its list items doesn't make a
                # list look like a dict.
                while j < len(lines) and (
                    not lines[j].strip() or lines[j].lstrip().startswith("#")
                ):
                    j += 1
                if j < len(lines) and lines[j].lstrip().startswith("- "):
                    new: list = []
                else:
                    new = {}
                if isinstance(container, dict):
                    container[k] = new
                else:
                    raise ValueError(f"key in non-dict at line {i + 1}")
                stack.append((indent + 2, new))
            else:
                if isinstance(container, dict):
                    container[k] = parse_scalar(v)
                else:
                    raise ValueError(f"key in non-dict at line {i + 1}")
        else:
            raise ValueError(f"cannot parse line {i + 1}: {raw!r}")
        i += 1
    return root


def parse_frontmatter(text: str) -> dict:
    """Extract and parse the leading `--- ... ---` YAML frontmatter block."""
    m = _FRONT_RE.match(text)
    if not m:
        raise ValueError("missing YAML frontmatter")
    return parse_simple_yaml(m.group(1))
