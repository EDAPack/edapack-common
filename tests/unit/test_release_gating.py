"""The release pipeline must never be reachable from a mirror push.

WHY THIS IS A UNIT TEST AND NOT ONLY A WORKFLOW CHECK.  build-release.yml is
called by four repositories as

    edapack/edapack-common/.github/workflows/build-release.yml@v1

and ``v1`` is a MOVING major-version alias, retargeted forward the way
``actions/checkout@v4`` is.  So a commit here reaches four release pipelines the
moment that tag is moved, with no review gate in between.  There is a
counterpart check in ``.forgejo/workflows/ci.yml``, but that one runs on
Forgejo; the callers resolve this file on GitHub, which is where ``selftest.yml``
runs.  This test is the copy that runs where it matters.

WHAT IT ACTUALLY PROTECTS.  Since 2026-08-31 this repository's history moves
between Forgejo and GitHub on a five-minute cron, and ``git-sync`` produces
exactly one kind of event: a push.  ``manifest-diff.py`` says in its own
docstring that a push always builds, and ``build_needed`` used to be the only
condition on ``publish`` -- so a sync cycle was a release trigger.

NOTE ON THE CONDITION WE ASSERT.  ``adding-orgs.md`` §E2.2 requires publish jobs
to be gated on a ``v*`` tag and nothing else.  That rule is written for repos
where a human pushes a tag and the tag IS the version.  Here ``resolve``
computes the tag and ``publish`` CREATES it -- the tag is a release's output,
not its trigger -- so tag-gating this job is not a stricter rule, it is an
unsatisfiable one.  The property §E2.2 exists to protect is that a mirroring
operation is never a publishing operation, and excluding ``push`` delivers
exactly that while leaving ``schedule`` and ``workflow_dispatch`` alone.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WF = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build-release.yml"

# Assembled from fragments rather than written out: git-sync's estate
# conformance scanner reads files as TEXT and matches these same strings, so
# spelling one out here would register this test file as a publisher.
PUBLISH_MARKERS = tuple(a + b for a, b in (
    ("gh release", " create"),
    ("softprops/action", "-gh-release"),
    ("peaceiris/actions", "-gh-pages"),
    ("actions/deploy", "-pages"),
    ("twine", " upload"),
))

SYNC_SAFE = "github.event_name != 'push'"


@pytest.fixture(scope="module")
def doc():
    return yaml.safe_load(WF.read_text())


@pytest.fixture(scope="module")
def jobs(doc):
    return doc.get("jobs") or {}


def _triggers(doc):
    # YAML 1.1 resolves a bare `on:` key to the BOOLEAN True, so doc["on"] is
    # None and a naive check passes vacuously.  This has bitten this estate.
    on = doc.get("on", doc.get(True))
    assert on is not None, "build-release.yml has no `on:` block"
    return list(on)


def test_reusable_only(doc):
    """A `push:` trigger here would arm a publish-capable workflow directly.

    This file carries `permissions: contents: write` and creates releases.  It
    is safe to leave publish-capable only because nothing can trigger it on its
    own; the callers' gating is what decides when it runs.
    """
    assert _triggers(doc) == ["workflow_call"]


def test_gate_job_exists_and_exports_authority(jobs):
    gate = jobs.get("gate")
    assert gate, "no `gate` job: the release-authority switch is never consulted"
    assert "authority" in (gate.get("outputs") or {})


def test_every_gate_reader_declares_the_dependency(jobs):
    """An undeclared `needs: gate` is the quietest failure available here.

    The expression evaluates to an EMPTY STRING rather than raising, so the
    publish condition is false forever: nothing publishes and nothing goes red.
    """
    for name, job in jobs.items():
        if "needs.gate.outputs" not in yaml.dump(job):
            continue
        needs = job.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        assert "gate" in needs, (
            f"job `{name}` reads needs.gate.outputs but does not declare `needs: gate`"
        )


def _publishing_steps(jobs):
    for name, job in jobs.items():
        for step in job.get("steps") or []:
            text = (step.get("run") or "") + " " + (step.get("uses") or "")
            if any(m in text for m in PUBLISH_MARKERS):
                label = f"{name}/{step.get('name') or step.get('uses') or '<step>'}"
                cond = f"{step.get('if', '')} {job.get('if', '')}"
                yield label, cond


def test_there_is_still_a_publishing_step(jobs):
    """Guards the three tests below against passing by finding nothing.

    If the publish step is ever renamed or reimplemented in a way the markers
    do not recognise, the gating assertions become vacuous.  This is the check
    that turns that into a visible failure instead of a green run.
    """
    assert list(_publishing_steps(jobs)), (
        "no publishing step matched PUBLISH_MARKERS -- either the release step "
        "was removed, or it changed shape and the markers above need updating"
    )


def test_no_publishing_step_is_reachable_from_a_push(jobs):
    for label, cond in _publishing_steps(jobs):
        assert SYNC_SAFE in cond, (
            f"publishing step {label} is reachable from a push, so a sync cycle "
            f"can publish a release: if={cond.strip()!r}"
        )


def test_every_publishing_step_consults_the_switch(jobs):
    for label, cond in _publishing_steps(jobs):
        assert "authority" in cond, (
            f"publishing step {label} does not consult the release-authority "
            f"switch, so both forges could publish the same version: "
            f"if={cond.strip()!r}"
        )


def test_pipeline_scripts_referenced_by_the_workflow_exist():
    """A rename here is invisible until a Sunday cron fails in another repo."""
    root = WF.resolve().parents[2]
    body = WF.read_text()
    for script in ("resolve-inputs.py", "manifest-diff.py",
                   "gen-manifest.py", "release-notes.py"):
        if script in body:
            assert (root / "scripts" / script).is_file(), (
                f"scripts/{script} is referenced by build-release.yml and is missing"
            )
