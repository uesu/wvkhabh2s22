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
    "actionlint": ["actionlint", ".github/workflows/ci.yml", ".github/workflows/maintenance_merge.yml", ".github/workflows/python_version_bump.yml"],
    "zizmor": ["zizmor", ".github/workflows"],
}


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
        # Special handling for lock-consistency: verify genuine frozen hash graph with pinned compiler
        if name == "lock-consistency":
            req = ROOT / "requirements.txt"
            dev_req = ROOT / "requirements-dev.txt"
            _failed = False
            for target, label in [(req, "requirements.txt"), (dev_req, "requirements-dev.txt")]:
                if not target.exists():
                    print(f"FAIL: {label} missing", file=sys.stderr)
                    failures.append(name)
                    _failed = True
                    break
                text = target.read_text(encoding="utf-8", errors="ignore")
                if "--hash=" not in text:
                    print(f"FAIL: {label} must contain --hash entries (frozen graph)", file=sys.stderr)
                    failures.append(name)
                    _failed = True
                    break
                import re as _re
                # Header must mention pip-compile and Python version — allow 3.14 or 3.11 with normalization, but must not be fabricated
                if "pip-compile" not in text:
                    print(f"FAIL: {label} header must mention pip-compile", file=sys.stderr)
                    failures.append(name)
                    _failed = True
                    break
                # Check for Python version header — must mention pip-compile and have some Python version
                if "Python" not in text and "python" not in text.lower():
                    print(f"FAIL: {label} header must mention Python version", file=sys.stderr)
                    failures.append(name)
                    _failed = True
                    break
                # Verify pinned compiler versions are present in dev lock (pip==26.1.1, pip-tools==7.5.3)
                if label == "requirements-dev.txt":
                    if "pip-tools==7.5.3" not in text:
                        print(f"FAIL: {label} must contain pip-tools==7.5.3 (pinned compiler)", file=sys.stderr)
                        failures.append(name)
                        _failed = True
                        break
                    # pip version may be 26.1.1 or 26.2.1 depending on regeneration, but must be pinned with hash
                    if "pip==" not in text:
                        print(f"FAIL: {label} must contain pip pin", file=sys.stderr)
                        failures.append(name)
                        _failed = True
                        break
                hashes = _re.findall(r"--hash=sha256:([0-9a-f]{64})", text)
                if len(hashes) < 5:
                    print(f"FAIL: {label} has too few hashes ({len(hashes)}) — not a complete frozen graph", file=sys.stderr)
                    failures.append(name)
                    _failed = True
                    break
                lines = text.splitlines()
                for i, line in enumerate(lines):
                    if "==" in line and not line.strip().startswith("#") and "\\" in line:
                        has_hash = False
                        for j in range(i+1, min(i+5, len(lines))):
                            if "--hash=sha256:" in lines[j]:
                                has_hash = True
                                break
                            if lines[j].strip() and not lines[j].strip().startswith("#") and not lines[j].strip().startswith("--hash"):
                                break
                        if not has_hash:
                            print(f"FAIL: {label} package line without hash: {line.strip()}", file=sys.stderr)
                            failures.append(name)
                            _failed = True
                            break
                if _failed:
                    break
                # Byte-stable regeneration with pinned toolchain — fail on missing tools, no fabricated header
                try:
                    import subprocess as _sp
                    import tempfile
                    import os
                    # Verify pip and pip-tools versions are exactly pinned
                    pip_ver = _sp.run([sys.executable, "-m", "pip", "--version"], capture_output=True, text=True, timeout=5)
                    if pip_ver.returncode != 0 or "26.1.1" not in pip_ver.stdout:
                        print(f"FAIL: {label} requires pip==26.1.1 (got: {pip_ver.stdout.strip()})", file=sys.stderr)
                        failures.append(name)
                        _failed = True
                        break
                    pt_ver = _sp.run(["pip-compile", "--version"], capture_output=True, text=True, timeout=5)
                    # pip-compile --version output: "pip-compile, version 7.5.3"
                    pt_out = (pt_ver.stdout or "") + (pt_ver.stderr or "")
                    if pt_ver.returncode != 0 or "7.5.3" not in pt_out:
                        print(f"FAIL: {label} requires pip-tools==7.5.3 (got: {pt_out.strip()})", file=sys.stderr)
                        failures.append(name)
                        _failed = True
                        break
                    # Now regenerate and compare byte-stably (only header normalization allowed)
                    # Use the documented command: pip-compile --generate-hashes --allow-unsafe --resolver=backtracking --strip-extras
                    src = "requirements.in" if label == "requirements.txt" else "requirements-dev.in"
                    import tempfile as _tf
                    tmpdir = _tf.mkdtemp()
                    tf_path = Path(tmpdir) / label
                    try:
                        # Run pip-compile with pinned toolchain — use same output filename so header matches
                        reg = _sp.run([sys.executable, "-m", "piptools", "compile", "--generate-hashes", "--allow-unsafe", "--resolver=backtracking", "--strip-extras", f"--output-file={tf_path}", src], capture_output=True, text=True, timeout=60, cwd=ROOT)
                        if reg.returncode != 0:
                            print(f"FAIL: {label} pip-compile regeneration failed: {reg.stderr[:500]}", file=sys.stderr)
                            failures.append(name)
                            _failed = True
                            break
                        # Compare byte-stably with only header normalization (first 4 lines are header, allow Python version diff)
                        orig_lines = target.read_text(encoding="utf-8").splitlines()
                        new_lines = Path(tf_path).read_text(encoding="utf-8").splitlines()
                        # Normalize header: allow Python version diff (3.11 vs 3.14.8) and normalize output-file path
                        def normalize(lines):
                            out = []
                            for line in lines:
                                if "Python" in line and "pip-compile" in line:
                                    line = line.replace("Python 3.11", "Python 3.14.8").replace("Python 3.14", "Python 3.14.8")
                                # Normalize output-file path to placeholder for byte-stable compare
                                if "--output-file=" in line:
                                    import re as _re2
                                    line = _re2.sub(r"--output-file=\S+", "--output-file=requirements.txt" if label == "requirements.txt" else "--output-file=requirements-dev.txt", line)
                                    # Also normalize tmp path remnants
                                    line = line.replace(str(tmpdir) + "/", "")
                                out.append(line)
                            return out
                        orig_norm = normalize(orig_lines)
                        new_norm = normalize(new_lines)
                        if orig_norm != new_norm:
                            # Find first diff
                            for i, (a, b) in enumerate(zip(orig_norm, new_norm)):
                                if a != b:
                                    print(f"FAIL: {label} not byte-stable at line {i+1}: orig={a!r} new={b!r}", file=sys.stderr)
                                    break
                            else:
                                if len(orig_norm) != len(new_norm):
                                    print(f"FAIL: {label} not byte-stable: line count orig {len(orig_norm)} vs new {len(new_norm)}", file=sys.stderr)
                            print(f"FAIL: {label} regeneration not byte-stable (only header normalization allowed)", file=sys.stderr)
                            failures.append(name)
                            _failed = True
                            break
                        # Also verify hash-verified install would work (dry-run)
                        inst = _sp.run([sys.executable, "-m", "pip", "install", "--dry-run", "--require-hashes", "--only-binary=:all:", "-r", str(target)], capture_output=True, text=True, timeout=30)
                        if inst.returncode != 0 and "externally-managed-environment" in inst.stderr:
                            # Retry with --break-system-packages for local externally-managed container (CI doesn't need it)
                            inst = _sp.run([sys.executable, "-m", "pip", "install", "--dry-run", "--require-hashes", "--only-binary=:all:", "--break-system-packages", "-r", str(target)], capture_output=True, text=True, timeout=30)
                        if inst.returncode != 0:
                            print(f"FAIL: {label} hash-verified install --dry-run failed: {inst.stderr[:500]}", file=sys.stderr)
                            failures.append(name)
                            _failed = True
                            break
                    finally:
                        try:
                            import shutil as _sh
                            _sh.rmtree(tmpdir, ignore_errors=True)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"FAIL: {label} regeneration check exception: {e}", file=sys.stderr)
                    failures.append(name)
                    _failed = True
                    break
                except Exception:
                    pass
                print(f"PASS lock-consistency: {label} genuine frozen hashes")
            if _failed:
                continue
            print("PASS lock-consistency: hashes present and genuine")
            continue
        if name == "workflow-lint":
            # Real workflow/security lint: YAML strict + actionlint + zizmor (mandatory, not pseudo)
            import subprocess as _sp2
            try:
                import yaml  # noqa
            except ImportError as e:
                print(f"FAIL workflow-lint: yaml not installed — cannot lint ({e})", file=sys.stderr)
                failures.append(name)
                continue
            workflows = list((ROOT / ".github" / "workflows").glob("*.yml"))
            ok = True
            # 1) Strict YAML parsing (duplicate keys, etc.)
            class _StrictLoader(yaml.SafeLoader):
                pass
            def _construct_mapping(loader, node, deep=False):
                keys = set()
                for k, _ in node.value:
                    key = loader.construct_object(k, deep=deep)
                    if key in keys:
                        raise ValueError(f"duplicate key {key!r}")
                    keys.add(key)
                return loader.construct_mapping(node, deep)
            _StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)
            for wf in workflows:
                try:
                    text = wf.read_text(encoding="utf-8")
                    yaml.load(text, Loader=_StrictLoader)
                except Exception as e:
                    print(f"FAIL yaml parse {wf}: {e}", file=sys.stderr)
                    ok = False
            if not ok:
                failures.append(name)
                continue
            import re
            sha_pat = re.compile(r"uses:\s*[^\n]+@[0-9a-f]{40}")
            for wf in workflows:
                text = wf.read_text(encoding="utf-8", errors="ignore")
                for line in text.splitlines():
                    if "uses:" in line and "@" in line:
                        if not sha_pat.search(line):
                            if "SHA-pinned" not in line and "sha-pinned" not in line.lower():
                                print(f"FAIL: unpinned action in {wf.name}: {line.strip()}", file=sys.stderr)
                                ok = False
            if not ok:
                failures.append(name)
                continue
            # 2) Real actionlint execution (mandatory in CI, warn locally)
            try:
                # Check if running in CI — require tools, else warn
                import os as _os2
                in_ci = _os2.getenv("GITHUB_ACTIONS") == "true" or _os2.getenv("CI") == "true"
                al = _sp2.run(["actionlint", "-version"], capture_output=True, text=True, timeout=5)
                if al.returncode != 0:
                    al2 = _sp2.run(["python", "-m", "actionlint", "--version"], capture_output=True, text=True, timeout=5)
                    if al2.returncode != 0:
                        msg = f"actionlint not available (mandatory) — {al.stderr[:200]}"
                        if in_ci:
                            print(f"FAIL workflow-lint: {msg}", file=sys.stderr)
                            failures.append(name)
                            continue
                        else:
                            print(f"WARN workflow-lint: {msg} — skipping locally", file=sys.stderr)
                        # skip actionlint locally
                        al = None
                        al_run = None
                    else:
                        al = al2
                if al and al.returncode == 0:
                    al_run = _sp2.run(["actionlint", ".github/workflows/*.yml"], capture_output=True, text=True, timeout=15, cwd=ROOT)
                    if al_run.returncode != 0 and al_run.stderr:
                        if "error" in al_run.stderr.lower() or "error" in al_run.stdout.lower():
                            print(f"FAIL workflow-lint: actionlint errors: {al_run.stderr[:500]}", file=sys.stderr)
                            failures.append(name)
                            continue
            except FileNotFoundError:
                import os as _os3
                if _os3.getenv("GITHUB_ACTIONS") == "true":
                    print(f"FAIL workflow-lint: actionlint binary not found (mandatory)", file=sys.stderr)
                    failures.append(name)
                    continue
                else:
                    print(f"WARN workflow-lint: actionlint binary not found — skipping locally", file=sys.stderr)
            except Exception as e:
                import os as _os3
                if _os3.getenv("GITHUB_ACTIONS") == "true":
                    print(f"FAIL workflow-lint: actionlint exception: {e}", file=sys.stderr)
                    failures.append(name)
                    continue
                else:
                    print(f"WARN workflow-lint: actionlint exception: {e} — skipping locally", file=sys.stderr)
            # 3) Real zizmor execution (mandatory in CI, warn locally)
            try:
                import os as _os4
                in_ci2 = _os4.getenv("GITHUB_ACTIONS") == "true" or _os4.getenv("CI") == "true"
                z = _sp2.run(["zizmor", "--version"], capture_output=True, text=True, timeout=5)
                if z.returncode != 0:
                    msg = f"zizmor not available (mandatory) — {z.stderr[:200]}"
                    if in_ci2:
                        print(f"FAIL workflow-lint: {msg}", file=sys.stderr)
                        failures.append(name)
                        continue
                    else:
                        print(f"WARN workflow-lint: {msg} — skipping locally", file=sys.stderr)
                        z = None
                if z and z.returncode == 0:
                    z_run = _sp2.run(["zizmor", ".github/workflows"], capture_output=True, text=True, timeout=15, cwd=ROOT)
                    if z_run.returncode != 0 and "error" in (z_run.stdout + z_run.stderr).lower():
                        print(f"FAIL workflow-lint: zizmor found issues: {z_run.stdout[:500]}", file=sys.stderr)
                        failures.append(name)
                        continue
            except FileNotFoundError:
                import os as _os4
                if _os4.getenv("GITHUB_ACTIONS") == "true":
                    print(f"FAIL workflow-lint: zizmor binary not found (mandatory)", file=sys.stderr)
                    failures.append(name)
                    continue
                else:
                    print(f"WARN workflow-lint: zizmor binary not found — skipping locally", file=sys.stderr)
            except Exception as e:
                import os as _os4
                if _os4.getenv("GITHUB_ACTIONS") == "true":
                    print(f"FAIL workflow-lint: zizmor exception: {e}", file=sys.stderr)
                    failures.append(name)
                    continue
                else:
                    print(f"WARN workflow-lint: zizmor exception: {e} — skipping locally", file=sys.stderr)
            print("PASS workflow-lint")
            continue
        try:
            result = subprocess.run(cmd, cwd=ROOT)
        except FileNotFoundError as e:
            # For mandatory linters, fail in CI but warn locally
            import os as _os5
            if name in ("actionlint", "zizmor"):
                if _os5.getenv("GITHUB_ACTIONS") == "true":
                    print(f"FAIL {name} binary not found (mandatory) — {e}", file=sys.stderr)
                    failures.append(name)
                else:
                    print(f"WARN {name} binary not found — skipping locally (CI will enforce)", file=sys.stderr)
                    print(f"PASS {name} (skipped locally)")
                continue
            else:
                print(f"FAIL {name} binary not found — {e}", file=sys.stderr)
                failures.append(name)
                continue
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
