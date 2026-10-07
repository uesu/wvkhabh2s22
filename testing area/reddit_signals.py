"""Pure Reddit safety/decision helpers for the V3 monitor.

This module is deliberately side-effect-free: no network, no environment
reads, no cache files and no logging.  ``reddit_main_v3.py`` owns I/O and
passes the facts gathered during a run into these helpers.
"""
from __future__ import annotations

import copy
import re


# ---------------------------------------------------------------------------
# Removal / pending-approval classification
# ---------------------------------------------------------------------------

_REMOVED_TITLE_RE = re.compile(
    r"^\s*\*{0,2}\[ ?(?:removed ?by ?(?:the )?moderators?|removed|deleted) ?\]\*{0,2}\s*$",
    re.I,
)
_REMOVED_WHOLE_BODY_RE = re.compile(
    r"^\*{0,2}\[ ?(?:deleted|removed) ?\]\*{0,2}$"
    r"|^\*{0,2}\[ ?removed ?by ?moderator ?\]\*{0,2}$",
    re.I,
)
_REMOVED_NOTICE_RES = (
    (re.compile(r"awaiting (?:moderator )?approval", re.I), "pending approval"),
    (re.compile(r"sorry,? (?:this|the) post (?:has been|was) (?:removed|deleted)", re.I), "removal notice"),
    (re.compile(r"\[ ?removed ?by ?moderator ?\]", re.I), "removed by moderator"),
    (re.compile(r"removed by (?:the )?(?:moderators?|reddit)", re.I), "removed by moderators/filters"),
    (re.compile(r"(?:was|has been) deleted by the person who originally posted it", re.I), "deleted by author"),
)


def removed_post_reason(title: str | None, body: str | None) -> str | None:
    """Return a short reason when a post body/title is a removal notice.

    AutoModerator footers such as "I am a bot, and this action was performed
    automatically" are diagnostic only.  They do not contain a removal phrase
    and must not classify a healthy post as removed.
    """
    t = str(title or "").strip()
    if _REMOVED_TITLE_RE.match(t):
        return "title marker"
    b = re.sub(r"\s+", " ", str(body or "")).strip()
    if not b:
        return None
    if len(b) <= 80 and _REMOVED_WHOLE_BODY_RE.match(b):
        return "whole-body marker"
    head = b[:400]
    for rx, reason in _REMOVED_NOTICE_RES:
        if rx.search(head):
            return reason
    return None


# ---------------------------------------------------------------------------
# Retraction decisions / payload rewrite
# ---------------------------------------------------------------------------


def listing_absence_proves_dead(
    post_id: str,
    published_ts: float,
    now: float,
    listing_ids: set[str] | None,
    listing_oldest_age: float | None,
    *,
    tail_margin_seconds: int = 300,
) -> bool:
    """True when a readable /new listing should still contain the post."""
    if not post_id or listing_ids is None or listing_oldest_age is None:
        return False
    if str(post_id).lower() in {str(x).lower() for x in listing_ids}:
        return False
    try:
        age = float(now) - float(published_ts)
        oldest = float(listing_oldest_age)
    except (TypeError, ValueError):
        return False
    return age <= oldest + max(0, int(tail_margin_seconds))


def retraction_decision(
    *,
    enabled: bool,
    mode: str,
    post_id: str,
    published_ts: float,
    now: float,
    listing_ids: set[str] | None,
    listing_oldest_age: float | None,
    live_removal_reason: str | None,
    tail_margin_seconds: int = 300,
) -> str | None:
    """Return ``edit``/``delete`` when both independent dead proofs exist."""
    if not enabled:
        return None
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"edit", "delete"}:
        return None
    if not listing_absence_proves_dead(
        post_id,
        published_ts,
        now,
        listing_ids,
        listing_oldest_age,
        tail_margin_seconds=tail_margin_seconds,
    ):
        return None
    if not live_removal_reason:
        return None
    return normalized_mode


def tombstone_payload(payload: dict, reason: str | None = None) -> dict:
    """Return a Components V2 payload with a tombstone notice prepended.

    The original title, body, media, stats and buttons are preserved; only a
    notice is inserted and the container accent is greyed.
    """
    out = copy.deepcopy(payload or {})
    notice = "⚠️ **This Reddit post is no longer live.**"
    if reason:
        notice += f" ({reason})"
    notice += "\n-# The original card is kept for context; Reddit removed or deleted the source post after delivery."
    components = out.get("components")
    if not isinstance(components, list) or not components:
        out["content"] = (notice + ("\n\n" + str(out.get("content")) if out.get("content") else ""))[:2000]
        return out
    first = components[0]
    if isinstance(first, dict):
        if first.get("type") == 17:
            first["accent_color"] = 0x808080
            inner = first.setdefault("components", [])
            if isinstance(inner, list):
                already = any(isinstance(c, dict) and c.get("type") == 10 and "no longer live" in str(c.get("content")) for c in inner[:2])
                if not already:
                    inner.insert(0, {"type": 10, "content": notice})
        else:
            components.insert(0, {"type": 10, "content": notice})
    return out


def tombstone_continuation_payload(reason: str | None = None) -> dict:
    """Round 66 (2026-10-05): the tombstone replacement for a body-
    continuation message — the follow-up that carried a long post's body
    remainder after its main card. The main card keeps its full tombstone
    via tombstone_payload; these text-only parts get the same notice,
    greyed, so a retracted long post reads consistently across every
    message that delivered it."""
    notice = "⚠️ **This Reddit post is no longer live.**"
    if reason:
        notice += f" ({reason})"
    notice += "\n-# The original card is kept for context; Reddit removed or deleted the source post after delivery."
    return {
        "flags": 1 << 15,  # IS_COMPONENTS_V2
        "components": [{"type": 17, "accent_color": 0x808080,
                        "components": [{"type": 10, "content": notice}]}],
    }
