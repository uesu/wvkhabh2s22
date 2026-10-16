#!/usr/bin/env python3
"""
Versioned JSON-to-Markdown renderer; no source requests, secrets, state mutations or posting.
Reads a run-report JSON (produced by monitor workflows) and renders GitHub Step Summary markdown.
Handles redaction, partial runs, and persistence status.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1

# Fields that must never be logged in plain text
REDACT_PATTERNS = [
    re.compile(r"https://discord\.com/api/webhooks/[^\s\"']+", re.IGNORECASE),
    re.compile(r"discord\.com/api/webhooks/[^\s\"']+", re.IGNORECASE),
    re.compile(r"Authorization\s*:\s*[^\n]+", re.IGNORECASE),
    re.compile(r"token=[^\s&\"']+", re.IGNORECASE),
    re.compile(r"BUMP_PAT[^\n]*", re.IGNORECASE),
    re.compile(r"REDDIT_FEED_TOKEN[^\n]*", re.IGNORECASE),
    re.compile(r"NITTER_RSS_TOKEN[^\n]*", re.IGNORECASE),
]

TOKEN_QUERY_RE = re.compile(r"([?&]token=)[^&\s\"']+", re.IGNORECASE)
WEBHOOK_RE = re.compile(r"https://discord\.com/api/webhooks/[^\s\"']+", re.IGNORECASE)


def redact_text(text: str) -> str:
    """Redact webhook tokens, Authorization, cookie values, token-bearing URLs and secret values."""
    if not isinstance(text, str):
        text = str(text)
    # Redact webhook URLs
    text = WEBHOOK_RE.sub("[REDACTED WEBHOOK]", text)
    text = TOKEN_QUERY_RE.sub(r"\1[REDACTED]", text)
    # Generic token patterns
    for pat in REDACT_PATTERNS:
        # Use sub with placeholder, avoid leaking length
        text = pat.sub("[REDACTED]", text)
    # Catch-all env dump redaction: if text looks like env dump, redact lines with SECRET/TOKEN/WEBHOOK
    lines = []
    for line in text.splitlines():
        # If line contains =, redact value part after =
        if any(k in line.upper() for k in ("SECRET", "TOKEN", "WEBHOOK", "AUTHORIZATION", "COOKIE")) and "=" in line:
            key, _ = line.split("=", 1)
            lines.append(f"{key}=[REDACTED]")
            continue
        lines.append(line)
    return "\n".join(lines)


def load_report(path: os.PathLike | str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data


def render_markdown(report: dict, persistence: dict | None = None) -> str:
    """
    Render report JSON to markdown. Report must contain:
    schema_version, workflow mode, actual checked-out code SHA, actual Python version,
    dependency-lock digest, runner image; execution result; targets; posted/edited/skipped/held;
    hosts; local save, commit and remote push statuses.
    """
    schema = report.get("schema_version", SCHEMA_VERSION)
    mode = report.get("workflow_mode") or report.get("mode") or "unknown"
    sha = report.get("code_sha") or report.get("checked_out_sha") or "unavailable"
    py_ver = report.get("python_version") or report.get("python") or "unavailable"
    lock_digest = report.get("lock_digest") or report.get("dependency_lock_digest") or "unavailable"
    runner = report.get("runner_image") or report.get("runner") or "unavailable"
    result = report.get("execution_result") or report.get("result") or "unavailable"
    # Map result to success/failure wording
    result_label = {"completed": "success", "degraded": "degraded", "failed": "failure", "not-run": "not-run"}.get(result, result)

    # Counters: configured/attempted/usable unique accounts separately from HTTP requests, feeds, posts
    configured = report.get("configured_accounts") or report.get("configured_targets") or 0
    attempted = report.get("attempted_accounts") or report.get("attempted") or 0
    usable = report.get("usable_accounts") or report.get("usable") or 0
    requests = report.get("http_requests") or report.get("request_count") or 0
    feeds_parsed = report.get("feeds_parsed") or report.get("feeds") or 0
    successful_responses = report.get("successful_responses") or 0
    posted = report.get("posted") or 0
    edited = report.get("edited") or 0
    skipped = report.get("skipped") or 0
    held = report.get("held") or 0
    # Reasons
    reasons = report.get("reason_counters") or report.get("reason_counts") or {}
    hosts_attempted = report.get("hosts_attempted") or []
    hosts_selected = report.get("hosts_selected") or []
    warnings = report.get("warnings") or []
    errors = report.get("errors") or []

    # Persistence
    if persistence is None:
        persistence = report.get("persistence") or {}
    local_save = persistence.get("local_save") or report.get("local_save") or "unavailable"
    commit = persistence.get("commit") or report.get("commit") or "unavailable"
    push = persistence.get("push") or report.get("push") or persistence.get("remote_push") or report.get("remote_push") or "unavailable"

    # Durations
    setup_dur = report.get("setup_duration_s") or report.get("setup_duration") or "n/a"
    monitor_dur = report.get("monitor_duration_s") or report.get("monitor_duration") or "n/a"
    persist_dur = report.get("persistence_duration_s") or report.get("persistence_duration") or "n/a"

    lines: list[str] = []
    lines.append(f"## {mode.title()} Monitor — run summary")
    lines.append("")
    lines.append(f"**Schema:** {schema}  •  **Result:** {result_label}  •  **Mode:** {mode}")
    lines.append(f"**Code SHA:** `{sha[:12] if len(sha) >= 12 else sha}`  •  **Python:** {py_ver}  •  **Lock:** `{str(lock_digest)[:12]}`  •  **Runner:** {runner}")
    lines.append("")
    lines.append(f"**Durations:** setup {setup_dur}s • monitor {monitor_dur}s • persistence {persist_dur}s")
    lines.append("")
    lines.append("### Targets")
    lines.append(f"- configured accounts: {configured}")
    lines.append(f"- attempted: {attempted}  •  usable: {usable}")
    lines.append(f"- http requests: {requests} (unique accounts counted separately)")
    lines.append(f"- feeds parsed: {feeds_parsed}  •  successful http responses: {successful_responses}")
    lines.append("")
    lines.append("### Outcomes")
    lines.append(f"- posted: {posted}  •  edited: {edited}  •  skipped: {skipped}  •  held: {held}")
    if reasons:
        lines.append("- reasons: " + ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items())))
    lines.append("")
    lines.append("### Hosts")
    lines.append(f"- attempted: {', '.join(hosts_attempted) if hosts_attempted else 'none'}")
    lines.append(f"- selected: {', '.join(hosts_selected) if hosts_selected else 'none'}")
    lines.append("- challenge/invalid/timeout/rate-limit kept distinct in report")
    lines.append("")
    lines.append("### State Persistence")
    # Distinguish save vs push failure
    lines.append(f"- local save: {local_save}")
    lines.append(f"- commit: {commit}")
    lines.append(f"- remote push: {push}")
    if push == "failed" and local_save == "success":
        lines.append("  > push failure after local save — dedup cache not persisted remotely")
    lines.append("")
    if warnings:
        lines.append("### Warnings")
        for w in warnings[:20]:  # bounded
            lines.append(f"- {redact_text(str(w))}")
        lines.append("")
    if errors:
        lines.append("### Errors")
        for e in errors[:20]:
            lines.append(f"- {redact_text(str(e))}")
        lines.append("")
    lines.append("_Summary rendered from structured report; no source requests, no secrets, no state mutation._")
    raw = "\n".join(lines)
    # Final redaction pass on whole markdown
    return redact_text(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render run summary JSON to markdown")
    parser.add_argument("--report", required=True, help="Path to report JSON")
    parser.add_argument("--persistence", help="Path to persistence JSON (optional, merged if separate)")
    parser.add_argument("--output", help="Output markdown path (default stdout)")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.report):
        # Missing report after early failure is unavailable, not fake zero activity
        placeholder = {
            "schema_version": SCHEMA_VERSION,
            "workflow_mode": "unknown",
            "code_sha": "unavailable",
            "python_version": "unavailable",
            "lock_digest": "unavailable",
            "runner_image": "unavailable",
            "execution_result": "not-run",
            "configured_accounts": 0,
            "attempted_accounts": 0,
            "usable_accounts": 0,
            "http_requests": 0,
            "posted": 0,
            "reason_counters": {},
            "hosts_attempted": [],
            "persistence": {"local_save": "unavailable", "commit": "unavailable", "push": "unavailable"},
            "warnings": [f"report unavailable: {args.report} not found"],
        }
        md = render_markdown(placeholder)
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
        else:
            print(md)
        return 0

    try:
        report = load_report(args.report)
    except (OSError, ValueError) as e:
        print(f"Failed to load report: {e}", file=sys.stderr)
        return 1

    persistence = None
    if args.persistence and os.path.isfile(args.persistence):
        try:
            persistence = load_report(args.persistence)
        except (OSError, ValueError):
            persistence = None

    # Do not measure overhead as performance claim; just render once
    try:
        md = render_markdown(report, persistence)
    except (KeyError, IndexError, AttributeError, TypeError, ValueError) as e:
        # Rendering walks an arbitrary report dict, so the realistic failures are missing or
        # wrongly typed fields. All of them stay an explicit error exit rather than a traceback.
        print(f"Failed to render report: {e}", file=sys.stderr)
        return 1

    # Failure to write optional telemetry must not resend a card or mask original failure
    try:
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
            # Also append to GITHUB_STEP_SUMMARY if present
            ghs = os.getenv("GITHUB_STEP_SUMMARY")
            if ghs:
                with open(ghs, "a", encoding="utf-8") as fh:
                    fh.write(md + "\n")
        else:
            print(md)
            ghs = os.getenv("GITHUB_STEP_SUMMARY")
            if ghs:
                with open(ghs, "a", encoding="utf-8") as fh:
                    fh.write(md + "\n")
    except (OSError, TypeError, ValueError) as e:
        # Telemetry write failure must not fail the overall job with exception that masks original
        print(f"Warning: failed to write summary: {e}", file=sys.stderr)
        print(md)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
