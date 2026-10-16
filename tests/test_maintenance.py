"""Offline maintenance helpers test — no network, no secrets.

Run: python tests/test_maintenance.py
Validates the shared helpers in .github/scripts/maintenance.py and python_version_bump.py
against the production spec (2026-10-15). All pure, side-effect free.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "scripts"))

import maintenance  # type: ignore
import python_version_bump  # type: ignore

def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg}: {a!r} != {b!r}")

def test_parse_version():
    assert_eq(maintenance.parse_version("3.14.8"), (3,14,8), "parse")
    assert_eq(maintenance.parse_version("3.14"), None, "incomplete")
    assert_eq(maintenance.parse_version("3.14.8a1"), None, "prerelease invalid")
    assert_eq(maintenance.parse_version(" 3.14.8 "), (3,14,8), "strip")
    assert_eq(maintenance.parse_version(""), None, "empty")

def test_compare_versions():
    assert_eq(maintenance.compare_versions("3.14.8", "3.14.9"), -1)
    assert_eq(maintenance.compare_versions("3.14.8", "3.14.8"), 0)
    assert_eq(maintenance.compare_versions("3.14.9", "3.14.8"), 1)
    # unknown version returns 0 per policy
    assert_eq(maintenance.compare_versions("bad", "3.14.8"), 0)

def test_classify_python():
    # patch
    r = maintenance.classify_python_update("3.14.8", "3.14.9")
    assert_eq(r["kind"], "patch")
    assert_eq(r["review_required"], False)
    # next minor
    r = maintenance.classify_python_update("3.14.8", "3.15.0")
    assert r["review_required"] is True and r["kind"] == "minor"
    # major
    r = maintenance.classify_python_update("3.14.8", "4.0.0")
    assert r["review_required"] is True and r["kind"] == "major"
    # unknown
    r = maintenance.classify_python_update("bad", "3.14.9")
    assert r["review_required"] is True

def test_classify_package():
    # patch
    r = maintenance.classify_package_update("pkg", "1.2.3", "1.2.4")
    assert_eq(r["reason"], "patch")
    assert r["review_required"] is False
    # minor
    r = maintenance.classify_package_update("pkg", "1.2.3", "1.3.0")
    assert_eq(r["reason"], "minor")
    assert r["review_required"] is False
    # major -> review
    r = maintenance.classify_package_update("pkg", "1.2.3", "2.0.0")
    assert r["review_required"] is True and r["reason"] == "major"
    # prerelease -> review
    r = maintenance.classify_package_update("pkg", "1.2.3", "1.2.4-rc1")
    assert r["review_required"] is True
    # 0.x minor is breaking
    r = maintenance.classify_package_update("pkg", "0.2.3", "0.3.0")
    assert r["review_required"] is True

def test_classify_group():
    low = {"risk": "low", "review_required": False, "reason": "patch"}
    high = {"risk": "high", "review_required": True, "reason": "major"}
    g = maintenance.classify_group_updates([low, high])
    assert_eq(g["risk"], "high")
    assert g["review_required"] is True
    g2 = maintenance.classify_group_updates([low])
    assert_eq(g2["risk"], "low")

def test_validate_changed_files():
    ok = maintenance.validate_changed_files([".python-version", "requirements.txt", "testing area/reddit_main_v3.py", "tests/test_maintenance.py"])
    assert ok["ok"] is True
    bad = maintenance.validate_changed_files(["README.md"])
    assert bad["ok"] is False
    # pending_reddit.json is excluded? Actually state files are allowed to be excluded_from_copy but not allowed to change in PR? Check ALLOWED_RUNTIME_PATTERNS — pending_reddit is not in allowed list for PR validation (should be blocked)
    # Our ALLOWED patterns include tests/test_*.py and fixtures; README should be blocked
    assert "README.md" in bad["reason"]

def test_select_candidate():
    now = int(time.time())
    manifest = [
        {"version": "3.14.8", "stable": True, "published_at": "2026-09-01T00:00:00Z", "files": [{"platform":"linux","arch":"x64"}], "release_url": "https://example.com/3.14.8"},
        {"version": "3.14.9", "stable": True, "published_at": "2026-10-01T00:00:00Z", "files": [{"platform":"linux","arch":"x64"}], "release_url": "https://example.com/3.14.9"},
        {"version": "3.15.0", "stable": True, "published_at": "2026-10-10T00:00:00Z", "files": [{"platform":"linux","arch":"x64"}], "release_url": "https://example.com/3.15.0"},
    ]
    # current 3.14.8 -> should pick patch 3.14.9 (newest patch). A patch never soaks, so it does
    # not depend on the release-date authority.
    r = maintenance.select_candidate("3.14.8", manifest, soak_days=30, now_ts=now, ignore_soak=False)
    assert_eq(r["candidate"], "3.14.9")
    assert_eq(r["kind"], "patch")
    # if no patch, should pick minor after soak — soaked against the python.org date
    manifest2 = [
        {"version": "3.14.8", "stable": True, "published_at": "2026-09-01T00:00:00Z", "files": [{"platform":"linux","arch":"x64"}], "release_url": ""},
        {"version": "3.15.0", "stable": True, "published_at": "2026-10-10T00:00:00Z", "files": [{"platform":"linux","arch":"x64"}], "release_url": ""},
    ]
    dates = {"3.15.0": "2026-10-10"}
    # now is 2026-10-12 (2 days after the authoritative 3.15.0 release) -> should defer
    now_soon = int(time.mktime(time.strptime("2026-10-12T00:00:00", "%Y-%m-%dT%H:%M:%S")))
    r = maintenance.select_candidate("3.14.8", manifest2, soak_days=30, now_ts=now_soon,
                                     release_dates=dates, release_date_state="ok")
    assert r.get("deferred") is True
    assert "soak" in r["reason"], r["reason"]
    # after soak, should return candidate
    now_late = int(time.mktime(time.strptime("2026-11-20T00:00:00", "%Y-%m-%dT%H:%M:%S")))
    r = maintenance.select_candidate("3.14.8", manifest2, soak_days=30, now_ts=now_late,
                                     release_dates=dates, release_date_state="ok")
    assert_eq(r["candidate"], "3.15.0")
    assert_eq(r["kind"], "minor")
    assert r.get("deferred") is False


def test_soak_age_comes_from_python_org_not_from_the_setup_python_manifest():
    """A setup-python timestamp is a mirror artefact and must never define soak age.

    These are the three ways the manifest's own ``published_at`` used to satisfy the soak window:
    the authority was never consulted, the authority does not know the version, or the archive was
    rebuilt long after the release so its timestamp claims an age the release does not have.
    """
    now_late = int(time.mktime(time.strptime("2026-11-20T00:00:00", "%Y-%m-%dT%H:%M:%S")))
    # published_at claims the release is over a year old; python.org says it is two days old.
    rebuilt = [
        {"version": "3.14.8", "stable": True, "files": [{"platform": "linux", "arch": "x64"}]},
        {"version": "3.15.0", "stable": True, "published_at": "2025-01-01T00:00:00Z",
         "files": [{"platform": "linux", "arch": "x64"}], "release_url": "u"},
    ]
    r = maintenance.select_candidate("3.14.8", rebuilt, soak_days=30, now_ts=now_late,
                                     release_dates={"3.15.0": "2026-11-18"}, release_date_state="ok")
    assert r.get("deferred") is True, r
    assert "soak" in r["reason"], r["reason"]
    assert "3.15.0" in r["release_dates"]["stripped_untrusted_dates"], r["release_dates"]

    # Authority never reached: an old-looking manifest timestamp cannot be read as "soaked".
    for state in ("", "unavailable"):
        r = maintenance.select_candidate("3.14.8", rebuilt, soak_days=30, now_ts=now_late,
                                         release_dates={}, release_date_state=state)
        assert r.get("deferred") is True, (state, r)
        assert_eq(r["reason"], "deferred: release-date-authority-unavailable")

    # Authority reached but silent about this version: unknown stays dateless and defers.
    r = maintenance.select_candidate("3.14.8", rebuilt, soak_days=30, now_ts=now_late,
                                     release_dates={"3.14.8": "2025-01-01"}, release_date_state="ok")
    assert r.get("deferred") is True, r
    assert_eq(r["reason"], "deferred: release-date-unavailable")
    assert "3.15.0" in r["release_dates"]["unknown"], r["release_dates"]

    # A cached authority reading is still authoritative.
    r = maintenance.select_candidate("3.14.8", rebuilt, soak_days=30, now_ts=now_late,
                                     release_dates={"3.15.0": "2026-01-05"}, release_date_state="cache")
    assert r.get("deferred") is False, r
    assert_eq(r["candidate"], "3.15.0")


# ---------------------------------------------------------------------------
# Fixtures shaped exactly like the live REST responses. The Checks API returns no
# workflow identity, so these records deliberately carry no workflow, workflow_id,
# path or workflow_run keys — provenance has to be resolved from the job and run.
# ---------------------------------------------------------------------------

REPO = "uesu/wvkhabh2s22"


def repo_side(full_name=REPO):
    """The repo association a real GET /pulls/{n} payload carries on both base and head.

    Branch name and base ref alone are not provenance, so fixtures that omit this are modelling
    a payload the API never returns.
    """
    return {"id": 1, "name": full_name.split("/")[-1], "full_name": full_name, "private": False}
HEAD = "abc123def456"
RUN_ID = 38848439019
JOB_ID = 116801413454
CHECK_ID = 990011
WORKFLOW_ID = 77
CI_PATH = ".github/workflows/ci.yml"
CI_NAME = "CI \u2014 syntax + offline validation"


def api_run(*, run_id=RUN_ID, attempt=1, head_sha=HEAD, status="completed", conclusion="success",
            path=CI_PATH, workflow_id=WORKFLOW_ID, name=CI_NAME, full_name=REPO, event="pull_request"):
    """Shaped like GET /repos/{owner}/{repo}/actions/runs/{run_id}."""
    return {
        "id": run_id, "name": name, "node_id": "WFR_kwLO1", "head_branch": "arena/session",
        "head_sha": head_sha, "run_number": 41, "event": event, "status": status,
        "conclusion": conclusion, "workflow_id": workflow_id, "path": path, "run_attempt": attempt,
        "url": f"https://api.github.com/repos/{full_name}/actions/runs/{run_id}",
        "html_url": f"https://github.com/{full_name}/actions/runs/{run_id}",
        "jobs_url": f"https://api.github.com/repos/{full_name}/actions/runs/{run_id}/jobs",
        "repository": {"id": 1, "name": full_name.split("/")[1], "full_name": full_name, "private": False},
        "head_repository": {"id": 1, "name": full_name.split("/")[1], "full_name": full_name},
    }


def api_job(*, job_id=JOB_ID, run_id=RUN_ID, attempt=1, name="maintenance-ci", head_sha=HEAD,
            status="completed", conclusion="success", workflow_name=CI_NAME, check_id=CHECK_ID,
            repository=REPO):
    """Shaped like GET /repos/{owner}/{repo}/actions/jobs/{job_id}.

    check_run_url is the job's own back-reference to the check run it produced; the resolver
    requires it to name this exact check in this exact repository, which is what proves
    production rather than a name match.
    """
    return {
        "id": job_id, "run_id": run_id, "run_attempt": attempt, "workflow_name": workflow_name,
        "head_sha": head_sha, "node_id": "J_kwLO2", "url": f"https://api.github.com/repos/{repository}/actions/jobs/{job_id}",
        "html_url": f"https://github.com/{repository}/actions/runs/{run_id}/job/{job_id}",
        "status": status, "conclusion": conclusion, "name": name, "steps": [],
        "check_run_url": f"https://api.github.com/repos/{repository}/check-runs/{check_id}",
        "labels": [], "runner_name": "GitHub Actions 100", "runner_group_name": "Default",
    }


def api_attempt(*, run_id=RUN_ID, attempt=1, head_sha=HEAD, status="completed",
                conclusion="success", path=CI_PATH, workflow_id=WORKFLOW_ID):
    """Shaped like GET /repos/{o}/{r}/actions/runs/{run_id}/attempts/{attempt_number}.

    There is no endpoint that lists a run's attempts, so currency is established by comparing
    the job's attempt with the run's current attempt and with this attempt-specific record.
    """
    return {
        "id": run_id, "run_attempt": attempt, "status": status, "conclusion": conclusion,
        "head_sha": head_sha, "path": path, "workflow_id": workflow_id, "name": CI_NAME,
        "url": f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/attempts/{attempt}",
    }


# A real Actions check run reports an opaque UUID here, observed read-only on the green
# maintenance-ci check: 0fb0ae38-2e89-5fc7-8093-e2d0d0cf857d. It is NOT the job id.
REAL_EXTERNAL_ID = "0fb0ae38-2e89-5fc7-8093-e2d0d0cf857d"


def api_check_run(name="maintenance-ci", *, check_id=CHECK_ID, head_sha=HEAD, job_id=JOB_ID,
                  run_id=RUN_ID, status="completed", conclusion="success",
                  app_slug="github-actions", external_id=REAL_EXTERNAL_ID,
                  html_url="sentinel", repository=REPO):
    """Shaped like one entry of GET /repos/{owner}/{repo}/commits/{ref}/check-runs.

    html_url is the canonical runs/<run_id>/job/<job_id> link the controller resolves
    provenance through; external_id is opaque metadata that must never be parsed as an id.
    """
    link = f"https://github.com/{repository}/actions/runs/{run_id}/job/{job_id}"
    if html_url != "sentinel":
        link = html_url
    return {
        "id": check_id, "head_sha": head_sha, "external_id": external_id,
        "url": f"https://api.github.com/repos/{repository}/check-runs/{check_id}",
        "html_url": link, "details_url": link,
        "status": status, "conclusion": conclusion, "started_at": "2026-10-16T03:00:00Z",
        "completed_at": "2026-10-16T03:04:00Z", "name": name,
        "check_suite": {"id": 5001, "status": status, "conclusion": conclusion, "head_sha": head_sha},
        "app": {"id": 15368, "slug": app_slug, "name": "GitHub Actions", "owner": {"login": "github"}},
        "output": {"title": None, "summary": None, "text": None},
    }


def resolved_check(name="maintenance-ci", *, job=None, run=None, attempt=None, **check_kwargs):
    """A live check run plus the raw job/run/attempt payloads the controller fetched for it."""
    check = api_check_run(name, **check_kwargs)
    check_id = check_kwargs.get("check_id", CHECK_ID)
    run_id = check_kwargs.get("run_id", RUN_ID)
    head_sha = check_kwargs.get("head_sha", HEAD)
    if job is not False:
        check["job"] = job if job is not None else api_job(name=name, check_id=check_id,
                                                           run_id=run_id, head_sha=head_sha)
    if run is not False:
        check["run"] = run if run is not None else api_run(run_id=run_id, head_sha=head_sha)
    if attempt is not False:
        check["attempt"] = attempt if attempt is not None else api_attempt(
            run_id=run_id, head_sha=head_sha)
    return check


def test_evaluate_checks():
    """A real Checks API record must pass once its producing job and run are resolved."""
    checks = [resolved_check("maintenance-ci"), resolved_check("smoke", check_id=CHECK_ID + 1, job_id=JOB_ID + 1)]
    # The live shape carries none of these — that is the point of the test.
    for record in checks:
        for absent in ("workflow", "workflow_id", "path", "workflow_run", "run_attempt"):
            assert absent not in record, f"a real check run never carries {absent}"
    r = maintenance.evaluate_checks(checks, ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is True, r["reason"]
    prov = r["provenance"][0]
    assert_eq(prov["run_id"], RUN_ID)
    assert_eq(prov["job_id"], JOB_ID)
    assert_eq(prov["check_id"], CHECK_ID)
    assert_eq(prov["job_name"], "maintenance-ci")
    assert_eq(prov["run_attempt"], 1)
    assert_eq(prov["workflow_path"], CI_PATH)
    assert_eq(prov["workflow_id"], WORKFLOW_ID)
    assert_eq(prov["repository"], REPO)
    assert_eq(prov["head_sha"], HEAD)

    # a check that cannot be resolved to a producer is missing evidence, not a pass
    r = maintenance.evaluate_checks([api_check_run("maintenance-ci")], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False and "provenance unresolved" in r["reason"], r["reason"]

    # a mandatory check absent from the head entirely
    r = maintenance.evaluate_checks(checks, ["maintenance-ci", "missing-check"], HEAD, HEAD, REPO)
    assert r["ok"] is False and "missing: missing-check" in r["reason"], r["reason"]

    # a failed gate
    failed = resolved_check("maintenance-ci", conclusion="failure")
    r = maintenance.evaluate_checks([failed], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False, r["reason"]

    # skipped is not success
    skipped = resolved_check("maintenance-ci", conclusion="skipped")
    r = maintenance.evaluate_checks([skipped], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False, r["reason"]

    # in progress is not success
    r = maintenance.evaluate_checks(
        [resolved_check("maintenance-ci", status="queued", conclusion=None)], ["maintenance-ci"], HEAD, HEAD, REPO
    )
    assert r["ok"] is False, r["reason"]

    # head mismatch between the fixture and the published PR head
    r = maintenance.evaluate_checks(checks, ["maintenance-ci"], HEAD, "def999", REPO)
    assert r["ok"] is False and "head mismatch" in r["reason"], r["reason"]

    # a check recorded against a different head than the PR head
    r = maintenance.evaluate_checks(
        [resolved_check("maintenance-ci", head_sha="def999")], ["maintenance-ci"], HEAD, HEAD, REPO
    )
    assert r["ok"] is False and "head mismatch" in r["reason"], r["reason"]

    # legacy commit statuses carry no producing job or run, so they can never authorize
    legacy = {"name": "maintenance-ci", "status": "completed", "conclusion": "success",
              "head_sha": HEAD, "app": {"slug": "ci-bot"}, "provenance": "legacy-commit-status"}
    r = maintenance.evaluate_checks([legacy], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False, r["reason"]

    # no checks at all
    r = maintenance.evaluate_checks([], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False and "no checks" in r["reason"], r["reason"]


def test_provenance_requires_the_exact_producer():
    """Wrong check, wrong job, wrong run, wrong attempt, wrong repo — each fails closed."""
    cases = {
        "job for a different check": resolved_check("maintenance-ci", job=api_job(job_id=JOB_ID + 7)),
        "job named differently": resolved_check("maintenance-ci", job=api_job(name="validate")),
        "job on another head": resolved_check("maintenance-ci", job=api_job(head_sha="def999")),
        "job still running": resolved_check("maintenance-ci", job=api_job(status="in_progress", conclusion=None)),
        "job failed": resolved_check("maintenance-ci", job=api_job(conclusion="failure")),
        "job without a run id": resolved_check("maintenance-ci", job={**api_job(), "run_id": None}),
        "run is not the job's run": resolved_check("maintenance-ci", run=api_run(run_id=RUN_ID + 3)),
        "run attempt is not the job's": resolved_check("maintenance-ci", run=api_run(attempt=2)),
        "run on another head": resolved_check("maintenance-ci", run=api_run(head_sha="def999")),
        "run still in progress": resolved_check("maintenance-ci", run=api_run(status="in_progress", conclusion=None)),
        "run failed": resolved_check("maintenance-ci", run=api_run(conclusion="failure")),
        "untrusted workflow path": resolved_check("maintenance-ci", run=api_run(path=".github/workflows/other.yml")),
        "run without a workflow id": resolved_check("maintenance-ci", run={**api_run(), "workflow_id": None}),
        "run from a fork repository": resolved_check("maintenance-ci", run=api_run(full_name="someone/fork")),
        "job superseded by a rerun": resolved_check(
            "maintenance-ci",
            job=api_job(attempt=1),
            run=api_run(attempt=2),
            attempt=api_attempt(attempt=2),
        ),
        "job unavailable": resolved_check("maintenance-ci", job=False),
        "run unavailable": resolved_check("maintenance-ci", run=False),
        "attempt record unavailable": resolved_check("maintenance-ci", attempt=False),
        "untrusted app": resolved_check("maintenance-ci", app_slug="third-party-app"),
        "untrusted check identity": resolved_check("not-a-known-gate", check_id=CHECK_ID + 2),
    }
    for label, check in cases.items():
        r = maintenance.evaluate_checks([check], ["maintenance-ci"], HEAD, HEAD, REPO)
        assert r["ok"] is False, f"{label} must fail closed but was accepted"
        assert r["failed"] or r["missing"], f"{label} must report a reason"

    # an untrusted check identity is rejected by the resolver itself, not merely absent
    provenance, problem = maintenance.resolve_check_provenance(
        resolved_check("not-a-known-gate", check_id=CHECK_ID + 2), head_sha=HEAD, repository=REPO
    )
    assert provenance is None and problem and "untrusted check identity" in problem, problem

    # and the repository must actually be supplied to be verified
    r = maintenance.evaluate_checks([resolved_check("maintenance-ci")], ["maintenance-ci"], HEAD, HEAD, None)
    assert r["ok"] is False and "repository not supplied" in r["reason"], r["reason"]

    # an opaque external_id is irrelevant to binding — a check with no canonical link is not.
    # (The old assertion here expected a non-numeric external_id to block, which is precisely
    # the mistake T2 corrects: external_id is metadata, the link is the route.)
    r = maintenance.evaluate_checks(
        [resolved_check("maintenance-ci", external_id="not-a-job-id")], ["maintenance-ci"], HEAD, HEAD, REPO
    )
    assert r["ok"] is True, f"an opaque external_id must not block a genuine check: {r['reason']}"
    unbound = resolved_check("maintenance-ci", html_url=None)
    unbound["details_url"] = None
    r = maintenance.evaluate_checks([unbound], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False and "no canonical Actions job link" in r["reason"], r["reason"]


def test_provenance_accepts_the_current_attempt_only():
    """A rerun that then succeeded is authorized; an earlier attempt of it is not.

    There is no endpoint listing a run's attempts, so currency comes from getWorkflowRun
    reporting the current attempt and getWorkflowRunAttempt returning that attempt's record.
    """
    current = resolved_check(
        "maintenance-ci",
        job=api_job(attempt=2),
        run=api_run(attempt=2),
        attempt=api_attempt(attempt=2),
    )
    r = maintenance.evaluate_checks([current], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is True, r["reason"]
    assert_eq(r["provenance"][0]["run_attempt"], 2)

    # the job belongs to attempt 1 but the run has moved on to attempt 2
    superseded = resolved_check(
        "maintenance-ci",
        job=api_job(attempt=1),
        run=api_run(attempt=2),
        attempt=api_attempt(attempt=2),
    )
    r = maintenance.evaluate_checks([superseded], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False and "stale attempt" in r["reason"], r["reason"]

    # an attempt record for a different attempt number proves nothing about this job
    mismatched = resolved_check(
        "maintenance-ci",
        job=api_job(attempt=1),
        run=api_run(attempt=1),
        attempt=api_attempt(attempt=2),
    )
    r = maintenance.evaluate_checks([mismatched], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False and "attempt record" in r["reason"], r["reason"]

    # an attempt record from another run entirely
    foreign = resolved_check(
        "maintenance-ci",
        job=api_job(attempt=1),
        run=api_run(attempt=1),
        attempt=api_attempt(run_id=RUN_ID + 9, attempt=1),
    )
    r = maintenance.evaluate_checks([foreign], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False and "attempt record" in r["reason"], r["reason"]

    # a failed current attempt is not a successful gate
    failed_now = resolved_check(
        "maintenance-ci",
        job=api_job(attempt=2, conclusion="failure"),
        run=api_run(attempt=2, conclusion="failure"),
        attempt=api_attempt(attempt=2, conclusion="failure"),
    )
    r = maintenance.evaluate_checks([failed_now], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False, r["reason"]


def test_opaque_external_id_is_never_parsed_as_a_job_id():
    """A real Actions external_id is a UUID; its leading digits must not invent a job."""
    # observed read-only on the green maintenance-ci check
    genuine = resolved_check("maintenance-ci", external_id=REAL_EXTERNAL_ID)
    r = maintenance.evaluate_checks([genuine], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is True, r["reason"]
    assert_eq(r["provenance"][0]["external_id"], REAL_EXTERNAL_ID)

    # leading digits that parseInt would turn into a bogus job id (83e46dd2-... -> 83)
    leading_digits = "83e46dd2-1111-2222-3333-444444444444"
    parsed_as_job = resolved_check(
        "maintenance-ci",
        external_id=leading_digits,
        # the link names the real job; if external_id were parsed, job 83 would be fetched
        job=api_job(job_id=83, check_id=CHECK_ID),
        html_url=f"https://github.com/{REPO}/actions/runs/{RUN_ID}/job/83",
    )
    r = maintenance.evaluate_checks([parsed_as_job], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is True, (
        "a UUID with leading digits must resolve through the canonical link, not be rejected: "
        + r["reason"]
    )

    # a missing or opaque external_id changes nothing: the link is the route
    for opaque in (None, "", "not-a-number", "0", leading_digits):
        check = resolved_check("maintenance-ci", external_id=opaque)
        r = maintenance.evaluate_checks([check], ["maintenance-ci"], HEAD, HEAD, REPO)
        assert r["ok"] is True, f"external_id={opaque!r} must not block a genuine check: {r['reason']}"

    # a numeric external_id that disagrees with the link is still resolved by the link
    contradictory = resolved_check("maintenance-ci", external_id="7")
    r = maintenance.evaluate_checks([contradictory], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is True, r["reason"]
    assert_eq(r["provenance"][0]["job_id"], JOB_ID)


def test_canonical_link_must_name_this_repository_and_check():
    """The link and the job's own back-reference are what bind a check to its producer."""
    cases = {
        "foreign host": f"https://gitlab.com/{REPO}/actions/runs/{RUN_ID}/job/{JOB_ID}",
        "foreign repository": f"https://github.com/someone/else/actions/runs/{RUN_ID}/job/{JOB_ID}",
        "scheme-relative link": f"//github.com/{REPO}/actions/runs/{RUN_ID}/job/{JOB_ID}",
        "insecure scheme": f"http://github.com/{REPO}/actions/runs/{RUN_ID}/job/{JOB_ID}",
        "non-numeric run id": f"https://github.com/{REPO}/actions/runs/abc/job/{JOB_ID}",
        "non-numeric job id": f"https://github.com/{REPO}/actions/runs/{RUN_ID}/job/xyz",
        "run link without a job": f"https://github.com/{REPO}/actions/runs/{RUN_ID}",
        "job link of another run": f"https://github.com/{REPO}/actions/runs/{RUN_ID + 5}/job/{JOB_ID}",
        "unrelated url": "https://github.com/uesu/wvkhabh2s22/commit/deadbeef",
        "no url at all": None,
    }
    for label, link in cases.items():
        check = resolved_check("maintenance-ci", html_url=link)
        check["details_url"] = link
        r = maintenance.evaluate_checks([check], ["maintenance-ci"], HEAD, HEAD, REPO)
        assert r["ok"] is False, f"{label} must fail closed but was accepted"

    # the job's back-reference has to name this exact check in this exact repository
    wrong_check = resolved_check("maintenance-ci", job=api_job(check_id=CHECK_ID + 1))
    r = maintenance.evaluate_checks([wrong_check], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False and "does not point back at this check" in r["reason"], r["reason"]

    foreign_repo_job = resolved_check("maintenance-ci", job=api_job(repository="someone/else"))
    r = maintenance.evaluate_checks([foreign_repo_job], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False and "does not point back at this check" in r["reason"], r["reason"]

    # a same-named job from an unrelated run is not this check's producer
    same_name_other_run = resolved_check(
        "maintenance-ci",
        job=api_job(job_id=JOB_ID + 1, run_id=RUN_ID + 1, check_id=CHECK_ID),
        run=api_run(run_id=RUN_ID + 1),
        attempt=api_attempt(run_id=RUN_ID + 1),
        html_url=f"https://github.com/{REPO}/actions/runs/{RUN_ID}/job/{JOB_ID}",
    )
    r = maintenance.evaluate_checks([same_name_other_run], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False and "is not the linked job" in r["reason"], r["reason"]


DEPENDABOT_PATCH = """@@ -1,5 +1,5 @@
 aiohttp==3.14.3 \\
-    --hash=sha256:aaaa
+    --hash=sha256:bbbb
 feedparser==6.0.14 \\
"""


def _decision(checks, repository=REPO, labels=(), auto_merge="yes"):
    """Run the real side-effect-free decision path over an API-shaped fixture."""
    fixture = {
        "pr": {
            "number": 30, "state": "open", "draft": False, "mergeable": True,
            "mergeable_state": "clean",
            "base": {"ref": "main", "repo": repo_side()},
            "head": {"sha": HEAD, "repo": repo_side()},
            "title": "build(deps): bump aiohttp from 3.14.3 to 3.14.5",
            "user": {"login": "dependabot[bot]"},
        },
        "checks": checks,
        "changed_files": ["requirements.txt"],
        "file_patches": {"requirements.txt": DEPENDABOT_PATCH},
        "dependency_updates": [{"name": "aiohttp", "old": "3.14.3", "new": "3.14.5", "kind": "patch"}],
        "auto_merge_vars": {"AUTO_MERGE_DEPENDABOT": auto_merge},
        "labels": list(labels),
        "reviews": [],
        "head_sha": HEAD,
        "repository": repository,
        "review_required": False,
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
        json.dump(fixture, fh)
        fname = fh.name
    try:
        return maintenance.decision_from_fixture(fname, str(ROOT / ".github" / "maintenance.json"))
    finally:
        os.unlink(fname)


def test_decision_from_fixture():
    """The controller must authorize a real, fully provenanced gate — and nothing less."""
    decision = _decision([resolved_check("maintenance-ci")])
    assert_eq(decision["decision"], "merge")
    assert decision["merge"] is True, decision["reason"]

    # an unresolved live check run is missing evidence, so the controller blocks
    blocked = _decision([api_check_run("maintenance-ci")])
    assert_eq(blocked["decision"], "blocked")
    assert blocked["merge"] is False
    assert "provenance unresolved" in blocked["reason"], blocked["reason"]

    # provenance must be verifiable against an exact repository. The association gate now binds
    # both sides of the PR to the repository the controller was invoked for, so a missing
    # repository is rejected before check provenance is even consulted.
    no_repo = _decision([resolved_check("maintenance-ci")], repository=None)
    assert_eq(no_repo["decision"], "blocked")
    assert "missing-expected-repository" in no_repo["reason"], no_repo["reason"]

    # a superseded attempt cannot authorize a merge
    stale = _decision([resolved_check("maintenance-ci", job=api_job(attempt=1),
                                      run=api_run(attempt=2), attempt=api_attempt(attempt=2))])
    assert_eq(stale["decision"], "blocked")
    assert "stale attempt" in stale["reason"], stale["reason"]

    # a run produced in another repository cannot authorize a merge
    fork = _decision([resolved_check("maintenance-ci", run=api_run(full_name="someone/fork"))])
    assert_eq(fork["decision"], "blocked")
    assert "is not " + REPO in fork["reason"], fork["reason"]

    # the job behind the check must be the required job of the trusted workflow
    wrong_job = _decision([resolved_check("maintenance-ci", job=api_job(name="validate"))])
    assert_eq(wrong_job["decision"], "blocked")
    assert "does not match the required check" in wrong_job["reason"], wrong_job["reason"]

    wrong_run = _decision([resolved_check("maintenance-ci", run=api_run(run_id=RUN_ID + 3))])
    assert_eq(wrong_run["decision"], "blocked")
    assert "is not the linked run" in wrong_run["reason"], wrong_run["reason"]

    # policy gates stay independent of provenance
    assert_eq(_decision([resolved_check("maintenance-ci")], labels=("hold",))["decision"], "blocked")
    assert "hold" in _decision([resolved_check("maintenance-ci")], labels=("hold",))["reason"]
    assert_eq(_decision([resolved_check("maintenance-ci")], auto_merge="no")["decision"], "blocked")
    assert "auto-merge not enabled" in _decision([resolved_check("maintenance-ci")], auto_merge="no")["reason"]

    # a green gate on a different head than the published PR head authorizes nothing
    other_head = _decision([resolved_check("maintenance-ci", head_sha="def999", job_id=JOB_ID + 1)])
    assert_eq(other_head["decision"], "blocked")
    assert "head mismatch" in other_head["reason"], other_head["reason"]


# ---------------------------------------------------------------------------
# Proposer publication binding. The proposer runs on the base branch and
# publishes a NEW commit, so the run's head_sha is the source head and can never
# equal the published proposal head. Nothing about a branch name, a title, a time
# window or membership of a mutable upstream version list identifies which run
# validated and published this proposal, so the proposer emits two records bound
# to its own run and attempt and the controller has to find both.
#
# Three identities stay deliberately distinct: the run actor who authorized the
# run, the PAT publisher who authored the PR, and the configured commit bot who
# signed the commit. Requiring them to be one login is what made a genuine
# publication unverifiable.
# ---------------------------------------------------------------------------

PROPOSER_PATH = ".github/workflows/python_version_bump.yml"
SOURCE_HEAD = "58547a93c000000000000000000000000000000a"
PUBLISHED_HEAD = "abcdef1234567890abcdef1234567890abcdef12"
PROPOSER_RUN_ID = 38849000001
RUN_ACTOR = "release-engineer"          # authorized the run (manual dispatch)
PAT_PUBLISHER = "bump-pat-owner"        # owns BUMP_PAT, authors the PR
COMMIT_BOT = "github-actions[bot]"      # configured commit identity in trusted policy
#: The git identity the publisher actually commits with (its own documented defaults), which is
#: also what configured_commit_identity derives from the bot login in trusted policy.
COMMIT_BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
CANDIDATE = "3.14.9"
PYTHON_PATCH = "@@ -1 +1 @@\n-3.14.8\n+3.14.9\n"

#: The exact bytes the validated candidate proposal publishes.
PROPOSAL_BYTES = {
    ".python-version": CANDIDATE + "\n",
    "requirements.txt": "# pinned by candidate\naiohttp==3.13.2\n",
    "requirements-dev.txt": "# pinned by candidate\nactionlint-py==1.7.7.24\n",
    ".github/maintenance.json": None,   # filled from the real policy, rewritten narrowly
}


def proposal_workspace(candidate=CANDIDATE):
    """Materialise the proposal bytes in a temp dir and digest them the way the controller does."""
    policy_text = (ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8")
    workspace = tempfile.mkdtemp(prefix="proposal-")
    root = pathlib.Path(workspace)
    (root / ".github").mkdir(parents=True, exist_ok=True)
    for name, content in PROPOSAL_BYTES.items():
        text = content if content is not None else python_version_bump.rewrite_policy_versions(
            policy_text, candidate)
        (root / name).write_text(text, encoding="utf-8")
    return root, python_version_bump.digest_files(root)


PROPOSAL_ROOT, PROPOSAL_DIGESTS = proposal_workspace()


def api_proposer_run(*, run_id=PROPOSER_RUN_ID, attempt=1, head_sha=SOURCE_HEAD,
                     status="completed", conclusion="success", path=PROPOSER_PATH,
                     actor=RUN_ACTOR, full_name=REPO, workflow_id=88):
    """Shaped like GET /repos/{owner}/{repo}/actions/runs/{run_id} for the proposer."""
    return {
        "id": run_id, "name": "Python version proposer", "path": path, "workflow_id": workflow_id,
        "head_branch": "main", "head_sha": head_sha, "run_number": 12, "run_attempt": attempt,
        "event": "workflow_dispatch", "status": status, "conclusion": conclusion,
        "run_started_at": "2026-10-16T02:00:00Z", "created_at": "2026-10-16T02:00:00Z",
        "updated_at": "2026-10-16T02:05:00Z",
        "actor": {"id": 7, "login": actor, "type": "User"},
        "repository": {"id": 1, "full_name": full_name, "name": full_name.split("/")[1]},
        "head_repository": {"id": 1, "full_name": full_name, "name": full_name.split("/")[1]},
    }


def api_proposer_attempt(*, run_id=PROPOSER_RUN_ID, attempt=1, head_sha=SOURCE_HEAD,
                         status="completed", conclusion="success"):
    """Shaped like GET .../actions/runs/{run_id}/attempts/{attempt_number} for the proposer.

    No endpoint lists a run's attempts, so the current attempt comes from getWorkflowRun and
    this attempt-specific record has to agree with it.
    """
    return {
        "id": run_id, "run_attempt": attempt, "status": status, "conclusion": conclusion,
        "head_sha": head_sha, "path": PROPOSER_PATH, "workflow_id": 88,
        "name": "Python version proposer", "head_branch": "main", "event": "workflow_dispatch",
        "url": f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/attempts/{attempt}",
    }


def api_published_commit(*, sha=PUBLISHED_HEAD, author_login=COMMIT_BOT, committer_login=COMMIT_BOT,
                         git_name=COMMIT_BOT, git_email=COMMIT_BOT_EMAIL,
                         committer_name=None, committer_email=None, include_git_identity=True):
    """Shaped like GET /repos/{owner}/{repo}/commits/{ref} for the published proposal head.

    This models what the publisher really produces: the PAT holder pushes, but the commit object
    is signed with the configured bot identity, so GitHub resolves both the top-level author and
    committer *login* and the inner git name/email to that bot. The PR author (PAT publisher) and
    the run actor stay different identities and are verified separately.
    """
    inner = {
        "author": {"name": git_name, "email": git_email, "date": "2026-10-16T02:03:00Z"},
        "committer": {
            "name": git_name if committer_name is None else committer_name,
            "email": git_email if committer_email is None else committer_email,
            "date": "2026-10-16T02:03:00Z",
        },
        "message": f"chore: bump CPython pin 3.14.8 -> {CANDIDATE}",
    }
    commit = {
        "sha": sha,
        "author": {"id": 8, "login": author_login, "type": "Bot" if author_login == COMMIT_BOT else "User"},
        "committer": {"id": 8, "login": committer_login,
                      "type": "Bot" if committer_login == COMMIT_BOT else "User"},
    }
    if include_git_identity:
        commit["commit"] = inner
    return commit


def api_validation_record(*, run_id=PROPOSER_RUN_ID, attempt=1, source_head=SOURCE_HEAD,
                          candidate=CANDIDATE, interpreter=CANDIDATE,
                          pip=maintenance.PINNED_PIP, pip_tools=maintenance.PINNED_PIP_TOOLS,
                          digests=None):
    """The proposer's own proposal-validation notice record."""
    return python_version_bump.build_validation_evidence(
        source_head=source_head, candidate=candidate, interpreter=interpreter,
        pip_version=pip, pip_tools_version=pip_tools,
        digests=PROPOSAL_DIGESTS if digests is None else digests,
        run_id=run_id, run_attempt=attempt,
    )


def api_publication_record(*, run_id=PROPOSER_RUN_ID, attempt=1, pull_number=31,
                           published_head=PUBLISHED_HEAD, branch=f"chore/python-{CANDIDATE}",
                           digests=None, pr_author=PAT_PUBLISHER, commit_identity=COMMIT_BOT,
                           run_actor=RUN_ACTOR):
    """The proposer's own proposal-publication notice record."""
    return python_version_bump.build_publication_evidence(
        run_id=run_id, run_attempt=attempt, pull_number=pull_number,
        published_head=published_head, branch=branch,
        digests=PROPOSAL_DIGESTS if digests is None else digests,
        pr_author=pr_author, commit_identity=commit_identity, run_actor=run_actor,
    )


def _proposer_checks():
    """The mandatory CI gate, resolved against the published proposal head.

    This is the ci.yml run that validated the published head — a different run from the
    proposer run that published it, so the two must not be conflated.
    """
    return [resolved_check("maintenance-ci", head_sha=PUBLISHED_HEAD)]


def _proposer_fixture(**overrides):
    """The API-shaped fixture a python-bump proposal produces, before the decision path."""
    fixture = {
        "pr": {
            "number": 31, "state": "open", "draft": False, "mergeable": True,
            "mergeable_state": "clean",
            "base": {"ref": "main", "repo": repo_side()},
            "head": {"sha": PUBLISHED_HEAD, "ref": f"chore/python-{CANDIDATE}", "repo": repo_side()},
            "title": f"chore: bump CPython pin 3.14.8 -> {CANDIDATE}",
            "user": {"login": PAT_PUBLISHER},
        },
        "checks": _proposer_checks(),
        "changed_files": [".python-version"],
        "file_patches": {".python-version": PYTHON_PATCH},
        "auto_merge_vars": {"AUTO_MERGE_PYTHON_BUMP": "yes"},
        "labels": [], "reviews": [], "head_sha": PUBLISHED_HEAD, "repository": REPO,
        "review_required": False, "deferred": False,
        "proposer_run": api_proposer_run(),
        "proposer_attempt": api_proposer_attempt(),
        "validation_record": api_validation_record(),
        "publication_record": api_publication_record(),
        "proposal_digests": dict(PROPOSAL_DIGESTS),
        "published_commit": api_published_commit(),
    }
    fixture.update(overrides)
    return fixture


def _proposer_decision(policy_text=None, **overrides):
    """Run the real decision path over an API-shaped python-bump proposal.

    ``policy_text`` substitutes the trusted base policy, so a test can exercise a policy that
    declares different security-evidence requirements without touching the shipped profile.
    """
    fixture = _proposer_fixture(**overrides)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
        json.dump(fixture, fh)
        fname = fh.name
    policy_path = str(ROOT / ".github" / "maintenance.json")
    tmp_policy = None
    try:
        if policy_text is not None:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ph:
                ph.write(policy_text)
                tmp_policy = ph.name
            policy_path = tmp_policy
        return maintenance.decision_from_fixture(fname, policy_path)
    finally:
        os.unlink(fname)
        if tmp_policy:
            os.unlink(tmp_policy)


def test_proposer_publication_bound_to_the_real_run():
    """A BUMP_PAT publication is authorized by its own records, with source head != published head."""
    decision = _proposer_decision()
    assert_eq(decision["decision"], "merge")
    assert decision["merge"] is True, decision["reason"]
    # the run's own head is the source head, deliberately not the published proposal head
    assert_eq(api_proposer_run()["head_sha"], SOURCE_HEAD)
    assert SOURCE_HEAD != PUBLISHED_HEAD
    # the three identities are distinct and all bound, not collapsed into one login
    assert RUN_ACTOR != PAT_PUBLISHER != COMMIT_BOT
    policy = json.loads((ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8"))
    fixture = _proposer_fixture()
    fixture["expected_commit_identity"] = policy["python"]["commit_identity"]
    verified, reason = maintenance.verify_publication_record(fixture, fixture["pr"], CANDIDATE)
    assert verified, reason
    for identity in (RUN_ACTOR, PAT_PUBLISHER, COMMIT_BOT):
        assert identity in reason, f"{identity} must be bound in the provenance: {reason}"
    assert str(PROPOSER_RUN_ID) in reason, reason


def test_proposer_publication_rejects_incomplete_evidence():
    """Every gap in the publication chain fails closed with a specific reason."""
    stale_locks = dict(PROPOSAL_DIGESTS)
    stale_locks["requirements.txt"] = "0" * 64
    cases = {
        "proposer run unavailable": {"proposer_run": None},
        "failed proposer run": {"proposer_run": api_proposer_run(conclusion="failure")},
        "run still in progress": {"proposer_run": api_proposer_run(status="in_progress", conclusion=None)},
        "run from a fork": {"proposer_run": api_proposer_run(full_name="someone/fork")},
        "untrusted proposer workflow": {"proposer_run": api_proposer_run(path=".github/workflows/other.yml")},
        "run without a workflow id": {"proposer_run": api_proposer_run(workflow_id=None)},
        "run carries no attempt number": {"proposer_run": {**api_proposer_run(), "run_attempt": None}},
        "proposer superseded by a rerun": {
            "proposer_run": api_proposer_run(attempt=1),
            "proposer_attempt": api_proposer_attempt(attempt=2),
        },
        "attempt record for another run": {"proposer_attempt": api_proposer_attempt(run_id=PROPOSER_RUN_ID + 4)},
        "attempt record on another source head": {"proposer_attempt": api_proposer_attempt(head_sha="ffffffff")},
        "attempt record failed": {"proposer_attempt": api_proposer_attempt(conclusion="failure")},
        "attempt record unavailable": {"proposer_attempt": None},
        "validation record missing": {"validation_record": None},
        "publication record missing": {"publication_record": None},
        "validation record from another run": {"validation_record": api_validation_record(run_id=PROPOSER_RUN_ID + 9)},
        "publication record from another attempt": {"publication_record": api_publication_record(attempt=3)},
        "publication record names another PR": {"publication_record": api_publication_record(pull_number=99)},
        "publication record names a stale head": {"publication_record": api_publication_record(published_head="deadbeef")},
        "publication record names another branch": {"publication_record": api_publication_record(branch="chore/python-3.15.0")},
        "publication record claims another author": {"publication_record": api_publication_record(pr_author="someone-else")},
        "publication record claims another run actor": {"publication_record": api_publication_record(run_actor="someone-else")},
        "publication record signed by another bot": {"publication_record": api_publication_record(commit_identity="not-the-bot")},
        "publication record without a commit identity": {"publication_record": {**api_publication_record(), "commit_identity": None}},
        "validation record from another source head": {"validation_record": api_validation_record(source_head="11111111")},
        "validation record for another candidate": {"validation_record": api_validation_record(candidate="3.15.0")},
        "validation not run by the candidate interpreter": {"validation_record": api_validation_record(interpreter="3.14.8")},
        "validation used unpinned pip": {"validation_record": api_validation_record(pip="25.0")},
        "validation used unpinned pip-tools": {"validation_record": api_validation_record(pip_tools="7.4.0")},
        "validated lock bytes differ from the published bytes": {"validation_record": api_validation_record(digests=stale_locks)},
        "published bytes moved after validation": {"proposal_digests": stale_locks},
        "digests never computed": {"proposal_digests": None},
        "publication record carries no digests": {"publication_record": {**api_publication_record(), "digests": None}},
        "published commit is not the PR head": {"published_commit": api_published_commit(sha="deadbeef")},
        "published commit unavailable": {"published_commit": None},
    }
    for label, overrides in cases.items():
        decision = _proposer_decision(**overrides)
        assert decision["merge"] is False, f"{label} must not authorize a merge"
        assert_eq(decision["decision"], "review-required")
        assert "publication unverified" in decision["reason"], f"{label}: {decision['reason']}"

    # A fixture that names no repository cannot be associated with this repository at all, so the
    # association gate blocks it before publication provenance is consulted.
    no_repo = _proposer_decision(repository=None)
    assert no_repo["merge"] is False
    assert_eq(no_repo["decision"], "blocked")
    assert "missing-expected-repository" in no_repo["reason"], no_repo["reason"]


def test_repository_association_binds_both_sides_of_the_pr():
    """Branch name and base ref are not provenance: a fork head or a missing association blocks."""
    assert _proposer_decision()["merge"] is True, "the same-repository proposal must still merge"

    def pr_with(**kwargs):
        pr = {
            "number": 31, "state": "open", "draft": False, "mergeable": True,
            "mergeable_state": "clean",
            "base": {"ref": "main", "repo": repo_side()},
            "head": {"sha": PUBLISHED_HEAD, "ref": f"chore/python-{CANDIDATE}", "repo": repo_side()},
            "title": f"chore: bump CPython pin 3.14.8 -> {CANDIDATE}",
            "user": {"login": PAT_PUBLISHER},
        }
        pr.update(kwargs)
        return pr

    cases = {
        "head comes from a fork": pr_with(
            head={"sha": PUBLISHED_HEAD, "ref": f"chore/python-{CANDIDATE}",
                  "repo": repo_side("attacker/wvkhabh2s22")}),
        "base names a foreign repository": pr_with(base={"ref": "main", "repo": repo_side("uesu/News-Express")}),
        "head carries no repository association": pr_with(head={"sha": PUBLISHED_HEAD, "ref": "x"}),
        "base carries no repository association": pr_with(base={"ref": "main"}),
    }
    for label, pr in cases.items():
        decision = _proposer_decision(pr=pr)
        assert decision["merge"] is False, f"{label} must not authorize a merge"
        assert_eq(decision["decision"], "blocked")
        assert ("repository association" in decision["reason"] or "fork PR" in decision["reason"]), \
            f"{label}: {decision['reason']}"


def test_security_evidence_is_a_declared_policy_not_an_absence():
    """Scan evidence is resolved from trusted policy; unconfigured blocks and unavailable defers.

    A repository either requires named scan evidence or has deliberately declared that it requires
    none. Those are different statements, and only the second one may skip the gate.
    """
    shipped = (ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8")
    profile = json.loads(shipped)
    mode, required = maintenance.common.security_policy(profile)
    assert_eq(mode, "disabled")
    assert_eq(required, [])
    assert (profile.get("security_evidence") or {}).get("reason"), "a disabled gate must record why"
    assert _proposer_decision()["merge"] is True, "the shipped declaration must not block"

    def policy_with(block):
        variant = json.loads(shipped)
        if block is None:
            variant.pop("security_evidence", None)
        else:
            variant["security_evidence"] = block
        return json.dumps(variant, indent=2)

    # Deleting the declaration is a configuration gap, not a pass.
    gap = _proposer_decision(policy_text=policy_with(None))
    assert gap["merge"] is False
    assert_eq(gap["decision"], "blocked")
    assert "security-evidence-unconfigured" in gap["reason"], gap["reason"]

    required_policy = policy_with({"mode": "required", "required": ["code-scanning"]})
    scan = {"state": "ok", "open": 0, "analysed": True}

    # Required and satisfied by a clean scan.
    clean = _proposer_decision(policy_text=required_policy, security_evidence={"code-scanning": scan})
    assert clean["merge"] is True, clean["reason"]

    # Required with unresolved findings blocks.
    dirty = _proposer_decision(policy_text=required_policy,
                               security_evidence={"code-scanning": {"state": "ok", "open": 3}})
    assert dirty["merge"] is False
    assert_eq(dirty["decision"], "blocked")
    assert "unresolved-code-scanning-alerts:3" in dirty["reason"], dirty["reason"]

    # Required but never collected, or collected and unavailable, defers to a human. An empty
    # requirement list is not a pass and an unavailable scan is not clean.
    for label, evidence in (("absent", None),
                            ("unavailable", {"code-scanning": {"state": "unavailable", "open": None}}),
                            ("malformed open count", {"code-scanning": {"state": "ok", "open": "none"}})):
        deferred = _proposer_decision(policy_text=required_policy, security_evidence=evidence)
        assert deferred["merge"] is False, f"{label} must not authorize a merge"
        assert_eq(deferred["decision"], "review-required")
        assert "security evidence unavailable" in deferred["reason"], f"{label}: {deferred['reason']}"

    # Evidence supplied by PR content cannot satisfy a policy that declares none required, and a
    # policy that requires a kind the evidence does not cover still defers.
    partial = _proposer_decision(policy_text=policy_with(
        {"mode": "required", "required": ["code-scanning", "secret-scanning"]}),
        security_evidence={"code-scanning": scan})
    assert partial["merge"] is False
    assert "security-evidence-missing:secret-scanning" in partial["reason"], partial["reason"]


def test_trusted_author_does_not_bypass_publication_provenance():
    """Whichever trusted identity authored the proposal, absent records authorize nothing."""
    for login in ("github-actions[bot]", "dependabot[bot]", PAT_PUBLISHER, RUN_ACTOR):
        pr = {
            "number": 31, "state": "open", "draft": False, "mergeable": True,
            "mergeable_state": "clean",
            "base": {"ref": "main", "repo": repo_side()},
            "head": {"sha": PUBLISHED_HEAD, "ref": f"chore/python-{CANDIDATE}", "repo": repo_side()},
            "title": f"chore: bump CPython pin 3.14.8 -> {CANDIDATE}",
            "user": {"login": login},
        }
        decision = _proposer_decision(pr=pr, proposer_run=None)
        assert decision["merge"] is False, f"{login} must not bypass provenance"
        assert "publication unverified" in decision["reason"], decision["reason"]
        # a complete chain authored by the same identity is still bound to the records
        bound = _proposer_decision(pr=pr, publication_record=api_publication_record(pr_author=login))
        assert bound["merge"] is True, f"{login}: {bound['reason']}"


def test_publication_verifies_the_fetched_commit_not_the_notice_claim():
    """A notice may copy the expected bot string; that is not verification of the commit object.

    Before this repository adopted the shared verifier, the commit identity was checked only
    against the string the publication notice reported about itself, so a publisher that claimed
    the configured bot while committing as anyone else passed. The independently fetched commit
    is now the authority for both the GitHub login and the git name/email of author and committer.
    """
    # The real shape still verifies: PAT publishes the PR, the actor authorized the run, and the
    # commit object is signed by the configured bot. Three identities, each bound to its own source.
    bound = _proposer_decision()
    assert bound["merge"] is True, bound["reason"]

    cases = {
        "commit pushed under the PAT owner's GitHub login": api_published_commit(author_login=PAT_PUBLISHER),
        "committed under the PAT owner's GitHub login": api_published_commit(committer_login=PAT_PUBLISHER),
        "git author name is not the configured bot": api_published_commit(git_name="Someone Else"),
        "git author email is not the configured bot": api_published_commit(git_email="attacker@example.com"),
        "git committer name is not the configured bot": api_published_commit(committer_name="Someone Else"),
        "git committer email is not the configured bot": api_published_commit(committer_email="attacker@example.com"),
        "commit object carries no git identity at all": api_published_commit(include_git_identity=False),
        "fetched commit is not the PR head": api_published_commit(sha="deadbeef" * 5),
        "fetched commit unavailable": None,
    }
    for label, commit in cases.items():
        decision = _proposer_decision(published_commit=commit)
        assert decision["merge"] is False, f"{label} must not authorize a merge"
        assert_eq(decision["decision"], "review-required")
        assert "publication unverified" in decision["reason"], f"{label}: {decision['reason']}"


def test_notice_records_survive_the_annotation_round_trip():
    """The records the workflow prints are the records the controller parses back."""
    validation = api_validation_record()
    publication = api_publication_record()
    for record, kind in ((validation, python_version_bump.VALIDATION_NOTICE),
                         (publication, python_version_bump.PUBLICATION_NOTICE)):
        line = python_version_bump.notice(kind, record)
        assert line is not None, f"{kind} evidence must fit in a bounded notice"
        assert line.startswith("::notice::"), line
        parsed = python_version_bump.parse_notice(line[len("::notice::"):], kind)
        assert parsed == record, f"{kind} record did not survive the round trip"
        assert python_version_bump.parse_notice(line, "other-kind") is None
    assert python_version_bump.parse_notice("::notice::proposal-validation not json",
                                            python_version_bump.VALIDATION_NOTICE) is None
    assert python_version_bump.parse_notice(None, python_version_bump.VALIDATION_NOTICE) is None
    oversized = python_version_bump.notice(
        python_version_bump.VALIDATION_NOTICE,
        {**validation, "digests": {k: "f" * 4000 for k in maintenance.PROPOSAL_FILES}},
        limit=200)
    assert oversized is None, "an oversized record must not be truncated into a lie"


def test_policy_reconciliation_is_narrow():
    """Only the two derived version fields may move; anything else is rejected."""
    before = (ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8")
    after = python_version_bump.rewrite_policy_versions(before, CANDIDATE)
    assert python_version_bump.policy_change_is_narrow(before, after, CANDIDATE)
    rewritten = json.loads(after)["python"]
    assert_eq(rewritten["current_version"], CANDIDATE)
    assert_eq(rewritten["supported_versions"], [CANDIDATE])
    # a one-version bump must stay a two-line diff: re-serialising would reformat every
    # unrelated array and make the proposal impossible to review
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert_eq(len(changed), 2)
    assert_eq(len(before.splitlines()), len(after.splitlines()))
    for old_line, new_line in changed:
        assert "current_version" in old_line or "supported_versions" in old_line, old_line
        assert "3.14.8" in old_line and CANDIDATE in new_line, (old_line, new_line)
    # the same rewrite is deterministic, so a regenerated proposal is byte-stable
    assert_eq(python_version_bump.rewrite_policy_versions(before, CANDIDATE), after)
    # an unrelated key change is not a narrow reconciliation
    tampered = json.loads(after)
    tampered["checks"]["mandatory"] = []
    assert not python_version_bump.policy_change_is_narrow(
        before, json.dumps(tampered, indent=2) + "\n", CANDIDATE)
    # neither is leaving another python field behind
    tampered2 = json.loads(after)
    tampered2["python"]["soak_days"] = 0
    assert not python_version_bump.policy_change_is_narrow(
        before, json.dumps(tampered2, indent=2) + "\n", CANDIDATE)
    assert not python_version_bump.policy_change_is_narrow(before, "not json", CANDIDATE)


# ---------------------------------------------------------------------------
# Candidate/publisher adapter integration: execute the ACTUAL Python blocks from
# python_version_bump.yml with mocked I/O. Helper-only fixtures would conceal a
# broken caller, so the caller's own source is extracted and run here.
# ---------------------------------------------------------------------------

BUMP_WORKFLOW = ROOT / ".github" / "workflows" / "python_version_bump.yml"


def workflow_step(step_name):
    """The full text of one workflow step, so a caller cannot drift from its helpers."""
    text = BUMP_WORKFLOW.read_text(encoding="utf-8")
    start = text.index(f"      - name: {step_name}")
    rest = text[start + 1:]
    end = start + 1 + (re.search(r"^      - name: ", rest, re.M).start() if re.search(
        r"^      - name: ", rest, re.M) else len(rest))
    return text[start:end]


def workflow_python_block(step_name):
    """Extract and dedent the embedded `python - <<'PY'` program of one step."""
    step = workflow_step(step_name)
    match = re.search(r"python - <<'PY'\n(.*?)^\s+PY\n", step, re.S | re.M)
    assert match, f"step {step_name!r} embeds no python program"
    lines = match.group(1).splitlines()
    indent = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
    return "\n".join(line[indent:] if line.strip() else "" for line in lines) + "\n"


PUBLISHER_BLOCK = workflow_python_block("Publish only the validated bytes (no candidate execution here)")
CANDIDATE_BLOCK = workflow_python_block(
    "Validate the candidate proposal bytes in an isolated workspace")


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def mock_git(root, *, diff_names, head_policy, commit_author):
    """A mock `git` covering exactly the calls the publisher makes."""
    script = f"""#!/bin/sh
case "$1 $2" in
  "diff --name-only") printf '%s\\n' {' '.join(repr(n) for n in diff_names) or "''"} ;;
  "show HEAD:.github/maintenance.json") cat {root / 'policy-before.json'} ;;
  "log -1") printf '%s\\n' {commit_author!r} ;;
  *) printf 'mock-git: unexpected %s\\n' "$*" >&2; exit 64 ;;
esac
"""
    binary = root / "bin" / "git"
    _write(binary, script)
    binary.chmod(0o755)
    _write(root / "policy-before.json", head_policy)
    return str(root / "bin")


def run_publisher(*, workspace, validated, env, path_prepend=None):
    """Execute the publisher's real Python program against mocked I/O."""
    script = workspace / "publisher.py"
    script.write_text(PUBLISHER_BLOCK, encoding="utf-8")
    environ = {
        "PATH": (path_prepend + ":" if path_prepend else "") + os.environ["PATH"],
        "PYTHONPATH": str(ROOT / ".github" / "scripts"),
        "LATEST": CANDIDATE, "CURRENT": "3.14.8",
        "GITHUB_SHA": SOURCE_HEAD, "GITHUB_RUN_ID": str(PROPOSER_RUN_ID),
        "GITHUB_RUN_ATTEMPT": "1",
    }
    environ.update({k: v for k, v in env.items() if v is not None})
    return subprocess.run([sys.executable, str(script)], cwd=workspace, env=environ,
                          capture_output=True, text=True, timeout=120)


VALIDATED_ARTIFACT_PATH = "/tmp/validated-proposal"


def stage_validated_artifact(*, record=None, files=None, self_consistent=False):
    """Seed the artifact at the exact path the publisher downloads to, and return both roots.

    The publisher reads a fixed path because that is where download-artifact puts the files in
    CI, so a test that does not reset it inherits the previous test's bytes and proves nothing.
    """
    validated = pathlib.Path(tempfile.mkdtemp(prefix="artifact-"))
    seed_validated_artifact(validated, record=record, files=files)
    if self_consistent:
        # An artifact whose record honestly digests its own (bad) bytes: only the semantic gates
        # can catch this, which is exactly the case worth testing.
        honest = dict(record or api_validation_record())
        honest["digests"] = python_version_bump.digest_files(validated)
        _write(validated / "proposal-validation.json",
               json.dumps(honest, sort_keys=True, indent=2) + "\n")
    shutil.rmtree(VALIDATED_ARTIFACT_PATH, ignore_errors=True)
    shutil.copytree(validated, VALIDATED_ARTIFACT_PATH)
    return validated


def seed_validated_artifact(directory, *, record=None, files=None):
    """The artifact the candidate job uploads: the exact validated bytes plus their record.

    ``files`` replaces the whole set, so a caller can model an artifact that is missing a file.
    """
    for name in maintenance.PROPOSAL_FILES:
        if files is not None and name not in files:
            continue
        content = (files or {}).get(name, (PROPOSAL_ROOT / name).read_text(encoding="utf-8"))
        _write(directory / name, content)
    _write(directory / "proposal-validation.json",
           json.dumps(record if record is not None else api_validation_record(),
                      sort_keys=True, indent=2) + "\n")


def publisher_workspace():
    """A checkout of the base branch at the source head, ready for the publisher to modify."""
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="publisher-"))
    (workspace / ".github").mkdir(parents=True)
    _write(workspace / ".python-version", "3.14.8\n")
    _write(workspace / "requirements.txt", "# base graph\naiohttp==3.13.1\n")
    _write(workspace / "requirements-dev.txt", "# base dev graph\n")
    _write(workspace / ".github" / "maintenance.json",
           (ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8"))
    return workspace


def test_publisher_step_publishes_only_the_validated_bytes():
    """The real publisher program republishes the validated bytes and verifies their digests."""
    workspace = publisher_workspace()
    validated = stage_validated_artifact()
    try:
        bin_dir = mock_git(
            workspace, diff_names=list(maintenance.PROPOSAL_FILES),
            head_policy=(ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8"),
            commit_author=COMMIT_BOT)
        result = run_publisher(workspace=workspace, validated=validated, env={},
                               path_prepend=bin_dir)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "published bytes match the validated proposal" in result.stdout, result.stdout
        assert "changed files accepted" in result.stdout, result.stdout
        for name in maintenance.PROPOSAL_FILES:
            assert (workspace / name).read_bytes() == (validated / name).read_bytes(), name
    finally:
        shutil.rmtree(VALIDATED_ARTIFACT_PATH, ignore_errors=True)


def test_publisher_step_rejects_every_way_the_artifact_can_be_wrong():
    """Wrong, stale or contradictory artifacts fail closed instead of being published."""
    base_policy = (ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8")
    tampered_policy = json.loads(base_policy)
    tampered_policy["checks"]["mandatory"] = []
    tampered_policy_text = json.dumps(tampered_policy, indent=2) + "\n"

    cases = {
        "artifact validated another candidate": dict(
            record=api_validation_record(candidate="3.15.0")),
        "artifact not validated by the candidate interpreter": dict(
            record=api_validation_record(interpreter="3.14.8")),
        "artifact validated a stale source head": dict(
            record=api_validation_record(source_head="1111111111111111111111111111111111111111")),
        "artifact validated unpinned pip": dict(record=api_validation_record(pip="25.0")),
        "artifact digests do not match its own bytes": dict(
            record=api_validation_record(
                digests={**PROPOSAL_DIGESTS, "requirements.txt": "0" * 64})),
        "artifact is missing a proposal file": dict(
            files={name: (PROPOSAL_ROOT / name).read_text(encoding="utf-8")
                   for name in maintenance.PROPOSAL_FILES if name != "requirements-dev.txt"}),
        "artifact rewrites policy beyond the derived versions": dict(
            files={**{n: (PROPOSAL_ROOT / n).read_text(encoding="utf-8")
                      for n in maintenance.PROPOSAL_FILES},
                   ".github/maintenance.json": tampered_policy_text},
            self_consistent=True),
    }
    expected_reason = {
        "artifact validated another candidate": "validated candidate 3.15.0 is not",
        "artifact not validated by the candidate interpreter": "not performed by the candidate interpreter",
        "artifact validated a stale source head": "is not the current checkout",
        "artifact validated unpinned pip": "do not match the validated proposal",
        "artifact digests do not match its own bytes": "do not match the validated proposal",
        "artifact is missing a proposal file": "proposal artifact is missing requirements-dev.txt",
        "artifact rewrites policy beyond the derived versions": "beyond the two derived version fields",
    }
    for label, kwargs in cases.items():
        workspace = publisher_workspace()
        validated = stage_validated_artifact(**kwargs)
        try:
            bin_dir = mock_git(workspace, diff_names=list(maintenance.PROPOSAL_FILES),
                               head_policy=base_policy, commit_author=COMMIT_BOT)
            result = run_publisher(workspace=workspace, validated=validated, env={},
                                   path_prepend=bin_dir)
            combined = result.stdout + result.stderr
            assert result.returncode != 0, f"{label} must not publish"
            # a specific reason, not an incidental crash: the publisher has to know why
            assert "::error::" in combined, f"{label}: {combined}"
            assert "Traceback" not in combined, f"{label} crashed instead of failing closed: {combined}"
            assert expected_reason[label] in combined, f"{label}: {combined}"
        finally:
            shutil.rmtree(VALIDATED_ARTIFACT_PATH, ignore_errors=True)


def test_publisher_step_rejects_a_commit_signed_by_another_identity():
    """The configured commit bot is trusted policy; a different signer is rejected."""
    workspace = publisher_workspace()
    validated = stage_validated_artifact()
    try:
        base_policy = (ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8")
        bin_dir = mock_git(workspace, diff_names=list(maintenance.PROPOSAL_FILES),
                           head_policy=base_policy, commit_author="someone-else")
        result = run_publisher(workspace=workspace, validated=validated, env={},
                               path_prepend=bin_dir)
        combined = result.stdout + result.stderr
        assert result.returncode != 0, "an unknown commit identity must not publish"
        assert "Traceback" not in combined, combined
        assert "is not the configured bot" in combined, combined
    finally:
        shutil.rmtree(VALIDATED_ARTIFACT_PATH, ignore_errors=True)


def test_publisher_step_reports_an_unchanged_proposal_without_publishing():
    """A byte-identical proposal is reported, not committed as an empty change."""
    workspace = publisher_workspace()
    # the validated bytes ARE the base bytes, so nothing would change
    record = api_validation_record(digests=python_version_bump.digest_files(workspace))
    validated = stage_validated_artifact(
        record=record,
        files={name: (workspace / name).read_text(encoding="utf-8")
               for name in maintenance.PROPOSAL_FILES})
    try:
        bin_dir = mock_git(workspace, diff_names=[], head_policy="", commit_author=COMMIT_BOT)
        result = run_publisher(workspace=workspace, validated=validated, env={},
                               path_prepend=bin_dir)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "byte-identical to the base" in result.stdout, result.stdout
    finally:
        shutil.rmtree(VALIDATED_ARTIFACT_PATH, ignore_errors=True)


def test_missing_pat_is_an_explicit_configuration_error():
    """An unconfigured publisher token fails loudly rather than publishing anonymously."""
    step = workflow_step("Verify BUMP_PAT is configured")
    assert "BUMP_PAT secret is not configured" in step, step
    match = re.search(r"run: \|\n(.*?)(?=\n      - name:|\Z)", step, re.S)
    assert match, "the PAT step embeds no run script"
    script = match.group(1)
    lines = script.splitlines()
    indent = min(len(l) - len(l.lstrip()) for l in lines if l.strip())
    body = "\n".join(l[indent:] for l in lines if l.strip()) + "\n"
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="pat-"))
    script_path = workspace / "check.sh"
    # GitHub Actions runs `run:` blocks with bash -eo pipefail, so use bash, not /bin/sh
    script_path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    script_path.chmod(0o755)
    missing = subprocess.run([str(script_path)], capture_output=True, text=True,
                             env={"PATH": os.environ["PATH"], "BUMP_PAT": ""}, timeout=60)
    assert missing.returncode != 0, "an absent PAT must fail closed"
    assert "::error::BUMP_PAT secret is not configured" in missing.stdout + missing.stderr
    present = subprocess.run([str(script_path)], capture_output=True, text=True,
                             env={"PATH": os.environ["PATH"], "BUMP_PAT": "x"}, timeout=60)
    assert present.returncode == 0, present.stdout + present.stderr
    assert "x" not in present.stdout, "the token value must never be echoed"


def test_candidate_step_validates_the_exact_proposal_under_the_candidate():
    """The real candidate program regenerates both graphs, runs the registry and emits evidence."""
    assert "piptools" in CANDIDATE_BLOCK and "--generate-hashes" in CANDIDATE_BLOCK
    # both graphs are regenerated with the registry's own seeded relative-output command
    assert '--output-file={lock}' in CANDIDATE_BLOCK, CANDIDATE_BLOCK
    for lock in ("requirements.txt", "requirements-dev.txt"):
        assert f'"{lock}"' in CANDIDATE_BLOCK, f"{lock} must be regenerated under the candidate"
    assert "--strip-extras" in CANDIDATE_BLOCK and "--resolver=backtracking" in CANDIDATE_BLOCK
    assert "--allow-unsafe" in CANDIDATE_BLOCK, "the seeded command must match the registry's"
    assert "ci.py" in CANDIDATE_BLOCK and "--profile" in CANDIDATE_BLOCK and "production" in CANDIDATE_BLOCK
    assert "policy_change_is_narrow" in CANDIDATE_BLOCK
    assert "build_validation_evidence" in CANDIDATE_BLOCK and "notice(" in CANDIDATE_BLOCK
    # the baseline is validated under its exact pin BEFORE any candidate is considered
    baseline = workflow_step("Validate the current baseline under its exact pin")
    assert "platform.python_version() == pin" in baseline, baseline
    assert "ci.py --profile production" in baseline
    # the candidate job installs the same toolchain the registry demands, not runtime only
    toolchain = workflow_step("Install validation toolchain (both frozen graphs plus mandatory linters)")
    for required in ("requirements.txt", "requirements-dev.txt", "zizmor==", "actionlint",
                     "sha256sum -c", "--require-hashes", "--only-binary=:all:"):
        assert required in toolchain, f"candidate toolchain is missing {required}"


def test_candidate_step_executes_and_emits_bounded_evidence():
    """Run the candidate program with mocked regeneration, install and registry I/O."""
    source = pathlib.Path(tempfile.mkdtemp(prefix="candidate-source-"))
    (source / ".github" / "scripts").mkdir(parents=True)
    (source / "testing area").mkdir()
    _write(source / ".python-version", "3.14.8\n")
    _write(source / "requirements.in", "aiohttp\n")
    _write(source / "requirements.txt", "# base graph\n")
    _write(source / "requirements-dev.in", "actionlint-py\n")
    _write(source / "requirements-dev.txt", "# base dev graph\n")
    _write(source / ".github" / "maintenance.json",
           (ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8"))
    # a registry stand-in whose exit code the test controls
    _write(source / ".github" / "scripts" / "ci.py",
           "import os, sys\nsys.exit(int(os.environ.get('FAKE_REGISTRY_RC', '0')))\n")

    # mocked piptools/pip so no network or real interpreter is needed
    fake = pathlib.Path(tempfile.mkdtemp(prefix="fake-modules-"))
    _write(fake / "piptools" / "__init__.py", "")
    _write(fake / "piptools" / "__main__.py", """
import pathlib, sys
argv = sys.argv[1:]
if argv[:1] == ['--version']:
    print('pip-compile, version 7.5.3')
    sys.exit(0)
out = [a.split('=', 1)[1] for a in argv if a.startswith('--output-file=')][0]
pathlib.Path(out).write_text('# regenerated under the candidate\n')
""")
    _write(fake / "pip" / "__init__.py", "")
    _write(fake / "pip" / "__main__.py", """
import sys
if '--version' in sys.argv:
    print('pip 26.1.1 from /fake/pip (python 3.14)')
sys.exit(0)
""")
    bin_dir = fake / "bin"
    bin_dir.mkdir()
    _write(bin_dir / "pip-compile", "#!/bin/sh\necho 'pip-compile, version 7.5.3'\n")
    (bin_dir / "pip-compile").chmod(0o755)

    script = source / "candidate.py"
    script.write_text(CANDIDATE_BLOCK, encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PYTHONPATH": f"{ROOT / '.github' / 'scripts'}:{fake}",
        "CANDIDATE": CANDIDATE, "GITHUB_SHA": SOURCE_HEAD,
        "GITHUB_RUN_ID": str(PROPOSER_RUN_ID), "GITHUB_RUN_ATTEMPT": "1",
        "FAKE_REGISTRY_RC": "0",
        # the program asserts sys.executable reports the candidate version
        "FAKE_INTERPRETER": CANDIDATE,
    }
    # The block calls sys.executable for the version probe; run it under a stub that reports
    # the candidate so the real assertion is exercised rather than bypassed.
    probe = source / "probe" / "sitecustomize.py"
    _write(probe, "")
    result = subprocess.run([sys.executable, str(script)], cwd=source, env=env,
                            capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        # the local interpreter is 3.11, so the honest expectation is the mismatch guard firing
        assert "candidate interpreter is" in result.stdout + result.stderr, (
            result.stdout + result.stderr)
        assert f"expected {CANDIDATE}" in result.stdout + result.stderr
    else:
        assert "::notice::proposal-validation" in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# Proposal artifact packaging. Uploading several separate absolute paths makes
# the action use their least common ancestor as the artifact root and excludes
# hidden files by default, so the publisher would find neither .python-version
# nor .github/maintenance.json, and the visible files would be nested one
# directory deeper than it looks. These tests round-trip a real archive.
# ---------------------------------------------------------------------------

def _zip_tree(root, archive, *, prefix="", skip_hidden=False):
    """Zip a tree the way the upload action would, honouring its documented defaults."""
    import zipfile
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if skip_hidden and any(part.startswith(".") for part in relative.split("/")):
                continue
            zf.write(path, prefix + relative)


def _unzip(archive, destination):
    import zipfile
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(destination)
    return destination


def proposal_checkout():
    """A base checkout whose proposal files already carry the validated candidate bytes."""
    workspace = publisher_workspace()
    for name in maintenance.PROPOSAL_FILES:
        (workspace / name).write_bytes((PROPOSAL_ROOT / name).read_bytes())
    return workspace


def test_staged_artifact_survives_a_real_archive_round_trip():
    """Stage, archive, extract: the publisher's expected tree arrives intact, dotfiles included."""
    workspace = proposal_checkout()
    record = api_validation_record()
    staged = pathlib.Path(tempfile.mkdtemp(prefix="staged-")) / "proposal-artifact"
    digests = python_version_bump.stage_proposal_artifact(workspace, record, staged)

    # every allowlisted path is present, including both dotfiles, and nothing else
    for name in python_version_bump.artifact_paths():
        assert (staged / name).is_file(), f"{name} was not staged"
    assert (staged / ".python-version").is_file(), "the pin is a hidden file and must be staged"
    assert (staged / ".github" / "maintenance.json").is_file()
    ok, reason, problems = python_version_bump.verify_artifact_layout(staged)
    assert ok, reason
    assert_eq(len(digests), len(python_version_bump.artifact_paths()))

    archive = pathlib.Path(tempfile.mkdtemp(prefix="archive-")) / "artifact.zip"
    _zip_tree(staged, archive)
    downloaded = _unzip(archive, pathlib.Path(tempfile.mkdtemp(prefix="download-")))
    ok, reason, _ = python_version_bump.verify_artifact_layout(downloaded)
    assert ok, f"the round trip lost something: {reason}"
    for name in maintenance.PROPOSAL_FILES:
        assert (downloaded / name).read_bytes() == (workspace / name).read_bytes(), name
    recovered = json.loads((downloaded / "proposal-validation.json").read_text(encoding="utf-8"))
    assert_eq(recovered, record)

    # and the real publisher consumes that extracted tree
    shutil.rmtree(VALIDATED_ARTIFACT_PATH, ignore_errors=True)
    shutil.copytree(downloaded, VALIDATED_ARTIFACT_PATH)
    try:
        target = proposal_checkout()
        bin_dir = mock_git(target, diff_names=list(maintenance.PROPOSAL_FILES),
                           head_policy=(ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8"),
                           commit_author=COMMIT_BOT)
        result = run_publisher(workspace=target, validated=downloaded, env={}, path_prepend=bin_dir)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "downloaded proposal artifact: proposal artifact layout complete" in result.stdout
    finally:
        shutil.rmtree(VALIDATED_ARTIFACT_PATH, ignore_errors=True)


def test_unstaged_upload_layout_is_rejected_by_the_publisher():
    """The layout the previous upload produced must fail closed, not publish a partial proposal.

    Several absolute search paths share /tmp as their least common ancestor, so visible files
    arrive nested under candidate-proposal/, and the hidden files are excluded entirely.
    """
    workspace = proposal_checkout()
    record = api_validation_record()
    staged = pathlib.Path(tempfile.mkdtemp(prefix="staged-")) / "proposal-artifact"
    python_version_bump.stage_proposal_artifact(workspace, record, staged)

    # reproduce the old multi-path upload: /tmp as root, hidden files dropped
    tmp_root = pathlib.Path(tempfile.mkdtemp(prefix="lca-"))
    shutil.copytree(staged, tmp_root / "candidate-proposal")
    shutil.copy2(staged / "proposal-validation.json", tmp_root / "proposal-validation.json")
    archive = pathlib.Path(tempfile.mkdtemp(prefix="archive-")) / "artifact.zip"
    _zip_tree(tmp_root, archive, skip_hidden=True)
    downloaded = _unzip(archive, pathlib.Path(tempfile.mkdtemp(prefix="download-")))

    ok, reason, missing = python_version_bump.verify_artifact_layout(downloaded)
    assert not ok, "the nested, dotfile-free layout must not pass as complete"
    assert ".python-version" in missing, missing
    assert ".github/maintenance.json" in missing, missing

    shutil.rmtree(VALIDATED_ARTIFACT_PATH, ignore_errors=True)
    shutil.copytree(downloaded, VALIDATED_ARTIFACT_PATH)
    try:
        bin_dir = mock_git(workspace, diff_names=[], head_policy="", commit_author=COMMIT_BOT)
        result = run_publisher(workspace=workspace, validated=downloaded, env={}, path_prepend=bin_dir)
        combined = result.stdout + result.stderr
        assert result.returncode != 0, "a partial artifact must not publish"
        assert "refusing to publish a partial proposal" in combined, combined
        assert "Traceback" not in combined, combined
    finally:
        shutil.rmtree(VALIDATED_ARTIFACT_PATH, ignore_errors=True)


def test_artifact_with_tampered_bytes_or_extra_paths_is_rejected():
    """Tampered content and unexpected extra paths both fail closed."""
    workspace = proposal_checkout()
    record = api_validation_record()

    tampered = pathlib.Path(tempfile.mkdtemp(prefix="tampered-"))
    python_version_bump.stage_proposal_artifact(workspace, record, tampered)
    lock = tampered / "requirements.txt"
    lock.write_text(lock.read_text(encoding="utf-8") + "\nmalicious-package==1.0\n", encoding="utf-8")
    ok, reason, _ = python_version_bump.verify_artifact_layout(tampered)
    assert ok, reason  # layout is complete; the digests are what catch this
    shutil.rmtree(VALIDATED_ARTIFACT_PATH, ignore_errors=True)
    shutil.copytree(tampered, VALIDATED_ARTIFACT_PATH)
    try:
        bin_dir = mock_git(workspace, diff_names=list(maintenance.PROPOSAL_FILES),
                           head_policy=(ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8"),
                           commit_author=COMMIT_BOT)
        result = run_publisher(workspace=workspace, validated=tampered, env={}, path_prepend=bin_dir)
        combined = result.stdout + result.stderr
        assert result.returncode != 0, "tampered bytes must not publish"
        assert "do not match the validated proposal" in combined, combined
    finally:
        shutil.rmtree(VALIDATED_ARTIFACT_PATH, ignore_errors=True)

    extra = pathlib.Path(tempfile.mkdtemp(prefix="extra-"))
    python_version_bump.stage_proposal_artifact(workspace, record, extra)
    _write(extra / ".git" / "config", "[core]\n")
    _write(extra / "unexpected.txt", "not part of a proposal\n")
    ok, reason, problems = python_version_bump.verify_artifact_layout(extra)
    assert not ok, "an artifact carrying extra paths must not be accepted"
    assert "unexpected paths" in reason, reason
    assert any(".git" in item for item in problems), problems


def test_staging_refuses_to_package_an_incomplete_proposal():
    """Staging fails closed rather than uploading a proposal missing a file."""
    workspace = publisher_workspace()
    (workspace / "requirements-dev.txt").unlink()
    destination = pathlib.Path(tempfile.mkdtemp(prefix="staged-")) / "out"
    try:
        python_version_bump.stage_proposal_artifact(workspace, api_validation_record(), destination)
    except FileNotFoundError as exc:
        assert "requirements-dev.txt" in str(exc), exc
    else:
        raise AssertionError("staging an incomplete proposal must raise")


# ---------------------------------------------------------------------------
# Cold-candidate bootstrap. A freshly selected interpreter does not inherit the
# previous one's site-packages, so the pinned compiler has to be bootstrapped
# inside it before the first compile call. Injecting a fake piptools through
# PYTHONPATH hides that ordering requirement, which is what the previous fixture
# did; this one starts from an interpreter that genuinely has no compiler.
# ---------------------------------------------------------------------------

COLD_VENV = pathlib.Path("/tmp/cold-candidate-venv")

FAKE_PIP = r"""
import os, pathlib, sys

LOG = pathlib.Path(os.environ["BOOTSTRAP_LOG"])
SITE = pathlib.Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
META = pathlib.Path(os.environ["FAKE_META_DIR"])
argv = sys.argv[1:]


def record(text):
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def write_metadata(directory, name, version):
    info = directory / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")


if "--version" in argv:
    print("pip 26.1.1 from /fake (python 3.11)")
    sys.exit(0)

joined = " ".join(argv)
record("pip " + joined)

if "pip==" in joined:
    write_metadata(META, "pip", joined.split("pip==")[1].split()[0])
elif "pip-tools==" in joined:
    version = joined.split("pip-tools==")[1].split()[0]
    write_metadata(META, "pip-tools", version)
    # a real install puts the package inside this interpreter's own prefix
    package = SITE / "piptools"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import os, pathlib, sys\n"
        "argv = sys.argv[1:]\n"
        "with open(os.environ['BOOTSTRAP_LOG'], 'a', encoding='utf-8') as h:\n"
        "    h.write('piptools ' + ' '.join(argv) + chr(10))\n"
        "if argv[:1] == ['compile']:\n"
        "    out = [a.split('=', 1)[1] for a in argv if a.startswith('--output-file=')][0]\n"
        "    pathlib.Path(out).write_text('# regenerated by the bootstrapped compiler\\n')\n"
        "sys.exit(0)\n", encoding="utf-8")
sys.exit(0)
"""


def cold_interpreter():
    """An interpreter with no compiler and no inherited site-packages."""
    if not (COLD_VENV / "bin" / "python").exists():
        subprocess.run([sys.executable, "-m", "venv", str(COLD_VENV)], check=True,
                       capture_output=True, timeout=300)
    site = next(p for p in (COLD_VENV / "lib").glob("python*/site-packages"))
    for stale in list(site.glob("piptools")) + list(site.glob("pip_tools-*.dist-info")):
        shutil.rmtree(stale, ignore_errors=True)
        if stale.is_file():
            stale.unlink()
    return COLD_VENV / "bin" / "python", site


def cold_candidate_source(interpreter, *, bootstrap=True):
    """A minimal repository whose registry is a stand-in, for the candidate block to consume."""
    source = pathlib.Path(tempfile.mkdtemp(prefix="cold-source-"))
    (source / ".github" / "scripts").mkdir(parents=True)
    # maintenance.py imports the shared cross-repository authority, so a tree that ships
    # maintenance.py alone is no longer runnable. Copying the common module here keeps the
    # cold-candidate harness honest about the real deployment unit.
    for helper in ("maintenance.py", "maintenance_common.py", "python_version_bump.py"):
        shutil.copy2(ROOT / ".github" / "scripts" / helper, source / ".github" / "scripts" / helper)
    _write(source / ".github" / "scripts" / "ci.py",
           "import os, sys\nsys.exit(int(os.environ.get('FAKE_REGISTRY_RC', '0')))\n")
    _write(source / ".python-version", "3.14.8\n")
    _write(source / "requirements.in", "aiohttp\n")
    _write(source / "requirements.txt", "# base graph\n")
    _write(source / "requirements-dev.in", "pip-tools\n")
    _write(source / "requirements-dev.txt", "# base dev graph\n")
    _write(source / ".github" / "maintenance.json",
           (ROOT / ".github" / "maintenance.json").read_text(encoding="utf-8"))
    block = CANDIDATE_BLOCK
    if not bootstrap:
        # remove exactly the bootstrap calls, leaving everything else identical
        start = block.index('subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade",')
        end = block.index("if work.exists():")
        block = block[:start] + block[end:]
    script = source / "candidate.py"
    script.write_text(block, encoding="utf-8")
    return source


def run_cold_candidate(*, bootstrap=True):
    interpreter, site = cold_interpreter()
    source = cold_candidate_source(interpreter, bootstrap=bootstrap)
    fake_dir = pathlib.Path(tempfile.mkdtemp(prefix="fake-pip-"))
    _write(fake_dir / "pip" / "__init__.py", "")
    _write(fake_dir / "pip" / "__main__.py", FAKE_PIP)
    log = pathlib.Path(tempfile.mkdtemp(prefix="bootstrap-log-")) / "order.log"
    log.write_text("", encoding="utf-8")
    actual = subprocess.run([str(interpreter), "-c",
                             "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
                            capture_output=True, text=True, check=True).stdout.strip()
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(fake_dir),
        "BOOTSTRAP_LOG": str(log),
        "FAKE_META_DIR": str(fake_dir),
        "CANDIDATE": actual,          # the running interpreter IS the candidate here
        "GITHUB_SHA": SOURCE_HEAD,
        "GITHUB_RUN_ID": str(PROPOSER_RUN_ID),
        "GITHUB_RUN_ATTEMPT": "1",
        "FAKE_REGISTRY_RC": "0",
    }
    result = subprocess.run([str(interpreter), str(source / "candidate.py")], cwd=source,
                            env=env, capture_output=True, text=True, timeout=600)
    order = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return result, order, site


def test_cold_candidate_bootstraps_the_compiler_before_the_first_compile():
    """A compiler-free interpreter must be bootstrapped before, and only then, regenerate locks."""
    interpreter, site = cold_interpreter()
    # the fixture really is cold: no compiler, no inherited site-packages
    cold_env = {**os.environ, "PYTHONPATH": ""}
    probe = subprocess.run(
        [str(interpreter), "-c", "import importlib.util; print(importlib.util.find_spec('piptools'))"],
        capture_output=True, text=True, check=True, env=cold_env).stdout.strip()
    assert_eq(probe, "None")

    result, order, site = run_cold_candidate()
    assert result.returncode == 0, result.stdout + result.stderr
    compiles = [i for i, line in enumerate(order) if line.startswith("piptools compile")]
    boot_tools = [i for i, line in enumerate(order) if "pip-tools==" in line]
    boot_pip = [i for i, line in enumerate(order) if "pip==" in line and "pip-tools" not in line]
    installs = [i for i, line in enumerate(order) if "--require-hashes" in line]
    assert boot_pip and boot_tools and compiles and installs, order
    # the BOOTSTRAP pip install is the first one; a later `pip install --upgrade` belongs to the
    # graph-installation step and must not be confused with it
    assert_eq(boot_pip[0], 0)
    assert min(boot_pip) < min(boot_tools), f"pip must be pinned before the compiler: {order}"
    assert min(boot_tools) < min(compiles), f"the compiler must exist before compiling: {order}"
    assert max(compiles) < min(installs), f"both graphs are generated before they are installed: {order}"
    assert compiles == sorted(compiles), order
    assert_eq(len(compiles), 2)
    # the compiler landed inside the candidate's own prefix, not an inherited site-packages
    assert (site / "piptools" / "__main__.py").is_file(), "bootstrap did not install into the candidate"
    # and the evidence records the bootstrapped versions, not a PATH executable's
    assert "::notice::proposal-validation" in result.stdout, result.stdout
    notice_line = [l for l in result.stdout.splitlines() if l.startswith("::notice::proposal-validation")][0]
    record = python_version_bump.parse_notice(notice_line, python_version_bump.VALIDATION_NOTICE)
    assert_eq(record["pip"], maintenance.PINNED_PIP)
    assert_eq(record["pip_tools"], maintenance.PINNED_PIP_TOOLS)
    ok, reason, _ = python_version_bump.verify_artifact_layout(pathlib.Path("/tmp/proposal-artifact"))
    assert ok, reason


def test_cold_candidate_without_the_bootstrap_fails_closed():
    """Removing the bootstrap must break the run — otherwise the test above proves nothing."""
    result, order, _site = run_cold_candidate(bootstrap=False)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, "a compiler-free candidate must not regenerate locks"
    assert not [line for line in order if line.startswith("piptools")], order
    assert "no working pinned compiler" in combined or "No module named piptools" in combined, combined


# ---------------------------------------------------------------------------
# Publication-evidence adapter integration: execute the ACTUAL JavaScript from
# maintenance_merge.yml against the real GitHub list ENVELOPES. The jobs endpoint
# returns {total_count, jobs: [...]}; destructuring `data` as the array throws a
# TypeError, and the caller's catch then silently loses every publication record.
# ---------------------------------------------------------------------------

EVIDENCE_START = "            const PROPOSER_WORKFLOW_PATH = "
EVIDENCE_END = "\n            // Determine PR number:"


def evidence_source():
    """Extract the real publication-evidence collector from the workflow, dedented."""
    text = (ROOT / ".github" / "workflows" / "maintenance_merge.yml").read_text(encoding="utf-8")
    start = text.index(EVIDENCE_START)
    end = text.index(EVIDENCE_END, start)
    lines = text[start:end].splitlines()
    return "\n".join(line[12:] if line.startswith(" " * 12) else line for line in lines)


EVIDENCE_HARNESS = r"""
const fs = require('fs');
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const calls = [];
const logs = [];
const owner = 'uesu', repo = 'wvkhabh2s22';
const trustedRepository = `${owner}/${repo}`;
const console = { log: (m) => logs.push(String(m)) };

// Real Octokit paginate flattens the envelope's list key across every page.
const LIST_KEYS = ['jobs', 'workflow_runs', 'annotations', 'check_runs'];
function listOf(data) {
  if (Array.isArray(data)) return data;
  if (data && typeof data === 'object') {
    for (const key of LIST_KEYS) if (Array.isArray(data[key])) return data[key];
  }
  return null;
}

function endpoint(kind) {
  return async (params) => {
    calls.push({ kind, params });
    const served = fixture[kind];
    if (served && served.throw) throw new Error(served.throw);
    const page = (params && params.page) || 1;
    const pages = served && served.pages;
    if (pages) return { data: pages[page - 1] !== undefined ? pages[page - 1] : (served.empty || []) };
    return { data: served !== undefined ? served : null };
  };
}

const github = {
  rest: {
    actions: {
      listWorkflowRuns: endpoint('listWorkflowRuns'),
      listJobsForWorkflowRun: endpoint('listJobsForWorkflowRun'),
      getWorkflowRun: endpoint('getWorkflowRun'),
      getWorkflowRunAttempt: endpoint('getWorkflowRunAttempt')
    },
    checks: { listAnnotations: endpoint('listAnnotations') },
    repos: { getCommit: endpoint('getCommit'), getContent: endpoint('getContent') }
  },
  paginate: async (method, params) => {
    const perPage = (params && params.per_page) || 100;
    const out = [];
    for (let page = 1; page <= 10; page++) {
      const response = await method(Object.assign({}, params, { page }));
      const list = listOf(response.data);
      if (!list) return response.data;   // let the caller's own normalization decide
      out.push(...list);
      if (list.length < perPage) break;
    }
    return out;
  }
};

(async () => {
  const result = { error: null, evidence: null };
  try {
""" + "PLACEHOLDER_BODY" + r"""
    result.evidence = await collectPublicationEvidence(fixture.pr);
  } catch (e) {
    result.error = `${e.constructor.name}: ${e.message}`;
  }
  result.calls = calls.map(c => c.kind);
  result.logs = logs;
  process.stdout.write(JSON.stringify(result));
})();
"""


def _run_evidence(fixture):
    """Execute the real collector in node against a recorded API-shaped fixture."""
    fixture_path = pathlib.Path(tempfile.mkdtemp(prefix="evidence-")) / "fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    script = pathlib.Path(tempfile.mkdtemp(prefix="evidence-js-")) / "adapter.js"
    body = EVIDENCE_HARNESS.replace("PLACEHOLDER_BODY", evidence_source())
    script.write_text(body, encoding="utf-8")
    result = subprocess.run(["node", str(script), str(fixture_path)],
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"node failed: {result.stderr[:1200]}"
    return json.loads(result.stdout)


def _annotation(message, level="notice"):
    return {"annotation_level": level, "message": message}


def _notice_annotation(record, kind):
    """The annotation GitHub stores for a notice: the command prefix is stripped from the message."""
    line = python_version_bump.notice(kind, record)
    assert line is not None, "evidence must fit in a bounded notice"
    prefix = "::notice::"
    assert line.startswith(prefix), line
    return _annotation(line[len(prefix):])


def _evidence_fixture(*, jobs_pages=None, annotations=None, runs=None, jobs=None,
                      validation=None, publication=None):
    """A recorded API-shaped fixture using the REAL envelopes, not bare arrays."""
    validation_record = api_validation_record() if validation is None else validation
    publication_record = api_publication_record() if publication is None else publication
    jobs_list = [{"id": 555001, "name": "select-and-validate"},
                 {"id": 555002, "name": "publish"}] if jobs is None else jobs
    annotation_list = ([
        _notice_annotation(validation_record, python_version_bump.VALIDATION_NOTICE),
        _notice_annotation(publication_record, python_version_bump.PUBLICATION_NOTICE),
    ] if annotations is None else annotations) if validation_record or publication_record else []
    run_record = {"id": PROPOSER_RUN_ID, "path": PROPOSER_PATH, "head_branch": "main",
                  "status": "completed", "conclusion": "success", "run_attempt": 1,
                  "head_sha": SOURCE_HEAD, "workflow_id": 88,
                  "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
                  "actor": {"login": RUN_ACTOR}}
    runs_list = [run_record] if runs is None else runs
    fixture = {
        "pr": {"number": 31, "base": {"ref": "main"},
               "head": {"sha": PUBLISHED_HEAD, "ref": f"chore/python-{CANDIDATE}"},
               "user": {"login": PAT_PUBLISHER}},
        "listWorkflowRuns": {"pages": [runs_list]},
        "listJobsForWorkflowRun": ({"pages": jobs_pages} if jobs_pages is not None
                                   else {"pages": [jobs_list]}),
        "listAnnotations": {"pages": [annotation_list]},
        "getWorkflowRun": run_record,
        "getWorkflowRunAttempt": {**run_record, "run_attempt": 1},
        "getCommit": api_published_commit(),
        "getContent": None,
    }
    return fixture


def _content_fixture():
    """Serve the actual proposal bytes so the JS digests can be compared with Python's."""
    import base64
    payload = {}
    for name in maintenance.PROPOSAL_FILES:
        raw = (PROPOSAL_ROOT / name).read_bytes()
        payload[name] = base64.b64encode(raw).decode("ascii")
    return payload


def test_publication_evidence_reads_the_real_jobs_envelope():
    """The real {total_count, jobs: [...]} envelope must yield both records, not a TypeError."""
    fixture = _evidence_fixture()
    content = _content_fixture()
    result = _run_evidence_with_content(fixture, content)
    assert result["error"] is None, f"the real envelope crashed the collector: {result['error']}"
    evidence = result["evidence"]
    assert evidence is not None, result["logs"]
    assert_eq(evidence["repository"], REPO)
    assert_eq(evidence["proposer_run"]["id"], PROPOSER_RUN_ID)
    assert_eq(evidence["proposer_attempt"]["run_attempt"], 1)
    assert evidence["validation_record"], result["logs"]
    assert evidence["publication_record"], result["logs"]
    assert_eq(evidence["validation_record"], api_validation_record())
    assert_eq(evidence["publication_record"], api_publication_record())
    assert_eq(evidence["published_commit"]["sha"], PUBLISHED_HEAD)
    # the JS sha256 over base64-decoded content must equal Python's digest of the same bytes
    assert_eq(evidence["proposal_digests"], dict(PROPOSAL_DIGESTS))
    assert "listJobsForWorkflowRun" in result["calls"], result["calls"]


def _run_evidence_with_content(fixture, content):
    """Variant harness that serves repos.getContent per requested path."""
    body = EVIDENCE_HARNESS.replace("PLACEHOLDER_BODY", evidence_source())
    body = body.replace(
        "    repos: { getCommit: endpoint('getCommit'), getContent: endpoint('getContent') }",
        """    repos: {
      getCommit: endpoint('getCommit'),
      getContent: async (params) => {
        calls.push({ kind: 'getContent', params });
        const encoded = FIXTURE_CONTENT[params.path];
        if (encoded === undefined) { const e = new Error('Not Found'); e.status = 404; throw e; }
        return { data: { content: encoded, encoding: 'base64' } };
      }
    }""")
    body = body.replace("const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));",
                        "const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));\n"
                        "const FIXTURE_CONTENT = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));")
    fixture_path = pathlib.Path(tempfile.mkdtemp(prefix="evidence-")) / "fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    content_path = fixture_path.parent / "content.json"
    content_path.write_text(json.dumps(content), encoding="utf-8")
    script = pathlib.Path(tempfile.mkdtemp(prefix="evidence-js-")) / "adapter.js"
    script.write_text(body, encoding="utf-8")
    result = subprocess.run(["node", str(script), str(fixture_path), str(content_path)],
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"node failed: {result.stderr[:1500]}"
    return json.loads(result.stdout)


def test_publication_evidence_finds_notices_on_the_second_page():
    """Records published by a job on page 2 must still be found; one page is not enough."""
    content = _content_fixture()
    page1 = [{"id": 555001, "name": "select-and-validate"}] * 100   # a full page, so paging continues
    page2 = [{"id": 555002, "name": "publish"}]
    fixture = _evidence_fixture(jobs_pages=[page1, page2])
    annotations_by_job = {
        555001: [],   # the first job publishes nothing
        555002: [
            _notice_annotation(api_validation_record(), python_version_bump.VALIDATION_NOTICE),
            _notice_annotation(api_publication_record(), python_version_bump.PUBLICATION_NOTICE),
        ],
    }
    body = EVIDENCE_HARNESS.replace("PLACEHOLDER_BODY", evidence_source())
    body = body.replace(
        "    repos: { getCommit: endpoint('getCommit'), getContent: endpoint('getContent') }",
        """    repos: {
      getCommit: endpoint('getCommit'),
      getContent: async (params) => {
        calls.push({ kind: 'getContent', params });
        const encoded = FIXTURE_CONTENT[params.path];
        if (encoded === undefined) { const e = new Error('Not Found'); e.status = 404; throw e; }
        return { data: { content: encoded, encoding: 'base64' } };
      }
    }""")
    body = body.replace(
        "    checks: { listAnnotations: endpoint('listAnnotations') },",
        """    checks: { listAnnotations: async (params) => {
        calls.push({ kind: 'listAnnotations', params });
        return { data: FIXTURE_ANNOTATIONS[String(params.check_run_id)] || [] };
      } },""")
    body = body.replace("const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));",
                        "const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));\n"
                        "const FIXTURE_CONTENT = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));\n"
                        "const FIXTURE_ANNOTATIONS = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));")
    directory = pathlib.Path(tempfile.mkdtemp(prefix="page2-"))
    (directory / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
    (directory / "content.json").write_text(json.dumps(content), encoding="utf-8")
    (directory / "annotations.json").write_text(
        json.dumps({str(k): v for k, v in annotations_by_job.items()}), encoding="utf-8")
    script = directory / "adapter.js"
    script.write_text(body, encoding="utf-8")
    result = subprocess.run(
        ["node", str(script), str(directory / "fixture.json"), str(directory / "content.json"),
         str(directory / "annotations.json")], capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[:1500]
    out = json.loads(result.stdout)
    assert out["error"] is None, out["error"]
    evidence = out["evidence"]
    assert evidence["validation_record"], out["logs"]
    assert evidence["publication_record"], "a page-2 notice must still be found"
    assert_eq(evidence["publication_record"]["pull_number"], 31)
    # both pages were really requested
    assert out["calls"].count("listJobsForWorkflowRun") >= 2, out["calls"]


def test_publication_evidence_fails_closed_on_malformed_lists():
    """A malformed or unavailable list yields no records, so publication stays unverified."""
    content = _content_fixture()
    cases = {
        "jobs envelope without a jobs array": {"listJobsForWorkflowRun": {"pages": [{"total_count": 2}]}},
        "jobs endpoint unavailable": {"listJobsForWorkflowRun": {"throw": "502 Bad Gateway"}},
        "runs envelope without a workflow_runs array": {"listWorkflowRuns": {"pages": [{"total_count": 1}]}},
        "runs endpoint unavailable": {"listWorkflowRuns": {"throw": "502 Bad Gateway"}},
        "annotations malformed": {"listAnnotations": {"pages": [{"message": "not a list"}]}},
        "notices absent": {"listAnnotations": {"pages": [[_annotation("an ordinary notice")]]}},
        "notice payload is not json": {"listAnnotations": {"pages": [[
            _annotation("proposal-publication {not json")]]}},
    }
    for label, overrides in cases.items():
        fixture = _evidence_fixture()
        fixture.update(overrides)
        result = _run_evidence_with_content(fixture, content)
        # the collector must never throw on a malformed envelope
        assert result["error"] is None, f"{label} crashed the collector: {result['error']}"
        evidence = result["evidence"]
        has_records = bool(evidence.get("validation_record")) and bool(evidence.get("publication_record"))
        assert not has_records, f"{label} must not produce publication records"
        assert not evidence.get("proposer_run"), f"{label} must not select a proposer run"


def test_publication_evidence_ignores_a_run_that_published_a_different_proposal():
    """Selection is by the record's PR number and head, not by first completed run."""
    content = _content_fixture()
    other = api_publication_record(pull_number=99, published_head="f" * 40)
    fixture = _evidence_fixture(
        annotations=[_notice_annotation(other, python_version_bump.PUBLICATION_NOTICE)])
    result = _run_evidence_with_content(fixture, content)
    assert result["error"] is None, result["error"]
    evidence = result["evidence"]
    assert not evidence.get("proposer_run"), "a run that published another PR must not be selected"
    assert any("not this proposal" in line for line in result["logs"]), result["logs"]


# ---------------------------------------------------------------------------
# Adapter integration: execute the ACTUAL JavaScript from maintenance_merge.yml
# against recorded API-shaped responses. Helper-only Python fixtures cannot
# conceal a broken caller, so the caller itself is run here.
# ---------------------------------------------------------------------------

ADAPTER_START = "            const canonicalJobLink = (url) => {"
ADAPTER_END = "\n            // Determine PR number:"


def adapter_source():
    """Extract the real provenance helpers from the workflow, dedented."""
    text = (ROOT / ".github" / "workflows" / "maintenance_merge.yml").read_text(encoding="utf-8")
    start = text.index(ADAPTER_START)
    end = text.index(ADAPTER_END, start)
    body = text[start:end]
    lines = [line[12:] if line.startswith(" " * 12) else line for line in body.splitlines()]
    return "\n".join(lines)


JS_HARNESS = r"""
const calls = [];
let runAttemptNow = 1;
let jobIdNow = 116819004343;
let checkIdNow = 990011;
const HEAD_SHA = process.argv[2];
const REPO_FULL = 'uesu/wvkhabh2s22';

function record(name, factory) {
  return async (params) => { calls.push({ name, params }); return { data: factory(params) }; };
}
const github = {
  rest: {
    actions: {
      getJobForWorkflowRun: record('getJobForWorkflowRun', (p) => ({
        id: p.job_id, run_id: 38853103562, run_attempt: runAttemptNow,
        workflow_name: 'CI', head_sha: HEAD_SHA, name: 'maintenance-ci',
        status: 'completed', conclusion: 'success',
        html_url: `https://github.com/${REPO_FULL}/actions/runs/38853103562/job/${p.job_id}`,
        check_run_url: `https://api.github.com/repos/${REPO_FULL}/check-runs/${checkIdNow}`
      })),
      getWorkflowRun: record('getWorkflowRun', () => ({
        id: 38853103562, run_attempt: runAttemptNow, head_sha: HEAD_SHA, path: '.github/workflows/ci.yml',
        workflow_id: 77, name: 'CI', status: 'completed', conclusion: 'success',
        repository: { full_name: REPO_FULL }, head_repository: { full_name: REPO_FULL }
      })),
      getWorkflowRunAttempt: record('getWorkflowRunAttempt', (p) => ({
        id: p.run_id, run_attempt: p.attempt_number, head_sha: HEAD_SHA,
        path: '.github/workflows/ci.yml', status: 'completed', conclusion: 'success'
      }))
    },
    repos: {
      listCommitStatusesForRef: record('listCommitStatusesForRef', () => ({ statuses: [] }))
    }
  }
};
const owner = 'uesu';
const repo = 'wvkhabh2s22';
const trustedRepository = `${owner}/${repo}`;
const console_log = [];
const console = { log: (m) => console_log.push(String(m)) };

__ADAPTER__

const liveCheck = () => ({
  id: checkIdNow, name: 'maintenance-ci', status: 'completed', conclusion: 'success',
  head_sha: HEAD_SHA, external_id: '0fb0ae38-2e89-5fc7-8093-e2d0d0cf857d',
  html_url: `https://github.com/${trustedRepository}/actions/runs/38853103562/job/${jobIdNow}`,
  details_url: `https://github.com/${trustedRepository}/actions/runs/38853103562/job/${jobIdNow}`,
  app: { slug: 'github-actions' }
});

(async () => {
  const out = {};
  // Snapshot 1: the decision step, attempt 1.
  out.first = await resolveChecks([liveCheck()], HEAD_SHA);
  out.callsAfterFirst = calls.length;
  // A rerun happens: new attempt, new job, new check run id.
  runAttemptNow = 2; jobIdNow = 116819999999; checkIdNow = 990022;
  out.second = await resolveChecks([liveCheck()], HEAD_SHA);
  out.callsAfterSecond = calls.length;

  // external_id must never be parsed into a job id.
  calls.length = 0;
  out.uuid = await resolveChecks([{
    id: 5, name: 'maintenance-ci', status: 'completed', conclusion: 'success', head_sha: HEAD_SHA,
    external_id: '83e46dd2-1111-2222-3333-444444444444',
    html_url: `https://github.com/${trustedRepository}/actions/runs/38853103562/job/${jobIdNow}`,
    app: { slug: 'github-actions' }
  }], HEAD_SHA);
  out.uuidJobIdsRequested = calls.filter(c => c.name === 'getJobForWorkflowRun').map(c => c.params.job_id);

  // A foreign-host link must not be fetched at all.
  calls.length = 0;
  out.foreign = await resolveChecks([{
    id: 6, name: 'maintenance-ci', status: 'completed', conclusion: 'success', head_sha: HEAD_SHA,
    external_id: '0fb0ae38-2e89-5fc7-8093-e2d0d0cf857d',
    html_url: 'https://github.com/someone/else/actions/runs/38853103562/job/116819999999',
    app: { slug: 'github-actions' }
  }], HEAD_SHA);
  out.foreignCalls = calls.filter(c => c.name !== 'listCommitStatusesForRef').length;
  out.log = console_log;
  process.stdout.write(JSON.stringify(out));
})();
"""


def _run_adapter(head_sha="abcdef1234567890abcdef1234567890abcdef12"):
    import shutil
    import subprocess

    node = shutil.which("node")
    assert node, "node is required to exercise the real adapter — fail closed, never skip"
    source = JS_HARNESS.replace("__ADAPTER__", adapter_source())
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
        fh.write(source)
        script = fh.name
    try:
        proc = subprocess.run([node, script, head_sha], capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, f"adapter harness failed: {proc.stderr[:2000]}"
        return json.loads(proc.stdout)
    finally:
        os.unlink(script)


def test_adapter_resolves_real_payloads_and_refetches_after_a_rerun():
    """The live adapter binds a real UUID-external_id check, and never reuses a snapshot's cache."""
    result = _run_adapter()

    first = result["first"][0]
    assert first.get("external_id") == "0fb0ae38-2e89-5fc7-8093-e2d0d0cf857d", first
    assert first["job"]["id"] == 116819004343, first["job"]
    assert first["run"]["run_attempt"] == 1, first["run"]
    assert first["attempt"]["run_attempt"] == 1, first["attempt"]
    assert first["job"]["check_run_url"].endswith("/check-runs/990011"), first["job"]

    # A rerun between the decision and the final pre-merge snapshot must be visible: the second
    # call has to fetch the current attempt again rather than reuse the first snapshot's records.
    second = result["second"][0]
    assert second["job"]["id"] == 116819999999, second["job"]
    assert second["run"]["run_attempt"] == 2, (
        f"the final snapshot reused a stale run record: {second['run']['run_attempt']}"
    )
    assert second["attempt"]["run_attempt"] == 2, second["attempt"]
    assert second["job"]["check_run_url"].endswith("/check-runs/990022"), second["job"]
    assert result["callsAfterSecond"] > result["callsAfterFirst"], (
        "the second snapshot issued no API calls — caches spanned both snapshots"
    )

    # both snapshots must be independently verifiable by the helper
    head = "abcdef1234567890abcdef1234567890abcdef12"
    for snapshot, attempt in ((first, 1), (second, 2)):
        provenance, problem = maintenance.resolve_check_provenance(
            snapshot, head_sha=head, repository="uesu/wvkhabh2s22"
        )
        assert provenance is not None, f"attempt {attempt} rejected a genuine record: {problem}"
        assert_eq(provenance["run_attempt"], attempt)

    # and the superseded first snapshot must be rejected once the run has moved on
    stale = dict(first)
    stale["run"] = dict(first["run"], run_attempt=2)
    stale["attempt"] = dict(first["attempt"], run_attempt=2)
    provenance, problem = maintenance.resolve_check_provenance(
        stale, head_sha=head, repository="uesu/wvkhabh2s22"
    )
    assert provenance is None and "stale attempt" in problem, problem


def test_adapter_never_parses_external_id_or_trusts_a_foreign_link():
    """Opaque ids stay opaque and a link outside this repository is never fetched."""
    result = _run_adapter()

    # 83e46dd2-... must not become job 83; the canonical link names the real job
    assert result["uuidJobIdsRequested"] == [116819999999], result["uuidJobIdsRequested"]
    assert result["uuid"][0]["job"]["id"] == 116819999999, result["uuid"][0]

    # a foreign repository's link resolves to nothing and costs no provenance fetch
    assert result["foreignCalls"] == 0, result["foreignCalls"]
    foreign = result["foreign"][0]
    assert "job" not in foreign and "run" not in foreign and "attempt" not in foreign, foreign
    provenance, problem = maintenance.resolve_check_provenance(
        foreign, head_sha="abcdef1234567890abcdef1234567890abcdef12", repository="uesu/wvkhabh2s22"
    )
    assert provenance is None and "no canonical Actions job link" in problem, problem


def _raising_opener(*args, **kwargs):
    """Stand-in for an unreachable release-date authority."""
    raise OSError("authority unreachable")


def test_proposer_cli_soaks_against_the_release_date_authority():
    """The real proposer entry point must not derive soak age from a mirror timestamp.

    Executed through ``python_version_bump.py --check`` in a subprocess, so a broken caller cannot
    hide behind a helper-only fixture. The release-date authority is seeded through its own cache
    file, which is the supported offline path — no network is involved in the positive case.
    """
    work = pathlib.Path(tempfile.mkdtemp(prefix="proposer-cli-"))
    cache = work / "release-dates.json"
    manifest = work / "manifest.json"
    output = work / "github_output"
    # A rebuilt archive: the manifest claims 3.15.0 shipped over a year ago, python.org says it
    # shipped two days ago.
    released_two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    manifest.write_text(json.dumps([
        {"version": "3.14.8", "stable": True, "release_url": "https://example.com/3.14.8",
         "files": [{"filename": "python-3.14.8-linux-x64.tar.gz", "arch": "x64", "platform": "linux"}]},
        {"version": "3.15.0", "stable": True, "published_at": "2025-01-01T00:00:00Z",
         "release_url": "https://example.com/3.15.0",
         "files": [{"filename": "python-3.15.0-linux-x64.tar.gz", "arch": "x64", "platform": "linux"}]},
    ]), encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / ".github" / "scripts") + os.pathsep + env.get("PYTHONPATH", "")
    env["GITHUB_OUTPUT"] = str(output)
    env["SOAK_DAYS"] = "30"
    env.pop("IGNORE_SOAK", None)
    env.pop("FORCE_VERSION", None)
    cmd = [sys.executable, str(ROOT / ".github" / "scripts" / "python_version_bump.py"),
           "--manifest", str(manifest), "--current", "3.14.8", "--check"]

    try:
        # 1. Authority reachable (seeded cache) and the release is two days old -> defer on soak.
        cache.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(),
                                     "dates": {"3.15.0": released_two_days_ago}}), encoding="utf-8")
        env["RELEASE_DATE_CACHE"] = str(cache)
        result = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "Traceback" not in combined, combined
        assert "release-date authority cache" in combined, combined
        assert "Deferred:" in combined and "soak" in combined, combined
        assert "bump=no" in output.read_text(encoding="utf-8"), output.read_text(encoding="utf-8")

        # 2. The authority knows nothing about this version -> still defer. Seeded through the
        # cache so the case is deterministic and offline: whether the live authority is reachable
        # must not change the outcome, because the invariant is that a mirror timestamp can never
        # produce a proposal.
        output.write_text("", encoding="utf-8")
        cache.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(),
                                     "dates": {"3.14.8": "2025-12-01"}}), encoding="utf-8")
        env["RELEASE_DATE_CACHE"] = str(cache)
        result = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "Traceback" not in combined, combined
        assert "Deferred:" in combined, combined
        assert "release-date-unavailable" in combined or "soak" in combined, combined
        assert "bump=no" in output.read_text(encoding="utf-8")
        assert "Candidate: 3.15.0" not in combined, combined

        # 3. An authority that cannot be reached reports itself unavailable rather than empty, so
        # the selector defers on release-date-authority-unavailable instead of guessing an age.
        dates, state, detail = maintenance.common.fetch_release_dates(
            opener=_raising_opener, cache_path=work / "never-written.json")
        assert_eq(dates, {})
        assert_eq(state, "unavailable")
        assert detail, "the failure must be reported, not swallowed"
        assert not (work / "never-written.json").exists(), "an unavailable authority must not be cached"
        unreachable = maintenance.select_candidate(
            "3.14.8", json.loads(manifest.read_text(encoding="utf-8")), soak_days=30,
            release_dates=dates, release_date_state=state)
        assert unreachable["deferred"] is True, unreachable
        assert_eq(unreachable["reason"], "deferred: release-date-authority-unavailable")

        # 4. The untrusted GitHub-releases enrichment that used to define soak age is gone. The
        # comment explaining why may mention it; the request it made may not.
        adapter = (ROOT / ".github" / "scripts" / "python_version_bump.py").read_text(encoding="utf-8")
        assert "releases/tags/v" not in adapter, "mirror enrichment call must stay removed"
        assert 'entry["published_at"]' not in adapter, "the adapter must no longer write a mirror timestamp"
        assert "fetch_release_dates" in adapter, "the authority must be consulted by the caller"
    finally:
        shutil.rmtree(work, ignore_errors=True)



def main():
    tests = [test_parse_version, test_compare_versions, test_classify_python, test_classify_package, test_classify_group, test_validate_changed_files, test_select_candidate, test_soak_age_comes_from_python_org_not_from_the_setup_python_manifest,
           test_evaluate_checks, test_provenance_requires_the_exact_producer, test_provenance_accepts_the_current_attempt_only, test_opaque_external_id_is_never_parsed_as_a_job_id, test_canonical_link_must_name_this_repository_and_check, test_decision_from_fixture, test_proposer_publication_bound_to_the_real_run, test_proposer_publication_rejects_incomplete_evidence, test_repository_association_binds_both_sides_of_the_pr,
           test_security_evidence_is_a_declared_policy_not_an_absence,
           test_trusted_author_does_not_bypass_publication_provenance, test_publication_verifies_the_fetched_commit_not_the_notice_claim, test_proposer_cli_soaks_against_the_release_date_authority, test_adapter_resolves_real_payloads_and_refetches_after_a_rerun, test_adapter_never_parses_external_id_or_trusts_a_foreign_link, test_publisher_step_publishes_only_the_validated_bytes, test_publisher_step_rejects_every_way_the_artifact_can_be_wrong, test_publisher_step_rejects_a_commit_signed_by_another_identity, test_publisher_step_reports_an_unchanged_proposal_without_publishing, test_missing_pat_is_an_explicit_configuration_error, test_candidate_step_validates_the_exact_proposal_under_the_candidate, test_candidate_step_executes_and_emits_bounded_evidence, test_notice_records_survive_the_annotation_round_trip, test_policy_reconciliation_is_narrow, test_staged_artifact_survives_a_real_archive_round_trip, test_unstaged_upload_layout_is_rejected_by_the_publisher, test_artifact_with_tampered_bytes_or_extra_paths_is_rejected, test_staging_refuses_to_package_an_incomplete_proposal, test_cold_candidate_bootstraps_the_compiler_before_the_first_compile, test_cold_candidate_without_the_bootstrap_fails_closed, test_publication_evidence_reads_the_real_jobs_envelope, test_publication_evidence_finds_notices_on_the_second_page, test_publication_evidence_fails_closed_on_malformed_lists, test_publication_evidence_ignores_a_run_that_published_a_different_proposal]
    failed = []
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            print(f"FAIL {fn.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed.append(fn.__name__)
    if failed:
        print(f"MAINTENANCE TEST: FAILED {failed}")
        sys.exit(1)
    print("MAINTENANCE TEST: ALL PASS")

if __name__ == "__main__":
    main()
