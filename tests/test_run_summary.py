"""Offline run-summary renderer test — no network, no secrets.

Run: python tests/test_run_summary.py
Validates .github/scripts/render_run_summary.py
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "scripts"))

import render_run_summary  # type: ignore

def test_redaction_keeps_both_catch_all_branches():
    """The KEY=value catch-all and its fall-through must behave exactly as before the lint cleanup.

    Two nested conditions in this loop were merged into one. The inner branch ends in `continue`
    and the outer fall-through appends the line unchanged, so the merge is only equivalent if both
    paths are pinned. A secret-keyword line with no "=" is the fall-through: it must survive
    verbatim rather than be dropped, rewritten, or half-redacted.
    """
    text = "\n".join([
        "COOKIE=chocolate-chip",           # keyword + "=" -> value redacted, key kept
        "AUTHORIZATION_COUNT=3",           # keyword + "=" -> value redacted
        "SECRETSAUCE is tasty",            # keyword, no "=" -> fall-through, unchanged
        "posted=12",                       # no keyword -> unchanged
        "plain line",                      # no keyword, no "=" -> unchanged
        "BUMP_PAT=ghp_supersecretvalue",   # token pattern redacts the whole line first
        "WEBHOOK_URL=https://discord.com/api/webhooks/123/abc",
        "NITTER_RSS_TOKEN: tokensecret",
    ])
    red = render_run_summary.redact_text(text)
    assert red == "\n".join([
        "COOKIE=[REDACTED]",
        "AUTHORIZATION_COUNT=[REDACTED]",
        "SECRETSAUCE is tasty",
        "posted=12",
        "plain line",
        "[REDACTED]",
        "WEBHOOK_URL=[REDACTED]",
        "[REDACTED]",
    ]), red
    for secret in ("chocolate-chip", "ghp_supersecretvalue", "tokensecret", "webhooks/123/abc"):
        assert secret not in red, f"{secret} leaked through redaction: {red}"


def test_redaction():
    text = "https://discord.com/api/webhooks/123/abc token=secret Authorization: Bearer xyz"
    red = render_run_summary.redact_text(text)
    assert "discord.com/api/webhooks" not in red or "[REDACTED" in red
    assert "secret" not in red or "[REDACTED" in red

def test_render_minimal():
    report = {
        "schema_version": 1,
        "workflow_mode": "reddit",
        "code_sha": "abc123def456abc123def456abc123def456abcd",
        "python_version": "3.14.8",
        "lock_digest": "deadbeef12345678",
        "runner_image": "ubuntu-latest",
        "execution_result": "completed",
        "configured_accounts": 6,
        "attempted_accounts": 6,
        "usable_accounts": 6,
        "http_requests": 12,
        "feeds_parsed": 6,
        "successful_responses": 10,
        "posted": 2,
        "edited": 0,
        "skipped": 1,
        "held": 0,
        "reason_counters": {"posted": 2, "skipped_removed": 1},
        "hosts_attempted": ["old.reddit.com", "vxreddit.com"],
        "hosts_selected": ["vxreddit.com"],
        "persistence": {"local_save": "success", "commit": "success", "push": "success"},
        "warnings": [],
        "errors": [],
        "setup_duration_s": 5,
        "monitor_duration_s": 40,
        "persistence_duration_s": 3,
    }
    md = render_run_summary.render_markdown(report)
    assert "Reddit" in md
    assert "completed" in md.lower() or "success" in md.lower()
    assert "3.14.8" in md
    assert "posted: 2" in md or "posted" in md.lower()

def test_render_missing_file():
    with tempfile.TemporaryDirectory() as td:
        missing = os.path.join(td, "nope.json")
        # render via CLI should produce placeholder not crash
        rc = render_run_summary.main(["--report", missing, "--output", os.path.join(td, "out.md")])
        assert rc == 0
        out = Path(os.path.join(td, "out.md")).read_text()
        assert "not-run" in out or "unavailable" in out

def test_load_and_cli():
    report = {
        "schema_version": 1,
        "workflow_mode": "twitter",
        "code_sha": "fff",
        "python_version": "3.14.8",
        "lock_digest": "abc",
        "runner_image": "ubuntu",
        "execution_result": "degraded",
        "configured_accounts": 5,
        "attempted_accounts": 5,
        "usable_accounts": 4,
        "http_requests": 10,
        "feeds_parsed": 5,
        "successful_responses": 4,
        "posted": 1,
        "reason_counters": {"partial_run": 1},
        "hosts_attempted": ["nitter.example"],
        "hosts_selected": [],
        "persistence": {"local_save": "success", "commit": "failed", "push": "failed"},
        "warnings": ["something"],
        "errors": [],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
        json.dump(report, fh)
        fname = fh.name
    try:
        loaded = render_run_summary.load_report(fname)
        assert loaded["workflow_mode"] == "twitter"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as out:
            outname = out.name
        rc = render_run_summary.main(["--report", fname, "--output", outname])
        assert rc == 0
        md = Path(outname).read_text()
        assert "Twitter" in md
        assert "degraded" in md.lower() or "Degraded" in md
        os.unlink(outname)
    finally:
        os.unlink(fname)

def test_actual_engine_report_count_precedes_classification():
    """Execute each engine's real final report-construction block, without I/O.

    This tests source order and the actual report dict, not a duplicate classifier.
    It does not dispatch main, call a source, post to Discord or save caches.
    """
    from types import SimpleNamespace

    for filename, current, initial in (
        ("reddit_main_v3.py", "posted", "posted_at_start"),
        ("twitter_v3.py", "posted_urls", "posted_urls_at_start"),
    ):
        tree = ast.parse((ROOT / "testing area" / filename).read_text())
        main = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "main")
        # The final outer try assigns the real report dict before serializing it.
        block = next(
            n for n in reversed(main.body)
            if isinstance(n, ast.Try)
            and any(isinstance(stmt, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "report" for t in stmt.targets)
                    for stmt in n.body)
        )
        report_index = next(
            i for i, stmt in enumerate(block.body)
            if isinstance(stmt, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "report" for t in stmt.targets)
        )
        code = compile(ast.Module(body=block.body[:report_index + 1], type_ignores=[]), filename, "exec")
        for delivered, errors, warnings, started, expected in (
            (1, ["source failed"], [], 20.0, "degraded"),
            (0, ["source failed"], [], 20.0, "failed"),
            (0, [], ["source warning"], 20.0, "degraded"),
            (0, [], [], 20.0, "success"),
            (0, [], [], None, "not-run"),
        ):
            ns = {
                "os": os, "_time": SimpleNamespace(time=lambda: 30.0),
                "_setup_start": 10.0, "_monitor_start": started,
                "_attempted_accounts": 1 if started else 0, "_usable_accounts": 0,
                "_errors": errors, "_warnings": warnings, "_execution_result": "success",
                "_http_requests": 0, "_feeds_parsed": 0, "_successful_responses": 0,
                "_skipped": 0, "_held": 0, "_reason_counters": {},
                "_hosts_attempted": [], "_hosts_selected": [],
                "SUBREDDITS": [], "ACCOUNTS": [], "pending": {},
                current: {"old", *(["new"] if delivered else [])}, initial: {"old"},
            }
            exec(code, ns)
            report = ns["report"]
            assert report["posted"] == delivered, (filename, report)
            assert report["execution_result"] == expected, (filename, report)


def main():
    tests = [test_actual_engine_report_count_precedes_classification, test_redaction, test_redaction_keeps_both_catch_all_branches, test_render_minimal, test_render_missing_file, test_load_and_cli]
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
        print(f"RUN SUMMARY TEST: FAILED {failed}")
        sys.exit(1)
    print("RUN SUMMARY TEST: ALL PASS")

if __name__ == "__main__":
    main()
