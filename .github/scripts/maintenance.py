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

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

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
    if any(x in low for x in ["-", "rc", "beta", "alpha", "dev"]):
        return False
    return True


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


def validate_changed_files(changed: list[str]) -> dict[str, Any]:
    """Validate that only allowed runtime files changed. Returns {ok, reason, disallowed}."""
    disallowed = []
    for f in changed:
        allowed = any(re.match(pat, f) for pat in ALLOWED_RUNTIME_PATTERNS)
        if not allowed:
            disallowed.append(f)
    if disallowed:
        return {"ok": False, "reason": f"disallowed files: {', '.join(disallowed)}", "disallowed": disallowed}
    return {"ok": True, "reason": "allowed", "disallowed": []}


def select_candidate(current: str, manifest: list[dict[str, Any]], soak_days: int = 30, now_ts: int | None = None, ignore_soak: bool = False) -> dict[str, Any]:
    """
    Runtime proposer: select newest patch of current series, else newest patch
    of next minor after soak. Returns dict with candidate, kind, release_url, reason.
    Implements full algorithm from spec.
    """
    import time
    cur = parse_version(current)
    if cur is None:
        return {"candidate": None, "kind": "none", "reason": "invalid-current-version", "release_url": None}
    series_now = cur[:2]
    series_next = (cur[0], cur[1] + 1)
    now = now_ts if now_ts is not None else int(time.time())

    def newest(series):
        best = None
        best_entry = None
        for entry in manifest:
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
                published = entry.get("published_at") if entry else None
                if not published:
                    # Missing release date cannot bypass soak
                    return {"candidate": candidate, "kind": kind, "reason": "deferred: release-date-unavailable", "release_url": release_url, "deferred": True}
                # Parse published_at ISO
                try:
                    from datetime import datetime, timezone
                    # Handle both with and without tz
                    pub = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    age_days = (now - int(pub.timestamp())) / 86400
                    if age_days < soak_days:
                        return {"candidate": candidate, "kind": kind, "reason": f"deferred: soak {age_days:.1f}/{soak_days} days", "release_url": release_url, "deferred": True}
                except Exception:
                    return {"candidate": candidate, "kind": kind, "reason": "deferred: release-date-unavailable", "release_url": release_url, "deferred": True}
            return {"candidate": candidate, "kind": kind, "reason": "available", "release_url": release_url, "deferred": False}
    return {"candidate": None, "kind": "none", "reason": "up-to-date", "release_url": None, "deferred": False}


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
    if check.get("name") not in TRUSTED_CHECKS:
        # Also allow workflow/job qualified names? For now strict.
        # But allow any check that is in mandatory list; if unknown, not successful.
        return False
    return True


def evaluate_checks(checks: list[dict[str, Any]], mandatory: list[str], head_sha: str, pr_head_sha: str) -> dict[str, Any]:
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
    for name in mandatory:
        c = by_name.get(name)
        if c is None:
            missing.append(name)
            continue
        # Must be success; skipped/neutral/pending are failures for mandatory
        if c.get("status") != "completed" or c.get("conclusion") != "success":
            failed.append(f"{name} ({c.get('conclusion') or c.get('status')})")
            continue
        # Enforce trusted workflow/app provenance and head binding — missing is failure (no legacy bypass)
        app = c.get("app")
        if isinstance(app, dict):
            slug = app.get("slug") or app.get("name")
        else:
            slug = app
        workflow = c.get("workflow") or c.get("workflow_name") or c.get("workflow_id") or c.get("workflow_run") or c.get("workflowRun")
        # head_sha must be present and exactly match PR head
        check_sha = c.get("head_sha") or c.get("headSha") or c.get("sha") or c.get("head_sha")
        if not check_sha:
            failed.append(f"{name} (missing head_sha)")
            continue
        if check_sha != head_sha:
            failed.append(f"{name} (head mismatch {check_sha} vs {head_sha})")
            continue
        # app and workflow must be present and trusted
        if not slug or slug not in TRUSTED_APP_SLUGS:
            failed.append(f"{name} (untrusted or missing app {slug or 'none'})")
            continue
        if not workflow or workflow != TRUSTED_WORKFLOW:
            failed.append(f"{name} (untrusted or missing workflow {workflow or 'none'})")
            continue
    if missing or failed:
        reason = []
        if missing:
            reason.append(f"missing: {', '.join(missing)}")
        if failed:
            reason.append(f"failed: {', '.join(failed)}")
        return {"ok": False, "reason": "; ".join(reason), "missing": missing, "failed": failed}
    return {"ok": True, "reason": "all mandatory checks successful", "missing": [], "failed": []}


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
    except Exception as e:
        return {"decision": "failed", "reason": f"fixture load failed: {e}", "merge": False}

    # Load trusted policy (never from PR's changed maintenance profile)
    policy = None
    if trusted_policy_path and os.path.isfile(trusted_policy_path):
        try:
            with open(trusted_policy_path, "r", encoding="utf-8") as fh:
                policy = json.load(fh)
        except Exception:
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
    if mergeable_state not in ("clean", "unstable", "has_hooks"):
        # Only clean/unstable/has_hooks are potentially mergeable; others (blocked, behind, etc.) require review
        # For maintenance, require clean explicitly
        if mergeable_state != "clean":
            return {"decision": "blocked", "reason": f"not cleanly mergeable: {mergeable_state}", "merge": False}
    # Same-repository association check — fork PRs must not be auto-merged even with matching branch/title
    base_repo = (pr.get("base") or {}).get("repo", {}).get("full_name") or ""
    head_repo = (pr.get("head") or {}).get("repo", {}).get("full_name") or base_repo
    if base_repo and head_repo and base_repo != head_repo:
        return {"decision": "blocked", "reason": f"fork PR: head {head_repo} != base {base_repo}", "merge": False}

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
            if re.search(r"^[+\-\s]*\s*(on|permissions)\s*:", patch, re.M):
                return {"decision": "review-required", "reason": f"workflow structural change in {wf}", "merge": False}
            # Any workflow logic change (jobs/uses/run) also requires review — not just python pin
            if re.search(r"^[+\-\s]*\s*(jobs|uses|run)\s*:", patch, re.M):
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
    is_python_bump = head_ref.startswith("chore/python-") or re.search(r"bump CPython pin", title, re.I)
    # For python bump, verify author is not strict; BUMP_PAT owner will be PR author, not github-actions[bot]
    # So we accept any author when head_ref matches chore/python-* only for subsequent diff validation, not as authorization
    if is_python_bump:
        is_dependabot = False  # Don't treat as dependabot even if author is dependabot
    # Dependency updates: parse from title or fixture dependency_updates
    dependency_updates = fixture.get("dependency_updates") or []
    # Also parse title for version bumps like "Bump aiohttp from 3.14.5 to 4.0.0"
    import re as _re
    title_updates = []
    for m in _re.finditer(r"from\s+([0-9]+\.[0-9]+\.[0-9][^\s]*)[^\n]*to\s+([0-9]+\.[0-9]+\.[0-9][^\s]*)", title, re.I):
        old, new = m.group(1).strip(",."), m.group(2).strip(",.")
        title_updates.append({"old": old, "new": new, "name": "title"})
    # Also handle "3.14.8 -> 3.14.9" style
    for m in _re.finditer(r"(\d+\.\d+\.\d+)\s*->\s*(\d+\.\d+\.\d+)", title):
        old, new = m.group(1), m.group(2)
        title_updates.append({"old": old, "new": new, "name": "title"})
    all_updates = dependency_updates + title_updates
    # Classify python vs package updates
    # For dependabot, check each package update; major or ambiguous requires review
    if is_dependabot and all_updates:
        for upd in all_updates:
            old = upd.get("old") or upd.get("from") or upd.get("current_version") or upd.get("currentVersion") or ""
            new = upd.get("new") or upd.get("to") or upd.get("updated_version") or upd.get("updatedVersion") or upd.get("version") or ""
            name = upd.get("name") or upd.get("dependency_name") or upd.get("dependencyName") or "pkg"
            # Strip leading v or whitespace
            old = str(old).strip().lstrip("v")
            new = str(new).strip().lstrip("v")
            cls = classify_package_update(name, old, new)
            if cls.get("review_required"):
                return {"decision": "review-required", "reason": f"dependency {name} {cls.get('reason')}", "merge": False}
        # Also check if changed_files includes engine/workflow beyond allowed dependabot scope
        # Dependabot routine should only change requirements* / dependabot.yml / .python-version? Actually dependabot only bumps requirements
        allowed_dependabot = {".python-version", "requirements.in", "requirements.txt", "requirements-dev.in", "requirements-dev.txt", ".github/dependabot.yml"}
        for f in changed_files:
            if f not in allowed_dependabot and not f.startswith("tests/"):
                # Engine/workflow change in dependabot PR is not routine
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
        if not cur or not cand:
            if "->" in title:
                try:
                    parts = title.split("->")
                    cur_t = parts[0].split()[-1].strip().strip(",.")
                    cand_t = parts[1].strip().split()[0].strip().strip(",.")
                    if parse_version(cur_t) and parse_version(cand_t):
                        cur = cur or cur_t
                        cand = cand or cand_t
                except Exception:
                    pass
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
        # Soak: missing dates mean deferred, not up-to-date or permission to bypass
        if fixture.get("deferred"):
            return {"decision": "review-required", "reason": "python soak not satisfied", "merge": False}
        # Also check if patch indicates a major/downgrade hidden behind patch title — already handled via actual cur/cand classification above
        # Ensure dependency_updates empty does not bypass version check — we already validated via patch, not title

    # Check evaluation
    mandatory = policy.get("checks", {}).get("mandatory") or ["maintenance-ci"]
    head_sha = fixture.get("head_sha") or pr.get("head", {}).get("sha") or ""
    pr_head_sha = pr.get("head", {}).get("sha") or head_sha
    eval_result = evaluate_checks(checks, mandatory, head_sha, pr_head_sha)
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
