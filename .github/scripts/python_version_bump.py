#!/usr/bin/env python3
"""
Thin runtime-proposer adapter; retains familiar entry point, does not duplicate shared rules.
Delegates to .github/scripts/maintenance.py for version selection and validation.
"""
from __future__ import annotations

import os
import re
import sys
import json
import pathlib
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Import shared helpers
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import maintenance  # type: ignore


def read_current_pin() -> str | None:
    """Read the exact pin from .python-version; validate complete numeric X.Y.Z."""
    pin_file = ROOT / ".python-version"
    if not pin_file.exists():
        return None
    text = pin_file.read_text(encoding="utf-8").strip()
    # First line only, ignore comments
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Validate X.Y.Z
        if maintenance.parse_version(line) is None:
            return None
        return line
    return None


# ---------------------------------------------------------------------------
# Proposal evidence. The merge controller cannot identify which run validated and
# published a proposal from a branch name, a title, a time window or membership of
# a mutable upstream version list, so the proposer emits two machine-readable
# records bound to its own run and attempt: one for the validated candidate bytes
# and one for the publication. Both are published as bounded notice annotations,
# which are retrievable read-only from a specific check run of a specific attempt.
# ---------------------------------------------------------------------------

#: The only files a routine Python bump proposal may touch. The lock graphs are
#: generated, and the policy file is permitted only for the two derived version
#: fields verified by ``rewrite_policy_versions``.
#: Single source of truth lives in the trusted helper, so the proposer and the controller can
#: never drift apart about which files a routine bump may touch.
PROPOSAL_FILES = maintenance.PROPOSAL_FILES

VALIDATION_NOTICE = "proposal-validation"
PUBLICATION_NOTICE = "proposal-publication"


def sha256_text(text: str) -> str:
    """Digest the exact bytes a file would contribute, newline-normalised by the caller."""
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_files(root: pathlib.Path, names=PROPOSAL_FILES) -> dict[str, str]:
    """Digest each proposal file as bytes on disk; a missing file is recorded as absent."""
    digests: dict[str, str] = {}
    for name in names:
        path = root / name
        if path.is_file():
            import hashlib

            digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            digests[name] = None
    return digests


def _python_section_span(text: str) -> tuple[int, int]:
    """The exact character span of the policy file's ``python`` object, braces included."""
    key = text.index('"python"')
    open_brace = text.index("{", key)
    depth = 0
    for index in range(open_brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return open_brace, index + 1
    raise ValueError("policy python section is not a balanced object")


def rewrite_policy_versions(text: str, candidate: str) -> str:
    """Rewrite ONLY the two derived version fields, preserving the file's exact formatting.

    The policy duplicates the runtime pin, so a bump has to move it or the two contradict each
    other. That is a narrow, generated change and nothing more: re-serialising the document would
    reformat every unrelated array and turn a one-line bump into a whole-file diff, which is both
    noisy and impossible to review. So the edit is textual and scoped to the python object, and it
    fails closed rather than guessing if either field is missing or ambiguous.
    """
    if maintenance.parse_version(candidate) is None:
        raise ValueError(f"refusing to write an invalid version into policy: {candidate!r}")
    start, stop = _python_section_span(text)
    section = text[start:stop]

    current_pattern = re.compile(r'("current_version"\s*:\s*)"[^"]*"')
    supported_pattern = re.compile(r'("supported_versions"\s*:\s*)\[[^\]]*\]')
    current_matches = current_pattern.findall(section)
    supported_matches = supported_pattern.findall(section)
    if len(current_matches) != 1 or len(supported_matches) != 1:
        raise ValueError(
            "policy python section does not carry exactly one current_version and one "
            f"supported_versions (found {len(current_matches)} and {len(supported_matches)})"
        )
    section = current_pattern.sub(lambda m: f'{m.group(1)}"{candidate}"', section, count=1)
    section = supported_pattern.sub(lambda m: f'{m.group(1)}["{candidate}"]', section, count=1)
    return text[:start] + section + text[stop:]


def policy_change_is_narrow(before: str, after: str, candidate: str) -> bool:
    """True only when the difference is exactly the two derived version fields.

    Checked twice over, because either test alone can be satisfied by an accident: the parsed
    documents must differ in nothing else, and the raw lines that differ must all be version
    lines. A reformatting-only change passes the first and fails the second.
    """
    try:
        old, new = json.loads(before), json.loads(after)
    except ValueError:
        return False
    try:
        if after != rewrite_policy_versions(before, candidate):
            return False
    except ValueError:
        return False
    new_current = (new.get("python") or {}).get("current_version")
    new_supported = (new.get("python") or {}).get("supported_versions")
    old_python = dict(old.get("python") or {})
    new_python = dict(new.get("python") or {})
    for key in ("current_version", "supported_versions"):
        old_python.pop(key, None)
        new_python.pop(key, None)
    if old_python != new_python:
        return False
    if {k: v for k, v in old.items() if k != "python"} != {
        k: v for k, v in new.items() if k != "python"
    }:
        return False
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    if len(before_lines) != len(after_lines):
        return False
    for old_line, new_line in zip(before_lines, after_lines):
        if old_line == new_line:
            continue
        if "current_version" not in old_line and "supported_versions" not in old_line:
            return False
    return new_current == candidate and new_supported == [candidate]


def build_validation_evidence(
    *,
    source_head: str,
    candidate: str,
    interpreter: str,
    pip_version: str,
    pip_tools_version: str,
    digests: dict[str, str | None],
    run_id: int | None,
    run_attempt: int | None,
) -> dict[str, Any]:
    """The candidate-validation record: what was validated, by which toolchain, in which run."""
    return {
        "source_head": source_head,
        "candidate_version": candidate,
        "interpreter": interpreter,
        "pip": pip_version,
        "pip_tools": pip_tools_version,
        "digests": digests,
        "run_id": run_id,
        "run_attempt": run_attempt,
    }


def build_publication_evidence(
    *,
    run_id: int | None,
    run_attempt: int | None,
    pull_number: int | None,
    published_head: str,
    branch: str,
    digests: dict[str, str | None],
    pr_author: str | None,
    commit_identity: str | None,
    run_actor: str | None,
) -> dict[str, Any]:
    """The publication record: which run published which PR head, under which identities.

    The three identities are deliberately separate and are never required to be equal: the run
    actor authorized the run (a person on manual dispatch, or the scheduler), the PR author is
    the PAT publisher, and the commit identity is the configured bot that signs the commit.
    """
    return {
        "run_id": run_id,
        "run_attempt": run_attempt,
        "pull_number": pull_number,
        "published_head": published_head,
        "branch": branch,
        "digests": digests,
        "pr_author": pr_author,
        "commit_identity": commit_identity,
        "run_actor": run_actor,
    }


def notice(kind: str, record: dict[str, Any], *, limit: int = 3000) -> str | None:
    """Render a record as a bounded workflow notice, or None when it would not fit."""
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    if len(payload) > limit:
        return None
    safe = payload.replace("%", "%25").replace("\r", " ").replace("\n", " ")
    return f"::notice::{kind} {safe}"


def parse_notice(message: str, kind: str) -> dict[str, Any] | None:
    """Recover a record from a notice; anything malformed yields None, never a guess.

    Both shapes are accepted because both occur in practice: the Checks API reports an
    annotation's ``message`` with the workflow-command prefix already stripped, while a raw log
    line still carries it. Requiring the prefix would make every real annotation unparseable.
    """
    if not isinstance(message, str):
        return None
    text = message.strip()
    if text.startswith("::notice::"):
        text = text[len("::notice::"):]
    prefix = f"{kind} "
    if not text.startswith(prefix):
        return None
    payload = text[len(prefix):].strip()
    try:
        record = json.loads(payload)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def verify_validated_bytes(
    evidence: dict[str, Any],
    actual_digests: dict[str, str | None],
    *,
    candidate: str,
    pinned_pip: str,
    pinned_pip_tools: str,
    expected_repository: str | None = None,
    repository: str | None = None,
) -> tuple[bool, str]:
    """Check a validation record against digests computed from the published bytes.

    The digests compared here are computed by the caller from the actual proposal content, so
    a proposal cannot vouch for itself. Anything absent, stale or mismatched fails closed.
    """
    if not isinstance(evidence, dict) or not evidence:
        return False, "validation record unavailable"
    if expected_repository and repository and repository != expected_repository:
        return False, f"record repository {repository} is not {expected_repository}"
    if evidence.get("candidate_version") != candidate:
        return False, (
            f"record validated {evidence.get('candidate_version')}, not the proposed {candidate}"
        )
    if evidence.get("interpreter") != candidate:
        return False, (
            f"record was not produced by the candidate interpreter "
            f"({evidence.get('interpreter')} != {candidate})"
        )
    if evidence.get("pip") != pinned_pip or evidence.get("pip_tools") != pinned_pip_tools:
        return False, (
            f"record used pip {evidence.get('pip')} / pip-tools {evidence.get('pip_tools')}, "
            f"not the pinned {pinned_pip} / {pinned_pip_tools}"
        )
    if not isinstance(evidence.get("run_id"), int) or evidence["run_id"] <= 0:
        return False, "record carries no run id"
    if not isinstance(evidence.get("run_attempt"), int) or evidence["run_attempt"] <= 0:
        return False, "record carries no run attempt"
    if not evidence.get("source_head"):
        return False, "record carries no source head"
    recorded = evidence.get("digests")
    if not isinstance(recorded, dict) or not recorded:
        return False, "record carries no digests"
    for name in PROPOSAL_FILES:
        if name not in recorded:
            return False, f"record has no digest for {name}"
        if recorded[name] != actual_digests.get(name):
            return False, (
                f"{name} digest {recorded[name]} does not match the published bytes "
                f"{actual_digests.get(name)}"
            )
    return True, "validated candidate bytes match the published proposal"


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Python version bump proposer adapter")
    parser.add_argument("--manifest", help="Path to versions-manifest.json")
    parser.add_argument("--current", help="Override current version (for testing)")
    parser.add_argument("--check", action="store_true", help="Check only, no PR")
    args = parser.parse_args(argv)

    current = args.current or read_current_pin()
    if not current:
        print("::error::Could not read current python-version from .python-version", file=sys.stderr)
        return 1
    print(f"Currently pinned: {current}")

    # Manual override via env
    force_version = os.getenv("FORCE_VERSION", "").strip()
    if force_version:
        print(f"Manual override requested: {force_version}")
        cand = force_version
        # Validate syntax, installability and risk checks still required
        if maintenance.parse_version(cand) is None:
            print(f"::error::Forced version {cand} is not valid X.Y.Z", file=sys.stderr)
            return 1
        classification = maintenance.classify_python_update(current, cand)
        print(f"Forced candidate {cand}: {classification}")
        if classification.get("review_required"):
            print("Forced version requires human review (major/downgrade)")
        # In real workflow, this would write to GITHUB_OUTPUT etc.
        return 0

    # Load manifest (path or default URL fetch is done by workflow; here we read file)
    manifest_path = args.manifest or "manifest.json"
    if not os.path.isfile(manifest_path):
        print(f"Manifest not found at {manifest_path}, nothing to do (deferred)", file=sys.stderr)
        return 0
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        print(f"::error::Failed to read manifest: {e}", file=sys.stderr)
        return 1
    # Enrich manifest entries with published_at via CPython releases API if missing (soak requires release date)
    # The versions-manifest.json lacks published_at; fetch from api.github.com/repos/python/cpython/releases/tags/vX.Y.Z
    try:
        import urllib.request
        token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "python-version-bump"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # data is typically list of {version, files} or dict; handle both
        entries = data if isinstance(data, list) else data.get("versions", [])
        for entry in entries:
            ver = entry.get("version") or entry.get("tag_name", "")
            if not ver:
                continue
            # Normalize to X.Y.Z without v prefix
            v = ver.lstrip("v")
            if entry.get("published_at") or entry.get("release_date"):
                continue
            url = f"https://api.github.com/repos/python/cpython/releases/tags/v{v}"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    rel = json.load(resp)
                    pub = rel.get("published_at") or rel.get("created_at")
                    if pub:
                        entry["published_at"] = pub
            except Exception as e:
                # If not found, try without v prefix or skip (defer without date)
                pass
    except Exception as e:
        print(f"WARN: could not enrich manifest with published_at: {e}", file=sys.stderr)

    soak_days = int(os.getenv("SOAK_DAYS", "30") or "30")
    ignore_soak = os.getenv("IGNORE_SOAK", "").lower() == "true"

    result = maintenance.select_candidate(current, data, soak_days=soak_days, ignore_soak=ignore_soak)
    cand = result.get("candidate")
    if not cand:
        print(result.get("reason", "up-to-date"))
        print("Already on newest usable CPython — nothing to do.")
        # Signal no bump via GITHUB_OUTPUT in workflow
        github_output = os.getenv("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as out:
                out.write("bump=no\n")
        return 0

    if result.get("deferred"):
        print(f"Deferred: {result.get('reason')} — candidate {cand} not proposed yet")
        github_output = os.getenv("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as out:
                out.write("bump=no\n")
                out.write(f"reason={result.get('reason')}\n")
        return 0

    # Validate candidate: exact X.Y.Z numeric, stable, etc.
    classification = maintenance.classify_python_update(current, cand)
    print(f"Candidate: {cand} ({classification.get('kind')} bump)")
    print(f"Classification: {classification}")

    # Write outputs for workflow
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as out:
            out.write(f"current={current}\n")
            out.write(f"latest={cand}\n")
            out.write(f"kind={classification.get('kind')}\n")
            out.write(f"bump=yes\n")

    # In full workflow, next steps would install candidate, validate wheels, run ci.py, etc.
    # This adapter only reports selection; validation is separate job steps using ci.py.
    return 0


if __name__ == "__main__":
    sys.exit(main())
