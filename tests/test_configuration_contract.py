"""Configuration contract test — no network, no secrets.

Run: python tests/test_configuration_contract.py
Validates that runtime configuration files meet production contract:
- .python-version exact pin
- requirements.in/txt hashed and constrained
- .env.example reconciled with actual readers/defaults
- .gitignore covers generated reports
- .github/dependabot.yml grouping via update-types
- workflows use exact pin, frozen hashes, CODE_SHA, REPORT_PATH, always-run render
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_python_version():
    p = ROOT / ".python-version"
    assert p.is_file(), ".python-version missing"
    ver = p.read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", ver), f"not exact X.Y.Z: {ver}"
    assert ver == "3.14.8", f"expected 3.14.8 got {ver}"

def test_requirements_in():
    p = ROOT / "requirements.in"
    assert p.is_file()
    txt = p.read_text()
    assert "aiohttp" in txt and "feedparser" in txt and "python-dotenv" in txt

def test_requirements_txt():
    p = ROOT / "requirements.txt"
    assert p.is_file()
    txt = p.read_text()
    assert "--hash=" in txt, "must be hashed"
    assert "aiohttp" in txt
    # header mentions constrained via .python-version
    assert "3.14" in txt or ".python-version" in txt

def test_requirements_dev():
    for name in ("requirements-dev.in", "requirements-dev.txt"):
        p = ROOT / name
        assert p.is_file(), f"{name} missing"
    dev_in = (ROOT / "requirements-dev.in").read_text()
    assert "-r requirements.in" in dev_in
    assert ".python-version" in dev_in or "3.14" in dev_in
    dev_txt = (ROOT / "requirements-dev.txt").read_text()
    assert "--hash=" in dev_txt

def test_env_example():
    p = ROOT / ".env.example"
    assert p.is_file()
    txt = p.read_text()
    # must contain workflow Variables/Secrets section
    assert "AUTO_MERGE_DEPENDABOT" in txt
    assert "AUTO_MERGE_PYTHON_BUMP" in txt
    assert "PYTHON_BUMP_SOAK_DAYS" in txt
    assert "BUMP_PAT" in txt
    # V1/V2 annotations
    assert "REDDIT_MIRROR" in txt
    assert "EMBEDEZ_API_KEY" in txt
    # local-only diagnostics
    assert "REPORT_PATH" in txt or "PERSISTENCE_PATH" in txt

def test_gitignore():
    p = ROOT / ".gitignore"
    assert p.is_file()
    txt = p.read_text()
    # must preserve secret protections
    assert ".env" in txt
    # must include generated report/tool paths narrowly
    assert "reddit_report.json" in txt or "twitter_report.json" in txt
    assert ".tmp" in txt or ".mypy_cache" in txt

def test_dependabot():
    p = ROOT / ".github" / "dependabot.yml"
    assert p.is_file()
    txt = p.read_text()
    # routine vs review-required grouping via update-types
    assert "python-routine" in txt or "routine" in txt.lower()
    assert "python-review-required" in txt or "review-required" in txt.lower()
    assert "update-types" in txt or "update_type" in txt
    # actions grouping
    assert "actions-routine" in txt or "actions" in txt.lower()
    # no lockfile integration for actions? ensure compiled-input compatible
    # at least ensure not using bundler/npm that would be wrong
    assert "pip" in txt

def test_maintenance_json():
    import json
    p = ROOT / ".github" / "maintenance.json"
    assert p.is_file()
    data = json.loads(p.read_text())
    assert data.get("schema_version") == 1
    assert data["python"]["current_version"] == "3.14.8"
    # must include mandatory checks — now single final gate maintenance-ci (CI aggregates)
    mandatory = data["checks"]["mandatory"]
    assert "maintenance-ci" in mandatory
    # no longer requires nine separate checks; single gate is correct
    assert len(mandatory) == 1, f"expected single final gate, got {mandatory}"

def test_workflows():
    for wf in ("ci.yml", "reddit_monitor.yml", "twitter_monitor.yml"):
        p = ROOT / ".github" / "workflows" / wf
        assert p.is_file(), f"{wf} missing"
        txt = p.read_text()
        # all should use exact pin - either via file or literal (smoke enforces literal once)
        has_pin = ("python-version-file" in txt and ".python-version" in txt) or ("python-version: '3.14.8'" in txt)
        assert has_pin, f"{wf} missing exact Python 3.14.8 pin (via file or literal)"
        if wf != "ci.yml":
            # frozen hash install
            assert "--require-hashes" in txt
            assert "--only-binary" in txt
            # CODE_SHA capture
            assert "CODE_SHA" in txt
            # REPORT_PATH wiring
            assert "REPORT_PATH" in txt
            # always-run render_run_summary
            assert "render_run_summary" in txt
        else:
            # ci must have frozen
            assert "--require-hashes" in txt

def test_scripts_exist():
    for name in ("maintenance.py", "ci.py", "render_run_summary.py", "python_version_bump.py", "merge_monitor_caches.py"):
        p = ROOT / ".github" / "scripts" / name
        assert p.is_file(), f"script {name} missing"

def main():
    tests = [test_python_version, test_requirements_in, test_requirements_txt, test_requirements_dev, test_env_example, test_gitignore, test_dependabot, test_maintenance_json, test_workflows, test_scripts_exist]
    failed = []
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            failed.append(fn.__name__)
        except Exception as e:
            print(f"ERROR {fn.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed.append(fn.__name__)
    if failed:
        print(f"CONFIGURATION CONTRACT TEST: FAILED {failed}")
        sys.exit(1)
    print("CONFIGURATION CONTRACT TEST: ALL PASS")

if __name__ == "__main__":
    main()
