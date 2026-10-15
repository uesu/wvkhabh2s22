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
