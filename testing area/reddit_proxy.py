# ---------------------------------------------------------------------------
# ■ Reddit proxy media services (round 13) — redditez / vxreddit / embeddit
# ---------------------------------------------------------------------------
# Card media (photos / galleries / videos with audio / GIFs) is fetched from
# one of three public proxy services, in PRIORITY ORDER:
#
#   1. redditez.com (EmbedEZ) — primary. The keyless search API resolves a
#      permalink to a stable key, then the bot embed page (Discordbot UA)
#      exposes og: tags: photos (incl. galleries) as embedez media URLs,
#      videos as a playable mp4 WITH audio, plus title/body/stats.
#   2. vxreddit.com — OpenGraph bot pages (Discordbot UA): every gallery
#      photo as a full-res i.redd.it og:image, videos as a muxed mp4 WITH
#      audio (redditvideo.mp4), stats in og:site_name.
#   3. embeddit.deltandy.me — Mastodon-style JSON API (no bot UA needed):
#      ALL gallery photos (up to 20), video with audio (under ~50 MB,
#      merge=true), stats + body + author in the JSON.
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
import html as html_lib

import aiohttp

PROXY_HEALTH_FILE = "proxy_health.json"

# Discord's scraper UA — vxreddit and embedez serve bot embed pages to
# social-preview bots only (vxreddit redirects everyone else to reddit.com).
PROXY_BOT_UA = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"

# Priority order (user-specified): redditez first; vxreddit + embeddit are
# the most uptime-reliable and are the fallbacks.
PROXY_SERVICES = ("redditez", "vxreddit", "embeddit")

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
# true} (merge=true => the video comes back WITH audio, under ~50 MB).
EMBEDDIT_BASE = os.getenv("EMBEDDIT_INSTANCE", "https://embeddit.deltandy.me").rstrip("/")
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

# Icon-style stats (redditez): "💬 152 🔁 0 💜 573 👀 0"
STATS_ICONS_RE = re.compile(r"💬\s*(\d[\d,]*)\s*🔁\s*(\d[\d,]*)\s*💜\s*(\d[\d,]*)")

# Warm-up: one known-good single-image post used to probe all three
# services once per run (override: PROXY_WARMUP_POST=<subreddit>/<post_id>).
WARMUP_POST_ID = os.getenv("PROXY_WARMUP_POST", "HonkaiStarRail_leaks/1whbjbh").strip()

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
            "title": meta.get("og:title"),
            "author": author,
            "subreddit": None,
            "body": clean_proxy_body(meta.get("og:description") or ""),
            "stats": stats,
            "media": media,
        }
    except Exception as e:
        logging.info(f"[{label}] vxreddit error: {e}")
        return None


async def _fetch_embeddit(session, path: str, label: str = ""):
    """Mastodon-spoof JSON API — no bot UA needed, no redirects. The status
    id is computed from the post id alone (encode {"type":"post",...}), so
    the subreddit in the path is irrelevant here."""
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
                logging.info(f"[{label}] embeddit HTTP {resp.status}.")
                return None
            data = await resp.json(content_type=None)
    except Exception as e:
        logging.info(f"[{label}] embeddit error: {e}")
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
async def fetch_proxy_post(session, path: str, label: str = "",
                           health: dict | None = None,
                           need_video: bool = False) -> dict | None:
    """Try the proxy services in priority order and return the normalized
    result with the MOST media (photos / GIFs / video). Round 25:
    partial results no longer stop the chain; ties keep service priority.
    The winning media list is copied and capped at MEDIA_CAP_ITEMS.
    A text/stats-only result never stops the chain (round 23, 2026-09-18):
    a service can have the post's text but not (yet) its images — e.g. a
    gallery post minutes after posting, when the og:image tags have not
    been rendered yet (1wj38fc: vxreddit served the title + stats while
    embeddit — never tried under the old rule — DID have both photos).
    The first text-only result is kept as the body/stats fallback; if no
    service produces media, that fallback is returned instead. Services
    the warm-up proved dead this run are skipped — unless ALL of them
    are dead, in which case every service gets a fresh try. Returns None
    when nothing works (the caller uses the native path).
    need_video (video posts): a result WITHOUT video media (thumbnails /
    text only) cannot win — the first such result is kept as a body/stats
    fallback while the chain keeps looking for the service that serves
    the actual muxed video (with audio) before the arctic CMAF fallback."""
    order = list(PROXY_SERVICES)
    health = health or {}
    marked_down = [s for s in order
                   if isinstance(health.get(s), dict) and health[s].get("ok") is False]
    if len(marked_down) < len(order):
        order = [s for s in order if s not in marked_down]
    fallback_result = None
    best_media_result = None
    for service in order:
        if service == "redditez":
            result = await _fetch_redditez(session, path, label)
        elif service == "vxreddit":
            result = await _fetch_vxreddit(session, path, label)
        else:
            result = await _fetch_embeddit(session, path, label)
        if result and (result["media"] or result.get("stats") or result.get("body")):
            if need_video and not any(m["kind"] == "video" for m in result["media"]):
                # video post, but this service only gave thumbnails/text:
                # keep the first such result as fallback, try the next proxy
                if fallback_result is None:
                    fallback_result = result
                logging.info(f"[{label}] {service} result has no video — "
                             f"trying the next proxy.")
                continue
            if result["media"]:
                # Round 25: keep looking for a more complete gallery (1wj0p83).
                # Equal counts preserve the higher-priority service and its order.
                if (best_media_result is None
                        or len(result["media"]) > len(best_media_result["media"])):
                    if best_media_result is not None:
                        logging.info(f"[{label}] {service} has "
                                     f"{len(result['media'])} media item(s) vs "
                                     f"{len(best_media_result['media'])} — "
                                     f"replacing the winner.")
                    best_media_result = result
                    logging.info(f"[{label}] proxy media via {service} — "
                                 f"{len(result['media'])} item(s) (best so far).")
                    if len(best_media_result["media"]) >= MEDIA_CAP_ITEMS:
                        logging.info(f"[{label}] media card capacity reached — "
                                     f"chain complete.")
                        break
                else:
                    logging.info(f"[{label}] {service} media "
                                 f"({len(result['media'])} item(s)) not more "
                                 f"complete than the best so far "
                                 f"({len(best_media_result['media'])}) — keeping "
                                 f"the earlier service.")
                continue
            # round 23 (2026-09-18): text/stats-only — NOT a winner. A
            # service can have the post's text but not (yet) its images
            # (too-new gallery posts — 1wj38fc: vxreddit served title +
            # stats minutes after posting while the og:image tags were
            # still missing, and the old rule stopped the chain here,
            # hiding embeddit, which DID have the photos). Keep the first
            # such result as the body/stats fallback and let the remaining
            # services have their shot at the media.
            if fallback_result is None:
                fallback_result = result
            logging.info(f"[{label}] {service} returned text only (no media) — "
                         f"continuing the chain for the media.")
        logging.info(f"[{label}] {service} had no usable data — trying the next proxy.")
    if best_media_result is not None:
        # Cap a copy: never mutate the service's original result/list.
        best_media_result = dict(best_media_result)
        best_media_result["media"] = best_media_result["media"][:MEDIA_CAP_ITEMS]
        logging.info(f"[{label}] proxy media winner — "
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
    raw = post_id or WARMUP_POST_ID
    parts = raw.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        logging.warning(f"PROXY_WARMUP_POST '{raw}' is invalid — use "
                        f"<subreddit>/<post_id>. Warm-up skipped.")
        return {}
    sub, pid = parts
    path = f"/r/{sub}/comments/{pid}/"
    label = "proxy warm-up"
    logging.info(f"Probing proxy media services (redditez, vxreddit, embeddit) "
                 f"with {raw}...")
    results = await asyncio.gather(
        _fetch_redditez(session, path, label),
        _fetch_vxreddit(session, path, label),
        _fetch_embeddit(session, path, label),
    )
    services = {}
    for service, result in zip(PROXY_SERVICES, results):
        if result and (result["media"] or result.get("body") or result.get("stats")):
            services[service] = {"ok": True, "detail": f"{len(result['media'])} media item(s)"}
        else:
            services[service] = {"ok": False, "detail": "no usable data"}
        state = "OK" if services[service]["ok"] else "DOWN"
        logging.info(f"[{label}] {service}: {state} — {services[service]['detail']}")
    save_proxy_health({"post": raw, "services": services})
    return services
