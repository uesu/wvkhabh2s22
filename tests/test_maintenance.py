"""Offline maintenance helpers test — no network, no secrets.

Run: python tests/test_maintenance.py
Validates the shared helpers in .github/scripts/maintenance.py and python_version_bump.py
against the production spec (2026-10-15). All pure, side-effect free.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "scripts"))

import maintenance  # type: ignore

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
    # current 3.14.8 -> should pick patch 3.14.9 (newest patch)
    r = maintenance.select_candidate("3.14.8", manifest, soak_days=30, now_ts=now, ignore_soak=False)
    assert_eq(r["candidate"], "3.14.9")
    assert_eq(r["kind"], "patch")
    # if no patch, should pick minor after soak
    manifest2 = [
        {"version": "3.14.8", "stable": True, "published_at": "2026-09-01T00:00:00Z", "files": [{"platform":"linux","arch":"x64"}], "release_url": ""},
        {"version": "3.15.0", "stable": True, "published_at": "2026-10-10T00:00:00Z", "files": [{"platform":"linux","arch":"x64"}], "release_url": ""},
    ]
    # now is 2026-10-15 (5 days after 3.15.0) -> should defer
    now_soon = int(time.mktime(time.strptime("2026-10-12T00:00:00", "%Y-%m-%dT%H:%M:%S")))
    r = maintenance.select_candidate("3.14.8", manifest2, soak_days=30, now_ts=now_soon)
    assert r.get("deferred") is True
    # after soak, should return candidate
    now_late = int(time.mktime(time.strptime("2026-11-20T00:00:00", "%Y-%m-%dT%H:%M:%S")))
    r = maintenance.select_candidate("3.14.8", manifest2, soak_days=30, now_ts=now_late)
    assert_eq(r["candidate"], "3.15.0")
    assert_eq(r["kind"], "minor")
    assert r.get("deferred") is False

# ---------------------------------------------------------------------------
# Fixtures shaped exactly like the live REST responses. The Checks API returns no
# workflow identity, so these records deliberately carry no workflow, workflow_id,
# path or workflow_run keys — provenance has to be resolved from the job and run.
# ---------------------------------------------------------------------------

REPO = "uesu/wvkhabh2s22"
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
            "mergeable_state": "clean", "base": {"ref": "main"}, "head": {"sha": HEAD},
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

    # provenance must be verifiable against an exact repository
    no_repo = _decision([resolved_check("maintenance-ci")], repository=None)
    assert_eq(no_repo["decision"], "blocked")
    assert "repository not supplied" in no_repo["reason"], no_repo["reason"]

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
# equal the published proposal head. Publication has to be bound to the run that
# produced it, not to a head-SHA coincidence.
# ---------------------------------------------------------------------------

PROPOSER_PATH = ".github/workflows/python_version_bump.yml"
SOURCE_HEAD = "58547a93c000000000000000000000000000000a"
PUBLISHED_HEAD = "abcdef1234567890abcdef1234567890abcdef12"
PROPOSER_RUN_ID = 38849000001
BUMP_ACTOR = "bump-bot"
PYTHON_PATCH = "@@ -1 +1 @@\n-3.14.8\n+3.14.9\n"


def api_proposer_run(*, run_id=PROPOSER_RUN_ID, attempt=1, head_sha=SOURCE_HEAD,
                     status="completed", conclusion="success", path=PROPOSER_PATH,
                     actor=BUMP_ACTOR, full_name=REPO, workflow_id=88,
                     started="2026-10-16T02:00:00Z", updated="2026-10-16T02:05:00Z"):
    """Shaped like GET /repos/{owner}/{repo}/actions/runs/{run_id} for the proposer."""
    return {
        "id": run_id, "name": "Python version proposer", "path": path, "workflow_id": workflow_id,
        "head_branch": "main", "head_sha": head_sha, "run_number": 12, "run_attempt": attempt,
        "event": "schedule", "status": status, "conclusion": conclusion,
        "run_started_at": started, "created_at": started, "updated_at": updated,
        "actor": {"id": 7, "login": actor, "type": "User"},
        "repository": {"id": 1, "full_name": full_name, "name": full_name.split("/")[1]},
        "head_repository": {"id": 1, "full_name": full_name, "name": full_name.split("/")[1]},
    }


def api_published_commit(*, sha=PUBLISHED_HEAD, login=BUMP_ACTOR, date="2026-10-16T02:03:00Z"):
    """Shaped like GET /repos/{owner}/{repo}/commits/{ref} for the published proposal head."""
    return {
        "sha": sha,
        "author": {"id": 7, "login": login, "type": "User"},
        "committer": {"id": 7, "login": login, "type": "User"},
        "commit": {
            "author": {"name": login, "date": date},
            "committer": {"name": login, "date": date},
            "message": "chore: bump CPython pin 3.14.8 -> 3.14.9",
        },
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
        "name": "Python version proposer", "head_branch": "main", "event": "schedule",
        "url": f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/attempts/{attempt}",
    }


def _proposer_checks():
    """The mandatory CI gate, resolved against the published proposal head.

    This is the ci.yml run that validated the published head — a different run from the
    proposer run that published it, so the two must not be conflated.
    """
    return [resolved_check("maintenance-ci", head_sha=PUBLISHED_HEAD)]


def _proposer_decision(**overrides):
    """Run the real decision path over an API-shaped python-bump proposal."""
    fixture = {
        "pr": {
            "number": 31, "state": "open", "draft": False, "mergeable": True,
            "mergeable_state": "clean", "base": {"ref": "main"},
            "head": {"sha": PUBLISHED_HEAD, "ref": "chore/python-3.14.9"},
            "title": "chore: bump CPython pin 3.14.8 -> 3.14.9",
            "user": {"login": BUMP_ACTOR},
        },
        "checks": _proposer_checks(),
        "changed_files": [".python-version"],
        "file_patches": {".python-version": PYTHON_PATCH},
        "auto_merge_vars": {"AUTO_MERGE_PYTHON_BUMP": "yes"},
        "labels": [], "reviews": [], "head_sha": PUBLISHED_HEAD, "repository": REPO,
        "review_required": False, "deferred": False,
        "publisher_run": api_proposer_run(),
        "publisher_attempt": api_proposer_attempt(),
        "published_commit": api_published_commit(),
        "manifest_versions": ["3.14.8", "3.14.9"],
    }
    fixture.update(overrides)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
        json.dump(fixture, fh)
        fname = fh.name
    try:
        return maintenance.decision_from_fixture(fname, str(ROOT / ".github" / "maintenance.json"))
    finally:
        os.unlink(fname)


def test_proposer_publication_bound_to_the_real_run():
    """A BUMP_PAT publication is authorized by evidence, with source head != published head."""
    decision = _proposer_decision()
    assert_eq(decision["decision"], "merge")
    assert decision["merge"] is True, decision["reason"]
    # the run's own head is the source head, deliberately not the published proposal head
    assert_eq(api_proposer_run()["head_sha"], SOURCE_HEAD)
    assert SOURCE_HEAD != PUBLISHED_HEAD


def test_proposer_publication_rejects_incomplete_evidence():
    """Every gap in the publication chain fails closed with a specific reason."""
    cases = {
        "publisher run unavailable": {"publisher_run": None},
        "failed publisher run": {"publisher_run": api_proposer_run(conclusion="failure")},
        "missing conclusion": {"publisher_run": api_proposer_run(conclusion=None)},
        "run still in progress": {"publisher_run": api_proposer_run(status="in_progress", conclusion=None)},
        "publisher superseded by a rerun": {
            "publisher_run": api_proposer_run(attempt=1),
            "publisher_attempt": api_proposer_attempt(attempt=2),
        },
        "attempt record for another run": {
            "publisher_attempt": api_proposer_attempt(run_id=PROPOSER_RUN_ID + 4),
        },
        "attempt record on another source head": {
            "publisher_attempt": api_proposer_attempt(head_sha="ffffffff"),
        },
        "attempt record failed": {
            "publisher_attempt": api_proposer_attempt(conclusion="failure"),
        },
        "attempt record unavailable": {"publisher_attempt": None},
        "run carries no attempt number": {
            "publisher_run": {**api_proposer_run(), "run_attempt": None},
        },
        "commit by another identity": {"published_commit": api_published_commit(login="someone-else")},
        "commit outside the run window": {"published_commit": api_published_commit(date="2026-10-16T09:00:00Z")},
        "commit before the run started": {"published_commit": api_published_commit(date="2026-10-16T01:00:00Z")},
        "version absent from the manifest": {"manifest_versions": ["3.14.8"]},
        "manifest unavailable": {"manifest_versions": None},
        "published commit is not the PR head": {"published_commit": api_published_commit(sha="deadbeef")},
        "run from a fork": {"publisher_run": api_proposer_run(full_name="someone/fork")},
        "untrusted proposer workflow": {"publisher_run": api_proposer_run(path=".github/workflows/other.yml")},
        "run without a workflow id": {"publisher_run": api_proposer_run(workflow_id=None)},
        "repository not supplied": {"repository": None},
        "published commit unavailable": {"published_commit": None},
    }
    for label, overrides in cases.items():
        decision = _proposer_decision(**overrides)
        assert decision["merge"] is False, f"{label} must not authorize a merge"
        assert_eq(decision["decision"], "review-required")
        assert "publication unverified" in decision["reason"], f"{label}: {decision['reason']}"


def test_trusted_author_does_not_bypass_publication_provenance():
    """A bot-authored proposal with no publication evidence is not authorized."""
    for login in ("github-actions[bot]", "dependabot[bot]", BUMP_ACTOR):
        decision = _proposer_decision(publisher_run=None)
        assert decision["merge"] is False, f"{login} must not bypass provenance"
        assert "publication unverified" in decision["reason"], decision["reason"]


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


def main():
    tests = [test_parse_version, test_compare_versions, test_classify_python, test_classify_package, test_classify_group, test_validate_changed_files, test_select_candidate, test_evaluate_checks, test_provenance_requires_the_exact_producer, test_provenance_accepts_the_current_attempt_only, test_opaque_external_id_is_never_parsed_as_a_job_id, test_canonical_link_must_name_this_repository_and_check, test_decision_from_fixture, test_proposer_publication_bound_to_the_real_run, test_proposer_publication_rejects_incomplete_evidence, test_trusted_author_does_not_bypass_publication_provenance, test_adapter_resolves_real_payloads_and_refetches_after_a_rerun, test_adapter_never_parses_external_id_or_trusts_a_foreign_link]
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
