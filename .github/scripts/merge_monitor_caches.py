#!/usr/bin/env python3
"""Round 61 (2026-10-04): semantic cache merge for the monitor workflows.

WHY THIS EXISTS — the 2026-10-03 double post (prod run #4014 vs cache
commit 2de1491). Two facts collided:

1.  ``workflow_dispatch`` pins GITHUB_SHA at DISPATCH time. A run queued
    behind another (concurrency group, cancel-in-progress: false) starts
    later but still checks out the tip as it was when the trigger arrived —
    WITHOUT the previous run's fresh dedup cache. The sync-to-tip step
    added in round 61 fixes that side.
2.  The persist step's old recovery (``git pull --rebase``) treated a
    content conflict as fatal: both runs had edited the same
    ``last_checked`` line in pending_reddit.json and both had appended to
    posted_reddit.json, git's LINE merge could not resolve it, and the
    whole run exited 1 with the dedup cache unpersisted ("the next run may
    re-post" — and it did).

Git merges lines; these files carry MEANING. This script merges the supported
JSON cache shapes semantically so workflow recovery does not depend on a line
merge of deduplication state:

*   JSON **list** files (e.g. posted_reddit.json, posted_tweets.json):
    the union, keeping the live tip's order first and appending our new
    keys in our order. NEVER drops a key from either side — a dedup key
    lost is a double post; a key kept too long is merely re-evicted by the
    per-sub cap logic on the next run (round 34). Deduplicated, first
    occurrence wins.
*   JSON **dict** files (e.g. pending_reddit.json, posted_messages.json):
    key union. On a per-key collision the entry with the NEWEST embedded
    timestamp wins (max of last_checked / first_seen / delivered_at /
    posted_ts when present); ours wins a timestamp tie or when neither
    side carries a timestamp (ours is the freshest full run).
*   Mixed/other JSON types (e.g. proxy_health.json telemetry when shapes
    diverge): ours wins wholesale — it is run telemetry, not correctness
    state.
*   HYGIENE RULE: when both a posted LIST file and a pending DICT file are
    being merged in one invocation, pending keys already present in the
    merged posted list are dropped. (Not load-bearing: the posting loop
    re-checks ``unique_key in posted`` immediately before the pipeline —
    reddit_main_v3.py round 36 — this just keeps pending_reddit.json
    minimal.)

Usage:
    merge_monitor_caches.py <ours_dir> <repo_dir> <file> [<file> ...]

``ours_dir``  holds OUR freshly-written cache files (copied aside before the
``git reset --hard`` to the live tip). ``repo_dir`` is the work tree sitting
on the live tip (THEIRS). Merged results are written back into
``repo_dir``. A file missing on one side is taken from the other; a file
missing on both sides is skipped. Any unreadable/invalid JSON input makes
the script exit non-zero LOUDLY — the workflow then reports
CACHE PUSH RECOVERY FAILED instead of pushing a half-merged state.

stdlib only; deterministic output (sorted dict keys, 2-space indent,
trailing newline) so repeated merges of identical state are byte-stable.
"""
from __future__ import annotations

import json
import os
import sys

_TS_FIELDS = ("last_checked", "first_seen", "delivered_at", "posted_ts")


def _load(path: str):
    """Return (exists, parsed). Invalid JSON raises — loud by design."""
    if not os.path.isfile(path):
        return False, None
    with open(path, "r", encoding="utf-8") as fh:
        return True, json.load(fh)


def _entry_ts(value) -> float:
    """Newest embedded timestamp of a dict entry; -inf when none present."""
    best = float("-inf")
    if isinstance(value, dict):
        for field in _TS_FIELDS:
            raw = value.get(field)
            try:
                ts = float(raw)
            except (TypeError, ValueError):
                continue
            if ts > best:
                best = ts
    return best


def merge_lists(theirs: list, ours: list) -> list:
    """Union: live-tip order first, our new keys appended; no dupes."""
    merged: list = []
    seen: set = set()
    for item in list(theirs) + list(ours):
        marker = json.dumps(item, sort_keys=True) if isinstance(item, (dict, list)) else item
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(item)
    return merged


def merge_dicts(theirs: dict, ours: dict) -> dict:
    """Key union; newest embedded timestamp wins a collision, ours on tie."""
    merged = dict(theirs)
    for key, our_val in ours.items():
        if key not in merged:
            merged[key] = our_val
            continue
        their_val = merged[key]
        if our_val == their_val:
            continue
        # ours wins ties and timestamp-less collisions (freshest full run)
        merged[key] = our_val if _entry_ts(our_val) >= _entry_ts(their_val) else their_val
    return merged


def merge_values(theirs, ours):
    if isinstance(theirs, list) and isinstance(ours, list):
        return merge_lists(theirs, ours)
    if isinstance(theirs, dict) and isinstance(ours, dict):
        return merge_dicts(theirs, ours)
    # shape mismatch or scalar telemetry: ours wins wholesale
    return ours


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: merge_monitor_caches.py <ours_dir> <repo_dir> <file> [...]",
              file=sys.stderr)
        return 2
    ours_dir, repo_dir, names = argv[1], argv[2], argv[3:]

    merged_by_name: dict = {}
    for name in names:
        ours_exists, ours = _load(os.path.join(ours_dir, name))
        theirs_exists, theirs = _load(os.path.join(repo_dir, name))
        if not ours_exists and not theirs_exists:
            continue
        if not theirs_exists:
            merged_by_name[name] = ours
        elif not ours_exists:
            merged_by_name[name] = theirs
        else:
            merged_by_name[name] = merge_values(theirs, ours)

    # hygiene: drop pending keys that the merged posted list already covers
    posted_lists = [v for v in merged_by_name.values() if isinstance(v, list)]
    if posted_lists:
        posted_keys = {k for lst in posted_lists for k in lst if isinstance(k, str)}
        for name, value in merged_by_name.items():
            if isinstance(value, dict) and "pending" in name:
                merged_by_name[name] = {k: v for k, v in value.items()
                                        if k not in posted_keys}

    for name, value in merged_by_name.items():
        out = os.path.join(repo_dir, name)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=2, sort_keys=isinstance(value, dict),
                      ensure_ascii=False)
            fh.write("\n")
        kind = "list" if isinstance(value, list) else type(value).__name__
        size = len(value) if isinstance(value, (list, dict)) else 1
        print(f"merged {name}: {kind} with {size} entr{'y' if size == 1 else 'ies'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:  # loud by design — never push a half-merge
        print(f"SEMANTIC MERGE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
