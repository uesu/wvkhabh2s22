#!/usr/bin/env python3
"""
Shared maintenance helpers — version selection, classification, diff validation,
and decision/report helpers. Mostly pure, testable, and shared between
News-Express and Game-Express.

Implements the update policy and runtime proposer algorithm from
docs/MAINTENANCE.md (spec 2026-10-15). No network, no secret, no write
side-effects in decision mode.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

# Shared cross-repository maintenance authority. This module is byte-identical in every
# repository that adopts it: the verification *semantics* (publication provenance, release-date
# authority, security evidence, notice envelopes, producer identity) live there, while everything
# repository-specific stays here and in .github/maintenance.json — proposal scope, trusted
# workflow/check names, allowed runtime paths, the JS merge controller and the lock-regenerating
# proposer. Adoption means delegating, not forking: a semantic fix lands once and applies
# everywhere, and no repository is hidden behind a repository-name conditional.
#
# Reaching a sibling module needs no sys.path manipulation: running this file as a script puts its
# own directory on sys.path, and every importer — ci.py, python_version_bump.py, the workflow's
# candidate/publisher blocks and the tests — inserts .github/scripts before importing.
import maintenance_common as common

SCHEMA_VERSION = common.SCHEMA_VERSION

# Pinned build toolchain. These live in the shared module, not in the registry, so the merge
# controller, the CI registry and the proposer can never drift apart about which pip/pip-tools
# produced a validated lock graph: publication evidence records the versions it actually used and
# is compared against these. A repository may override them from its own profile via
# compiler_pins(); the module constants are the shared default.
PINNED_PIP = common.PINNED_PIP
PINNED_PIP_TOOLS = common.PINNED_PIP_TOOLS

# Allowed runtime files that may change in a valid maintenance PR.
# Production without docs must work; docs/history are never runtime.
ALLOWED_RUNTIME_PATTERNS = [
    r"^\.python-version$",
    r"^requirements\.in$",
    r"^requirements\.txt$",
    r"^requirements-dev\.in$",
    r"^requirements-dev\.txt$",
    r"^\.github/maintenance\.json$",
    r"^\.github/scripts/maintenance\.py$",
    r"^\.github/scripts/maintenance_common\.py$",
    r"^\.github/scripts/python_version_bump\.py$",
    r"^\.github/scripts/ci\.py$",
    r"^\.github/scripts/render_run_summary\.py$",
    r"^\.github/scripts/merge_monitor_caches\.py$",
    r"^\.github/workflows/ci\.yml$",
    r"^\.github/workflows/python_version_bump\.yml$",
    r"^\.github/workflows/maintenance_merge\.yml$",
    r"^\.github/workflows/reddit_monitor\.yml$",
    r"^\.github/workflows/twitter_monitor\.yml$",
    r"^\.github/dependabot\.yml$",
    r"^\.env\.example$",
    r"^\.gitignore$",
    r"^testing area/reddit_main_v3\.py$",
    r"^testing area/reddit_proxy\.py$",
    r"^testing area/twitter_v3\.py$",
    r"^testing area/reddit_main\.py$",
    r"^testing area/reddit_main_v2_embedez\.py$",
    r"^testing area/reddit_signals\.py$",
    r"^testing area/twitter_proxy\.py$",
    r"^testing area/twitter_v1\.py$",
    r"^testing area/twitter_v2_button_outside\.py$",
    r"^testing area/video_diag\.py$",
    r"^tests/test_.*\.py$",
    r"^tests/fixtures/.*$",
]

# Trusted check identity: name must match expected workflow/job set.
# The final gate is the only mandatory check; detailed sub-checks run inside `validate` but are not separate GitHub checks.
TRUSTED_CHECKS = {
    "maintenance-ci",
    "maintenance-ci (required)",
    "smoke",
    "reddit-signals",
    "twitter-media",
    "maintenance",
    "run-summary",
    "configuration-contract",
    "lock-consistency",
    "workflow-lint",
}
TRUSTED_APP_SLUGS = {"github-actions"}
TRUSTED_WORKFLOW = "CI — syntax + offline validation"

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def parse_version(v: str) -> tuple[int, int, int] | None:
    """Parse X.Y.Z numerically, returns tuple or None if invalid."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not VERSION_RE.match(v):
        return None
    try:
        parts = tuple(int(p) for p in v.split("."))
        if len(parts) != 3:
            return None
        return parts  # type: ignore[return-value]
    except ValueError:
        return None



def parse_requirement_diff(patch: str) -> list[dict[str, str]]:
    """Parse requirement changes from a unified diff patch.
    Returns list of {name, old, new} for each package where version changed.
    Handles --hash lines and preserves order.
    """
    import re
    changes = []
    # Look for lines like -package==1.2.3 \ and +package==1.2.4 \
    # Also handle -package==1.2.3 (without trailing \) in some diffs
    old_re = re.compile(r"^\-\s*([A-Za-z0-9_\-\.\[\]]+)==([^\s\\]+)")
    new_re = re.compile(r"^\+\s*([A-Za-z0-9_\-\.\[\]]+)==([^\s\\]+)")
    olds = {}
    news = {}
    for line in patch.splitlines():
        if line.startswith(("---", "+++")):
            continue
        m_old = old_re.match(line)
        if m_old:
            name = m_old.group(1).split("[")[0].strip()
            ver = m_old.group(2).strip()
            olds[name.lower()] = (name, ver)
        m_new = new_re.match(line)
        if m_new:
            name = m_new.group(1).split("[")[0].strip()
            ver = m_new.group(2).strip()
            news[name.lower()] = (name, ver)
    # Match old and new by name
    for key in set(olds) | set(news):
        if key in olds and key in news:
            old_name, old_ver = olds[key]
            new_name, new_ver = news[key]
            if old_ver != new_ver:
                changes.append({"name": old_name, "old": old_ver, "new": new_ver})
        elif key in news and key not in olds:
            # New package added
            new_name, new_ver = news[key]
            changes.append({"name": new_name, "old": "", "new": new_ver})
        elif key in olds and key not in news:
            # Package deleted
            old_name, old_ver = olds[key]
            changes.append({"name": old_name, "old": old_ver, "new": ""})
    return changes


def compare_versions(a: str, b: str) -> int:
    """Numeric compare: -1 if a<b, 0 if equal, 1 if a>b. Prerelease/invalid => treat as unknown."""
    pa = parse_version(a)
    pb = parse_version(b)
    if pa is None or pb is None:
        # Unknown versioning is review-required per policy
        return 0
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def is_stable_version(v: str) -> bool:
    """Stable means X.Y.Z numeric, no prerelease marker."""
    if parse_version(v) is None:
        return False
    # Prerelease markers contain -, rc, beta, alpha
    low = v.lower()
    return not any(x in low for x in ("-", "rc", "beta", "alpha", "dev"))


def classify_python_update(current: str, candidate: str) -> dict[str, Any]:
    """Classify a Python version change."""
    cur = parse_version(current)
    cand = parse_version(candidate)
    if cur is None or cand is None:
        return {"risk": "high", "review_required": True, "reason": "unknown-version", "kind": "unknown"}
    if cand <= cur:
        # downgrade or same
        if cand < cur:
            return {"risk": "high", "review_required": True, "reason": "downgrade", "kind": "downgrade"}
        return {"risk": "none", "review_required": False, "reason": "up-to-date", "kind": "none"}
    # Same major
    if cand[0] != cur[0]:
        return {"risk": "high", "review_required": True, "reason": "major", "kind": "major"}
    if cand[1] == cur[1]:
        # patch
        if cand[2] > cur[2]:
            return {"risk": "low", "review_required": False, "reason": "patch", "kind": "patch"}
        return {"risk": "none", "review_required": False, "reason": "up-to-date", "kind": "none"}
    if cand[1] == cur[1] + 1 and cand[0] == cur[0]:
        return {"risk": "medium", "review_required": True, "reason": "next-minor", "kind": "minor"}
    # Skips a minor series
    return {"risk": "high", "review_required": True, "reason": "skipped-minor", "kind": "minor"}


def classify_package_update(name: str, old: str, new: str) -> dict[str, Any]:
    """Classify a package version change for Dependabot grouping."""
    # Unknown/ambiguous versioning
    if not old or not new:
        return {"risk": "high", "review_required": True, "reason": "unknown-version"}
    # Detect prerelease markers
    for v in (old, new):
        low = v.lower()
        if any(x in low for x in ["rc", "beta", "alpha", "dev", "pre"]):
            return {"risk": "high", "review_required": True, "reason": "prerelease"}
    old_p = parse_version(old)
    new_p = parse_version(new)
    if old_p is None or new_p is None:
        # Ambiguous versioning
        return {"risk": "high", "review_required": True, "reason": "ambiguous-versioning"}
    if new_p[0] != old_p[0]:
        return {"risk": "high", "review_required": True, "reason": "major"}
    if new_p[0] == 0 and new_p[1] != old_p[1]:
        # 0.x minor is breaking-risk per policy
        return {"risk": "high", "review_required": True, "reason": "breaking-0.x-minor"}
    if new_p[1] != old_p[1]:
        return {"risk": "low", "review_required": False, "reason": "minor"}
    if new_p[2] != old_p[2]:
        return {"risk": "low", "review_required": False, "reason": "patch"}
    return {"risk": "none", "review_required": False, "reason": "up-to-date"}


def classify_group_updates(updates: list[dict[str, Any]]) -> dict[str, Any]:
    """Grouped updates: highest-risk member controls entire PR."""
    if not updates:
        return {"risk": "none", "review_required": False, "reason": "empty"}
    highest = {"risk": "none", "review_required": False, "reason": "routine"}
    risk_order = {"none": 0, "low": 1, "medium": 2, "high": 3}
    for u in updates:
        # Each entry already classified; find max risk
        r = u.get("risk", "high")
        if risk_order.get(r, 3) > risk_order.get(highest["risk"], 0):
            highest = u
        # Any review-required makes group review-required
        if u.get("review_required"):
            highest["review_required"] = True
    return highest


def validate_changed_files(
    changed: list[str],
    allowed: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate that only allowed runtime files changed. Returns {ok, reason, disallowed}.

    ``allowed`` NARROWS the permitted set for a caller that knows exactly which files its own
    proposal may touch — a routine Python bump publishes the pin, both generated lock graphs and
    the two derived policy version fields, and nothing else. It can never widen it: a path has to
    satisfy the trusted runtime patterns AND appear in ``allowed``, so a caller cannot authorize
    a file the shared policy forbids.
    """
    narrowed = {str(path) for path in (allowed or ())}
    disallowed = []
    for f in changed:
        if not any(re.match(pat, f) for pat in ALLOWED_RUNTIME_PATTERNS) or narrowed and f not in narrowed:
            disallowed.append(f)
    if disallowed:
        scope = "outside this proposal's permitted files" if narrowed else "disallowed"
        return {
            "ok": False,
            "reason": f"disallowed files ({scope}): {', '.join(disallowed)}",
            "disallowed": disallowed,
        }
    return {"ok": True, "reason": "allowed", "disallowed": []}


def select_candidate(
    current: str,
    manifest: list[dict[str, Any]],
    soak_days: int = 30,
    now_ts: int | None = None,
    ignore_soak: bool = False,
    release_dates: dict[str, str] | None = None,
    release_date_state: str = "",
) -> dict[str, Any]:
    """
    Runtime proposer: select newest patch of current series, else newest patch
    of next minor after soak. Returns dict with candidate, kind, release_url, reason.
    Implements full algorithm from spec.

    Soak age comes from the python.org downloads API, never from a setup-python manifest
    timestamp. ``apply_release_dates`` strips any ``published_at``/``release_date`` the manifest
    carries itself, because a rebuilt archive's publication timestamp is an artefact of the
    mirror and would silently redefine the age of an old release. A minor candidate therefore
    soaks only against an authoritative date: when the authority was not reached, or does not
    know that version, the selector defers instead of guessing an age.
    """
    import time
    cur = parse_version(current)
    if cur is None:
        return {"candidate": None, "kind": "none", "reason": "invalid-current-version", "release_url": None}
    series_now = cur[:2]
    series_next = (cur[0], cur[1] + 1)
    now = now_ts if now_ts is not None else int(time.time())

    enriched, date_summary = common.apply_release_dates(manifest, release_dates or {}, release_date_state)
    authority_ok = str(release_date_state) in ("ok", "cache")

    def newest(series):
        best = None
        best_entry = None
        for entry in enriched:
            raw = entry.get("version", "")
            if not entry.get("stable"):
                continue
            if not is_stable_version(raw):
                continue
            parts = parse_version(raw)
            if parts is None or parts[:2] != series:
                continue
            # Check OS/arch: linux x64 required
            files = entry.get("files", [])
            if not any(f.get("platform") == "linux" and f.get("arch") == "x64" for f in files):
                continue
            if best is None or parts > best:
                best = parts
                best_entry = entry
        return best, best_entry

    # Prefer newest patch in current series
    for series, kind in ((series_now, "patch"), (series_next, "minor")):
        best, entry = newest(series)
        if best and best > cur:
            candidate = ".".join(str(p) for p in best)
            release_url = entry.get("release_url", "") if entry else ""
            # Soak check for minor
            if kind == "minor" and not ignore_soak:
                if not authority_ok:
                    # The release-date authority was not consulted successfully. Its absence
                    # cannot be read as "old enough" — that is how a rebuilt archive would
                    # bypass the soak window.
                    return {"candidate": candidate, "kind": kind, "reason": "deferred: release-date-authority-unavailable", "release_url": release_url, "deferred": True, "release_dates": date_summary}
                published = entry.get("release_date") if entry else None
                trusted = bool(published) and str(entry.get("release_date_source") or "") == common.RELEASE_DATE_SOURCE
                if not trusted:
                    # Missing release date cannot bypass soak
                    return {"candidate": candidate, "kind": kind, "reason": "deferred: release-date-unavailable", "release_url": release_url, "deferred": True, "release_dates": date_summary}
                # Parse the authoritative ISO date
                try:
                    pub = common.parse_iso_date(str(published))
                    if pub is None:
                        raise ValueError(f"unparseable release date {published!r}")
                    age_days = (now - int(pub.timestamp())) / 86400
                    if age_days < soak_days:
                        return {"candidate": candidate, "kind": kind, "reason": f"deferred: soak {age_days:.1f}/{soak_days} days", "release_url": release_url, "deferred": True, "release_dates": date_summary}
                except (ValueError, OverflowError, OSError):
                    # parse_iso_date returns None for anything unparseable and is re-raised as
                    # ValueError above; timestamp() can overflow or go out of platform range.
                    # Every one of those defers rather than guessing an age or crashing the run.
                    return {"candidate": candidate, "kind": kind, "reason": "deferred: release-date-unavailable", "release_url": release_url, "deferred": True, "release_dates": date_summary}
            return {"candidate": candidate, "kind": kind, "reason": "available", "release_url": release_url, "deferred": False, "release_dates": date_summary}
    return {"candidate": None, "kind": "none", "reason": "up-to-date", "release_url": None, "deferred": False, "release_dates": date_summary}


def is_successful_check(check: dict[str, Any]) -> bool:
    """Require the trusted expected successful set and latest-head association.
    Skipped/neutral/unrelated are NOT successful for mandatory checks.
    """
    # Check must be completed and conclusion success, and name in trusted set
    if check.get("status") != "completed":
        return False
    if check.get("conclusion") != "success":
        return False
    # Identity check: trusted name
    # Also allow workflow/job qualified names? For now strict.
    # But allow any check that is in mandatory list; if unknown, not successful.
    return check.get("name") in TRUSTED_CHECKS


TRUSTED_WORKFLOW_PATHS = {".github/workflows/ci.yml"}


_CANONICAL_JOB_LINK = re.compile(
    r"^https://github\.com/(?P<repository>[^/]+/[^/]+)/actions/runs/(?P<run_id>\d+)/job/(?P<job_id>\d+)/?$"
)


def parse_canonical_job_link(url: Any, repository: str | None) -> tuple[int, int] | None:
    """Extract (run_id, job_id) from a canonical Actions job link for THIS repository.

    The link is the only reliable route from a check run to its producer, so it is matched
    exactly: https scheme, the github.com host, this owner/repo, and numeric run and job ids.
    A foreign host, a foreign repository, a scheme-relative URL or a non-numeric id yields
    None rather than a guess.
    """
    if not isinstance(url, str) or not repository:
        return None
    match = _CANONICAL_JOB_LINK.match(url.strip())
    if not match or match.group("repository") != repository:
        return None
    return int(match.group("run_id")), int(match.group("job_id"))


def resolve_check_provenance(
    check: dict[str, Any],
    *,
    head_sha: str,
    repository: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve one check run to the Actions job and workflow run that actually produced it.

    The Checks API returns no workflow identity: a real ``check_runs`` record carries ``id``,
    ``name``, ``head_sha``, ``status``, ``conclusion``, ``app``, ``external_id`` and
    ``html_url`` — never ``workflow``, ``workflow_id``, ``path`` or ``workflow_run``. Reading
    those keys off the check therefore rejects every genuine record, so provenance has to be
    resolved and then bound.

    ``external_id`` is opaque: for a real Actions check it is a UUID such as
    ``0fb0ae38-2e89-5fc7-8093-e2d0d0cf857d``, not the numeric job id, and parsing its leading
    digits would silently invent a job (``83e46dd2-...`` -> 83). It is recorded as metadata and
    never used for identity. The canonical ``html_url`` link supplies the run and job ids, and
    the fetched job's own ``check_run_url`` has to point back at this exact check in this exact
    repository — that mutual reference, not a name match, is the proof of production.

    The caller attaches three raw fetched payloads: ``job`` (actions.getJobForWorkflowRun),
    ``run`` (actions.getWorkflowRun, which reports the current attempt) and ``attempt``
    (actions.getWorkflowRunAttempt for the job's own attempt number). There is no
    listWorkflowRunAttempts endpoint, so currency is established by comparing the job's attempt
    against the run's current attempt and against the fetched attempt-specific record. Any gap
    fails closed; nothing here invents a field.
    """
    name = check.get("name")
    job = check.get("job") if isinstance(check.get("job"), dict) else None
    run = check.get("run") if isinstance(check.get("run"), dict) else None
    attempt = check.get("attempt") if isinstance(check.get("attempt"), dict) else None

    if not repository:
        return None, f"{name} (repository not supplied — cannot verify provenance)"
    if not isinstance(check.get("id"), int) or check.get("id") <= 0:
        return None, f"{name} (no check run id — cannot bind provenance)"
    if check.get("head_sha") != head_sha:
        return None, f"{name} (head mismatch {check.get('head_sha')} vs {head_sha})"
    if check.get("status") != "completed" or check.get("conclusion") != "success":
        return None, f"{name} ({check.get('conclusion') or check.get('status')})"
    app = check.get("app")
    slug = (app.get("slug") or app.get("name")) if isinstance(app, dict) else app
    if slug not in TRUSTED_APP_SLUGS:
        return None, f"{name} (untrusted or missing app {slug or 'none'})"
    if name not in TRUSTED_CHECKS:
        return None, f"{name} (untrusted check identity)"

    link = parse_canonical_job_link(check.get("html_url"), repository) or \
        parse_canonical_job_link(check.get("details_url"), repository)
    if link is None:
        return None, (
            f"{name} (no canonical Actions job link for {repository} in "
            f"{check.get('html_url') or check.get('details_url') or 'either url'})"
        )
    link_run_id, link_job_id = link

    if job is None:
        return None, f"{name} (producing job unavailable — provenance unresolved)"
    if job.get("id") != link_job_id:
        return None, f"{name} (fetched job {job.get('id')} is not the linked job {link_job_id})"
    expected_check_url = f"https://api.github.com/repos/{repository}/check-runs/{check.get('id')}"
    if job.get("check_run_url") != expected_check_url:
        return None, (
            f"{name} (job {job.get('id')} does not point back at this check: "
            f"{job.get('check_run_url')} != {expected_check_url})"
        )
    if job.get("name") != name:
        return None, f"{name} (job name {job.get('name')} does not match the required check)"
    if job.get("head_sha") != head_sha:
        return None, f"{name} (job head mismatch {job.get('head_sha')} vs {head_sha})"
    if job.get("status") != "completed" or job.get("conclusion") != "success":
        return None, f"{name} (job {job.get('conclusion') or job.get('status')})"
    if job.get("run_id") != link_run_id:
        return None, f"{name} (job belongs to run {job.get('run_id')}, not the linked run {link_run_id})"
    if not isinstance(job.get("run_attempt"), int) or job.get("run_attempt") <= 0:
        return None, f"{name} (job carries no attempt number)"

    if run is None:
        return None, f"{name} (producing workflow run unavailable — provenance unresolved)"
    if run.get("id") != link_run_id:
        return None, f"{name} (fetched run {run.get('id')} is not the linked run {link_run_id})"
    if run.get("run_attempt") != job.get("run_attempt"):
        return None, (
            f"{name} (stale attempt {job.get('run_attempt')} — "
            f"the run's current attempt is {run.get('run_attempt')})"
        )
    if run.get("head_sha") != head_sha:
        return None, f"{name} (run head mismatch {run.get('head_sha')} vs {head_sha})"
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        return None, f"{name} (run {run.get('conclusion') or run.get('status')})"
    if run.get("path") not in TRUSTED_WORKFLOW_PATHS:
        return None, f"{name} (untrusted workflow path {run.get('path')})"
    if not isinstance(run.get("workflow_id"), int) or run.get("workflow_id") <= 0:
        return None, f"{name} (run carries no workflow id)"
    for key in ("repository", "head_repository"):
        record = run.get(key)
        full_name = record.get("full_name") if isinstance(record, dict) else record
        if full_name != repository:
            return None, f"{name} (run {key} {full_name} is not {repository})"

    if attempt is None:
        return None, f"{name} (attempt-specific run record unavailable — currency unproven)"
    if attempt.get("id") != link_run_id or attempt.get("run_attempt") != job.get("run_attempt"):
        return None, (
            f"{name} (attempt record {attempt.get('id')}/{attempt.get('run_attempt')} is not "
            f"run {link_run_id} attempt {job.get('run_attempt')})"
        )
    if attempt.get("head_sha") != head_sha:
        return None, f"{name} (attempt head mismatch {attempt.get('head_sha')} vs {head_sha})"
    if attempt.get("status") != "completed" or attempt.get("conclusion") != "success":
        return None, f"{name} (attempt {attempt.get('conclusion') or attempt.get('status')})"
    if attempt.get("path") not in TRUSTED_WORKFLOW_PATHS:
        return None, f"{name} (untrusted attempt workflow path {attempt.get('path')})"

    return (
        {
            "name": name,
            "check_id": check.get("id"),
            "check_run_url": job.get("check_run_url"),
            "job_id": job.get("id"),
            "job_name": job.get("name"),
            "run_id": run.get("id"),
            "run_attempt": run.get("run_attempt"),
            "workflow_id": run.get("workflow_id"),
            "workflow_path": run.get("path"),
            "workflow_name": run.get("name") or job.get("workflow_name"),
            "head_sha": head_sha,
            "repository": repository,
            "app": slug,
            # opaque metadata only — never an identity
            "external_id": check.get("external_id"),
        },
        None,
    )


TRUSTED_PROPOSER_PATH = ".github/workflows/python_version_bump.yml"
# The PR-creation API call can complete slightly after the run record's updated_at.
_PUBLICATION_GRACE_SECONDS = 300


def _iso_to_epoch(value: Any) -> float | None:
    """Parse a GitHub timestamp; None when it is absent or unparseable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        # fromisoformat parses a trailing "Z" itself on the pinned runtime (3.14.8, and any
        # 3.11+), so the zero-offset string rewrite is dead weight.
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


#: The only files a routine Python bump proposal may touch, in trusted policy order. The lock
#: graphs are generated; the policy file is permitted only for the two derived version fields.
PROPOSAL_FILES = (
    ".python-version",
    "requirements.txt",
    "requirements-dev.txt",
    ".github/maintenance.json",
)


def verify_publication_record(
    fixture: dict[str, Any],
    pr: dict[str, Any],
    candidate_version: str | None,
    *,
    pinned_pip: str | None = None,
    pinned_pip_tools: str | None = None,
    trusted_proposer_path: str | None = None,
    proposal_files: tuple[str, ...] | None = None,
) -> tuple[bool, str]:
    """Bind a published proposal to the run and attempt that actually validated and published it.

    A branch name, a title, a time window or membership of a mutable upstream version list
    identifies nothing: several proposer runs can share them, and the first completed run on the
    default branch may have validated a different candidate. The proposer therefore emits two
    machine-readable records bound to its own run and attempt — one for the validated candidate
    bytes and one for the publication — and this verifier requires both, from the same attempt of
    the same successful run of the trusted proposer workflow in this exact repository.

    Three identities stay distinct and are never collapsed into one login: the run actor who
    authorized the run, the PAT publisher who authored the PR, and the configured commit bot who
    signed the commit. Each is bound through the actual publication record and the actual PR,
    not assumed equal. The proposal digests compared here are computed by the caller from the
    published file contents, so a proposal cannot vouch for itself.

    The verification semantics are the shared cross-repository implementation in
    ``maintenance_common``; this adapter supplies what is repository-specific — the trusted
    proposer workflow path, this repository's proposal scope (pin plus both regenerated lock
    graphs), and the pinned compiler versions. Those pins are taken from the trusted profile and
    never from the evidence: letting the record declare the pins it happened to use would let a
    proposal vouch for its own toolchain. The commit identity is likewise verified against the
    independently fetched commit object, not against the string the notice reports about itself.
    """
    evidence = fixture if isinstance(fixture, dict) else {}
    repository = str(evidence.get("repository") or "").strip()
    proposer_path = trusted_proposer_path or TRUSTED_PROPOSER_PATH
    verdict = common.verify_publication_record(
        evidence,
        pr if isinstance(pr, dict) else {},
        candidate_version=candidate_version,
        trusted_proposer_path=proposer_path,
        proposal_files=PROPOSAL_FILES if proposal_files is None else tuple(proposal_files),
        pinned_pip=pinned_pip or PINNED_PIP,
        pinned_pip_tools=pinned_pip_tools or PINNED_PIP_TOOLS,
        repository=repository,
    )
    reason = str(verdict.get("reason") or "publication-unverified")
    if verdict.get("state") != "ready":
        return False, reason

    run = evidence.get("proposer_run") or {}
    validation = evidence.get("validation_record") or {}
    publication = evidence.get("publication_record") or {}
    head = str(((pr or {}).get("head") or {}).get("sha") or "")
    source_head = str(validation.get("source_head") or "")
    return True, (
        f"proposal bound to {proposer_path} run {run.get('id')} attempt {run.get('run_attempt')}: "
        f"source {source_head[:12]}, published PR #{(pr or {}).get('number')} head {head[:12]}, "
        f"actor {publication.get('run_actor')}, author {publication.get('pr_author')}, "
        f"commit {publication.get('commit_identity')}"
    )


# Legacy alias: earlier callers and fixtures used this name.
verify_proposer_publication = verify_publication_record


def evaluate_checks(
    checks: list[dict[str, Any]],
    mandatory: list[str],
    head_sha: str,
    pr_head_sha: str,
    repository: str | None = None,
) -> dict[str, Any]:
    """
    Evaluate PR checks for merge controller.
    Returns {ok, reason, missing, failed}.
    All mandatory must be successful on exact head SHA and from trusted workflow/app.
    """
    if head_sha != pr_head_sha:
        return {"ok": False, "reason": "head mismatch", "missing": mandatory, "failed": []}
    if not checks:
        return {"ok": False, "reason": "no checks", "missing": mandatory, "failed": []}
    # Fail closed on pagination truncation: if total_count indicates more than supplied, block
    # Real workflow handles pagination; fixture can signal truncation via total_count
    # Map checks by name
    by_name = {c.get("name"): c for c in checks}
    missing = []
    failed = []
    resolved: list[dict[str, Any]] = []
    for name in mandatory:
        c = by_name.get(name)
        if c is None:
            missing.append(name)
            continue
        # Must be success; skipped/neutral/pending are failures for mandatory
        if c.get("status") != "completed" or c.get("conclusion") != "success":
            failed.append(f"{name} ({c.get('conclusion') or c.get('status')})")
            continue
        # Provenance is resolved, not read: the Checks API does not return workflow identity,
        # so the producing job and run must be fetched and bound to this exact check.
        _provenance, problem = resolve_check_provenance(c, head_sha=head_sha, repository=repository)
        if problem:
            failed.append(problem)
            continue
        resolved.append(_provenance)
    if missing or failed:
        reason = []
        if missing:
            reason.append(f"missing: {', '.join(missing)}")
        if failed:
            reason.append(f"failed: {', '.join(failed)}")
        return {
            "ok": False,
            "reason": "; ".join(reason),
            "missing": missing,
            "failed": failed,
            "provenance": resolved,
        }
    return {
        "ok": True,
        "reason": "all mandatory checks successful",
        "missing": [],
        "failed": [],
        "provenance": resolved,
    }


def decision_from_fixture(fixture_path: str | os.PathLike, trusted_policy_path: str | os.PathLike | None = None) -> dict[str, Any]:
    """
    Side-effect-free decision mode taking fixture JSON. Never falls back to real GitHub writes.
    fixture_path: JSON file with {pr, checks, changed_files, auto_merge_vars, reviews, ...}
    trusted_policy_path: path to maintenance.json (default branch), not PR's changed file.
    Returns decision report dict.
    """
    try:
        with open(fixture_path, "r", encoding="utf-8") as fh:
            fixture = json.load(fh)
    except (OSError, ValueError) as e:
        # Unreadable file (OSError) or malformed JSON/encoding (ValueError, which covers
        # json.JSONDecodeError and UnicodeDecodeError). Both stay a failed decision, never a pass.
        return {"decision": "failed", "reason": f"fixture load failed: {e}", "merge": False}

    # Load trusted policy (never from PR's changed maintenance profile)
    policy = None
    if trusted_policy_path and os.path.isfile(trusted_policy_path):
        try:
            with open(trusted_policy_path, "r", encoding="utf-8") as fh:
                policy = json.load(fh)
        except (OSError, ValueError):
            policy = None
    if policy is None:
        # Fallback to defaults, but never execute PR-supplied commands
        policy = {"checks": {"mandatory": ["maintenance-ci"]}}

    pr = fixture.get("pr", {})
    checks = fixture.get("checks", [])
    changed_files = fixture.get("changed_files", [])
    reviews = fixture.get("reviews", [])
    labels = fixture.get("labels", [])

    # Basic PR state gates — unknown mergeability must defer, not pass
    if pr.get("state") != "open":
        return {"decision": "blocked", "reason": "PR not open", "merge": False}
    if pr.get("draft"):
        return {"decision": "blocked", "reason": "draft PR", "merge": False}
    mergeable = pr.get("mergeable")
    mergeable_state = pr.get("mergeable_state")
    if mergeable is False or mergeable_state == "dirty":
        return {"decision": "blocked", "reason": "merge conflict", "merge": False}
    if mergeable is None or mergeable_state in (None, "unknown", ""):
        # GitHub reports mergeable as null/unknown while computing — defer, do not pass
        return {"decision": "blocked", "reason": f"mergeability unknown ({mergeable_state or mergeable}) — defer", "merge": False}
    # Only clean/unstable/has_hooks are potentially mergeable; others (blocked, behind, etc.) require review
    # For maintenance, require clean explicitly
    if mergeable_state not in ("clean", "unstable", "has_hooks") and mergeable_state != "clean":
        return {"decision": "blocked", "reason": f"not cleanly mergeable: {mergeable_state}", "merge": False}
    # Same-repository association check — fork PRs must not be auto-merged even with matching branch/title
    base_repo = (pr.get("base") or {}).get("repo", {}).get("full_name") or ""
    head_repo = (pr.get("head") or {}).get("repo", {}).get("full_name") or base_repo
    if base_repo and head_repo and base_repo != head_repo:
        return {"decision": "blocked", "reason": f"fork PR: head {head_repo} != base {base_repo}", "merge": False}
    # Branch name and base ref alone are not provenance. Both sides of the PR are bound to the
    # repository the controller was invoked for, which only the trusted caller can supply: a
    # missing association, a foreign base or a cross-fork head must never reach the merge call.
    expected_repo = str(fixture.get("repository") or "").strip()
    association_failure = common.repo_association_failure(pr, expected_repo)
    if association_failure:
        return {"decision": "blocked", "reason": f"repository association: {association_failure}", "merge": False}

    # Security evidence is resolved from trusted policy, never from PR content. A repository
    # either requires named scan evidence or has deliberately declared that it requires none;
    # an unconfigured profile is a configuration gap and blocks, and unavailable evidence defers
    # to a human instead of reading as clean.
    security_mode, security_required = common.security_policy(policy)
    security = common.verify_security_evidence(
        fixture.get("security_evidence"), security_required, mode=security_mode
    )
    if security.get("state") == "failed":
        return {"decision": "blocked", "reason": f"security evidence: {security.get('reason')}", "merge": False}
    if security.get("state") != "ready":
        return {
            "decision": "review-required",
            "reason": f"security evidence unavailable: {security.get('reason')}",
            "merge": False,
        }

    # Hold labels / changes requested — latest per user, pagination fail-closed
    # If reviews are paginated, fixture may indicate truncated; fail closed if so
    if fixture.get("reviews_truncated") or fixture.get("total_reviews", 0) > len(reviews):
        return {"decision": "blocked", "reason": "reviews pagination incomplete — fail closed", "merge": False}
    if fixture.get("files_truncated") or fixture.get("total_files", 0) > len(changed_files):
        return {"decision": "blocked", "reason": "files pagination incomplete — fail closed", "merge": False}
    mandatory_hold = (policy.get("update_policy", {}).get("hold_labels") or ["hold", "do-not-merge", "review-required"])
    for lab in labels:
        if lab in mandatory_hold:
            return {"decision": "blocked", "reason": f"hold label: {lab}", "merge": False}
    # Latest review per user matters; dismissed/approved supersedes prior CHANGES_REQUESTED
    # COMMENTED must not erase an active CHANGES_REQUESTED — only APPROVED/DISMISSED or new CHANGES_REQUESTED counts
    latest_by_user: dict[str, dict] = {}
    for rev in reviews:
        user = (rev.get("user") or {}).get("login") or (rev.get("author") or {}).get("login") or str(rev.get("user") or "unknown")
        state = rev.get("state")
        # Ignore COMMENTED and PENDING for decision — they do not override CHANGES_REQUESTED
        if state in ("COMMENTED", "PENDING", "commented", "pending"):
            continue
        # Use submitted_at if present to determine order; otherwise last occurrence wins
        # Only track states that affect decision: CHANGES_REQUESTED, APPROVED, DISMISSED
        if state in ("CHANGES_REQUESTED", "APPROVED", "DISMISSED", "CHANGES_REQUESTED", "APPROVED"):
            latest_by_user[user] = rev
        else:
            # For other states, still track if no prior decision-affecting review
            if user not in latest_by_user:
                latest_by_user[user] = rev
    for rev in latest_by_user.values():
        if rev.get("state") == "CHANGES_REQUESTED":
            return {"decision": "blocked", "reason": "changes requested", "merge": False}

    # Diff validation — filenames plus workflow content
    diff_result = validate_changed_files(changed_files)
    if not diff_result["ok"]:
        return {"decision": "blocked", "reason": diff_result["reason"], "merge": False}
    # Structural workflow validation: workflow file changes beyond version pin require human review
    # For fixture, check file_patches if provided
    file_patches = fixture.get("file_patches") or {}
    workflow_changed = [f for f in changed_files if f.startswith(".github/workflows/")]
    if workflow_changed:
        for wf in workflow_changed:
            patch = file_patches.get(wf, "")
            # Missing patch for workflow file is ambiguous -> fail closed as review-required
            if not patch:
                return {"decision": "review-required", "reason": f"workflow change {wf} missing patch (ambiguous)", "merge": False}
            # Structural workflow changes (triggers/permissions) always require human review — keyword presence is not proof
            if re.search(r"^[+\-\s]*\s*(on|permissions)\s*:", patch, re.MULTILINE):
                return {"decision": "review-required", "reason": f"workflow structural change in {wf}", "merge": False}
            # Any workflow logic change (jobs/uses/run) also requires review — not just python pin
            if re.search(r"^[+\-\s]*\s*(jobs|uses|run)\s*:", patch, re.MULTILINE):
                return {"decision": "review-required", "reason": f"workflow logic change in {wf}", "merge": False}
            # Even a single line like `echo python-version` in a run step is not a valid python bump diff
            # Python bump must only touch version/lock files, not workflows (handled below)
            # Also, if workflow changed alongside engine changes in a dependabot PR, it's not routine
            # Dependabot routine should only touch requirements* and dependabot.yml

    # Risk classification based on actual dependency/runtime changes, not just title
    # Determine PR type via branch/title/author, not just title keyword "major"
    head_ref = (pr.get("head") or {}).get("ref") or ""
    title = pr.get("title") or ""
    author = pr.get("user", {}).get("login", "")
    is_dependabot = author == "dependabot[bot]"
    # Python bump is chore/python-* branch OR explicit bump title; branch alone is NOT trusted publisher — provenance must be validated via diff
    is_python_bump = head_ref.startswith("chore/python-") or re.search(r"bump CPython pin", title, re.IGNORECASE)
    # For python bump, verify author is not strict; BUMP_PAT owner will be PR author, not github-actions[bot]
    # So we accept any author when head_ref matches chore/python-* only for subsequent diff validation, not as authorization
    if is_python_bump:
        is_dependabot = False  # Don't treat as dependabot even if author is dependabot
    # Dependency updates: parse from title or fixture dependency_updates
    dependency_updates = fixture.get("dependency_updates") or []
    # Also parse title for version bumps like "Bump aiohttp from 3.14.5 to 4.0.0"
    import re as _re
    title_updates = []
    for m in _re.finditer(r"from\s+([0-9]+\.[0-9]+\.[0-9][^\s]*)[^\n]*to\s+([0-9]+\.[0-9]+\.[0-9][^\s]*)", title, _re.IGNORECASE):
        old, new = m.group(1).strip(",."), m.group(2).strip(",.")
        title_updates.append({"old": old, "new": new, "name": "title"})
    # Also handle "3.14.8 -> 3.14.9" style
    for m in _re.finditer(r"(\d+\.\d+\.\d+)\s*->\s*(\d+\.\d+\.\d+)", title):
        old, new = m.group(1), m.group(2)
        title_updates.append({"old": old, "new": new, "name": "title"})
    all_updates = dependency_updates + title_updates
    # Classify python vs package updates
    # For dependabot, check each package update; major or ambiguous requires review
    # Must parse every authoritative diff member and compare metadata against it — title/supplied metadata alone is not authoritative
    if is_dependabot:
        # Always parse authoritative diff from file_patches (real diff), not just when supplied list is empty
        authoritative_from_diff = []
        for req_file in ["requirements.txt", "requirements-dev.txt", "requirements.in", "requirements-dev.in"]:
            patch = file_patches.get(req_file, "")
            if patch:
                parsed = parse_requirement_diff(patch)
                authoritative_from_diff.extend(parsed)
                # Also check for bad source/index directive in patch
                if "--extra-index-url" in patch or "--index-url" in patch or "--trusted-host" in patch:
                    return {"decision": "review-required", "reason": f"dependabot patch contains source directive in {req_file}", "merge": False}
            elif req_file in changed_files:
                # Changed file but no patch or truncated — missing/truncated content requires review
                return {"decision": "review-required", "reason": f"dependabot missing patch for {req_file}", "merge": False}
        # Also check .python-version patch for Python version (but dependabot shouldn't change it)
        py_patch = file_patches.get(".python-version", "")
        if py_patch and ".python-version" in changed_files:
            return {"decision": "review-required", "reason": "dependabot changes .python-version (not routine)", "merge": False}
        # If authoritative diff has changes but supplied metadata is empty/missing, that's unparseable group metadata → review
        if authoritative_from_diff and not all_updates:
            return {"decision": "review-required", "reason": "dependabot missing update metadata (unparseable)", "merge": False}
        # If diff has changes, always classify every diff member (highest risk controls), regardless of title
        if authoritative_from_diff:
            for upd in authoritative_from_diff:
                old = upd.get("old", "")
                new = upd.get("new", "")
                name = upd.get("name", "pkg")
                old = str(old).strip().lstrip("v")
                new = str(new).strip().lstrip("v")
                cls = classify_package_update(name, old, new)
                if cls.get("review_required"):
                    return {"decision": "review-required", "reason": f"dependency {name} {cls.get('reason')}", "merge": False}
            # Compare supplied metadata against authoritative diff — disagreement requires review
            if all_updates:
                # Normalize both to lower name -> (old,new)
                def norm(lst):
                    d = {}
                    for u in lst:
                        n = (u.get("name") or u.get("dependency_name") or u.get("dependencyName") or "pkg").lower()
                        o = str(u.get("old") or u.get("from") or u.get("current_version") or u.get("currentVersion") or "").strip().lstrip("v")
                        ne = str(u.get("new") or u.get("to") or u.get("updated_version") or u.get("updatedVersion") or u.get("version") or "").strip().lstrip("v")
                        d[n] = (o, ne)
                    return d
                diff_map = {k.lower(): (v["old"], v["new"]) for k, v in ((x["name"].lower(), x) for x in authoritative_from_diff)}
                supplied_map = norm(all_updates)
                # Check for missing, truncated, or mismatched
                if set(diff_map.keys()) != set(supplied_map.keys()):
                    return {"decision": "review-required", "reason": "dependabot metadata vs diff mismatch (missing/truncated)", "merge": False}
                for k, (o_sup, n_sup) in supplied_map.items():
                    o_diff, n_diff = diff_map.get(k, ("", ""))
                    if o_sup != o_diff or n_sup != n_diff:
                        return {"decision": "review-required", "reason": f"dependabot {k} title vs diff disagreement", "merge": False}
        # Build authoritative_updates as diff + supplied for remaining checks (including extra-file)
        authoritative_updates = authoritative_from_diff + list(all_updates)
        if authoritative_updates:
            for upd in authoritative_updates:
                old = upd.get("old") or upd.get("from") or upd.get("current_version") or upd.get("currentVersion") or ""
                new = upd.get("new") or upd.get("to") or upd.get("updated_version") or upd.get("updatedVersion") or upd.get("version") or ""
                name = upd.get("name") or upd.get("dependency_name") or upd.get("dependencyName") or "pkg"
                old = str(old).strip().lstrip("v")
                new = str(new).strip().lstrip("v")
                cls = classify_package_update(name, old, new)
                if cls.get("review_required"):
                    return {"decision": "review-required", "reason": f"dependency {name} {cls.get('reason')}", "merge": False}
                # Also check for bad source/index directive in patch (redundant but keep)
                for req_file in ["requirements.txt", "requirements-dev.txt"]:
                    patch = file_patches.get(req_file, "")
                    if patch and ("--extra-index-url" in patch or "--index-url" in patch or "--trusted-host" in patch):
                        return {"decision": "review-required", "reason": f"dependabot patch contains source directive in {req_file}", "merge": False}
        else:
            # No authoritative updates found but is_dependabot and no changed_files? Should not happen
            # If is_dependabot but no updates parsed and no dependency files changed, still require review for safety
            if any(f.startswith((".github/workflows/", "testing area/")) for f in changed_files):
                return {"decision": "review-required", "reason": "dependabot changes non-dependency file without metadata", "merge": False}
        if authoritative_updates:
            for upd in authoritative_updates:
                old = upd.get("old") or upd.get("from") or upd.get("current_version") or upd.get("currentVersion") or ""
                new = upd.get("new") or upd.get("to") or upd.get("updated_version") or upd.get("updatedVersion") or upd.get("version") or ""
                name = upd.get("name") or upd.get("dependency_name") or upd.get("dependencyName") or "pkg"
                # Strip leading v or whitespace
                old = str(old).strip().lstrip("v")
                new = str(new).strip().lstrip("v")
                cls = classify_package_update(name, old, new)
                if cls.get("review_required"):
                    return {"decision": "review-required", "reason": f"dependency {name} {cls.get('reason')}", "merge": False}
                # Also check for bad source/index directive in patch (e.g., --extra-index-url, --index-url)
                for req_file in ["requirements.txt", "requirements-dev.txt"]:
                    patch = file_patches.get(req_file, "")
                    if patch and ("--extra-index-url" in patch or "--index-url" in patch or "--trusted-host" in patch):
                        return {"decision": "review-required", "reason": f"dependabot patch contains source directive in {req_file}", "merge": False}
        else:
            # No authoritative updates found but is_dependabot and no changed_files? Should not happen
            # If is_dependabot but no updates parsed and no dependency files changed, still require review for safety
            if any(f.startswith((".github/workflows/", "testing area/")) for f in changed_files):
                return {"decision": "review-required", "reason": "dependabot changes non-dependency file without metadata", "merge": False}
        # Also check if changed_files includes engine/workflow beyond allowed dependabot scope
        allowed_dependabot = {".python-version", "requirements.in", "requirements.txt", "requirements-dev.in", "requirements-dev.txt", ".github/dependabot.yml"}
        for f in changed_files:
            if f not in allowed_dependabot and not f.startswith("tests/"):
                # Engine/workflow change in dependabot PR is not routine — highest risk controls
                return {"decision": "review-required", "reason": f"dependabot PR changes non-dependency file {f}", "merge": False}
    # For python bump, strict validation: only version/lock files, actual diff versions, no engine/workflow/policy changes
    if is_python_bump:
        # Strict allowlist for Python bump — only version and lock files, not engine/tests/workflows
        strict_allowed = {".python-version", "requirements.in", "requirements.txt", "requirements-dev.in", "requirements-dev.txt"}
        for f in changed_files:
            if f not in strict_allowed:
                return {"decision": "review-required", "reason": f"python bump changes non-allowed file {f}", "merge": False}
        # Truncated or missing patch must not become routine
        if fixture.get("files_truncated") or fixture.get("total_files", 0) > len(changed_files):
            return {"decision": "review-required", "reason": "files pagination incomplete — fail closed", "merge": False}
        if fixture.get("reviews_truncated") or fixture.get("total_reviews", 0) > len(reviews):
            return {"decision": "review-required", "reason": "reviews pagination incomplete — fail closed", "merge": False}
        # Need actual patch for .python-version to parse versions — do not trust title alone when dependency_updates is empty
        py_patch = file_patches.get(".python-version", "")
        if ".python-version" in changed_files and not py_patch:
            return {"decision": "review-required", "reason": "python bump missing .python-version patch (unparsed)", "merge": False}
        # Also if no file_patches at all but changed_files non-empty, treat as unparsed
        if changed_files and not file_patches:
            return {"decision": "review-required", "reason": "python bump missing patches (unparsed)", "merge": False}
        # Parse actual versions from patch — handle +/- markers and version strings
        cur_from_patch = None
        cand_from_patch = None
        if py_patch:
            for line in py_patch.splitlines():
                # Look for version in diff lines: -3.14.8 and +3.14.9, ignoring ---/+++
                if line.startswith("-") and not line.startswith("---"):
                    m = re.search(r"(\d+\.\d+\.\d+)", line)
                    if m and not cur_from_patch:
                        cur_from_patch = m.group(1)
                if line.startswith("+") and not line.startswith("+++"):
                    m = re.search(r"(\d+\.\d+\.\d+)", line)
                    if m and not cand_from_patch:
                        cand_from_patch = m.group(1)
        # Prefer patch-derived versions, fallback to fixture, then title — but never trust title alone when patch disagrees
        cur = cur_from_patch or fixture.get("current_version")
        cand = cand_from_patch or fixture.get("candidate_version")
        # Fallback to title "->" pattern only if patch missing and fixture missing
        if (not cur or not cand) and "->" in title:
            # Best-effort title fallback only. Every realistic failure here is a malformed title:
            # a missing side of the arrow (IndexError), an empty fragment (IndexError), or a
            # non-string title (AttributeError/TypeError). Suppressing them leaves cur/cand empty,
            # and the very next block turns that into review-required, so the fallthrough is the
            # fail-closed path rather than a swallowed error.
            with contextlib.suppress(IndexError, AttributeError, TypeError, ValueError):
                parts = title.split("->")
                cur_t = parts[0].split()[-1].strip().strip(",.")
                cand_t = parts[1].strip().split()[0].strip().strip(",.")
                if parse_version(cur_t) and parse_version(cand_t):
                    cur = cur or cur_t
                    cand = cand or cand_t
        if not cur or not cand:
            return {"decision": "review-required", "reason": "python version unparsed — missing patch or fixture versions", "merge": False}
        # Verify candidate version actually appears in patch when patch exists (do not trust fixture/title alone)
        if py_patch and cand not in py_patch:
            return {"decision": "review-required", "reason": "candidate version not in .python-version patch", "merge": False}
        # Classify actual version change — not reassuring title when dependency_updates is empty
        cls = classify_python_update(cur, cand)
        if cls.get("review_required"):
            # Includes major, downgrade, skipped-minor, unknown, and next-minor (medium) which needs soak/human review
            return {"decision": "review-required", "reason": f"python {cls.get('reason')}", "merge": False}
        # Also classify every changed lock dependency — hidden major/new/deleted package or bad source must be review
        for req_file in ["requirements.txt", "requirements-dev.txt", "requirements.in", "requirements-dev.in"]:
            if req_file in changed_files:
                patch = file_patches.get(req_file, "")
                if not patch:
                    # Missing or truncated patch for lock file — must not become routine
                    return {"decision": "review-required", "reason": f"python bump missing patch for {req_file} (unparseable)", "merge": False}
                # Check for bad source/index directive
                if "--extra-index-url" in patch or "--index-url" in patch or "--trusted-host" in patch:
                    return {"decision": "review-required", "reason": f"python bump patch contains source directive in {req_file}", "merge": False}
                # Parse package changes and classify each
                parsed = parse_requirement_diff(patch)
                # If patch shows changes but parsing yields nothing, it's unparseable
                # Check if patch actually contains version changes — if so, it's unparseable
                if patch.strip() and not parsed and "==" in patch:
                    return {"decision": "review-required", "reason": f"python bump unparseable lock diff in {req_file}", "merge": False}
                for upd in parsed:
                    old = upd.get("old", "")
                    new = upd.get("new", "")
                    name = upd.get("name", "pkg")
                    # Also check for new/deleted package (old or new empty)
                    if not old or not new:
                        return {"decision": "review-required", "reason": f"python bump {name} new/deleted package", "merge": False}
                    cls_pkg = classify_package_update(name, old, new)
                    if cls_pkg.get("review_required"):
                        return {"decision": "review-required", "reason": f"python bump dependency {name} {cls_pkg.get('reason')}", "merge": False}
        # Soak: missing dates mean deferred, not up-to-date or permission to bypass
        if fixture.get("deferred"):
            return {"decision": "review-required", "reason": "python soak not satisfied", "merge": False}
        # Verify actual publication provenance. Branch, title and body markers are not
        # authorization, and neither is a trusted author identity: the run and attempt that
        # validated and published this proposal have to be bound to the published head, to the
        # published bytes and to their own source head. The expected commit identity comes from
        # trusted base policy, never from the fixture or from PR content.
        if is_python_bump:
            trusted_commit = ((policy or {}).get("python") or {}).get("commit_identity")
            evidence_fixture = dict(fixture)
            if trusted_commit:
                evidence_fixture["expected_commit_identity"] = trusted_commit
            verified, reason = verify_publication_record(evidence_fixture, pr, cand)
            if not verified:
                return {
                    "decision": "review-required",
                    "reason": f"python bump publication unverified: {reason}",
                    "merge": False,
                }

    # Check evaluation
    mandatory = policy.get("checks", {}).get("mandatory") or ["maintenance-ci"]
    head_sha = fixture.get("head_sha") or pr.get("head", {}).get("sha") or ""
    pr_head_sha = pr.get("head", {}).get("sha") or head_sha
    repository = fixture.get("repository") or None
    eval_result = evaluate_checks(checks, mandatory, head_sha, pr_head_sha, repository)
    if not eval_result["ok"]:
        return {"decision": "blocked", "reason": eval_result["reason"], "merge": False}

    # Risk / review handling: if fixture says review_required, block auto-merge
    if fixture.get("review_required"):
        return {"decision": "review-required", "reason": "human review required", "merge": False}

    # Auto-merge variable check (fail closed on empty/unknown)
    auto_vars = fixture.get("auto_merge_vars", {})
    if is_dependabot:
        if auto_vars.get("AUTO_MERGE_DEPENDABOT") != "yes":
            return {"decision": "blocked", "reason": "auto-merge not enabled", "merge": False}
    elif is_python_bump:
        if auto_vars.get("AUTO_MERGE_PYTHON_BUMP") != "yes":
            return {"decision": "blocked", "reason": "auto-merge not enabled for python bump", "merge": False}
        # Check that python bump is patch only (already classified above)
    else:
        # Unknown author/type -> require human review
        return {"decision": "review-required", "reason": "unknown PR type", "merge": False}

    return {"decision": "merge", "reason": "all gates passed", "merge": True}


def load_maintenance_profile(path: str | os.PathLike = ".github/maintenance.json") -> dict[str, Any]:
    """Load and validate maintenance.json."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    # Basic schema check
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {data.get('schema_version')}")
    return data


def main(argv: list[str] | None = None) -> int:
    """CLI for fixture-based decision mode. Never does real GitHub writes."""
    import argparse
    parser = argparse.ArgumentParser(description="Maintenance decision helper (fixture mode only)")
    parser.add_argument("--fixture", required=True, help="Path to fixture JSON")
    parser.add_argument("--policy", help="Path to trusted maintenance.json")
    parser.add_argument("--output", help="Output JSON path")
    args = parser.parse_args(argv)
    decision = decision_from_fixture(args.fixture, args.policy)
    out = json.dumps(decision, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
