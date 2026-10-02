"""Pure Reddit safety/decision helpers for the V3 monitor.

This module is deliberately side-effect-free: no network, no environment
reads, no cache files and no logging.  ``reddit_main_v3.py`` owns I/O and
passes the facts gathered during a run into these helpers.
"""
from __future__ import annotations

import copy
import html
import re
import unicodedata
from urllib.parse import urlparse


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
# NSFW classification
# ---------------------------------------------------------------------------

_NSFw_BADGE_RE = re.compile(
    r"<small\b[^>]*class\s*=\s*(['\"])(?=[^'\"]*\bnsfw\b)[^'\"]*\1[^>]*>\s*NSFW\s*</small>",
    re.I | re.S,
)
_POST_PAGE_HINT_RE = re.compile(
    r"\bclass\s*=\s*(['\"])[^'\"]*\b(?:post|post_title|created|score|comments?)\b[^'\"]*\1"
    r"|\bcomments/([a-z0-9]+)/",
    re.I,
)
_UNREADABLE_PAGE_RE = re.compile(
    r"(too many requests|rate limited|not found|404|403|captcha|cloudflare|bot check|error loading|upstream connect error)",
    re.I,
)


def nsfw_from_post_page(page_html: str | None) -> bool | None:
    """Read Redlib's explicit NSFW badge from a rendered post page.

    Returns True for ``<small class="nsfw">NSFW</small>``, False for a
    readable post page without that badge, and None when the page is empty or
    not recognisably readable.  The spoiler badge is separate in Redlib's
    template and is intentionally ignored.
    """
    if not isinstance(page_html, str) or not page_html.strip():
        return None
    if _NSFw_BADGE_RE.search(page_html):
        return True
    if _UNREADABLE_PAGE_RE.search(page_html) and not _POST_PAGE_HINT_RE.search(page_html):
        return None
    if _POST_PAGE_HINT_RE.search(page_html):
        return False
    return None


def nsfw_allowlist_forms(value) -> set[str]:
    """Case-insensitive allowlist forms for a post ID or Reddit author."""
    raw = str(value or "").strip().casefold()
    if not raw:
        return set()
    forms = {raw}
    if raw.startswith("/u/"):
        forms.add(raw[3:])
    elif raw.startswith("u/"):
        forms.add(raw[2:])
    if raw.startswith("t3_"):
        forms.add(raw[3:])
    return forms


def _allowlisted(post_id: str, author: str, allowlist) -> bool:
    candidates = nsfw_allowlist_forms(post_id) | nsfw_allowlist_forms(author)
    allowed: set[str] = set()
    for value in allowlist or ():
        allowed.update(nsfw_allowlist_forms(value))
    return bool(candidates & allowed)


def _bool_or_unknown(value) -> bool | None:
    return value if isinstance(value, bool) else None


def nsfw_gate_reason(
    post_id: str,
    author: str,
    markers: dict | None,
    *,
    allowlist=(),
    fail_open: bool = False,
    require_subreddit: bool = False,
) -> str | None:
    """Return the hold reason for an unsafe/unknown post, else None.

    ``markers`` is the I/O layer's normalized metadata.  Missing, malformed or
    failed metadata returns ``nsfw_unknown`` unless ``fail_open`` is set.  A
    live-page fallback is represented as ``archive_status='missing'`` plus a
    ``page_nsfw`` boolean.
    """
    if _allowlisted(post_id, author, allowlist):
        return None

    unknown = None if fail_open else "nsfw_unknown"
    if not isinstance(markers, dict):
        return unknown
    if markers.get("lookup_error") or markers.get("malformed"):
        return unknown

    archive_status = markers.get("archive_status")
    if archive_status == "missing":
        page_nsfw = _bool_or_unknown(markers.get("page_nsfw"))
        if page_nsfw is True:
            return "nsfw_flag"
        if page_nsfw is False:
            return None
        return unknown

    over_18 = _bool_or_unknown(markers.get("over_18"))
    if over_18 is None:
        return unknown
    thumbnail_nsfw = str(markers.get("thumbnail") or "").strip().casefold() == "nsfw"
    if over_18 or thumbnail_nsfw:
        return "nsfw_flag"

    subreddit_over18 = _bool_or_unknown(markers.get("subreddit_over18"))
    if subreddit_over18 is True:
        return "nsfw_subreddit"
    if require_subreddit and subreddit_over18 is None:
        return unknown

    if markers.get("source_present"):
        source_over18 = _bool_or_unknown(markers.get("source_over_18"))
        if source_over18 is None:
            return unknown
        source_thumbnail_nsfw = str(markers.get("source_thumbnail") or "").strip().casefold() == "nsfw"
        if source_over18 or source_thumbnail_nsfw:
            return "nsfw_crosspost_source"
        source_subreddit_over18 = _bool_or_unknown(markers.get("source_subreddit_over18"))
        if source_subreddit_over18 is True:
            return "nsfw_crosspost_source"
        if require_subreddit and source_subreddit_over18 is None:
            return unknown

    return None


# ---------------------------------------------------------------------------
# Settle timing
# ---------------------------------------------------------------------------


def settle_window_seconds(
    confirmed_in_new: bool,
    *,
    verified_seconds: int = 60,
    unverified_seconds: int = 300,
) -> int:
    """Return the settle window for a post given same-run /new evidence."""
    try:
        verified = max(0, int(verified_seconds))
    except (TypeError, ValueError):
        verified = 60
    try:
        unverified = max(0, int(unverified_seconds))
    except (TypeError, ValueError):
        unverified = 300
    return verified if confirmed_in_new else unverified


def settle_holds(
    published_ts: float,
    now: float,
    *,
    confirmed_in_new: bool = False,
    verified_seconds: int = 60,
    unverified_seconds: int = 300,
) -> bool:
    """True when a post is still inside its evidence-based settle window."""
    window = settle_window_seconds(
        confirmed_in_new,
        verified_seconds=verified_seconds,
        unverified_seconds=unverified_seconds,
    )
    if window <= 0:
        return False
    return (float(now) - float(published_ts)) < window


# ---------------------------------------------------------------------------
# Duplicate identity
# ---------------------------------------------------------------------------

_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")


def reddit_media_key(filename: str) -> str:
    """Strip Reddit preview slug prefixes while preserving bare filenames."""
    name = str(filename or "").split("?", 1)[0].strip("/")
    m = re.search(r"(?:^|-)([A-Za-z0-9]+\.(?:jpe?g|png|gif|webp))$", name, re.I)
    return m.group(1) if m else name


def i_reddit_identity(url: str) -> str | None:
    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in ("i.redd.it", "preview.redd.it") or not parsed.path:
        return None
    return "https://i.redd.it/" + reddit_media_key(parsed.path.rsplit("/", 1)[-1])


def media_identity(url) -> str | None:
    """Return a provable exact-media identity, or None to fail open."""
    raw = str(url or "").strip()
    if not raw.lower().startswith(("http://", "https://")):
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path or ""
    if not host or not path:
        return None
    if host in ("i.redd.it", "preview.redd.it"):
        return i_reddit_identity(raw)
    if host in ("www.reddit.com", "old.reddit.com", "np.reddit.com", "reddit.com", "api.reddit.com"):
        return None
    if host.endswith(".reddit.com") or host.endswith(".redditmedia.com"):
        return None
    return host + path.rstrip("/")


def dup_media_hit(posted_map: dict | None, identity: str | None) -> str | None:
    """Return the posted winner key for identity, otherwise fail open."""
    if not identity or not isinstance(posted_map, dict):
        return None
    return posted_map.get(identity)


def _normalize_fingerprint_part(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = _ZERO_WIDTH_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text).casefold()
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat[0] in ("P", "S"):
            out.append(" ")
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def content_fingerprint(subreddit: str, author: str, title: str) -> str | None:
    """Build a strict ``sub|author|title`` identity for author re-uploads.

    Case, punctuation, whitespace and zero-width differences normalize away,
    then the identity matches exactly.  Unknown/deleted authors or empty titles
    return None so the gate fails open.
    """
    sub = _normalize_fingerprint_part(subreddit)
    auth = _normalize_fingerprint_part(author)
    ttl = _normalize_fingerprint_part(title)
    if not sub or not auth or auth in {"deleted", "unknown", "automoderator"} or not ttl:
        return None
    return f"{sub}|{auth}|{ttl}"


def duplicate_repost_hit(posted_map: dict | None, fingerprint: str | None) -> str | None:
    if not fingerprint or not isinstance(posted_map, dict):
        return None
    return posted_map.get(fingerprint)


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


# Round 46: listing provenance. Reddit RSS includes posts awaiting moderation;
# only an API-backed HTML listing can prove public release.
LISTING_HTML = "html"
LISTING_RSS = "rss"
PENDING_APPROVAL = "pending approval"

def listing_proves_release(in_listing, listing_source: str = LISTING_HTML) -> bool:
    return in_listing is True and listing_source != LISTING_RSS


def page_is_post_page(page_html, post_id) -> bool:
    """True only when ``page_html`` plausibly renders the post ``post_id``.

    A successful HTTP response is not sufficient evidence: mirrors can return
    Cloudflare interstitials, JavaScript shells, or rate-limit pages with a
    200 status.  A rendered Reddit post page carries its own permalink marker.
    """
    h = str(page_html or "")
    pid = str(post_id or "").strip().lower()
    if not h or not pid:
        return False
    return bool(re.search(r"/comments/%s(?![0-9a-z])" % re.escape(pid), h, re.I))


def queue_verdict(*, strong_hold_text=False, weak_hold_text=False,
                  in_listing=None, listing_source=LISTING_HTML,
                  listing_oldest_age=None, page_age_seconds=None,
                  post_age_seconds=None, tail_margin_seconds=0,
                  weak_grace_seconds=0):
    """Decide whether a post must be HELD as awaiting moderator approval.

    ROUND 48 (2026-10-01) — PRECEDENCE FIX. Round 46 asked the listing first:
    a post present in an API-backed /new listing was treated as proof of
    release, and the post page's own banner was never consulted. Live
    incident r/HonkaiStarRail_leaks 1wuwjiw (created 19:06, delivered 19:16,
    page still reading "Post is awaiting moderator approval") shows that
    assumption is not safe: a queued post CAN be visible in a listing.

    Direct evidence from the post's own page now wins. The banner is rendered
    by Reddit for that specific post and says exactly one thing — it is in
    the queue — while listing membership is only an inference. Order:

      1. STRONG page text ("awaiting moderator approval" ...)  -> HOLD
      2. an API-backed listing containing the post             -> release
      3. WEAK text (AutoModerator source-rule comment) inside the grace -> HOLD
      4. round-38d negative space (API-backed listings only)   -> HOLD
      5. otherwise                                             -> release
    """
    if strong_hold_text:
        return PENDING_APPROVAL
    if listing_proves_release(in_listing, listing_source):
        return None
    if weak_hold_text:
        try:
            age = float(post_age_seconds)
        except (TypeError, ValueError):
            age = None
        if age is None or age < float(weak_grace_seconds or 0):
            return PENDING_APPROVAL
    if listing_source == LISTING_RSS or in_listing is None or listing_oldest_age is None:
        return None
    try:
        page_age = float(page_age_seconds)
        span = float(listing_oldest_age)
    except (TypeError, ValueError):
        return None
    return PENDING_APPROVAL if page_age < span + float(tail_margin_seconds or 0) else None
