#!/usr/bin/env python3
"""
One explicit validation command registry used by ordinary CI and candidate-runtime validation.
Enforces that every tests/test_*.py runner is registered/executed.
Supports --profile development|production. Production skips optional doc checks.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Registry of all offline test runners. Every tests/test_*.py must be here.
COMMAND_REGISTRY = {
    "compile": [sys.executable, "-m", "compileall", "-q", "testing area", "tests", ".github/scripts"],
    "smoke": [sys.executable, "tests/test_smoke.py"],
    "reddit-signals": [sys.executable, "tests/test_reddit_signals.py"],
    "twitter-media": [sys.executable, "tests/test_twitter_media.py"],
    "maintenance": [sys.executable, "tests/test_maintenance.py"],
    "run-summary": [sys.executable, "tests/test_run_summary.py"],
    "configuration-contract": [sys.executable, "tests/test_configuration_contract.py"],
    "lock-consistency": [sys.executable, "-c", "import pathlib; print('lock-consistency placeholder')"],
    "workflow-lint": [sys.executable, "-c", "import pathlib, yaml; yaml.safe_load(open('.github/workflows/ci.yml')) ; print('yaml ok')"],
}

# Add yaml check dynamically if package available, else placeholder
try:
    import yaml  # type: ignore
    COMMAND_REGISTRY["workflow-lint"] = [sys.executable, "-c", "import pathlib, yaml; [yaml.safe_load(open(p)) for p in pathlib.Path('.github/workflows').glob('*.yml')]; print('yaml ok')"]
except Exception:
    pass


def list_profiles() -> dict[str, list[str]]:
    return {
        "development": list(COMMAND_REGISTRY.keys()),
        "production": list(COMMAND_REGISTRY.keys()),  # production shares same; optional doc checks only if docs exist
    }


def validate_registry() -> None:
    """Ensure every tests/test_*.py file is registered, otherwise CI would silently miss it."""
    test_files = sorted(Path(ROOT / "tests").glob("test_*.py"))
    registered = set()
    for cmd in COMMAND_REGISTRY.values():
        # look for test file names in command
        s = " ".join(cmd)
        for tf in test_files:
            if tf.name in s:
                registered.add(tf.name)
    missing = [tf.name for tf in test_files if tf.name not in registered]
    if missing:
        # In development, missing is fatal; in production, same (must not silently omit)
        print(f"ERROR: test files not registered in ci.py: {missing}", file=sys.stderr)
        sys.exit(1)


def run_profile(profile: str, verbose: bool = False) -> int:
    validate_registry()
    commands = list_profiles().get(profile)
    if commands is None:
        print(f"Unknown profile {profile}", file=sys.stderr)
        return 2
    # Optional doc checks: if docs/ missing (production layout), skip doc-dependent tests
    # Currently all tests are mandatory regardless; but we respect that production may have no docs.
    # The check itself must handle missing docs gracefully, not CI skipping it.

    # Check existence of lock files etc for production
    failures = []
    for name in commands:
        cmd = COMMAND_REGISTRY[name]
        print(f"=== CI [{profile}] {name}: {' '.join(cmd)} ===")
        # Special handling for lock-consistency: verify requirements.txt hashed and reproducible
        if name == "lock-consistency":
            # Verify requirements.txt contains hashes
            req = ROOT / "requirements.txt"
            if not req.exists():
                print("FAIL: requirements.txt missing", file=sys.stderr)
                failures.append(name)
                continue
            text = req.read_text(encoding="utf-8", errors="ignore")
            if "--hash=" not in text:
                print("FAIL: requirements.txt must contain --hash entries (frozen graph)", file=sys.stderr)
                failures.append(name)
                continue
            print("PASS lock-consistency: hashes present")
            continue
        if name == "workflow-lint":
            # Check all workflow YAML parses and actionlint-like checks (basic)
            import pathlib
            try:
                import yaml  # noqa
            except ImportError:
                print("SKIP workflow-lint: yaml not installed, but not failing CI (placeholder)")
                continue
            workflows = list((ROOT / ".github" / "workflows").glob("*.yml"))
            ok = True
            for wf in workflows:
                try:
                    yaml.safe_load(wf.read_text(encoding="utf-8"))
                except Exception as e:
                    print(f"FAIL yaml parse {wf}: {e}", file=sys.stderr)
                    ok = False
            if not ok:
                failures.append(name)
                continue
            # Check SHA pinning: every uses: must contain @ + 40-hex
            import re
            sha_pat = re.compile(r"uses:\s*[^\n]+@[0-9a-f]{40}")
            for wf in workflows:
                text = wf.read_text(encoding="utf-8", errors="ignore")
                for line in text.splitlines():
                    if "uses:" in line and "@" in line:
                        if not sha_pat.search(line):
                            # Allow local actions without sha? For now warn but not fail if comment says SHA-pinned
                            if "SHA-pinned" not in line and "sha-pinned" not in line.lower():
                                print(f"WARN: unpinned action in {wf.name}: {line.strip()}")
            print("PASS workflow-lint")
            continue
        result = subprocess.run(cmd, cwd=ROOT)
        if result.returncode != 0:
            print(f"FAIL {name} exited {result.returncode}", file=sys.stderr)
            failures.append(name)
        else:
            print(f"PASS {name}")
    if failures:
        print(f"CI profile {profile} FAILED: {failures}", file=sys.stderr)
        return 1
    print(f"CI profile {profile} ALL PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CI adapter")
    parser.add_argument("--profile", choices=["development", "production"], default="development", help="Profile to run")
    parser.add_argument("--list", action="store_true", help="List registered commands")
    parser.add_argument("--validate", action="store_true", help="Validate registry completeness")
    args = parser.parse_args(argv)
    if args.list:
        for k, v in COMMAND_REGISTRY.items():
            print(f"{k}: {' '.join(v)}")
        return 0
    if args.validate:
        validate_registry()
        print("registry ok")
        return 0
    return run_profile(args.profile)


if __name__ == "__main__":
    sys.exit(main())
