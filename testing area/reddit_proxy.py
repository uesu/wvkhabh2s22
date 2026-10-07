# ---------------------------------------------------------------------------
# ■ Reddit proxy media services (round 13; dispatch reworked in round 49) ----
# ---------------------------------------------------------------------------
# Card media (photos / galleries / videos with audio / GIFs) comes from three
# public proxy services. The PRIORITY / TIE-BREAK order is:
#
#   1. vxreddit.com — OpenGraph bot pages (Discordbot UA): every gallery
#      photo as a full-res i.redd.it og:image, videos as a muxed mp4 WITH
#      audio (redditvideo.mp4), stats in og:site_name. The round-49 field
#      table ranks it first: most complete and most stable, and it resolves
#      both /comments/ paths and /s/ share links.
#   2. redditez.com (EmbedEZ) — the keyless search API resolves a permalink
#      to a stable key, then the bot embed page (Discordbot UA) exposes
#      og: tags: photos (incl. galleries) as embedez media URLs, videos as a
#      playable mp4 WITH audio, plus title/body/stats. Field table:
#      intermittent, and it rejects /s/ share links.
#   3. embeddit.deltandy.me — Mastodon-style JSON API (no bot UA needed),
#      plus the round-54 Components-V2 page used when the instance runs the
#      rewrite: ALL gallery photos (up to 20; the rewrite caps at 10), stats
#      + body + author in the JSON. Its video is the v.redd.it DASH fallback
#      stream WITHOUT audio — the `merge=true` id requests a merged copy,
#      but what is served (both eras, and the rewrite's PR #2 so far) is the
#      audio-less fallback, and upstream does not list audio in the rewrite
#      to-do — so embeddit stays the LAST resort for video (round 50 /
#      incident 1wv12qb: a silent embeddit video must never cancel a
#      vxreddit/redditez answer in flight).
#
# Round 49 replaced the serial chain with BOUNDED CONCURRENT WAVES; the order
# above is the tie-break, NOT the request order. Wave 1 dispatches the first
# two eligible services together at t=0; the rest are held back
# PROXY_WAVE_DELAY seconds and are cancelled before opening a socket when
# wave 1 already produced a decisive answer. A decisive result (a video on a
# video post, or MEDIA_CAP_ITEMS items) cancels everything in flight; an
# incomplete gallery may still wait PROXY_GALLERY_GRACE for a straggler that
# could hold a more complete gallery (round 25). PROXY_MAX_CONCURRENCY caps
# in-flight requests. See fetch_proxy_post for the full state machine.
#
# The winning service's own URLs are used VERBATIM in the card (mixing the
# services' CDNs in one card is fine — Discord fetches each media URL
# itself). A per-run warm-up probes all three with one known post and
# writes proxy_health.json (auto-committed with the cache); the posting
# loop skips services the warm-up proved dead — unless ALL are dead, in
# which case every service gets a fresh try per post. If no service can
# serve a post, the caller falls back to the native RSS media path
# (round 12) — this module never blocks posting.
#
# Sources (all public, read 2026-09-16):
#   github.com/dylanpdx/vxReddit     (Flask app; templates/*.html)
#   github.com/DeltAndy123/Embeddit  (Express app; src/richEmbed.ts,
#                                     src/util/encode.ts)
#   embedez.com/api/v1  (keyless search endpoint + bot embed pages, the
#                        same flow the EZ Discord bot uses)
# ---------------------------------------------------------------------------
import os
import re
import json
import time
import asyncio
import logging
import weakref
import html as html_lib
import urllib.parse

import aiohttp

PROXY_HEALTH_FILE = "proxy_health.json"

# Discord's scraper UA — vxreddit and embedez serve bot embed pages to
# social-preview bots only (vxreddit redirects everyone else to reddit.com).
PROXY_BOT_UA = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"

# ---------------------------------------------------------------------------
# ■ ROUND 49 (2026-10-01): PRIORITY ORDER + PARALLEL DISPATCH
# Priority is now evidence-based instead of assumed. Field comparison of the
# three services on the same posts (live screenshots + the prod Actions logs
# of 2026-10-01 11:50 / 11:55):
#   vxreddit  - most complete and most stable; resolves BOTH the canonical
#               /comments/ form and the /s/ share-link form; served the video
#               for HSR 1wurg8f and for the profile post 1wuv4j8.
#   redditez  - intermittent. The 11:55 run logged "redditez: OK" at warm-up
#               and then "redditez had no usable data" for BOTH posts seconds
#               later. Also rejects /s/ share links ("Could not find provider
#               for this url. Malformed url?").
#   embeddit  - weakest: video without audio, GIFs broken. Last resort.
# The order below is the TIE-BREAK order (equal media counts keep the
# earlier service); it is no longer the order in which services are asked,
# because round 49 asks them concurrently — see fetch_proxy_post.
# ---------------------------------------------------------------------------
PROXY_SERVICES = ("vxreddit", "redditez", "embeddit")

# Round 49 dispatch tuning.
# WAVE_DELAY: the third service is held back this long and is never even
#   dispatched when the first two already answered - so the common case still
#   costs two requests, exactly like the serial chain with the round-48d exit.
# GALLERY_GRACE: once SOME media has been found, this is the longest we will
#   wait for a straggler that might hold a MORE COMPLETE gallery (round 25).
#   A decisive answer (video on a video post, or MEDIA_CAP_ITEMS items) ends
#   the wait immediately and cancels whatever is still in flight.
# MAX_CONCURRENCY: a global ceiling on in-flight proxy requests, so a run with
#   many new posts cannot hammer one service.
# 0.5 s is tuned on the prod timings of 2026-10-01 11:55 (vxreddit 0.42 s,
# redditez 0.76 s, embeddit 0.31 s): a healthy vxreddit answers BEFORE the
# delay elapses, so a video post never dispatches the third service at all.
# ---------------------------------------------------------------------------
# ■ GHA-SAFE ENV READERS (round 49b, 2026-10-01)
# A repository Variable wired as `VAR: ${{ vars.VAR }}` arrives as an EMPTY
# STRING when it is not set, so int("")/float("") raises at import. That is
# exactly what killed the 20:10 UTC production runs:
#   reddit_proxy  -> "reddit_proxy module unavailable - native media only:
#                     could not convert string to float: ''"  (proxy chain OFF)
#   twitter_v3    -> ValueError: invalid literal for int() with base 10: ''
# Round 36 already learned this for reddit_main_v3 (_env_int); these are the
# same guard for this module. Empty / blank / garbage -> the documented default.
# ---------------------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or str(default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or str(default))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# ■ ROUND 53 (2026-10-01): THE SAME GUARD FOR *STRING* VARIABLES
# Round 49b fixed the numeric readers but left the two STRING Variables on
# bare os.getenv(name, default) — and that form only uses the default when the
# variable is ABSENT. A workflow line `EMBEDDIT_INSTANCE: ${{ vars.X }}` with
# no Variable set exports an EMPTY STRING, which is present, so the default
# never applied:
#
#   EMBEDDIT_BASE  = ""  -> every request went to the RELATIVE url
#                           "/api/v1/statuses/<id>", which aiohttp rejects with
#                           InvalidURL before a socket is ever opened. The prod
#                           log of 2026-10-01 23:13 shows exactly that:
#                           "embeddit error: /api/v1/statuses/4f1y49…" for
#                           EVERY post — Embeddit was effectively dead, not the
#                           instance (the same status id returns valid JSON
#                           when prefixed with the default host).
#   WARMUP_POST_ID = ""  -> "PROXY_WARMUP_POST '' is invalid … Warm-up skipped"
#                           on every run, so proxy_health.json was never
#                           refreshed and dead services were never skipped.
#
# _env_str() treats unset AND empty/whitespace as "not configured".
# ---------------------------------------------------------------------------
def _env_str(name: str, default: str) -> str:
    """GHA-safe string option: unset, empty and blank all mean the default."""
    return (os.getenv(name) or "").strip() or default

PROXY_WAVE_DELAY = _env_float("PROXY_WAVE_DELAY", 0.5)
PROXY_GALLERY_GRACE = _env_float("PROXY_GALLERY_GRACE", 1.5)
PROXY_MAX_CONCURRENCY = _env_int("PROXY_MAX_CONCURRENCY", 8)

# A reddit SHARE link - /r/<sub>/s/<id> or /u/<name>/s/<id>. The <id> is a
# share token, NOT a post id, so it cannot be rewritten into a /comments/
# path without a network round-trip. redditez/EmbedEZ answers such a url with
# "Could not find provider for this url. Malformed url?" (user-verified), so
# round 49 simply does not spend a request on it; vxreddit resolves it fine.
SHARE_LINK_RE = re.compile(r"^/(?:r|u|user)/[^/\s?]+/s/[^/\s?]+", re.I)

# Round 25: two gallery containers x ten items; nothing more fits a card.
MEDIA_CAP_ITEMS = 20

# --- redditez / EmbedEZ (keyless public API) --------------------------------
REDDITEZ_SEARCH_ENDPOINT = "https://embedez.com/api/v1/providers/search"
REDDITEZ_EMBED_PAGE = "https://embedez.com/embed/{key}"
# What the embed page reports when the EmbedEZ backend — redditez's engine,
# the service that fetches the Reddit post FOR us — is down or could not
# fetch the post from Reddit at that moment. It is a service-side fetch
# failure (not a problem with the post itself), so it means "redditez
# unavailable right now" and the post falls through to the next service.
# Detected per post, not only at warm-up.
REDDITEZ_FAIL_MARKERS = ("Failed to Get Post", "non-JSON response")

# --- vxreddit -----------------------------------------------------------------
VXREDDIT_BASE = "https://www.vxreddit.com"
VXREDDIT_FAIL_MARKERS = ("Failed to get data from Reddit", "Internal server error")
# og:site_name = "u/<author> on r/<sub> - ⬆️ <ups> | 💬 <comments>"
VXREDDIT_STATS_RE = re.compile(r"u/(\S+) on r/(\S+) - ⬆️ (\d+)(?: \| 💬 (\d+))?")

# --- embeddit -------------------------------------------------------------------
# Mastodon-spoof API: GET /api/v1/statuses/<encoded-id> -> JSON. The encoded
# id is Embeddit's idEncode of {"type": "post", "id": <post_id>, "merge":
# true} (the id asks for the merged video, under ~50 MB). Field observation
# (rounds 49/50/53/54): the video this service actually serves is the
# v.redd.it DASH fallback stream WITHOUT audio — see the module header.
EMBEDDIT_DEFAULT_BASE = "https://embeddit.deltandy.me"


def _normalize_instance(raw: str, default: str, variable: str) -> str:
    """Round 53: turn an operator-supplied instance value into a usable base
    URL, or fall back to the documented default with ONE clear log line.

    Accepted: "https://host", "http://host", "host", "host/" and any of those
    with surrounding whitespace (the REDDIT_MIRROR Variable in this repo is
    stored bare, as `embeddit.deltandy.me`, so a bare host must work here).
    Rejected (-> default): empty, whitespace, a scheme other than http(s),
    an embedded space, or anything with no host part.
    """
    value = (raw or "").strip().rstrip("/")
    if not value:
        return default
    if (re.match(r"^[a-z][a-z0-9+.-]*:", value, re.I)
            and not re.match(r"^[a-z][a-z0-9+.-]*:\d+(?:/|$)", value, re.I)):
        candidate = value               # has an explicit scheme — keep it
    else:
        # bare host (possibly host:port — "localhost:3000" is a port, not a
        # scheme) — assume https
        candidate = f"https://{value}"
    parts = urllib.parse.urlsplit(candidate)
    if (parts.scheme.lower() not in ("http", "https") or not parts.netloc
            or re.search(r"\s", candidate)):
        logging.warning(
            f"{variable} '{raw}' is not a usable http(s) host — "
            f"falling back to {default}.")
        return default
    return candidate.rstrip("/")


# EMBEDDIT_INSTANCE (repo Variable) overrides the host; empty/unset/invalid
# keeps the public default instance (round 53).
EMBEDDIT_BASE = _normalize_instance(
    _env_str("EMBEDDIT_INSTANCE", EMBEDDIT_DEFAULT_BASE),
    EMBEDDIT_DEFAULT_BASE, "EMBEDDIT_INSTANCE")
EMBEDDIT_HOST = EMBEDDIT_BASE.split("://", 1)[-1]

# ---------------------------------------------------------------------------
# ■ ROUND 53: RUN-SCOPED EMBEDDIT AVAILABILITY
# ■ ROUND 54: COMPONENT-EMBED READINESS (Embeddit rewrite auto-adaptation)
# The Embeddit rewrite (DeltAndy123/Embeddit, branch `rewrite`, sources read
# 2026-10-02) is a full rewrite to Bun + Hono that REMOVES the Mastodon spoof:
# the rewrite's router has no /api/v1/statuses route at all (src/index.ts:
# only /r/*, /u/*, /user/*, /test). Instead, the post page itself — the SAME
# /r/<sub>/comments/<id>/<title> path this module already builds — serves
# bots (botOnly middleware, our Discordbot UA qualifies) an HTML shell whose
# only payload is
#     <script id="discord:component-embed" type="application/json">
#       {"component": {<Discord Components-V2 container>}}
#     </script>
# (src/views/EmbedPage.tsx + JsonScript.tsx). That JSON holds everything the
# Mastodon JSON held: header "-# in **[r/sub](…)**  •  by **[u/author](…)**",
# title "### <title>", a MediaGallery (images / gallery with captions / the
# video fallback_url), the selftext body, a stats line
# "**<:u:…>  N   •   <:c:…>  M**" and the footer (src/embed/builders/post.ts,
# src/embed/parts.ts, src/embed/components.ts).
#
# The adaptation is a run-scoped three-state machine:
#   mastodon (default)  — today's behavior, byte-for-byte: the status API is
#                         asked first; timeouts/429/5xx/parse misses stay
#                         per-post misses exactly as before.
#   component           — entered automatically when the status API answers
#                         ROUTE-GONE evidence (404/410/501, or a 200 that is
#                         not JSON — the live pre-rewrite instance answers an
#                         unknown/dead POST with Cloudflare 502, verified
#                         2026-10-01, never 404) AND the component page for
#                         the same post parses. One WARNING line, then every
#                         later embeddit lookup this run goes straight to the
#                         component page. Notes: the rewrite's video is the
#                         DASH fallback stream (no audio — unchanged from the
#                         Mastodon era, embeddit stays last-resort for video)
#                         and galleries cap at 10 items (MAX_GALLERY_ITEMS).
#   unavailable         — route gone AND the component page did not parse
#                         either: the round-53 behavior — one WARNING, then
#                         embeddit is dropped from the dispatch order for the
#                         rest of the run (never below one service).
# A new process (= every monitor run) starts back at "mastodon", so the probe
# order re-verifies the instance each run with zero extra requests in the
# common case.
# ---------------------------------------------------------------------------
_embeddit_unavailable: "str | None" = None
_embeddit_component_mode = False


def embeddit_unavailable_reason() -> "str | None":
    """Why Embeddit is skipped for the rest of this run (None = available)."""
    return _embeddit_unavailable


def embeddit_component_mode() -> bool:
    """True when this run auto-switched to the rewrite's Component-Embed
    page after detecting that the Mastodon status API is gone (round 54)."""
    return _embeddit_component_mode


def reset_embeddit_availability():
    """Clear the run-scoped state (used by the tests; a new process starts
    clean anyway)."""
    global _embeddit_unavailable, _embeddit_component_mode
    _embeddit_unavailable = None
    _embeddit_component_mode = False


def _mark_embeddit_unavailable(reason: str):
    global _embeddit_unavailable
    if _embeddit_unavailable is None:
        _embeddit_unavailable = reason
        logging.warning(
            f"embeddit: {EMBEDDIT_HOST} serves neither the Mastodon status "
            f"API ({reason}) nor a parseable Component-Embed page — skipping "
            f"embeddit for the rest of this run (redditez/vxreddit/native "
            f"media are unaffected). Set the EMBEDDIT_INSTANCE Variable to a "
            f"working instance, or leave it unset once the service is back.")


def _mark_embeddit_component_mode(reason: str):
    global _embeddit_component_mode
    if not _embeddit_component_mode:
        _embeddit_component_mode = True
        logging.warning(
            f"embeddit: {EMBEDDIT_HOST} no longer serves the Mastodon status "
            f"API ({reason}) but DOES serve the rewrite's Component-Embed "
            f"page (the Embeddit rewrite) — switched to the component parser for "
            f"the rest of this run. Caveats unchanged from the field table: "
            f"video is the audio-less fallback stream and galleries cap at "
            f"10 items, so embeddit remains the last-resort service.")
EMBEDDIT_ENCODE_CHARS = "1234567890abcdefghijklmnopqrstuvwxyz"
# Footer line inside the content HTML: "⬆️ 305 • 💬 21"
# (compact numbers occur too: "⬆️ 1.1K • 💬 108")
EMBEDDIT_STATS_RE = re.compile(
    r"⬆️ (\d[\d,]*(?:\.\d+)?[KkMm]?) • 💬 (\d[\d,]*(?:\.\d+)?[KkMm]?)")


def _compact_int(s: str) -> int:
    """'1.1K' -> 1100, '2M' -> 2000000, '1,234' -> 1234, '57' -> 57."""
    s = (s or "").strip().replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KkMm])?", s)
    if not m:
        return 0
    n = float(m.group(1))
    if m.group(2):
        n *= 1_000_000 if m.group(2).upper() == "M" else 1_000
    return int(n)


# account.display_name = "u/<author> (@ r/<subreddit>)"
EMBEDDIT_AUTHOR_RE = re.compile(r"u/(\S+) \(@ (r/[^\s)]+)")

# --- embeddit rewrite Component-Embed page (round 54) ------------------------
# The bot page's only payload. Attribute order is as emitted by EmbedPage.tsx
# today, but the regex tolerates any order/quoting around the id.
EMBEDDIT_COMPONENT_SCRIPT_RE = re.compile(
    r"<script\b[^>]*\bid=(?P<q>[\"'])discord:component-embed(?P=q)[^>]*>"
    r"(?P<json>.*?)</script>", re.I | re.S)
# Discord Components V2 type ids (discord-api-types/v10) used by the rewrite.
DC_ACTION_ROW, DC_SECTION, DC_TEXT = 1, 9, 10
DC_THUMBNAIL, DC_GALLERY, DC_SEPARATOR, DC_CONTAINER = 11, 12, 14, 17
# Custom-emoji markup inside the stats line: "<:u:…>  168   •   <:c:…>  56"
DISCORD_CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
# The stats TextDisplay once the emoji markup is stripped — the WHOLE line
# must match, so a body line that merely contains "•" can never be eaten.
COMPONENT_STATS_LINE_RE = re.compile(
    r"^\*\*\s*(\d[\d,]*(?:\.\d+)?[KkMm]?)\s*•\s*(\d[\d,]*(?:\.\d+)?[KkMm]?)\s*\*\*$")
# Header small line: "-# in **[r/sub](…)**  •  by **[u/author](…)**"
COMPONENT_SUB_RE = re.compile(r"\[(r/[^\]\s]+)\]\(")
COMPONENT_AUTHOR_RE = re.compile(r"\[u/([^\]\s]+)\]\(")

# Icon-style stats (redditez): "💬 152 🔁 0 💜 573 👀 0"
STATS_ICONS_RE = re.compile(r"💬\s*(\d[\d,]*)\s*🔁\s*(\d[\d,]*)\s*💜\s*(\d[\d,]*)")

# Warm-up: one known-good single-image post used to probe all three
# services once per run (override: PROXY_WARMUP_POST=<subreddit>/<post_id>).
DEFAULT_WARMUP_POST = "HonkaiStarRail_leaks/1whbjbh"
WARMUP_POST_ID = _env_str("PROXY_WARMUP_POST", DEFAULT_WARMUP_POST)

PROXY_TIMEOUT = aiohttp.ClientTimeout(total=20)


# ---------------------------------------------------------------------------
# ■ Embeddit status-id codec (port of src/util/encode.ts)
# ---------------------------------------------------------------------------
def status_id_encode(obj) -> str:
    """Embeddit's idEncode, ported 1:1 (live-verified 2026-09-16 against the
    hosted instance): every character of the compact JSON string becomes
    two chars of EMBEDDIT_ENCODE_CHARS (code // 36, then code % 36 — the
    alphabet is 36 chars)."""
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    size = len(EMBEDDIT_ENCODE_CHARS)
    out = []
    for ch in text:
        code = ord(ch)
        out.append(EMBEDDIT_ENCODE_CHARS[code // size])
        out.append(EMBEDDIT_ENCODE_CHARS[code % size])
    return "".join(out)


def status_id_decode(encoded: str) -> str:
    """Inverse of status_id_encode (used by the smoke test)."""
    size = len(EMBEDDIT_ENCODE_CHARS)
    out = []
    for i in range(0, len(encoded) - 1, 2):
        code = (EMBEDDIT_ENCODE_CHARS.index(encoded[i]) * size
                + EMBEDDIT_ENCODE_CHARS.index(encoded[i + 1]))
        out.append(chr(code))
    return "".join(out)


# ---------------------------------------------------------------------------
# ■ Parsing helpers (pure — unit-testable offline)
# ---------------------------------------------------------------------------
def _og_meta(page_html: str) -> dict:
    """og:/twitter: meta tags -> dict. og:image collects ALL occurrences in
    order (galleries emit one tag per photo); every other key keeps the
    first value. HTML entities are unescaped."""
    meta = {}
    for m in re.finditer(r"<meta[^>]+>", page_html, re.I):
        tag = m.group(0)
        pm = re.search(r'property="([^"]+)"', tag) or re.search(r'name="([^"]+)"', tag)
        cm = re.search(r'content="([^"]*)"', tag)
        if not (pm and cm):
            continue
        key = pm.group(1).strip().lower()
        val = cm.group(1).strip()
        for _ in range(3):  # unescape until stable (double-escaped pages)
            k2, v2 = html_lib.unescape(key), html_lib.unescape(val)
            if (k2, v2) == (key, val):
                break
            key, val = k2, v2
        if key == "og:image":
            meta.setdefault("og:image", []).append(val)
        else:
            meta.setdefault(key, val)
    return meta


def _strip_html(value) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<span\s+[^>]*\bclass=[\"\x27][^\"\x27]*\b(?:md-)?spoiler(?:-text)?\b[^\"\x27]*[\"\x27][^>]*>(.*?)</span>", r"||\1||", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    lines = [re.sub(r"\s{2,}", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


_BLOCK_BOUNDARY_RE = re.compile(r"(?i)</?(?:p|div|li|tr|table|h[1-6]|blockquote|ul|ol)\b[^>]*>")



# ---------------------------------------------------------------------------
# ■ ROUND 20/21 (2026-09-17): SIMPLE RAW 'PLAIN LINK' MANGLE FIX (1whe2tr)
# Replaces the round 19 rule with the simple fix. The original post is a
# RAW plain line ("Firefly video" + a bare URL), so a mangled line — the
# feed's doubled/nested URL-link garbage, e.g.
#   Firefly video [[U](U)](U](U))     (the doubled card shape)
#   Firefly video [U](U](U))          (the feed's plain shape)
#   the multi-line family, or any deeper nesting of the same shape
# — collapses to ONE plain line with the BARE URL exactly once, raw —
# exactly like the original post (Discord auto-links the bare URL in the
# component v2 container; NO markdown wrapping):
#   Firefly video https://b23.tv/...
# Detection (all must hold): the line's URL appears 2+ times (a mangle
# repeats it), the URL sits right after a markdown '[' opener, and the
# line is not fully explained by well-formed '[text](url)' links + prose
# (a mangle leaves the URL in garbage tails like '](U](U))' or
# unbalanced brackets). Clean single links, repeated real links, raw
# label+URL lines, bare-URL lines, prose and every round 15/16 shape are
# left byte-identical.
# ---------------------------------------------------------------------------
_LINK_OK = re.compile(r"\[[^\[\]]*\]\(https?://[^\s()]*\)")

def _fix_doubled_link_line(line: str) -> str:
    um = re.search(r"https?://[^\s\[\]\(\)]+", line)
    if not um:
        return line
    url = um.group(0)
    # A mangle repeats the same URL; a plain line has it at most twice
    # (the '[U](U)' text + target of ONE real link).
    if line.count(url) < 2:
        return line
    head = line[:um.start()]
    # The URL must sit right after a markdown '[' opener (the feed wraps
    # the bare URL in a link); repeated bare URLs in prose are untouched.
    if not re.search(r"\[[ \t]*$", head):
        return line
    # Never touch a line whose label part already contains another link.
    if "](" in head:
        return line
    # Mangle evidence: strip every well-formed '[text](url)' link; a
    # mangle still leaves the URL in garbage tails ('](U](U))') or
    # unbalanced brackets ('[['). Clean lines are fully explained by
    # real links + prose.
    rest = _LINK_OK.sub("", line)
    if url not in rest and rest.count("[") == rest.count("]"):
        return line
    label = re.sub(r"[\[\][ \t]+", " ", head)
    label = re.sub(r"\s{2,}", " ", label).strip()
    # ONE plain line, the bare URL exactly once — raw, exactly like the
    # original post. Discord auto-links the bare URL in the container.
    return (label + " " if label else "") + url


# ---------------------------------------------------------------------------
# ■ ROUND 22 (2026-09-17): CLEAN AUTO-LINKED 'PLAIN LINK' FACE (1whe2tr)
# The round-20/21 fix covers the feed's MANGLED faces and the raw
# 'label / bare URL' pairs, but the same post can also arrive from a
# source that AUTO-LINKS the post's bare URLs (the post-page rendering):
# the original line 'Firefly video https://b23.tv/…' becomes the CLEAN
# markdown 'Firefly video [https://b23.tv/…](https://b23.tv/…)'. Clean
# links are otherwise intentionally left untouched (rounds 15/20/21) —
# but the components-v2 card renders the body as PLAIN TEXT, so a link
# whose text IS its own URL shows its literal brackets:
#   'Firefly video [https://b23.tv/…](https://b23.tv/…)'
# Round 22 collapses such URL-labelled links to the bare URL —
#   'Firefly video https://b23.tv/…'
# — exactly the original line (Discord auto-links it in the container).
# Scope guards: a line with mangle residue (a leftover URL or unbalanced
# brackets after stripping well-formed links) is left byte-identical for
# the round-20/21 repairers; descriptive links ('[text](U)' with text
# different from U) are untouched; prose is untouched.
# ---------------------------------------------------------------------------
_URL_LABELLED = re.compile(r"\[(https?://[^\s\[\]]+)\]\(\1\)")

def _unwrap_url_labelled_links(line: str) -> str:
    """Round 22: on a CLEAN line (no mangle residue), turn every
    URL-labelled markdown link '[U](U)' into the bare URL 'U'. Lines
    with mangle residue or only descriptive links pass through
    byte-identical."""
    if not _URL_LABELLED.search(line):
        return line
    rest = _LINK_OK.sub("", line)
    if re.search(r"https?://", rest) or rest.count("[") != rest.count("]"):
        return line
    return _URL_LABELLED.sub(r"\1", line)


def _repair_label_url_mangle(lines: list) -> list:
    """Round 20/21/22 pre-pass for repair_mangled_link_lines:
      * a mangled link line collapses to one plain 'label + bare URL'
        line (round 20);
      * a bare URL line under a plain label line (the original post's
        "label / URL" pair, at most one blank line between) becomes ONE
        plain 'label URL' line (round 21);
      * a CLEAN URL-labelled link '[U](U)' (a source auto-linking the
        post's bare URL) becomes the bare URL (round 22).
    Clean lines and the round 15/16 shapes pass through byte-identical
    (descriptive links stay markdown); repair_mangled_link_lines still
    runs afterwards and keeps handling the multi-line family exactly as
    before."""
    out = []
    for raw in lines:
        line = _fix_doubled_link_line(raw.strip())
        line = _unwrap_url_labelled_links(line)
        if re.fullmatch(r"https?://[^\s\[\]]+", line) and out:
            if out[-1] and not re.search(r"https?://", out[-1]):
                out[-1] = out[-1] + " " + line
                continue
            if (len(out) >= 2 and out[-1] == "" and out[-2]
                    and not re.search(r"https?://", out[-2])):
                out[-2] = out[-2] + " " + line
                out.pop()
                continue
        out.append(line)
    return out


def repair_mangled_link_lines(lines: list) -> list:
    """Repair the feed's mangled markdown-link pairs.

    A source line 'Word rest [U](U)' can arrive as a tail line
    'Word](prev-url)' (the url may be doubled: 'Word](u)](u)'), the
    post's own blank line, and a continuation 'Word) rest [U' or
    'Word) rest [[U](U)' (the feed re-glues an extra '[' before the
    link; the final line also gets a '](u)](u))' tail glued on).
    Repairs (round 22: the repaired link becomes the BARE URL, raw —
    the components-v2 card renders the body as plain text, so a
    markdown link would show its literal brackets):
      * tail + continuation (at most one blank line between) ->
        'Word rest U'; the url is the continuation's own link when
        complete, else the next tail's url, else the opener's;
      * leftover '[[U](U)' -> '[U](U)';
      * an orphan continuation 'Word) rest [U](U)' whose tail was dropped
        keeps the word: 'Word rest U';
      * a pure tail line with no continuation is left for the junk filter.
    """
    tail_re = re.compile(r"([^\s\[\]]+)\]\(https?://[^\s]*?\)?")
    dbl_re = re.compile(r"\[\[(https?://[^\s\[\]]+)\]\((https?://[^\s\[\]]+)\)")
    link_re = re.compile(r"\[(https?://[^\s\[\]]+)\]\((https?://[^\s\[\]]+)\)")
    opener_re = re.compile(r"\[(https?://[^\s\[\]]+)$")
    glue_re = re.compile(r"(?:\]?\(https?://[^\s\[\]]*\)?[\)\]]*)+$")

    def _finish(rest, nxt):
        rest = dbl_re.sub(r"[\1](\2)", rest)
        m = link_re.search(rest)
        if m:
            before = rest[:m.start()].strip()
            after = rest[m.end():].strip()
            # round 22: the bare URL (the link's target), raw
            text = m.group(2)
            if before:
                text = before + " " + text
            if after and not glue_re.fullmatch(after):
                text += " " + after
            return text
        um = opener_re.search(rest)
        if um:
            close = None
            if nxt is not None:
                nm = tail_re.fullmatch(nxt)
                if nm:
                    close = re.sub(r"\].*$", "", nm.group(0)[len(nm.group(1)) + 2:]).rstrip(")")
            # round 22: the bare URL (the tail's target when it knows one)
            text = close or um.group(1)
            before = rest[:um.start()].strip()
            if before:
                text = before + " " + text
            return text
        return rest.rstrip()

    out = []
    seen_tails = set()
    i = 0
    n = len(lines)
    while i < n:
        tm = tail_re.fullmatch(lines[i])
        if tm:
            seen_tails.add(tm.group(1))
        if tm and i + 1 < n:
            word = tm.group(1)
            j = i + 1
            if lines[j] == "":
                j += 1
            if j < n and lines[j].startswith(word + ")"):
                rest = lines[j][len(word) + 1:]
                text = _finish(rest, lines[j + 1] if j + 1 < n else None)
                merged = word
                if text:
                    if re.match(r"[A-Za-z0-9\[]", text[0]):
                        merged += " " + text
                    else:
                        merged += text
                out.append(merged.rstrip())
                if j == i + 2:
                    out.append("")
                i = j + 1
                continue
        line = dbl_re.sub(r"[\1](\2)", lines[i])
        cm = re.match(r"^(\S+)\)\s+(.*)$", line)
        if cm and cm.group(1) in seen_tails and (link_re.search(line) or opener_re.search(line)):
            line = cm.group(1) + " " + dbl_re.sub(r"[\1](\2)", cm.group(2)).strip()
        out.append(line)
        i += 1
    return out


def _apply_quote_markers(lines: list) -> list:
    """Round 16: \x01/\x02 blockquote markers -> Discord '> ' quote lines."""
    out = []
    quote = False
    for ln in lines:
        appended = False
        had_markers = ("\x01" in ln) or ("\x02" in ln)
        while "\x01" in ln or "\x02" in ln:
            marker = "\x01" if "\x01" in ln else "\x02"
            pos = ln.find(marker)
            if pos:
                out.append(("> " + ln[:pos]) if quote else ln[:pos])
                appended = True
            ln = ln[pos + 1:]
            quote = marker == "\x01"
        if ln:
            out.append("> " + ln if quote else ln)
        elif quote and not appended:
            out.append(">")
        elif not had_markers:
            out.append(ln)
    return out


def _collapse_blanks(lines: list) -> str:
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


def _line_stage(lines: list) -> list:
    """Drop recognizable feed navigation/footer artifacts, not prose."""
    kept = []
    for line in repair_mangled_link_lines(_repair_label_url_mangle(lines)):
        if line.lower() in ("link", "comments", "[link]", "[comments]", "permalink"):
            continue
        if re.match(r"^submitted\)?\s+by\s+\[?\s*/?u/", line, re.I):
            continue
        # merged linked footer on ONE line ("[](url) submitted by [/u/x](url)
        # to [r/y](url)") — the feed emits it merged, so an anchored rule misses it
        if re.search(r"submitted\s+by\s+\[?\s*/?u/", line, re.I):
            continue
        if re.fullmatch(r"\[\s*\]\]?\(https?://\S+\)", line):
            continue
        if re.fullmatch(r"[^\s\[\]]+\]\(https?://\S+?\)?", line):
            continue
        if re.fullmatch(r"\[https?://[^\s\[\]]+", line):
            continue
        line = re.sub(r"\[[^\]]*\]\(https?://v\.redd\.it/[^\s)]*\)", "", line)
        line = re.sub(r"https?://v\.redd\.it/[^\s<>\])]*", "", line)
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        # round 21: a bare URL line stays RAW — Discord auto-links it in
        # the component v2 container (no markdown wrapping).
        kept.append(line)
    return kept

def clean_proxy_body(value) -> str:
    """selftext/og:description HTML -> clean Discord-markdown card text:
      • <a href="URL">text</a>  ->  [text](URL)   (links stay clickable)
      • <b>/<strong>            ->  **bold**
      • paragraph/list/heading boundaries -> real newlines
      • redd.it media links ([text](url)) and BARE redd.it URLs are removed
        — the media already sits in the gallery, not in the text (fixes the
        "...s=cd816…dc0Seems like the..." glued-URL artifact)
    """
    if not value:
        return ""
    text = re.sub(r"(?is)<span\s+[^>]*\bclass=[\"\x27][^\"\x27]*\b(?:md-)?spoiler(?:-text)?\b[^\"\x27]*[\"\x27][^>]*>(.*?)</span>", r"||\1||", value)
    text = re.sub(r'(?is)<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r'[\2](\1)', text)
    text = re.sub(r"(?is)<(?:b|strong)\s*>(.*?)</(?:b|strong)>", r"**\1**", text)
    # round 16: same structural markdown as the RSS path
    text = re.sub(r"(?is)<blockquote[^>]*>", "\x01", text)
    text = re.sub(r"(?i)</blockquote>", "\x02", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</li\s*>", " ", text)
    text = re.sub(r"(?i)</?(?:ul|ol)[^>]*>", "\n", text)
    text = _BLOCK_BOUNDARY_RE.sub("\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    # round 16: the feed double-escapes entities (&amp;gt;) — unescape to stable
    for _ in range(3):
        unescaped = html_lib.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    # round 16: relative reddit links (selftext user/wiki links) -> absolute
    text = re.sub(r"\]\(/u/([^\s/]+)/?\)", r"](https://www.reddit.com/user/\1/)", text)
    text = re.sub(r"\]\(/r/([^\s)]+)\)?",
                  lambda m: "](" + "https://www.reddit.com/r/" + m.group(1) + ")", text)
    # redd.it media link -> gone (gallery has the image)
    text = re.sub(r"\[[^\]]*\]\(\s*https?://(?:i\.|preview\.|external-preview\.)?redd\.it/[^\s)]*\s*\)", " ", text)
    # bare redd.it URL -> gone (but never touch a markdown link target)
    text = re.sub(r"(?<!\]\()https?://(?:i\.|preview\.|external-preview\.)?redd\.it/[^\s<>)\]]+(?!\))", " ", text)
    lines = [re.sub(r"\s{2,}", " ", ln).strip() for ln in text.splitlines()]
    lines = _apply_quote_markers(lines)
    return _collapse_blanks(_line_stage(lines))


def parse_icon_stats(line: str):
    """"💬 152 🔁 0  573 👀 0" -> {"comments": 152, "ups": 573} or None."""
    m = STATS_ICONS_RE.search(line or "")
    if not m:
        return None
    return {
        "comments": int(m.group(1).replace(",", "")),
        "ups": int(m.group(3).replace(",", "")),
    }


def parse_redditez_search(data: dict):
    """Search API JSON -> the stable search key (or None)."""
    if not isinstance(data, dict):
        return None
    inner = data.get("data")
    if isinstance(inner, dict) and inner.get("key"):
        return inner["key"]
    return None


def _media_file_key(url: str):
    """reddit file ID for an i.redd.it / preview.redd.it URL -> 'id.ext'
    (slug-v0 prefix removed), None for any other URL (e.g. embedez
    redirects, which carry no file id)."""
    m = re.match(r"https?://(?:i|preview)\.redd\.it/([\w.-]+\.(?:jpe?g|png|gif|webp))",
                 url or "", re.I)
    if not m:
        return None
    return re.sub(r"^.+-v\d+-", "", m.group(1).lower())


def dedupe_proxy_media(media: list) -> list:
    """Drops the duplicate gallery items the proxy embed pages add:
      1. the SAME reddit file id twice (same photo via a different
         CDN/rendition — e.g. [2pnv1.jpeg, 2pnv1.jpeg])
      2. a 140px feed-crop thumbnail item (width=140 / crop=1:1)
      3. the extra 'main image' tag: the page appends ONE more og:image
         (the post's main photo) after the N real media items — e.g.
         [embedez.0, embedez.1, embedez.2, i.redd.it/<main photo>]
    """
    if len(media) <= 1:
        return list(media)
    out = []
    seen = set()
    for m in media:
        k = _media_file_key(m.get("url", ""))
        if k:
            if k in seen:
                continue
            seen.add(k)
        out.append(m)
    # 2: 140px feed-crop thumbnails are never real media
    out = [m for m in out
           if not (re.search(r"(?:[?&]width=140\b|[?&]height=140\b)", m.get("url", ""))
                   or "crop=1:1" in m.get("url", ""))]
    # 3: trailing 'main image' duplicate (N embedez redirects + 1 redd.it tag)
    n_redirects = sum(1 for m in out
                      if "embedez.com/api/v2/redirect" in m.get("url", ""))
    if (len(out) >= 2 and n_redirects >= 1 and len(out) == n_redirects + 1
            and _media_file_key(out[-1].get("url", ""))):
        out = out[:-1]
    return out


def parse_embeddit_post(data: dict):
    """Embeddit /api/v1/statuses JSON -> normalized dict (or None).
    Normalized shape (shared by all three services):
      {"service", "title", "author", "subreddit", "body", "stats",
       "media": [{"kind": image|gif|video, "url": ...}, ...]}
    """
    if not isinstance(data, dict) or not data.get("account"):
        return None
    account = data["account"]
    author = None
    subreddit = None
    dm = EMBEDDIT_AUTHOR_RE.search(str(account.get("display_name") or ""))
    if dm:
        author = dm.group(1)
        subreddit = dm.group(2)[2:]
    content = data.get("content") or ""
    title = None
    body_lines = []
    stats = None
    tm = re.search(r"(?is)<a\s[^>]*>\s*<b>(.*?)</b>\s*</a>", content)
    if tm:
        # markdown-rendered shape: <a><b>title</b></a> link first, then the
        # body in <br>/<div> blocks, then a <b>⬆️ N • 💬 M</b> stats footer
        title = html_lib.unescape(tm.group(1)).strip()
        text = clean_proxy_body(content)
        lines = [ln for ln in text.splitlines() if ln]
        body_lines = lines[1:]  # line 1 is the [title](permalink) link
        if body_lines:
            fm = EMBEDDIT_STATS_RE.search(body_lines[-1])
            if fm:
                stats = {
                    "ups": _compact_int(fm.group(1)),
                    "comments": _compact_int(fm.group(2)),
                }
                body_lines = body_lines[:-1]  # it becomes the stats row
    else:
        # plain-text shape (no anchor) — everything glued on one line:
        # "Titlehttps://v.redd.it/…⬆️ N • 💬 M" (live: 1wfz61f, 1wgjk4a)
        plain = html_lib.unescape(re.sub(r"(?i)<br\s*/?>", "\n", content))
        plain = re.sub(r"<[^>]+>", " ", plain)
        fm = EMBEDDIT_STATS_RE.search(plain)
        if fm:
            stats = {
                "ups": _compact_int(fm.group(1)),
                "comments": _compact_int(fm.group(2)),
            }
            plain = plain[:fm.start()].rstrip()
        # bare media/video URLs glued to the text -> gone (the media sits in
        # the gallery / video tile)
        plain = re.sub(r"https?://[^\s<>]+", " ", plain)
        body_lines = [re.sub(r"\s{2,}", " ", ln).strip()
                      for ln in plain.splitlines()]
        body_lines = [ln for ln in body_lines if ln]
        title = body_lines[0] if body_lines else None
        body_lines = body_lines[1:]
    media = []
    for att in data.get("media_attachments") or []:
        url = att.get("url") if isinstance(att, dict) else None
        if not url:
            continue
        if att.get("type") == "video":
            media.append({"kind": "video", "url": url})
        else:
            kind = "gif" if url.split("?")[0].lower().endswith(".gif") else "image"
            media.append({"kind": kind, "url": url})
    return {
        "service": "embeddit",
        "title": title,
        "author": author,
        "subreddit": subreddit,
        "body": "\n".join(body_lines).strip(),
        "stats": stats,
        "media": media,
    }


def _component_media_kind(url: str) -> str:
    """Media kind from a rewrite gallery/thumbnail URL. The rewrite's video
    post puts the v.redd.it DASH fallback mp4 in the gallery."""
    base = (url or "").split("?")[0].lower()
    if base.endswith(".gif"):
        return "gif"
    if base.endswith((".mp4", ".webm", ".mov")) or "v.redd.it" in base:
        return "video"
    return "image"


def parse_embeddit_component_page(page_html: str):
    """Embeddit-rewrite bot page -> the SAME normalized dict every
    other parser in this module emits (or None).

    The page is an HTML shell whose only payload is the
    <script id="discord:component-embed" type="application/json"> JSON:
    {"component": {type:17 Container, components:[…]}} — see the round-54
    header. Walked recursively; only component types the rewrite actually
    emits are read, unknown types are skipped (forward-compatible). Pure —
    unit-testable offline.
    """
    m = EMBEDDIT_COMPONENT_SCRIPT_RE.search(page_html or "")
    if not m:
        return None
    try:
        data = json.loads(m.group("json"))
    except ValueError:
        return None
    component = data.get("component") if isinstance(data, dict) else None
    if not isinstance(component, dict):
        return None

    texts: list = []
    media: list = []

    def walk(node):
        if not isinstance(node, dict):
            return
        ntype = node.get("type")
        if ntype == DC_TEXT:
            content = node.get("content")
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
        elif ntype == DC_GALLERY:
            for item in node.get("items") or []:
                if isinstance(item, dict):
                    url = (item.get("media") or {}).get("url")
                    if url:
                        media.append({"kind": _component_media_kind(url),
                                      "url": url})
        elif ntype == DC_SECTION:
            for child in node.get("components") or []:
                walk(child)
            accessory = node.get("accessory")
            if isinstance(accessory, dict) and accessory.get("type") == DC_THUMBNAIL:
                url = (accessory.get("media") or {}).get("url")
                if url:   # link-post preview thumbnail
                    media.append({"kind": _component_media_kind(url),
                                  "url": url})
        elif ntype in (DC_CONTAINER, DC_ACTION_ROW):
            for child in node.get("components") or []:
                walk(child)
        # Separators, buttons, thumbnails outside sections, and any FUTURE
        # component type carry no post data for the card — skipped.

    walk(component)
    if not texts and not media:
        return None

    title = None
    author = None
    subreddit = None
    stats = None
    body_lines = []
    for text in texts:
        if title is None and text.startswith("### "):
            title = text[4:].strip()
            continue
        if text.startswith("-#"):
            # Small gray lines: the "in r/sub by u/author" header and the
            # "Posted <t:…:R>" footer. Metadata, never card body.
            if subreddit is None:
                sm = COMPONENT_SUB_RE.search(text)
                if sm:
                    subreddit = sm.group(1)[2:]
            if author is None:
                am = COMPONENT_AUTHOR_RE.search(text)
                if am:
                    author = am.group(1)
            continue
        plain = DISCORD_CUSTOM_EMOJI_RE.sub("", text).strip()
        if stats is None:
            fm = COMPONENT_STATS_LINE_RE.match(plain)
            if fm:
                stats = {"ups": _compact_int(fm.group(1)),
                         "comments": _compact_int(fm.group(2))}
                continue
        body_lines.append(text)

    return {
        "service": "embeddit",
        "title": title,
        "author": author,
        "subreddit": subreddit,
        "body": "\n".join(body_lines).strip(),
        "stats": stats,
        "media": media,
    }


def _media_from_og(meta: dict) -> list:
    """og: tags -> media list. og:video (muxed mp4 with audio) first, then
    every og:image in order (galleries emit one per photo)."""
    media = []
    video_url = meta.get("og:video:secure_url") or meta.get("og:video")
    if video_url:
        media.append({"kind": "video", "url": video_url})
    for img in meta.get("og:image", []):
        kind = "gif" if img.split("?")[0].lower().endswith(".gif") else "image"
        media.append({"kind": kind, "url": img})
    return media


# ---------------------------------------------------------------------------
# ■ Per-service fetchers (each returns the normalized dict, or None)
# ---------------------------------------------------------------------------
async def _fetch_redditez(session, path: str, label: str = ""):
    """Step 1: keyless search API (permalink -> stable key).
    Step 2: bot embed page (Discordbot UA) -> og: tags.
    When the embed page shows "Failed to Get Post | EmbedEZ — Reddit
    returned a non-JSON response", the EmbedEZ backend (which fetches the
    post from Reddit on our behalf) is down or unavailable at that moment —
    a service-side failure, not a problem with the post. Treated as a miss:
    the next service (vxreddit/embeddit) is tried."""
    permalink = "https://www.reddit.com" + path
    try:
        async with session.get(
            REDDITEZ_SEARCH_ENDPOINT,
            params={"url": permalink},
            headers={"User-Agent": PROXY_BOT_UA},
            timeout=PROXY_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                logging.info(f"[{label}] redditez search HTTP {resp.status}.")
                return None
            data = await resp.json(content_type=None)
        key = parse_redditez_search(data)
        if not key:
            logging.info(f"[{label}] redditez search returned no key: {str(data)[:150]}")
            return None
        async with session.get(
            REDDITEZ_EMBED_PAGE.format(key=key),
            headers={"User-Agent": PROXY_BOT_UA},
            timeout=PROXY_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                logging.info(f"[{label}] redditez embed page HTTP {resp.status}.")
                return None
            page = await resp.text(errors="replace")
        if any(marker in page for marker in REDDITEZ_FAIL_MARKERS):
            logging.info(f"[{label}] redditez embed page reports a Reddit fetch "
                         f"failure — next service will be tried.")
            return None
        meta = _og_meta(page)
        media = dedupe_proxy_media(_media_from_og(meta))
        if not media and not meta.get("og:title"):
            logging.info(f"[{label}] redditez embed page had no usable media.")
            return None
        stats = (parse_icon_stats(meta.get("og:site_name") or "")
                 or parse_icon_stats(meta.get("og:description") or ""))
        body = clean_proxy_body(meta.get("og:description") or "")
        # when the post has no selftext the page puts the icon-stats string
        # in og:description — that is not a body
        if STATS_ICONS_RE.search(body):
            body = ""
        return {
            "service": "redditez",
            "title": meta.get("og:title"),
            "author": None,
            "subreddit": None,
            "body": body,
            "stats": stats,
            "media": media,
        }
    except Exception as e:
        logging.info(f"[{label}] redditez error: {e}")
        return None


async def _fetch_vxreddit(session, path: str, label: str = ""):
    """Bot embed page (Discordbot UA). Redirects are NOT followed — a 3xx
    means the bot page was not served (UA mismatch or service down).
    og:site_name carries the stats line: u/<author> on r/<sub> - ⬆️ N | 💬 M."""
    try:
        async with session.get(
            VXREDDIT_BASE + path,
            headers={"User-Agent": PROXY_BOT_UA},
            timeout=PROXY_TIMEOUT,
            allow_redirects=False,
        ) as resp:
            if 300 <= resp.status < 400:
                logging.info(f"[{label}] vxreddit redirected (bot page not served) — skipping.")
                return None
            if resp.status != 200:
                logging.info(f"[{label}] vxreddit HTTP {resp.status}.")
                return None
            page = await resp.text(errors="replace")
        if any(marker in page for marker in VXREDDIT_FAIL_MARKERS):
            logging.info(f"[{label}] vxreddit reports a Reddit fetch failure — "
                         f"next service will be tried.")
            return None
        meta = _og_meta(page)
        media = dedupe_proxy_media(_media_from_og(meta))
        title = meta.get("og:title")
        # A vxReddit bot page for a crosspost's own permalink can return HTTP
        # 200 without the failure markers while exposing only its generic
        # site title. With no media, that placeholder must not become the
        # Reddit post title (for example, a profile-post crosspost).
        if not media and (title or "").strip().lower() == "vxreddit":
            logging.info(f"[{label}] vxreddit returned only its generic "
                         f"placeholder title (\"vxReddit\") and no media — "
                         f"treated as a fetch miss.")
            return None
        stats = None
        author = None
        sm = VXREDDIT_STATS_RE.search(meta.get("og:site_name") or "")
        if sm:
            author = sm.group(1)
            stats = {
                "ups": int(sm.group(3)),
                "comments": int(sm.group(4)) if sm.group(4) else 0,
            }
        return {
            "service": "vxreddit",
            "title": title,
            "author": author,
            "subreddit": None,
            "body": clean_proxy_body(meta.get("og:description") or ""),
            "stats": stats,
            "media": media,
        }
    except Exception as e:
        logging.info(f"[{label}] vxreddit error: {e}")
        return None


async def _fetch_embeddit_component(session, path: str, label: str = "",
                                    probing: bool = False):
    """Round 54: the rewrite's bot page (same /comments/ path, Discordbot UA)
    -> parse_embeddit_component_page. `probing=True` quiets the per-attempt
    log lines while _fetch_embeddit is only TESTING whether the instance
    runs the rewrite."""
    if not re.search(r"/comments/([a-zA-Z0-9]+)/", path or ""):
        return None
    try:
        async with session.get(
            f"{EMBEDDIT_BASE}{path}",
            headers={"User-Agent": PROXY_BOT_UA},
            timeout=PROXY_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                if not probing:
                    logging.info(f"[{label}] embeddit component page HTTP "
                                 f"{resp.status} from {EMBEDDIT_HOST}.")
                return None
            page = await resp.text()
    except Exception as e:
        if not probing:
            logging.info(f"[{label}] embeddit component page error "
                         f"({type(e).__name__}) for {EMBEDDIT_HOST}: {e}")
        return None
    result = parse_embeddit_component_page(page)
    if not result and not probing:
        logging.info(f"[{label}] embeddit component page had no parseable "
                     f"embed data.")
    return result


async def _embeddit_route_gone(session, path: str, label: str, reason: str):
    """Round 54: the Mastodon status API just answered route-gone evidence.
    Probe the rewrite's Component-Embed page with the SAME post: parseable ->
    switch this run to component mode and return the result; otherwise ->
    round-53 unavailable (dropped from the dispatch order for the run)."""
    result = None
    try:
        result = await _fetch_embeddit_component(session, path, label,
                                                 probing=True)
    except Exception:                                   # pragma: no cover
        result = None
    if result is not None:
        _mark_embeddit_component_mode(reason)
        return result
    _mark_embeddit_unavailable(reason)
    return None


async def _fetch_embeddit(session, path: str, label: str = ""):
    """Mastodon-spoof JSON API — no bot UA needed, no redirects. The status
    id is computed from the post id alone (encode {"type":"post",...}), so
    the subreddit in the path is irrelevant here.

    Round 54: when this run already proved the instance migrated to the
    rewrite, the Component-Embed page is fetched instead; when it
    already proved the instance serves neither, this is a no-op."""
    if _embeddit_unavailable is not None:
        return None
    if _embeddit_component_mode:
        return await _fetch_embeddit_component(session, path, label)
    m = re.search(r"/comments/([a-zA-Z0-9]+)/", path or "")
    if not m:
        return None
    pid = m.group(1)
    status_id = status_id_encode({"type": "post", "id": pid, "merge": True})
    try:
        async with session.get(
            f"{EMBEDDIT_BASE}/api/v1/statuses/{status_id}",
            headers={"User-Agent": PROXY_BOT_UA},
            timeout=PROXY_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                # Round 53/54: 404/410/501 = the route itself is gone on
                # this instance (a dead POST answers 502 here) — either the
                # rewrite landed (switch) or the instance is broken (drop).
                if resp.status in (404, 410, 501):
                    return await _embeddit_route_gone(
                        session, path, label, f"HTTP {resp.status}")
                logging.info(
                    f"[{label}] embeddit HTTP {resp.status} from {EMBEDDIT_HOST}.")
                return None
            try:
                data = await resp.json(content_type=None)
            except Exception as e:
                return await _embeddit_route_gone(
                    session, path, label,
                    f"non-JSON 200 response ({type(e).__name__})")
    except Exception as e:
        # Round 53: name the host and the exception CLASS. The old line
        # printed only str(e), which for aiohttp's InvalidURL is just the url
        # — the 2026-10-01 incident looked like a remote failure when it was
        # an empty EMBEDDIT_INSTANCE Variable building a relative url.
        logging.info(
            f"[{label}] embeddit error ({type(e).__name__}) "
            f"for {EMBEDDIT_HOST}: {e}")
        return None
    result = parse_embeddit_post(data)
    if not result:
        logging.info(f"[{label}] embeddit returned no parseable post data.")
    return result


async def fetch_embeddit_stats(session, path: str, label: str = ""):
    """Lightweight stats fetch via the Embeddit JSON (no bot gate, ~1 s).
    Used when the winning proxy service did not provide stats — the
    redditez og page often lacks the stats line. Returns
    {"ups": N, "comments": M} or None."""
    result = await _fetch_embeddit(session, path, label or "stats")
    return result.get("stats") if result else None


# ---------------------------------------------------------------------------
# ■ Fallback chain + warm-up
# ---------------------------------------------------------------------------
_PROXY_FETCHERS = {
    "redditez": lambda s, p, l: _fetch_redditez(s, p, l),
    "vxreddit": lambda s, p, l: _fetch_vxreddit(s, p, l),
    "embeddit": lambda s, p, l: _fetch_embeddit(s, p, l),
}

# One semaphore per running event loop (tests call asyncio.run repeatedly).
_proxy_gates: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _proxy_gate():
    """Global ceiling on concurrent proxy requests for this event loop."""
    loop = asyncio.get_running_loop()
    gate = _proxy_gates.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(max(1, PROXY_MAX_CONCURRENCY))
        _proxy_gates[loop] = gate
    return gate


def proxy_services_for(path: str | None) -> list:
    """Round 49: the services worth asking for THIS path, in tie-break order.

    redditez/EmbedEZ cannot resolve a reddit SHARE link (/r/<sub>/s/<token>);
    it answers "Could not find provider for this url. Malformed url?", so the
    request is pure latency. It is dropped for share links unless it is the
    only service left.
    """
    order = list(PROXY_SERVICES)
    if path and SHARE_LINK_RE.match(path) and len(order) > 1:
        order = [s for s in order if s != "redditez"]
    # Round 53: an instance without the Mastodon status API stays dropped for
    # the rest of the run (never to empty — a last remaining service is still
    # asked, same rule the share-link drop uses).
    if _embeddit_unavailable is not None and len(order) > 1:
        order = [s for s in order if s != "embeddit"]
    return order


def _better_media(candidate: dict, cand_rank: int,
                  best: dict | None, best_rank: int) -> bool:
    """Round 25 rule, made arrival-order independent (round 49).

    Serially, "more items wins, ties keep the earlier service" fell out of the
    loop order. Concurrently the results arrive in whatever order the network
    returns them, so the tie-break is now explicit: more media wins; an equal
    count keeps the service with the better PROXY_SERVICES rank.
    """
    if best is None:
        return True
    if len(candidate["media"]) != len(best["media"]):
        return len(candidate["media"]) > len(best["media"])
    return cand_rank < best_rank


async def fetch_proxy_post(session, path: str, label: str = "",
                           health: dict | None = None,
                           need_video: bool = False) -> dict | None:
    """Ask the proxy services for a post and return the normalized result
    with the MOST media (photos / GIFs / video).

    ROUND 49 (2026-10-01) — PARALLEL DISPATCH. The rules below are unchanged;
    only the way the services are ASKED changed. The prod Actions log of
    2026-10-01 11:55 shows the old serial chain costing ~1.5 s per post:

        11:55:50.681  redditez had no usable data
        11:55:51.098  proxy media via vxreddit - 1 item(s)   (+0.417 s)
        11:55:51.408  embeddit returned text only            (+0.310 s)

    Every service waited for the previous one to fail. Now:

      * the two services that actually deliver (vxreddit, redditez) are
        dispatched TOGETHER at t=0;
      * embeddit is held back PROXY_WAVE_DELAY seconds and is CANCELLED
        before it ever opens a socket when wave 1 already produced a DECISIVE
        answer - which is the common case for a video post, so a video post
        still costs two requests, exactly like the round-48d serial exit;
      * the moment a DECISIVE answer lands (a video on a video post, per
        round 48d, or MEDIA_CAP_ITEMS items) everything still in flight is
        cancelled and the result is returned;
      * otherwise, once SOME media exists, stragglers get at most
        PROXY_GALLERY_GRACE seconds to produce a MORE COMPLETE gallery
        (round 25), and are then cancelled.

    Unchanged decision rules:
      * round 23 - a text/stats-only answer never wins; it is kept as the
        body/stats fallback and returned only if nobody produced media.
      * round 23 - on a video post (need_video) a result without video media
        cannot win; it becomes the fallback.
      * round 25 - more media wins; ties keep the higher-priority service
        (now resolved by explicit rank, since results arrive out of order).
      * warm-up - services marked ok=false this run are skipped, unless ALL
        of them are marked down, in which case everyone is retried.
      * the winning media list is copied and capped at MEDIA_CAP_ITEMS.
    Returns None when nothing worked (the caller uses the native path).
    """
    order = proxy_services_for(path)
    health = health or {}
    marked_down = [s for s in order
                   if isinstance(health.get(s), dict) and health[s].get("ok") is False]
    if len(marked_down) < len(order):
        order = [s for s in order if s not in marked_down]
    if not order:
        return None

    rank = {service: i for i, service in enumerate(order)}
    fallback_result = None
    fallback_rank = len(order)
    best_media_result = None
    best_rank = len(order)

    wave1_settled = asyncio.Event()

    async def run(service: str, delay: float):
        if delay > 0:
            # Wave 2 waits out the delay OR starts the instant wave 1 has
            # settled without a decisive answer - whichever happens first.
            try:
                await asyncio.wait_for(wave1_settled.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
        async with _proxy_gate():
            return service, await _PROXY_FETCHERS[service](session, path, label)

    loop = asyncio.get_running_loop()
    tasks = {}
    wave1 = set()
    for i, service in enumerate(order):
        # wave 1 = the two best services, wave 2 = the rest, delayed.
        delay = 0.0 if i < 2 else PROXY_WAVE_DELAY
        task = asyncio.ensure_future(run(service, delay))
        tasks[task] = service
        if i < 2:
            wave1.add(task)
    pending = set(tasks)
    grace_deadline = None
    decisive = False

    try:
        while pending and not decisive:
            timeout = None
            if grace_deadline is not None:
                timeout = max(0.0, grace_deadline - loop.time())
            done, pending = await asyncio.wait(
                pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                logging.info(f"[{label}] gallery grace expired - going with "
                             f"{len(best_media_result['media'])} item(s) from "
                             f"{best_media_result['service']}.")
                break
            # Deterministic tie-breaks: when several services answer within
            # the same wait() batch, process them in PRIORITY order so the
            # winner never depends on set iteration order (round 49).
            for task in sorted(done, key=lambda t: rank[tasks[t]]):
                try:
                    service, result = task.result()
                except asyncio.CancelledError:
                    continue
                except Exception as e:                      # pragma: no cover
                    logging.info(f"[{label}] proxy task error: {e}")
                    continue
                r = rank[service]
                if not (result and (result["media"] or result.get("stats")
                                    or result.get("body"))):
                    # Round 49: ONE line per attempt. The serial chain logged
                    # both "returned text only" and "had no usable data" for
                    # the same attempt (the text-only branch fell through).
                    logging.info(f"[{label}] {service} had no usable data.")
                    continue
                if need_video and not any(m["kind"] == "video" for m in result["media"]):
                    if r < fallback_rank:
                        fallback_result, fallback_rank = result, r
                    logging.info(f"[{label}] {service} result has no video - "
                                 f"keeping it only as a text/stats fallback.")
                    continue
                if not result["media"]:
                    if r < fallback_rank:
                        fallback_result, fallback_rank = result, r
                    logging.info(f"[{label}] {service} returned text only "
                                 f"(no media) - still waiting for media.")
                    continue
                if _better_media(result, r, best_media_result, best_rank):
                    if best_media_result is not None:
                        logging.info(f"[{label}] {service} has "
                                     f"{len(result['media'])} media item(s) vs "
                                     f"{len(best_media_result['media'])} from "
                                     f"{best_media_result['service']} - "
                                     f"replacing the winner.")
                    best_media_result, best_rank = result, r
                    logging.info(f"[{label}] proxy media via {service} - "
                                 f"{len(result['media'])} item(s) (best so far).")
                else:
                    logging.info(f"[{label}] {service} media "
                                 f"({len(result['media'])} item(s)) not more "
                                 f"complete than the best so far "
                                 f"({len(best_media_result['media'])}) - keeping "
                                 f"{best_media_result['service']}.")
                if len(best_media_result["media"]) >= MEDIA_CAP_ITEMS:
                    logging.info(f"[{label}] media card capacity reached - "
                                 f"chain complete.")
                    decisive = True
                    break
                # ROUND 48d: a reddit video post has exactly one video and the
                # card shows one player, so no later answer can beat this —
                # PROVIDED nothing still in flight outranks the winner.
                #
                # ROUND 50 FIX (incident 1wv12qb, 2026-10-01): a crosspost's
                # media resolves against the ORIGINAL post, and on that
                # second lookup vxreddit/redditez (rank 0/1, video WITH
                # audio per the round-49 field table) were still pending
                # when embeddit (rank 2, NO audio) answered with a video
                # first and this check cancelled them mid-flight — the card
                # posted silent. A video answer is only decisive once no
                # pending task could still outrank it; otherwise it falls
                # through to the existing bounded grace window below, same
                # as an incomplete gallery, so a higher-priority video (if
                # one arrives) replaces it via the normal _better_media
                # tie-break before the chain gives up.
                if need_video and any(m["kind"] == "video"
                                      for m in best_media_result["media"]):
                    if any(rank[tasks[t]] < r for t in pending):
                        logging.info(f"[{label}] video via {service} but a "
                                     f"higher-priority service is still in "
                                     f"flight — waiting up to "
                                     f"{PROXY_GALLERY_GRACE}s for it before "
                                     f"calling the chain complete.")
                    else:
                        logging.info(f"[{label}] video resolved via {service} - "
                                     f"chain complete (no gallery can beat it).")
                        decisive = True
                        break
            if decisive or not pending:
                break
            if not (wave1 & pending):
                wave1_settled.set()   # wave 2 may start now, no need to wait
            if best_media_result is not None and grace_deadline is None:
                # Round 25 is deliberately NOT short-circuited here: 1wj0p83
                # proved that a LOWER-priority service can hold the complete
                # gallery (embeddit had all 13 items while the others had 1),
                # so "somebody returned media" is not a reason to stop. Only a
                # DECISIVE answer (video / cap, handled above) ends the wait;
                # everything else gets the bounded grace window.
                grace_deadline = loop.time() + PROXY_GALLERY_GRACE
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    if best_media_result is not None:
        # Cap a copy: never mutate the service's original result/list.
        best_media_result = dict(best_media_result)
        best_media_result["media"] = best_media_result["media"][:MEDIA_CAP_ITEMS]
        logging.info(f"[{label}] proxy media winner - "
                     f"{len(best_media_result['media'])} item(s).")
        return best_media_result
    return fallback_result


def load_proxy_health() -> dict:
    """Reads the services section of proxy_health.json ({} when missing)."""
    try:
        with open(PROXY_HEALTH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        services = data.get("services") if isinstance(data, dict) else None
        return services if isinstance(services, dict) else {}
    except Exception:
        return {}


def save_proxy_health(health: dict):
    """Writes proxy_health.json without churning its timestamp on quiet runs.

    The warm-up runs every monitor cycle. Preserve the previous timestamp when
    the service states are unchanged so cache-only commits remain quiet.
    """
    try:
        previous = {}
        try:
            with open(PROXY_HEALTH_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                previous = loaded
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

        same_services = previous.get("services") == health.get("services")
        timestamp = previous.get("ts") if same_services else int(time.time())
        payload = {"ts": timestamp, **health}
        if same_services and payload == previous:
            return
        with open(PROXY_HEALTH_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving proxy health: {e}")


async def proxy_warmup(session, post_id: str | None = None) -> dict:
    """Warm-up link test: probes ALL three services (in parallel) with one
    known post — by default PROXY_WARMUP_POST, a stable single-image post —
    and records the result in proxy_health.json. The posting loop then
    skips services marked ok=false (unless all are dead). Any failure here
    only marks the service down for this run; posting never depends on the
    warm-up succeeding."""
    raw = (post_id or WARMUP_POST_ID or "").strip()

    def _valid(value: str) -> bool:
        parts = value.split("/", 1)
        return len(parts) == 2 and bool(parts[0].strip()) and bool(parts[1].strip())

    if not _valid(raw):
        # Round 53: a malformed value no longer costs the whole warm-up — the
        # built-in known-good post is used instead, so proxy_health.json stays
        # fresh and dead services keep being skipped.
        logging.warning(f"PROXY_WARMUP_POST '{raw}' is invalid — use "
                        f"<subreddit>/<post_id>. Falling back to "
                        f"{DEFAULT_WARMUP_POST}.")
        raw = DEFAULT_WARMUP_POST
        if not _valid(raw):                                  # pragma: no cover
            return {}
    sub, pid = (p.strip() for p in raw.split("/", 1))
    path = f"/r/{sub}/comments/{pid}/"
    label = "proxy warm-up"
    # ROUND 49 FIX: the probes are built FROM the dispatch map, so the result
    # labels can never drift from the call order again. Before this, the
    # gather was hard-coded (redditez, vxreddit, embeddit) while the zip used
    # PROXY_SERVICES — reordering PROXY_SERVICES for the new tie-break would
    # have swapped the redditez and vxreddit health records, marking the
    # wrong service dead for the whole run.
    probes = tuple(_PROXY_FETCHERS)
    logging.info(f"Probing proxy media services ({', '.join(probes)}) "
                 f"with {raw}...")
    results = await asyncio.gather(
        *[_PROXY_FETCHERS[name](session, path, label) for name in probes])
    services = {}
    for service, result in zip(probes, results):
        if result and (result["media"] or result.get("body") or result.get("stats")):
            services[service] = {"ok": True, "detail": f"{len(result['media'])} media item(s)"}
        else:
            services[service] = {"ok": False, "detail": "no usable data"}
        state = "OK" if services[service]["ok"] else "DOWN"
        logging.info(f"[{label}] {service}: {state} — {services[service]['detail']}")
    save_proxy_health({"post": raw, "services": services})
    return services
