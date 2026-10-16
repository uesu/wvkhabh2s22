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
            status="completed", conclusion="success", workflow_name=CI_NAME):
    """Shaped like GET /repos/{owner}/{repo}/actions/jobs/{job_id}."""
    return {
        "id": job_id, "run_id": run_id, "run_attempt": attempt, "workflow_name": workflow_name,
        "head_sha": head_sha, "node_id": "J_kwLO2", "url": f"https://api.github.com/repos/{REPO}/actions/jobs/{job_id}",
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}/job/{job_id}",
        "status": status, "conclusion": conclusion, "name": name, "steps": [],
        "check_run_url": f"https://api.github.com/repos/{REPO}/check-runs/{CHECK_ID}",
        "labels": [], "runner_name": "GitHub Actions 100", "runner_group_name": "Default",
    }


def api_attempts(*attempts):
    """Shaped like the workflow_run_attempts array of GET .../actions/runs/{run_id}/attempts."""
    return [
        {"id": RUN_ID, "run_attempt": number, "status": "completed", "conclusion": conclusion,
         "head_sha": HEAD, "path": CI_PATH, "workflow_id": WORKFLOW_ID}
        for number, conclusion in attempts
    ]


def api_check_run(name="maintenance-ci", *, check_id=CHECK_ID, head_sha=HEAD, job_id=JOB_ID,
                  status="completed", conclusion="success", app_slug="github-actions",
                  external_id="sentinel"):
    """Shaped like one entry of GET /repos/{owner}/{repo}/commits/{ref}/check-runs.

    external_id is the job id for Actions-produced checks, and html_url is the canonical
    runs/<run_id>/job/<job_id> link the controller resolves provenance through.
    """
    external = str(job_id) if external_id == "sentinel" else external_id
    return {
        "id": check_id, "head_sha": head_sha, "external_id": external,
        "url": f"https://api.github.com/repos/{REPO}/check-runs/{check_id}",
        "html_url": f"https://github.com/{REPO}/actions/runs/{RUN_ID}/job/{job_id}" if external else None,
        "details_url": f"https://github.com/{REPO}/actions/runs/{RUN_ID}/job/{job_id}" if external else None,
        "status": status, "conclusion": conclusion, "started_at": "2026-10-16T03:00:00Z",
        "completed_at": "2026-10-16T03:04:00Z", "name": name,
        "check_suite": {"id": 5001, "status": status, "conclusion": conclusion, "head_sha": head_sha},
        "app": {"id": 15368, "slug": app_slug, "name": "GitHub Actions", "owner": {"login": "github"}},
        "output": {"title": None, "summary": None, "text": None},
    }


def resolved_check(name="maintenance-ci", *, job=None, run=None, attempts=None, **check_kwargs):
    """A live check run plus the raw job/run/attempt payloads the controller fetched for it."""
    check = api_check_run(name, **check_kwargs)
    if job is not False:
        check["job"] = job if job is not None else api_job(name=name)
    if run is not False:
        check["run"] = run if run is not None else api_run()
    if attempts is not False:
        check["attempts"] = attempts if attempts is not None else api_attempts((1, "success"))
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
        "stale attempt superseded by a rerun": resolved_check(
            "maintenance-ci", attempts=api_attempts((1, "success"), (2, "failure"))
        ),
        "job unavailable": resolved_check("maintenance-ci", job=False),
        "run unavailable": resolved_check("maintenance-ci", run=False),
        "attempt history unavailable": resolved_check("maintenance-ci", attempts=False),
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

    # a check with no run id at all cannot be bound
    r = maintenance.evaluate_checks(
        [resolved_check("maintenance-ci", external_id="not-a-job-id")], ["maintenance-ci"], HEAD, HEAD, REPO
    )
    assert r["ok"] is False, r["reason"]


def test_provenance_accepts_the_current_attempt_only():
    """A rerun that then succeeded is authorized; an earlier attempt of it is not."""
    current = resolved_check(
        "maintenance-ci",
        job=api_job(attempt=2),
        run=api_run(attempt=2),
        attempts=api_attempts((1, "failure"), (2, "success")),
    )
    r = maintenance.evaluate_checks([current], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is True, r["reason"]
    assert_eq(r["provenance"][0]["run_attempt"], 2)

    superseded = resolved_check(
        "maintenance-ci",
        job=api_job(attempt=1),
        run=api_run(attempt=1),
        attempts=api_attempts((1, "success"), (2, "failure")),
    )
    r = maintenance.evaluate_checks([superseded], ["maintenance-ci"], HEAD, HEAD, REPO)
    assert r["ok"] is False and "stale attempt" in r["reason"], r["reason"]


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
    stale = _decision([resolved_check("maintenance-ci", attempts=api_attempts((1, "success"), (2, "failure")))])
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
    assert "is not the job's run" in wrong_run["reason"], wrong_run["reason"]

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


def api_proposer_attempts(*numbers):
    return [{"id": PROPOSER_RUN_ID, "run_attempt": n, "status": "completed", "conclusion": "success"}
            for n in numbers]


def _proposer_checks():
    """The mandatory gate, resolved against the published proposal head."""
    check = resolved_check("maintenance-ci", head_sha=PUBLISHED_HEAD)
    check["job"] = api_job(head_sha=PUBLISHED_HEAD, run_id=PROPOSER_RUN_ID)
    check["run"] = api_run(run_id=PROPOSER_RUN_ID, head_sha=PUBLISHED_HEAD)
    return [check]


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
        "publisher_attempts": api_proposer_attempts(1),
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
        "stale publisher attempt": {"publisher_attempts": api_proposer_attempts(1, 2)},
        "attempt history unavailable": {"publisher_attempts": None},
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


def main():
    tests = [test_parse_version, test_compare_versions, test_classify_python, test_classify_package, test_classify_group, test_validate_changed_files, test_select_candidate, test_evaluate_checks, test_provenance_requires_the_exact_producer, test_provenance_accepts_the_current_attempt_only, test_decision_from_fixture, test_proposer_publication_bound_to_the_real_run, test_proposer_publication_rejects_incomplete_evidence, test_trusted_author_does_not_bypass_publication_provenance]
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
