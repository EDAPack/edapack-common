#!/usr/bin/env python3
"""Decide whether a release is needed by diffing input manifests.

Given a freshly-resolved candidate (from resolve-inputs.py / gen-manifest.py)
and the last published release's manifest (or none), decide `build_needed` and
summarize which inputs changed. Implements the change-gate from the design:

  - no prior manifest                       -> build (first release)
  - force=true                              -> build
  - trigger is push                         -> build (CI always builds pushes)
  - trigger is workflow_dispatch + pinned   -> build (explicit manual request)
  - trigger is schedule, digests equal      -> SKIP
  - digests differ                          -> build, report changed inputs

Outputs a JSON object and, when --github-output is given, writes
`build_needed`, `changed_summary` to that file in GITHUB_OUTPUT format.

Exit codes: 0 always (decision is in the payload, not the exit code).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _index_by_name(manifest: dict) -> dict:
    return {i["name"]: i for i in manifest.get("inputs", [])}


def diff_inputs(candidate: dict, previous: dict | None) -> list:
    """Return a list of {name, change, from, to} describing input changes."""
    if previous is None:
        return [
            {"name": i["name"], "change": "added", "from": None, "to": i.get("version") or i["resolved_sha"][:12]}
            for i in candidate.get("inputs", [])
        ]
    prev = _index_by_name(previous)
    cur = _index_by_name(candidate)
    changes = []
    for name, ci in cur.items():
        pi = prev.get(name)
        if pi is None:
            changes.append({"name": name, "change": "added", "from": None,
                            "to": ci.get("version") or ci["resolved_sha"][:12]})
        elif pi.get("resolved_sha") != ci.get("resolved_sha"):
            changes.append({
                "name": name, "change": "updated",
                "from": pi.get("version") or pi.get("resolved_sha", "")[:12],
                "to": ci.get("version") or ci["resolved_sha"][:12],
            })
    for name, pi in prev.items():
        if name not in cur:
            changes.append({"name": name, "change": "removed",
                            "from": pi.get("version") or pi.get("resolved_sha", "")[:12], "to": None})
    return changes


def decide(candidate: dict, previous: dict | None, trigger: str, force: bool, pinned: bool) -> dict:
    changes = diff_inputs(candidate, previous)
    if previous is None:
        reason = "no prior release"
        needed = True
    elif force:
        reason = "force requested"
        needed = True
    elif trigger == "push":
        reason = "push build"
        needed = True
    elif trigger == "workflow_dispatch" and pinned:
        reason = "manual pinned build"
        needed = True
    else:
        digest_changed = candidate.get("inputs_digest") != previous.get("inputs_digest")
        needed = digest_changed
        reason = "inputs changed" if digest_changed else "no input change"
    return {
        "build_needed": needed,
        "reason": reason,
        "changed_inputs": changes,
        "changed_summary": _summary(changes) if needed else "No input changes since last release.",
    }


def _summary(changes: list) -> str:
    if not changes:
        return "No input changes detected."
    lines = []
    for c in changes:
        if c["change"] == "added":
            lines.append(f"- {c['name']}: added @ {c['to']}")
        elif c["change"] == "removed":
            lines.append(f"- {c['name']}: removed (was {c['from']})")
        else:
            lines.append(f"- {c['name']}: {c['from']} -> {c['to']}")
    return "\n".join(lines)


def _load(path: Path | None) -> dict | None:
    if not path or str(path) == "none" or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--candidate", required=True, type=Path)
    p.add_argument("--previous", type=Path, default=None,
                   help="Last release manifest; omit or 'none' if no prior release.")
    p.add_argument("--trigger", required=True, choices=["schedule", "workflow_dispatch", "push"])
    p.add_argument("--force", action="store_true")
    p.add_argument("--pinned", action="store_true",
                   help="A core_ref/override was supplied to this dispatch.")
    p.add_argument("--github-output", type=Path, default=None)
    args = p.parse_args(argv)

    candidate = _load(args.candidate)
    previous = _load(args.previous)
    result = decide(candidate, previous, args.trigger, args.force, args.pinned)

    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"build_needed={'true' if result['build_needed'] else 'false'}\n")
            fh.write("changed_summary<<EOF\n" + result["changed_summary"] + "\nEOF\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
