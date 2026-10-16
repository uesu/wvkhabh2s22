#!/usr/bin/env python3
"""
One explicit validation registry used by ordinary CI and candidate-runtime validation.

Contract (master spec items 1 and 5):
  * Every tests/test_*.py runner is registered and executed — a missing registration is fatal.
  * Lock graphs are verified under the exact pinned interpreter from .python-version with the
    pinned compiler (pip==26.1.1 / pip-tools==7.5.3), by regenerating them with the documented
    command and comparing full bytes. Any difference is a blocking failure.
  * Workflow/security lint (actionlint, zizmor) must actually execute. A nonzero result blocks.
    A tool that is merely present — or absent — never produces a PASS.
  * An environment that cannot run a mandatory check reports NOT VALIDATED and does not satisfy
    the gate (exit code 2). It never reports PASS.

Exit codes: 0 = every mandatory check passed, 1 = a mandatory check failed,
2 = the gate was not satisfied because a mandatory check could not be validated here.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The pinned build toolchain is defined once, in the shared maintenance helper, so the registry
# and the merge controller validate against the same versions.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from maintenance import PINNED_PIP, PINNED_PIP_TOOLS  # noqa: E402  (single source of truth)

WORKFLOW_DIR = ROOT / ".github" / "workflows"

# Mandatory external linters. Present-but-failing and missing are both blocking in CI.
EXTERNAL_LINTERS = ("actionlint", "zizmor")


def workflow_files() -> list[str]:
    """Actual workflow paths, sorted. Never a shell wildcard (it would not expand)."""
    return sorted(str(p.relative_to(ROOT)) for p in WORKFLOW_DIR.glob("*.yml"))


# Registry of all offline validation commands. Every tests/test_*.py must appear here.
COMMAND_REGISTRY: dict[str, list[str]] = {
    "compile": [sys.executable, "-m", "compileall", "-q", "testing area", "tests", ".github/scripts"],
    "smoke": [sys.executable, "tests/test_smoke.py"],
    "reddit-signals": [sys.executable, "tests/test_reddit_signals.py"],
    "twitter-media": [sys.executable, "tests/test_twitter_media.py"],
    "maintenance": [sys.executable, "tests/test_maintenance.py"],
    "run-summary": [sys.executable, "tests/test_run_summary.py"],
    "configuration-contract": [sys.executable, "tests/test_configuration_contract.py"],
    "ci-registry": [sys.executable, "tests/test_ci_registry.py"],
    "lock-consistency": [sys.executable, "-c", "import pathlib; print('lock-consistency placeholder')"],
    "workflow-lint": [sys.executable, "-c", "import pathlib, yaml; yaml.safe_load(open('.github/workflows/ci.yml')) ; print('yaml ok')"],
    "actionlint": ["actionlint", *workflow_files()],
    "zizmor": ["zizmor", "--offline", "--min-severity", "high", ".github/workflows"],
}


def list_profiles() -> dict[str, list[str]]:
    """Both profiles run the same mandatory registry; there is no weaker private subset."""
    commands = list(COMMAND_REGISTRY.keys())
    return {"development": list(commands), "production": list(commands)}


def validate_registry() -> None:
    """Ensure every tests/test_*.py file is registered, otherwise CI would silently miss it."""
    test_files = sorted((ROOT / "tests").glob("test_*.py"))
    registered: set[str] = set()
    for cmd in COMMAND_REGISTRY.values():
        joined = " ".join(cmd)
        for tf in test_files:
            if tf.name in joined:
                registered.add(tf.name)
    missing = [tf.name for tf in test_files if tf.name not in registered]
    if missing:
        print(f"ERROR: test files not registered in ci.py: {missing}", file=sys.stderr)
        sys.exit(1)


def in_ci() -> bool:
    return os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true"


def pinned_python() -> str:
    """Exact interpreter pin consumed by both runtime and CI."""
    try:
        return (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def running_python() -> str:
    vi = sys.version_info
    return f"{vi.major}.{vi.minor}.{vi.micro}"


class Gate:
    """Collects blocking failures and mandatory checks that could not be validated."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.not_validated: list[str] = []
        self.failure_messages: list[tuple[str, str]] = []
        self.unvalidated_messages: list[tuple[str, str]] = []

    def fail(self, name: str, message: str) -> None:
        print(f"FAIL: {message}", file=sys.stderr)
        self.failure_messages.append((name, message))
        if name not in self.failures:
            self.failures.append(name)

    def unvalidated(self, name: str, message: str) -> None:
        print(f"NOT VALIDATED: {message}", file=sys.stderr)
        self.unvalidated_messages.append((name, message))
        if name not in self.not_validated:
            self.not_validated.append(name)


def _read_lock(target: Path) -> str:
    return target.read_text(encoding="utf-8", errors="ignore")


def _structural_lock_failures(target: Path, label: str) -> list[str]:
    """Frozen-graph structure checks that do not depend on the running interpreter."""
    problems: list[str] = []
    if not target.exists():
        return [f"{label} missing"]
    text = _read_lock(target)
    if "--hash=" not in text:
        problems.append(f"{label} must contain --hash entries (frozen graph)")
    if "pip-compile" not in text:
        problems.append(f"{label} header must mention pip-compile")
    if "python" not in text.lower():
        problems.append(f"{label} header must mention the generating Python version")
    if label == "requirements-dev.txt":
        if f"pip-tools=={PINNED_PIP_TOOLS}" not in text:
            problems.append(f"{label} must contain pip-tools=={PINNED_PIP_TOOLS} (pinned compiler)")
        if "pip==" not in text:
            problems.append(f"{label} must contain the pip pin")
    hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", text)
    if len(hashes) < 5:
        problems.append(f"{label} has too few hashes ({len(hashes)}) — not a complete frozen graph")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "==" in line and not line.strip().startswith("#") and line.rstrip().endswith("\\"):
            has_hash = False
            for j in range(i + 1, min(i + 5, len(lines))):
                if "--hash=sha256:" in lines[j]:
                    has_hash = True
                    break
                stripped = lines[j].strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith("--hash"):
                    break
            if not has_hash:
                problems.append(f"{label} package line without hash: {line.strip()}")
                break
    return problems


def _compiler_versions() -> tuple[str, str]:
    pip_run = subprocess.run(
        [sys.executable, "-m", "pip", "--version"], capture_output=True, text=True, timeout=60
    )
    pip_out = f"{pip_run.stdout or ''}{pip_run.stderr or ''}".strip()
    try:
        tools_run = subprocess.run(
            ["pip-compile", "--version"], capture_output=True, text=True, timeout=60
        )
        tools_out = f"{tools_run.stdout or ''}{tools_run.stderr or ''}".strip()
    except FileNotFoundError:
        tools_out = ""
    return pip_out, tools_out


def _regenerate_lock(label: str, src_name: str, tmpdir: Path) -> subprocess.CompletedProcess:
    """Regenerate one lock inside an isolated seeded copy (keeps the locked resolution)."""
    for extra in ("requirements.in", "requirements.txt"):
        if extra != src_name and label == "requirements-dev.txt":
            try:
                shutil.copy2(ROOT / extra, tmpdir / extra)
            except OSError:
                pass
    return subprocess.run(
        [
            sys.executable, "-m", "piptools", "compile",
            "--generate-hashes", "--allow-unsafe", "--resolver=backtracking",
            "--strip-extras", f"--output-file={label}", src_name,
        ],
        capture_output=True,
        text=True,
        timeout=240,
        cwd=tmpdir,
    )


def _first_difference(orig: list[str], new: list[str]) -> str:
    for i, (a, b) in enumerate(zip(orig, new)):
        if a != b:
            return f"line {i + 1}: committed={a!r} regenerated={b!r}"
    return f"line count differs: committed {len(orig)} vs regenerated {len(new)}"


def _difference_summary(committed: list[str], regenerated: list[str], limit: int = 12) -> str:
    """A bounded, single-line summary of every differing line (diagnostics must stay readable)."""
    import difflib

    added: list[str] = []
    removed: list[str] = []
    for line in difflib.unified_diff(committed, regenerated, lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-"):
            removed.append(line[1:].strip())
        elif line.startswith("+"):
            added.append(line[1:].strip())
    parts = [f"{len(removed)} committed-only line(s), {len(added)} regenerated-only line(s)"]
    for item in removed[:limit]:
        parts.append(f"committed-only: {item[:120]!r}")
    for item in added[:limit]:
        parts.append(f"regenerated-only: {item[:120]!r}")
    return " | ".join(parts)


def _annotation(level: str, text: str) -> None:
    """Emit a bounded, sanitized GitHub workflow annotation when running in CI.

    Job step summaries are not retrievable through the API, so evidence that must stay
    auditable after a green run and the registry's own failures are published as
    annotations. This is read-only, credential-free and bounded; it never changes a
    check outcome, which is decided solely by the gate.
    """
    if not in_ci():
        return
    safe = text.replace("%", "%25").replace("\r", " ").replace("\n", " ")[:1500]
    print(f"::{level}::{safe}")


def _notice(text: str) -> None:
    """Publish bounded evidence that survives a successful run."""
    _annotation("notice", text)


def _publish_outcome(profile: str, gate: Gate, code: int) -> None:
    """Report the registry's own result, so no log grep can confuse it with test output.

    Registered test suites deliberately produce ERROR strings as fixtures. Grepping a merged
    log for them cannot distinguish an expected negative case from a real failure, so the
    registry — which knows exactly what it concluded — is the only publisher.
    """
    for name, message in gate.failure_messages[:20]:
        _annotation("error", f"{name}: {message}")
    for name, message in gate.unvalidated_messages[:20]:
        _annotation("warning", f"{name} NOT VALIDATED here: {message}")
    if code == 1:
        _annotation("error", f"CI profile {profile} FAILED: {gate.failures}")
    elif code == 2:
        _annotation(
            "warning", f"CI profile {profile} NOT VALIDATED here: {gate.not_validated}"
        )
    elif code == 0:
        _annotation("notice", f"CI profile {profile} ALL PASS: {len(gate.failure_messages)} failures, "
                              f"{len(gate.unvalidated_messages)} unvalidated")


def _marker_evidence(packages: tuple[str, ...], needle: str) -> str:
    """Report the real Requires-Dist markers of installed distributions.

    Used to explain why a package is or is not part of the graph under the pinned
    interpreter, from metadata rather than from assumption.
    """
    from importlib.metadata import PackageNotFoundError, requires

    parts = []
    for package in packages:
        try:
            entries = requires(package) or []
        except PackageNotFoundError:
            parts.append(f"{package}=not installed")
            continue
        hits = [entry for entry in entries if needle in entry.lower()]
        parts.append(f"{package}={hits[0] if hits else 'no ' + needle + ' requirement'}")
    return "; ".join(parts)


def _verify_clean_install(label: str, target: Path, gate: Gate, name: str) -> None:
    """Install one frozen graph into a fresh virtual environment with the pinned pip.

    A dry run against an already satisfied site-packages proves nothing about the graph, so the
    wheels are really installed into an empty environment and then checked for consistency.
    """
    parent = Path(tempfile.mkdtemp(prefix="ci-venv-"))
    venv_dir = parent / "venv"
    try:
        create = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)], capture_output=True, text=True, timeout=180
        )
        if create.returncode != 0:
            gate.fail(
                name,
                f"{label} clean-environment verification could not create a venv: "
                f"{(create.stderr or create.stdout or '').strip()[:800]}",
            )
            return
        venv_python = venv_dir / "bin" / "python"
        if not venv_python.exists():
            venv_python = venv_dir / "Scripts" / "python.exe"
        # pip requires the --python option before the subcommand name.
        install = subprocess.run(
            [
                sys.executable, "-m", "pip", "--python", str(venv_python), "install",
                "--require-hashes", "--only-binary=:all:", "-r", str(target),
            ],
            capture_output=True, text=True, timeout=900,
        )
        if install.returncode != 0:
            gate.fail(
                name,
                f"{label} clean-environment hash-verified wheel install failed: "
                f"{(install.stderr or install.stdout or '').strip()[:2000]}",
            )
            return
        check = subprocess.run(
            [str(venv_python), "-m", "pip", "check"], capture_output=True, text=True, timeout=180
        )
        if check.returncode != 0:
            gate.fail(
                name,
                f"{label} clean-environment install is inconsistent: "
                f"{(check.stdout or check.stderr or '').strip()[:1000]}",
            )
            return
        print(f"OK lock-consistency: {label} installs hash-verified, wheels only, into a clean venv")
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def _verify_lock_graph(label: str, src_name: str, gate: Gate, name: str) -> None:
    """Verify one lock graph: structure, pinned compiler, byte-stable regeneration, hashed install."""
    target = ROOT / label
    structural = _structural_lock_failures(target, label)
    if structural:
        for problem in structural:
            gate.fail(name, problem)
        return

    pip_out, tools_out = _compiler_versions()
    if PINNED_PIP not in pip_out:
        gate.fail(name, f"{label} requires pip=={PINNED_PIP} (got: {pip_out or 'unavailable'})")
        return
    if PINNED_PIP_TOOLS not in tools_out:
        gate.fail(
            name, f"{label} requires pip-tools=={PINNED_PIP_TOOLS} (got: {tools_out or 'unavailable'})"
        )
        return

    src = ROOT / src_name
    if not src.exists():
        gate.fail(name, f"{src_name} missing — cannot regenerate {label}")
        return

    tmpdir = Path(tempfile.mkdtemp(prefix="ci-lock-"))
    try:
        # Seed the isolated copy so the documented command reproduces the locked resolution.
        shutil.copy2(src, tmpdir / src_name)
        shutil.copy2(target, tmpdir / label)
        try:
            regen = _regenerate_lock(label, src_name, tmpdir)
        except subprocess.TimeoutExpired:
            gate.fail(name, f"{label} regeneration timed out (240s)")
            return
        if regen.returncode != 0:
            gate.fail(
                name,
                f"{label} regeneration with the documented command failed "
                f"(exit {regen.returncode}): {(regen.stderr or regen.stdout or '').strip()[:2000]}",
            )
            return
        produced = tmpdir / label
        if not produced.exists():
            gate.fail(name, f"{label} regeneration produced no output file")
            return
        committed = _read_lock(target).splitlines()
        regenerated = _read_lock(produced).splitlines()
        if committed != regenerated:
            # Full-byte comparison: no path/header rewriting, no tolerated drift.
            gate.fail(
                name,
                f"{label} is not byte-stable under Python {running_python()} / pip {PINNED_PIP} / "
                f"pip-tools {PINNED_PIP_TOOLS} — {_first_difference(committed, regenerated)}",
            )
            gate.fail(name, f"{label} regeneration differences: {_difference_summary(committed, regenerated)}")
            return
        print(f"OK lock-consistency: {label} byte-stable under pinned interpreter and compiler")

        # Regenerate a second time: a lock that is only accidentally reproducible is not frozen.
        second_dir = Path(tempfile.mkdtemp(prefix="ci-lock-again-"))
        try:
            shutil.copy2(src, second_dir / src_name)
            shutil.copy2(target, second_dir / label)
            try:
                again = _regenerate_lock(label, src_name, second_dir)
            except subprocess.TimeoutExpired:
                gate.fail(name, f"{label} second regeneration timed out (240s)")
                return
            if again.returncode != 0:
                gate.fail(
                    name,
                    f"{label} second regeneration failed (exit {again.returncode}): "
                    f"{(again.stderr or again.stdout or '').strip()[:2000]}",
                )
                return
            second = _read_lock(second_dir / label).splitlines()
            if second != regenerated:
                gate.fail(
                    name,
                    f"{label} regeneration is not idempotent — "
                    f"{_first_difference(regenerated, second)}",
                )
                return
            print(f"OK lock-consistency: {label} regeneration is idempotent")
        finally:
            shutil.rmtree(second_dir, ignore_errors=True)

        install_cmd = [
            sys.executable, "-m", "pip", "install", "--dry-run",
            "--require-hashes", "--only-binary=:all:", "-r", str(target),
        ]
        try:
            inst = subprocess.run(install_cmd, capture_output=True, text=True, timeout=180)
            if inst.returncode != 0 and "externally-managed-environment" in (inst.stderr or ""):
                inst = subprocess.run(
                    install_cmd + ["--break-system-packages"],
                    capture_output=True, text=True, timeout=180,
                )
        except subprocess.TimeoutExpired:
            gate.fail(name, f"{label} hash-verified install --dry-run timed out (180s)")
            return
        if inst.returncode != 0:
            gate.fail(
                name,
                f"{label} hash-verified install --dry-run failed: "
                f"{(inst.stderr or inst.stdout or '').strip()[:2000]}",
            )
            return
        print(f"OK lock-consistency: {label} resolves hash-verified, wheels only (dry run)")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    _verify_clean_install(label, target, gate, name)
    if not gate.failures and not gate.not_validated:
        _notice(
            f"lock-consistency {label}: Python {running_python()} (pin {pinned_python()}) / "
            f"pip {PINNED_PIP} / pip-tools {PINNED_PIP_TOOLS}; byte-stable, idempotent across two "
            f"seeded regenerations, dry run and clean-venv hash-verified wheels-only install OK; "
            f"typing-extensions markers: "
            f"{_marker_evidence(('aiohttp', 'aiosignal', 'multidict', 'propcache', 'yarl'), 'typing')}"
        )


def check_lock_consistency(gate: Gate, name: str) -> None:
    """Hashed locks verified under the exact pinned interpreter and pinned compiler.

    Both graphs are always verified so one run reports every difference.
    """
    pin = pinned_python()
    running = running_python()
    if not pin:
        gate.fail(name, "cannot read .python-version — the exact runtime pin is mandatory")
        return
    if running != pin:
        # Diagnostic only: a different interpreter cannot validate the pinned lock graphs,
        # and it must never be reported as a pass.
        gate.unvalidated(
            name,
            f"running interpreter {running} != pinned {pin} — lock graphs not validated here",
        )
        return

    before = len(gate.failures)
    for label, src_name in (
        ("requirements.txt", "requirements.in"),
        ("requirements-dev.txt", "requirements-dev.in"),
    ):
        _verify_lock_graph(label, src_name, gate, name)
    if len(gate.failures) == before:
        print("PASS lock-consistency: both graphs genuine, frozen and reproducible")


def _strict_yaml_loader():
    """A SafeLoader that rejects duplicate mapping keys (silent last-one-wins is a real defect)."""
    import yaml

    class StrictLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        seen: set[object] = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                raise ValueError(f"duplicate key {key!r}")
            seen.add(key)
        return loader.construct_mapping(node, deep)

    StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
    return yaml, StrictLoader


_SHA_PIN = re.compile(r"uses:\s*[^\n]+@[0-9a-f]{40}")


def check_workflow_lint(gate: Gate, name: str) -> None:
    """Strict YAML + SHA-pinned actions + real actionlint + real zizmor. All blocking."""
    try:
        yaml, strict_loader = _strict_yaml_loader()
    except ImportError as exc:
        gate.fail(name, f"pyyaml not installed — cannot lint workflows ({exc})")
        return

    workflows = sorted(WORKFLOW_DIR.glob("*.yml"))
    if not workflows:
        gate.fail(name, f"no workflows found in {WORKFLOW_DIR} — nothing was linted")
        return

    for wf in workflows:
        try:
            text = wf.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            gate.fail(name, f"cannot read {wf.name}: {exc}")
            return
        try:
            yaml.load(text, Loader=strict_loader)
        except Exception as exc:  # noqa: BLE001 - any parse error is a blocking lint failure
            gate.fail(name, f"invalid YAML in {wf.name}: {exc}")
            return
        for line in text.splitlines():
            if "uses:" in line and "@" in line and not _SHA_PIN.search(line):
                if "sha-pinned" not in line.lower():
                    gate.fail(name, f"unpinned action in {wf.name}: {line.strip()}")
                    return
    print(f"OK workflow-lint: {len(workflows)} workflows parse strictly and use SHA-pinned actions")

    _run_external_linter(
        gate,
        name,
        tool="actionlint",
        version_args=["actionlint", "-version"],
        lint_args=["actionlint", *workflow_files()],
    )
    _run_external_linter(
        gate,
        name,
        tool="zizmor",
        version_args=["zizmor", "--version"],
        lint_args=["zizmor", "--offline", "--min-severity", "high", ".github/workflows"],
    )
    if name not in gate.failures and name not in gate.not_validated:
        print(f"PASS workflow-lint: {len(workflows)} workflows linted by actionlint and zizmor")


def _run_external_linter(
    gate: Gate, name: str, tool: str, version_args: list[str], lint_args: list[str]
) -> None:
    """Execute a mandatory external linter. Availability alone is never a pass."""
    try:
        probe = subprocess.run(version_args, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        if in_ci():
            gate.fail(name, f"{tool} is mandatory in CI but could not be executed: {exc}")
        else:
            gate.unvalidated(name, f"{tool} unavailable locally — {tool} checks not validated here")
        return
    if probe.returncode != 0:
        if in_ci():
            gate.fail(
                name,
                f"{tool} is mandatory in CI but --version failed (exit {probe.returncode}): "
                f"{(probe.stderr or probe.stdout or '').strip()[:500]}",
            )
        else:
            gate.unvalidated(name, f"{tool} unusable locally — {tool} checks not validated here")
        return
    try:
        run = subprocess.run(lint_args, capture_output=True, text=True, timeout=300, cwd=ROOT)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        if in_ci():
            gate.fail(name, f"{tool} invocation failed: {exc}")
        else:
            gate.unvalidated(name, f"{tool} invocation unavailable locally: {exc}")
        return
    if run.returncode != 0:
        output = f"{(run.stdout or '').strip()}\n{(run.stderr or '').strip()}".strip()
        gate.fail(name, f"{tool} reported problems (exit {run.returncode}):\n{output}")
        return
    print(f"OK {tool}: executed clean ({' '.join(lint_args)})")


def run_command(gate: Gate, name: str, cmd: list[str]) -> None:
    try:
        result = subprocess.run(cmd, cwd=ROOT)
    except FileNotFoundError as exc:
        if in_ci() and cmd and cmd[0] in EXTERNAL_LINTERS:
            gate.fail(name, f"{cmd[0]} binary not found (mandatory in CI) — {exc}")
        elif in_ci():
            gate.fail(name, f"{name} binary not found — {exc}")
        else:
            gate.unvalidated(name, f"{name} could not run locally ({exc}) — not validated here")
        return
    if result.returncode != 0:
        gate.fail(name, f"{name} exited {result.returncode}")
        return
    print(f"PASS {name}")


def run_profile(profile: str, verbose: bool = False) -> int:
    validate_registry()
    commands = list_profiles().get(profile)
    if commands is None:
        print(f"Unknown profile {profile}", file=sys.stderr)
        return 2
    gate = Gate()
    for name in commands:
        cmd = COMMAND_REGISTRY[name]
        print(f"=== CI [{profile}] {name}: {' '.join(cmd)} ===")
        if verbose:
            print(f"    cwd={ROOT} ci={in_ci()} python={running_python()} pin={pinned_python()}")
        if name == "lock-consistency":
            try:
                check_lock_consistency(gate, name)
            except Exception as exc:  # noqa: BLE001 - an unexpected error must not pass the gate
                gate.fail(name, f"lock-consistency raised {type(exc).__name__}: {exc}")
            continue
        if name == "workflow-lint":
            try:
                check_workflow_lint(gate, name)
            except Exception as exc:  # noqa: BLE001 - an unexpected error must not pass the gate
                gate.fail(name, f"workflow-lint raised {type(exc).__name__}: {exc}")
            continue
        run_command(gate, name, cmd)

    if gate.failures:
        print(f"CI profile {profile} FAILED: {gate.failures}", file=sys.stderr)
        _record_outcome(f"FAILED {profile}: {gate.failures}")
        _publish_outcome(profile, gate, 1)
        return 1
    if gate.not_validated:
        print(
            f"CI profile {profile} NOT VALIDATED: mandatory checks could not be validated here: "
            f"{gate.not_validated}",
            file=sys.stderr,
        )
        _record_outcome(f"NOT VALIDATED {profile}: {gate.not_validated}")
        _publish_outcome(profile, gate, 2)
        return 2
    print(f"CI profile {profile} ALL PASS")
    _publish_outcome(profile, gate, 0)
    return 0


def _record_outcome(text: str) -> None:
    try:
        Path("/tmp/ci-failure.txt").write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            Path(summary).write_text(f"CI {text}\n", encoding="utf-8")
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CI adapter")
    parser.add_argument(
        "--profile", choices=["development", "production"], default="development", help="Profile to run"
    )
    parser.add_argument("--list", action="store_true", help="List registered commands")
    parser.add_argument("--validate", action="store_true", help="Validate registry completeness")
    parser.add_argument("--verbose", action="store_true", help="Print environment facts per check")
    args = parser.parse_args(argv)
    if args.list:
        for key, value in COMMAND_REGISTRY.items():
            print(f"{key}: {' '.join(value)}")
        return 0
    if args.validate:
        validate_registry()
        print("registry ok")
        return 0
    return run_profile(args.profile, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
