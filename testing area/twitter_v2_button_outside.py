# ---------------------------------------------------------------------------
# ■ Full Components V2 Edition — twitter_v2_button_outside.py
# ---------------------------------------------------------------------------
# This is a separate file — your V1 twitter_v1.py and V3 twitter_v3.py stay untouched.
# To use it: change the workflow's run line to:
#            run: python "testing area/twitter_v2_button_outside.py"
#
# Same behavior as twitter_v3.py, but the action row is a SEPARATE component
# OUTSIDE/BELOW the type-17 container (classic V2 look).
#
# ■ What's new in this revision (2026-09-11, round 4):
#   • FIXED Discord 400 {"components": ["0"]} errors — caused by tweets with
#     EMPTY text (quote-posts-of-media, X Articles) producing an invalid empty
#     text component. Text is now chunked (<=1900 chars/component) and empty
#     components are never emitted; long posts are chunked, not rejected.
#   • QUOTED POST support — tweet.quote renders as
#     >>> [Quote](url) from **Name** (@user) + quoted text + quoted media.
#   • X ARTICLES / LINK CARDS — when a tweet has no media, the script fetches
#     the tweet's own x.com page OpenGraph image (article banner / link-card
#     image) into the gallery; falls back to the first external link's OG
#     image (works for hoyo.link etc.). Profile pictures are never used.
#   • GIF support — tweets whose video is type \"gif\" (tweet_video/*.mp4) are
#     re-pointed to an animated image rendition so they play inline as an
#     image, not a video player. Two community converters are PROBED in order
#     (never forced): (1) the official gif.fxtwitter.com .webp (V1's asset,
#     intermittently 530s), then (2) ROUND 6: fastgif (a third-party
#     converter at fastgif-production.up.railway.app — only its .gif
#     extension works, and it 500s on unknown ids so it's safely probeable).
#     If neither answers, the mp4 is kept, which still plays as a video.
#   • PORTRAIT VIDEO fix — vertical videos (h > w) that "load but won't play"
#     in the gallery are re-pointed through FxTwitter's embed proxy
#     (api.fxtwitter.com/2/go?url=...), the exact URL V1 embeds use, which
#     plays them correctly.
#   • /status/:id API path — FxTwitter resolves purely by tweet id and
#     ignores the screen name in the path (re-verified 2026-09-17: even a
#     nonexistent handle returns 200 with the correct payload, so the old
#     "screen-name path 404s for reposts/articles" note no longer holds).
#     Plain-ID is used as the shortest form. Read Post links use the TRUE
#     author from the payload.
#   • "Replying to @user" line when the tweet is a reply.
#   • Custom animated button emoji (starwardhmm / starward11 / starwardfans).
#   • ROUND 5 (2026-09-11): portrait proxy-wrap retracted to an opt-in
#     toggle (PORTRAIT_PROXY, default False) — follow-up tests proved the
#     'loads but won't play' vertical-video issue was a transient Discord
#     proxy warm-up, not a portrait incompatibility; direct URLs now used
#     for all videos. Ananta_EN added to the default account list.
#   • Everything from round 3 is kept: clickable hashtags/mentions, smart
#     video sizing (probe real bytes; >256 MB -> smaller formats[] rendition
#     or thumbnail+watch link), /en translation, timestamps, null-safe color.
#   • ROUND 10 (2026-09-13): FULL X ARTICLE support — FxTwitter's API returns
#     each article as clean JSON (tweet.article: title, cover image, draft.js
#     text blocks, in-article images/GIFs — no x.com scraping, no login).
#     Article tweets now post as: cover image -> **title** + full body text
#     (chunked like tweets) -> in-article media gallery (GIFs go through the
#     same animated gif.fxtwitter/fastgif chain) -> stats -> buttons. Note:
#     FxTwitter does not translate article bodies, so non-English articles
#     post in their original language (normal tweets still get /en).
#   • ROUND 10: GALLERY_VIDEO_LIMIT safety cap (default 0 = OFF, behaviour
#     unchanged). After running "testing area/video_diag.py", set it to the
#     largest size Discord proved plays (e.g. 100 * 1024 * 1024). When set,
#     videos above it auto-downgrade to the largest variant at/below it, or
#     post a "watch on X" note — the gallery never carries a tile Discord's
#     proxy can't play ("image not found").
# ---------------------------------------------------------------------------
import os
import re
import json
import time
import html as html_lib
import asyncio
import logging
import aiohttp
import feedparser
from email.utils import parsedate_to_datetime
from urllib.parse import quote as url_quote
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
load_dotenv()

# ROUND 11 (backported from V3, 2026-09-17): tweet-data fallback chain
# (fxtwitter -> fixupx -> vxtwitter -> twitterez). Soft import: if the
# module file is missing, the script logs a warning and keeps using the
# legacy direct FxTwitter call.
try:
    import twitter_proxy
except Exception as e:  # only when the file is absent from the repo
    twitter_proxy = None
    logging.warning(f"twitter_proxy unavailable ({e}) — using direct FxTwitter only.")

ACCOUNTS_STR = os.getenv("ACCOUNTS", "TYPEII_EN,PomPom_HonkaiSR,Wuthering_Waves,HonkaiNA,Ananta_EN")
ACCOUNTS = [acc.strip() for acc in ACCOUNTS_STR.split(",") if acc.strip()]

DEFAULT_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

CACHE_FILE = "posted_tweets.json"
MAX_CACHE_SIZE = 500
MAX_AGE_SECONDS = 3 * 3600

# round 12 (backported from V3, 2026-09-17): nitter fleet refreshed from the
# status.d420.de tracker (2026-09-17) + live plain-RSS probes. The tracker's
# health/RSS flags flip back and forth, so the WHOLE tracked fleet is listed
# — order is today's evidence (verified-fresh first, known-dead last); the
# chain stops at the first instance that answers with real entries.
#   jaydenha.uk        verified fresh via plain RSS probe (2026-09-17) -> FIRST
#   meowing.monster    verified fresh via plain RSS probe (2026-09-17)
#   click / xitter.cc  RSS "disabled" at probe time — kept: the tracker's RSS
#                      flags flip back and forth
#   miningtcup.me      healthy, RSS ok BUT behind a bot check — needs the
#                      emailed RSS token (see RSS_TOKEN_ENV); without a valid
#                      token the chain logs the challenge and moves on
#   netbub             unreachable at probe time (flags flip back)
#   thepixora          alive but behind a dog-captcha bot check (2026-09-17)
#   perennialte.ch     serves a STALE RSS (site works in browsers)
#   privacydev.net / nitter.net  500'd on 2026-09-17
#   xcancel.com        suspended 2026-09-14 — kept: auto-revives in the chain
#                      if it returns
RSS_INSTANCES = [
    "https://nitter.jaydenha.uk",
    "https://nitter.meowing.monster",
    "https://nitter.click",
    "https://nitter.xitter.cc",
    "https://nitter.miningtcup.me",
    "https://nitter.netbub.com",
    "https://shitter.thepixora.com",
    "https://nitter.perennialte.ch",
    "https://nitter.privacydev.net",
    "https://nitter.net",
    "https://xcancel.com",
]

# round 12 (backported from V3, 2026-09-17): token-gated instances.
# nitter.miningtcup.me hides its RSS behind a bot check; the operator emails
# out RSS tokens (request sent to nitter-rss@miningtcup.me). When the token
# arrives, set it as the repo VARIABLE NITTER_RSS_TOKEN (Settings -> Secrets
# and variables -> Actions -> Variables) — the next run picks it up (read at
# call time, no code change). The token is sent BOTH ways: Authorization:
# Bearer header and ?token= query param, so either convention works. Round
# 14 (2026-09-18): the operator (ted@miningtcup.me) confirmed the token is
# accepted anywhere in the User-Agent too, so it now goes out in all three
# places at once.
RSS_TOKEN_ENV = {
    "https://nitter.miningtcup.me": "NITTER_RSS_TOKEN",
}

FXTWITTER_API_BASE = "https://api.fxtwitter.com"
FXTWITTER_VIDEO_PROXY = "https://api.fxtwitter.com/2/go?url="
GIF_WEBP_BASE = "https://gif.fxtwitter.com/tweet_video/"
# ROUND 6: independent third-party converter discovered live (2026-09-12).
# It turns tweet_video mp4s into REAL GIFs — always request the .gif
# extension (its .webp route errors). Unknown ids 500, so it is probeable.
GIF_RAILWAY_BASE = "https://fastgif-production.up.railway.app/tweet_video/"
IS_COMPONENTS_V2 = 1 << 15  # Flag for rich card layout

# ---------------------------------------------------------------------------
# ■ VIDEO GALLERY LIMITS (from live Discord tests, 2026-09-11)
# 191 MB confirmed plays, 405 MB confirmed fails -> 256 MiB sits safely
# between them. Tune here if Discord changes its behaviour.
# ---------------------------------------------------------------------------
VIDEO_SIZE_LIMIT = 256 * 1024 * 1024          # 256 MiB
RISKY_DURATION_SECONDS = 300                   # > 5 min ...
RISKY_MIN_PIXELS = 1920 * 1080                 # ... AND >= 1080p = risky when size unknown
MAX_VARIANT_PROBES = 3                         # smaller mp4s to try before giving up

# Set True ONLY if portrait videos are PROVEN broken again (see gallery_video_url).
PORTRAIT_PROXY = False

# ROUND 10 (2026-09-13): optional safety cap for gallery tiles. 0 = OFF
# (use VIDEO_SIZE_LIMIT above). LIVE-VERIFIED 2026-09-13 via
# "testing area/video_diag.py": 171 MB and 234 MB gallery tiles PLAY;
# 521 MB and up FAIL ("image not found") in every URL style — so the
# 256 MiB default sits in a proven-safe gap and needs no change.
# If Discord's proxy ever breaks a <= 256 MB tile again (rare + transient —
# the same URL plays fine when re-posted), set this to a smaller proven
# value, e.g.  GALLERY_VIDEO_LIMIT = 100 * 1024 * 1024  # 100 MiB.
# When active, videos above it are downgraded to the largest variant at or
# below it, or get a "watch on X" note — so the gallery never carries a
# tile Discord's proxy can't play.
GALLERY_VIDEO_LIMIT = 0

# ---------------------------------------------------------------------------
# ■ TEXT LIMITS — a Discord text display component must be 1..2000 chars;
# we chunk at 1900 for safety and allow up to 4 chunks (7600 chars) of body.
# ---------------------------------------------------------------------------
TEXT_CHUNK_SIZE = 1900
MAX_TEXT_COMPONENTS = 4
QUOTE_TEXT_BUDGET = 1500  # quote text shares a component with the quote header

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}
DISCORD_BOT_UA = "Discordbot/2.0; +https://discordapp.com"

LANGUAGE_NAMES = {
    "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "fr": "French",
    "de": "German", "es": "Spanish", "pt": "Portuguese", "ru": "Russian",
    "ar": "Arabic", "it": "Italian", "id": "Indonesian", "th": "Thai",
    "vi": "Vietnamese", "tl": "Filipino", "hi": "Hindi", "tr": "Turkish",
    "nl": "Dutch", "pl": "Polish", "uk": "Ukrainian", "sv": "Swedish",
}

# ---------------------------------------------------------------------------
# ■ BUTTON CONFIGURATION (NESTED INSIDE V2 CONTAINER)
# ---------------------------------------------------------------------------
READ_POST_LABEL = "Read Post"
READ_POST_EMOJI = {"id": "1472388018689282261", "name": "starwardhmm", "animated": True}
STATIC_BUTTONS = [
    {
        "label": "Citlali News",
        "url": "https://discord.gg/HyrVP9wRXu",
        "emoji": {"id": "1439878792653832253", "name": "starward11", "animated": True},
    },
    {
        "label": "Donate",
        "url": "https://ko-fi.com/jieunlatte",
        "emoji": {"id": "1509026327548657914", "name": "starwardfans", "animated": True},
    },
]


def get_webhook_for_account(account: str) -> str | None:
    sanitized = re.sub(r"[^A-Za-z0-9]", "_", account).upper()
    env_key = f"WEBHOOK_{sanitized}"
    webhook = os.getenv(env_key)
    if webhook:
        return webhook
    if DEFAULT_WEBHOOK_URL:
        return DEFAULT_WEBHOOK_URL
    return None


def load_posted_urls() -> set:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_posted_urls(posted_urls: set):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(list(posted_urls)[-MAX_CACHE_SIZE:], f, indent=2)
    except Exception as e:
        logging.error(f"Error saving cache: {e}")


def accent_from_color(color) -> int:
    """Null-safe hex color -> int (tweet color can legitimately be null)."""
    try:
        if not color:
            return 0x1DA1F2
        return int(str(color).lstrip("#"), 16)
    except (ValueError, TypeError):
        return 0x1DA1F2


def linkify_text(text: str) -> str:
    """
    Makes #hashtags and @mentions clickable inside tweet text, exactly like
    FxTwitter's own auto-embeds do:
        #zzzero   -> [#zzzero](https://x.com/hashtag/zzzero)
        @user     -> [@user](https://x.com/user)
    Lookbehinds protect URLs (example.com/path#anchor) and emails (a@b.com).
    Bare http(s) links in the text are already auto-linked by Discord.
    Round 28 (2026-09-18): the hashtag is percent-encoded in the link
    target — some artists put non-ASCII characters inside tags (live case:
    #zzzero + U+3164 HANGUL FILLER, zzzeroㅤ), and the raw character made
    Discord's markdown parser reject the link (literal [#tag ](...) text
    printed). The URL now matches exactly what X itself links to
    (x.com/hashtag/zzzero%E3%85%A4): pure ASCII, always clickable.
    """
    if not text:
        return ""
    def _link_hashtag(match: "re.Match") -> str:
        tag = match.group(1)
        return f"[#{tag}](https://x.com/hashtag/{url_quote(tag, safe='')})"

    text = re.sub(r"(?<![\w/])#(\w+)", _link_hashtag, text)
    text = re.sub(r"(?<![\w@/])@([A-Za-z0-9_]{1,15})", r"[@\1](https://x.com/\1)", text)
    return text


def translation_is_identical(original: str, translated: str) -> bool:
    """round 28 (2026-09-18): X's per-tweet `lang` is an automatic language
    GUESS, and it misfires on short/ambiguous text — e.g. 'Maintenance 🩸
    #zzzero #Claret #Roxy' (one word that exists in several languages +
    hashtags) came back as lang=fr. The /en call still answers with a
    `translation` object, but its text is the SAME as the original (the
    translator had nothing to change), so posting it as 'Translated from
    French' is wrong. This compares the two texts case/whitespace/
    punctuation/emoji-insensitively: if they are the same, the card is
    posted as-is (no translation block). A real translation always differs."""
    def _norm(s: str) -> str:
        s = (s or "").lower()
        s = re.sub(r"[^\w]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()
    return _norm(original) == _norm(translated)


def chunk_text(text: str, max_components: int = MAX_TEXT_COMPONENTS) -> list:
    """
    Splits body text into <= max_components chunks, each <= TEXT_CHUNK_SIZE
    (an EMPTY string yields ZERO chunks — a 0-length text component is what
    caused Discord's 400 {"components": ["0"]} errors). Prefers cutting at
    newlines. Over-long text is cut off with an explicit truncation note.
    """
    text = (text or "").strip()
    if not text:
        return []
    cap = TEXT_CHUNK_SIZE * max_components
    truncated = len(text) > cap
    if truncated:
        text = text[:cap]
    chunks = []
    i = 0
    while i < len(text) and len(chunks) < max_components:
        end = min(i + TEXT_CHUNK_SIZE, len(text))
        if end < len(text):
            newline_cut = text.rfind("\n", i, end)
            if newline_cut > i + TEXT_CHUNK_SIZE // 2:
                end = newline_cut + 1
        part = text[i:end].strip()
        if part:
            chunks.append(part)
        i = end
    if truncated or i < len(text):
        marker = "\n… *(open the post for the full text)*"
        chunks[-1] = chunks[-1][:TEXT_CHUNK_SIZE - len(marker)] + marker
    return chunks


def resolve_tweet_ts(tweet: dict, fallback_ts: float) -> int:
    """Best-effort tweet creation time for the Discord <t:...> timestamp."""
    stamp = tweet.get("created_timestamp")
    if isinstance(stamp, (int, float)) and stamp > 0:
        return int(stamp)
    created_at = tweet.get("created_at")
    if created_at:
        try:
            return int(parsedate_to_datetime(str(created_at)).timestamp())
        except Exception:
            pass
    return int(fallback_ts)


# ---------------------------------------------------------------------------
# ■ SMART MEDIA HANDLING (videos, GIFs, portraits, card images)
# ---------------------------------------------------------------------------
def ranked_mp4_variants(video: dict) -> list:
    """FxTwitter's formats[] list, mp4s only, highest bitrate first."""
    formats = [f for f in (video.get("formats") or []) if isinstance(f, dict)]
    mp4s = [f for f in formats if f.get("container") == "mp4" and f.get("url")]
    mp4s.sort(key=lambda f: f.get("bitrate") or 0, reverse=True)
    return [f["url"] for f in mp4s]


def is_risky_unknown_video(video: dict) -> bool:
    """Only used when size can't be probed: > 5 min AND >= 1080p = risky."""
    duration = video.get("duration") or 0
    pixels = (video.get("width") or 0) * (video.get("height") or 0)
    return duration > RISKY_DURATION_SECONDS and pixels >= RISKY_MIN_PIXELS


def is_gif_video(video: dict) -> bool:
    """True for X "GIFs" — they arrive as tiny looping tweet_video mp4s."""
    url = video.get("url") or ""
    return video.get("type") == "gif" or "tweet_video" in url


def gallery_video_url(video: dict, raw_url: str) -> str:
    """
    Gallery URL for a video. Portrait/vertical videos (h > w) were once
    observed to "load but not play" right after posting, but follow-up tests
    showed the SAME direct URLs playing fine in Components V2 shortly after
    (even media that had failed earlier) — a transient Discord proxy warm-up
    issue, not a real incompatibility. So direct URLs are used for everything
    (same as every other embed service, and no extra load on FxTwitter).
    If genuine portrait breakage is ever confirmed again, set
    PORTRAIT_PROXY = True to re-route vertical videos through FxTwitter's
    embed proxy (/2/go?url=), which historically played them.
    """
    if PORTRAIT_PROXY:
        width = video.get("width") or 0
        height = video.get("height") or 0
        if height > width > 0:
            return FXTWITTER_VIDEO_PROXY + url_quote(raw_url, safe="")
    return raw_url


async def probe_video_size(session: aiohttp.ClientSession, url: str,
                           timeout: int = 8) -> int | None:
    """
    Returns the video's real byte size, or None if it can't be determined.
    Probes the DIRECT twimg URL (never the proxy wrapper).
    HEAD first; falls back to a 1-byte Range GET + Content-Range.
    """
    try:
        async with session.head(url, headers=BROWSER_HEADERS, allow_redirects=True,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > 0:
                return int(cl)
    except Exception:
        pass
    try:
        headers = {**BROWSER_HEADERS, "Range": "bytes=0-0"}
        async with session.get(url, headers=headers, allow_redirects=True,
                               timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            content_range = resp.headers.get("Content-Range")  # "bytes 0-0/949197256"
            if content_range and "/" in content_range:
                total = content_range.rsplit("/", 1)[1].strip()
                if total.isdigit() and int(total) > 0:
                    return int(total)
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > 0:
                return int(cl)
    except Exception:
        pass
    return None


async def _probe_image_url(session: aiohttp.ClientSession, url: str) -> bool:
    """HEAD-checks that a URL answers 200 with an image content type."""
    try:
        async with session.head(url, headers=BROWSER_HEADERS, allow_redirects=True,
                                timeout=aiohttp.ClientTimeout(total=8)) as resp:
            content_type = resp.headers.get("Content-Type") or ""
            return resp.status == 200 and content_type.startswith("image/")
    except Exception:
        return False


async def resolve_gif_image(session: aiohttp.ClientSession,
                            video_url: str) -> tuple[str | None, str]:
    """
    X GIFs ship as tweet_video/*.mp4. Two community converters re-encode them
    into REAL animated images that Discord renders inline (better than an mp4
    player for tiny loops). They are probed in order — never forced:

      1. gif.fxtwitter.com/tweet_video/<id>.webp — the official FxTwitter
         asset that V1 embeds use. Verified INTERMITTENTLY DOWN (Cloudflare
         530/1033), so it's only used when the probe succeeds.
      2. fastgif-production.up.railway.app/tweet_video/<id>.gif — independent
         third-party converter (round 6). Only its .gif route works (its
         .webp route 500s), and unknown ids 500, which makes it probeable.

    Returns (url, source) with source "gif.fxtwitter" | "fastgif", or
    (None, "") when neither answers — caller then keeps the mp4 (it still
    plays, just as a video).
    """
    match = re.search(r"tweet_video/([^./]+)\.mp4", video_url)
    if not match:
        return None, ""
    key = match.group(1)
    webp_url = GIF_WEBP_BASE + key + ".webp"
    if await _probe_image_url(session, webp_url):
        return webp_url, "gif.fxtwitter"
    gif_url = GIF_RAILWAY_BASE + key + ".gif"
    if await _probe_image_url(session, gif_url):
        return gif_url, "fastgif"
    return None, ""


async def collect_media(session: aiohttp.ClientSession, tweet_like: dict) -> tuple[list, list]:
    """
    Returns (gallery_items, notes) for any tweet-shaped object (works for
    quoted tweets too — they use the same media shape).

    Order of decisions per video:
      • GIF (type=gif / tweet_video)  -> animated WebP image if the CDN is up,
                                         otherwise fall back to mp4 handling
      • size <= VIDEO_SIZE_LIMIT      -> playable in gallery (portraits proxied)
      • size >  limit, variant fits   -> smaller mp4 + downgrade note
      • size >  limit, nothing fits   -> thumbnail + "watch on X" note
      • size unknown                  -> untouched unless clearly risky
    """
    gallery_items = []
    notes = []
    media = tweet_like.get("media", {}) or {}

    for v in media.get("videos", []) or []:
        video_url = v.get("url")
        if not video_url:
            continue

        # --- GIFs: prefer an animated image rendition (image, not video) ---
        if is_gif_video(v):
            gif_url, gif_src = await resolve_gif_image(session, video_url)
            if gif_url:
                if gif_src != "gif.fxtwitter":
                    logging.info(f"gif.fxtwitter.com down; using {gif_src} for {video_url}")
                gallery_items.append({"media": {"url": gif_url}})
                continue
            logging.info(f"No GIF converter answered for {video_url} — keeping mp4 player.")

        size = await probe_video_size(session, video_url)

        # ROUND 10: optional gallery safety cap (GALLERY_VIDEO_LIMIT, 0 = off)
        effective_limit = GALLERY_VIDEO_LIMIT or VIDEO_SIZE_LIMIT

        if size is None:  # can't detect -> don't force a change (safe default)
            if is_risky_unknown_video(v):
                thumbnail = v.get("thumbnail_url")
                if thumbnail:
                    gallery_items.append({"media": {"url": thumbnail}})
                minutes = round((v.get("duration") or 0) / 60)
                notes.append(f"⚠️ Long high-res video (~{minutes} min) likely won't play in this "
                             f"card — [▶️ Watch it on X]({video_url})")
            else:
                gallery_items.append({"media": {"url": gallery_video_url(v, video_url)}})
            continue

        if size <= effective_limit:
            gallery_items.append({"media": {"url": gallery_video_url(v, video_url)}})
            continue

        # --- too big for Discord's gallery ---
        mb = size / (1024 * 1024)
        _variants = ranked_mp4_variants(v)
        if effective_limit < VIDEO_SIZE_LIMIT:
            # ROUND 10: guard active -> check ALL variants (not just the top 3
            # by bitrate) so a much smaller rendition still gets a chance.
            _candidates = _variants
        else:
            _candidates = _variants[:MAX_VARIANT_PROBES]
        swapped = False
        for variant_url in _candidates:
            if variant_url == video_url:
                continue
            variant_size = await probe_video_size(session, variant_url)
            if variant_size is not None and variant_size <= effective_limit:
                vmb = variant_size / (1024 * 1024)
                gallery_items.append({"media": {"url": gallery_video_url(v, variant_url)}})
                notes.append(f"🔽 Original video is ~{mb:.0f} MB — showing a smaller version "
                             f"(~{vmb:.0f} MB) so it plays in Discord. "
                             f"[▶️ Watch full quality on X]({video_url})")
                logging.info(f"Video downgraded: {mb:.0f}MB -> {vmb:.0f}MB variant")
                swapped = True
                break
        if not swapped:
            thumbnail = v.get("thumbnail_url")
            if thumbnail:
                gallery_items.append({"media": {"url": thumbnail}})
            notes.append(f"⚠️ Video is ~{mb:.0f} MB — too large for this card. "
                         f"[▶️ Watch it on X]({video_url})")

    for p in media.get("photos", []) or []:
        if p.get("url"):
            gallery_items.append({"media": {"url": p["url"]}})

    return gallery_items, notes


# ---------------------------------------------------------------------------
# ■ X ARTICLES (round 10, 2026-09-13)
# FxTwitter's API returns the full article as JSON (tweet.article):
#   title, preview_text, cover_media, media_entities,
#   content = draft.js {blocks: [...], entityMap: [...]}
# 'unstyled' blocks are paragraphs, 'atomic' blocks are media slots whose
# entityRanges[0].key indexes entityMap -> data.mediaItems[].mediaId ->
# media_entities[] (ApiImage / ApiGif / ApiVideo). No x.com scraping needed.
# ---------------------------------------------------------------------------
def article_body_text(article: dict) -> str:
    """Joins the article's text blocks into readable paragraphs."""
    paragraphs = []
    list_buf = []
    for block in (article.get("content", {}) or {}).get("blocks", []) or []:
        btype = block.get("type") or "unstyled"
        text = (block.get("text") or "").strip()
        if not text:
            continue
        if btype == "unordered-list-item":
            list_buf.append(f"• {text}")
            continue
        if list_buf:
            paragraphs.append("\n".join(list_buf))
            list_buf = []
        if btype in ("header-one", "header-two", "header-three"):
            text = f"**{text}**"
        paragraphs.append(text)
    if list_buf:
        paragraphs.append("\n".join(list_buf))
    return "\n\n".join(paragraphs)


def article_cover_item(article: dict) -> dict | None:
    """The article's banner/cover image as a gallery item (or None)."""
    info = (article.get("cover_media") or {}).get("media_info") or {}
    url = info.get("original_img_url")
    if url:
        return {"media": {"url": url}}
    return None


async def article_media_items(session: aiohttp.ClientSession,
                              article: dict) -> tuple[list, list]:
    """
    Gallery items for media embedded INSIDE the article, in document order.
    Images (ApiImage) are used as-is; GIFs (ApiGif) and videos (ApiVideo) are
    fed through collect_media() as a tweet-shaped object so they get EXACTLY
    the same handling as tweet media: the animated gif.fxtwitter/fastgif
    chain for GIFs, and size probe + downgrade notes for videos.
    """
    items = []
    notes = []
    by_id = {str(e.get("media_id")): e for e in article.get("media_entities") or []}
    entity_map = (article.get("content", {}) or {}).get("entityMap") or []
    for block in (article.get("content", {}) or {}).get("blocks", []) or []:
        if block.get("type") != "atomic":
            continue
        for er in block.get("entityRanges", []) or []:
            entry = next((em for em in entity_map
                          if str(em.get("key")) == str(er.get("key"))), None)
            if not entry:
                continue
            media_items_data = ((entry.get("value") or {}).get("data") or {}).get("mediaItems") or []
            for mi in media_items_data:
                ent = by_id.get(str(mi.get("mediaId")))
                if not ent:
                    continue
                info = ent.get("media_info") or {}
                typename = info.get("__typename")
                if typename == "ApiImage":
                    url = info.get("original_img_url")
                    if url:
                        items.append({"media": {"url": url}})
                elif typename in ("ApiGif", "ApiVideo"):
                    variants = [v for v in (info.get("variants") or [])
                                if v.get("url") and v.get("content_type") == "video/mp4"]
                    if not variants:
                        continue
                    fake_video = {
                        "url": variants[0]["url"],
                        "type": "gif" if typename == "ApiGif" else "video",
                        "formats": [{"container": "mp4", "url": v["url"],
                                     "bitrate": v.get("bitrate")} for v in variants],
                    }
                    v_items, v_notes = await collect_media(session, {"media": {"videos": [fake_video]}})
                    items.extend(v_items)
                    notes.extend(v_notes)
    return items, notes


# ---------------------------------------------------------------------------
# ■ OPENGRAPH CARD IMAGES (X Articles, link cards)
# ---------------------------------------------------------------------------
OG_IMAGE_RE = re.compile(r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                         re.IGNORECASE)
OG_IMAGE_RE_ALT = re.compile(r'<meta[^>]+content=[\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
                             re.IGNORECASE)
OG_IMAGE_RE_ALT2 = re.compile(r'<meta[^>]+content="([^"]+)"[^>]+(?:property|name)=["\']og:image["\']',
                              re.IGNORECASE)


async def fetch_og_image(session: aiohttp.ClientSession, url: str) -> str | None:
    """
    Fetches a page's OpenGraph image (og:image). x.com serves proper meta tags
    to crawlers — for X Articles this is the article's banner image, and for
    tweets containing a link card it's the card image. Profile pictures are
    rejected (a plain text tweet must not sprout the author's avatar).
    """
    for attempt in range(3):  # x.com rate-limits erratically -> retry with backoff
        for ua in (DISCORD_BOT_UA, BROWSER_HEADERS["User-Agent"]):
            try:
                async with session.get(url, headers={"User-Agent": ua, "Accept": "text/html"},
                                       allow_redirects=True,
                                       timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        continue
                    body = await resp.content.read(512 * 1024)
                page = body.decode("utf-8", errors="ignore")
                match = (OG_IMAGE_RE.search(page) or OG_IMAGE_RE_ALT.search(page)
                         or OG_IMAGE_RE_ALT2.search(page))
                if match:
                    img = html_lib.unescape(match.group(1)).strip()
                    if img.startswith("//"):
                        img = "https:" + img
                    if img.startswith("http") and "profile_images" not in img:
                        return img
            except Exception:
                continue
        if attempt < 2:
            await asyncio.sleep(1.5)
    return None


def first_external_link(text: str) -> str | None:
    """First http(s) link in the text that isn't an X/Twitter internal URL."""
    for match in re.finditer(r"https?://[^\s]+", text or ""):
        url = match.group(0)
        if not re.search(r"//(www\.)?(x\.com|twitter\.com|t\.co|pic\.x\.com|pic\.twitter\.com)", url):
            return url
    return None


# ---------------------------------------------------------------------------
# ■ QUOTED TWEETS
# ---------------------------------------------------------------------------
def build_quote_components(quote: dict, q_gallery: list, q_notes: list) -> list:
    """
    Renders the quoted tweet like the better embed services do:
        >>> [Quote](url) from **Name** ([@user](x.com/user))
        <quoted text>
    followed by its own media gallery.
    """
    author = quote.get("author", {}) or {}
    q_name = (author.get("name") or "").strip() or (author.get("screen_name") or "Unknown")
    q_screen = author.get("screen_name") or ""
    q_url = quote.get("url") or (f"https://x.com/{q_screen}/status/{quote.get('id')}"
                                 if q_screen else "https://x.com")
    head = (f">>> [**Quote**]({q_url}) from **{q_name}** "
            f"([**@{q_screen}**](https://x.com/{q_screen}))" if q_screen
            else f">>> [**Quote**]({q_url}) from **{q_name}**")

    q_text = (quote.get("text") or "").strip()
    if q_text:
        budget = QUOTE_TEXT_BUDGET
        if len(q_text) > budget:
            q_text = q_text[:budget].rstrip() + " …"
        head = f"{head}\n\n{linkify_text(q_text)}"

    components = [
        {"type": 14, "divider": True, "spacing": 2},
        {"type": 10, "content": head},
    ]
    if q_gallery:
        components.append({"type": 12, "items": q_gallery[:10]})
    for note in q_notes:
        components.append({"type": 10, "content": note})
    return components


def parse_test_tweet_id(value: str) -> tuple:
    """round 12 (backported from V3, 2026-09-17): TEST_TWEET_ID recovery helper.

    Accepts 'screen_name/tweet_id' or a full x.com/twitter.com status URL
    and returns (screen_name, tweet_id), or (None, None) when unparseable.
    """
    value = (value or "").strip()
    if not value:
        return None, None
    if "status/" in value:
        match = re.search(r"status/(\d+)", value)
        if not match:
            return None, None
        tweet_id = match.group(1)
        before = value.split("/status/", 1)[0].rstrip("/")
        account = before.rsplit("/", 1)[-1]
        return (account or None), tweet_id
    # bare screen_name/tweet_id form
    account, _, tweet_id = value.partition("/")
    account, tweet_id = account.strip(), tweet_id.strip()
    if account and re.fullmatch(r"\d+", tweet_id):
        return account, tweet_id
    return None, None


class _TestFeedEntry:
    """A feedparser-shaped entry for ONE specific tweet (test-tweet mode)."""

    def __init__(self, link):
        self.link = link

    def get(self, key, default=None):
        return default


class _TestFeed:
    """A feedparser-shaped feed with a single (test) entry."""

    def __init__(self, entries):
        self.entries = entries


async def fetch_working_feed(session: aiohttp.ClientSession, account: str):
    # round 12 (backported from V3, 2026-09-17): every attempt is logged.
    # Before this, a dead nitter fleet made the whole monitor a silent
    # no-op (zero log lines).
    headers = {"User-Agent": "Mozilla/5.0"}
    for instance in RSS_INSTANCES:
        feed_url = f"{instance}/{account}/rss"
        req_headers = headers
        token_env = RSS_TOKEN_ENV.get(instance)
        token = os.getenv(token_env, "").strip() if token_env else ""
        if token:
            # Token-gated instance (bot check): send the RSS token as a
            # Bearer header, inside the User-Agent (round 14, 2026-09-18 —
            # the operator confirmed the token is accepted anywhere in the
            # UA) and as a ?token= query param. A wrong/missing token just
            # gets a challenge/403 answer, which is logged below and the
            # chain moves on.
            req_headers = {**headers,
                           "Authorization": f"Bearer {token}",
                           "User-Agent": f"Mozilla/5.0 {token}"}
            feed_url = f"{feed_url}?token={url_quote(token, safe='')}"
        try:
            async with session.get(feed_url, headers=req_headers,
                                   timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    content = await response.text()
                    feed = await asyncio.to_thread(feedparser.parse, content)
                    if feed.entries:
                        logging.info(f"[{account}] feed OK via {instance} — "
                                     f"{len(feed.entries)} entries.")
                        return feed
                    logging.info(f"[{account}] {instance}: HTTP 200 but 0 entries "
                                 f"(bot check / stale instance?) — trying next.")
                else:
                    logging.info(f"[{account}] {instance}: HTTP {response.status} — "
                                 f"trying next.")
        except Exception as e:
            logging.info(f"[{account}] {instance}: {type(e).__name__}: {e} — "
                         f"trying next.")
    logging.warning(f"[{account}] NO working nitter instance this run — "
                    f"account skipped (see TEST_TWEET_ID to rebuild one tweet).")
    return None


async def fetch_tweet_details(session: aiohttp.ClientSession, account: str, tweet_id: str,
                              lang_suffix: str = "") -> dict | None:
    """
    Fetches tweet data from FxTwitter's API using the PLAIN-ID path
    (/status/:id). The true author is read from the payload afterwards
    (which is what round-13 repost detection relies on), so a REPOST
    resolves to the ORIGINAL author, never the feed account.
    lang_suffix: '/en' etc.

    CORRECTED 2026-09-17: an earlier version of this comment claimed the
    screen-name path (/<name>/status/:id) returns 404 for reposts of other
    authors' tweets, X Articles and some newer tweets. Re-verified live on
    2026-09-17 — that is NO LONGER TRUE. FxTwitter resolves purely by tweet
    id and ignores the screen name in the path: api.fxtwitter.com/<anything>
    /status/2099456877558202445 returns HTTP 200 with the correct @zeroartwo
    payload, even for a screen name that does not exist. The plain-id path
    is kept because it is the shortest form and cannot drift out of sync
    with the payload's author — NOT because the other path 404s.
    """
    url = f"{FXTWITTER_API_BASE}/status/{tweet_id}{lang_suffix}"
    headers = {"User-Agent": "NewsFlashBot/3.0"}
    try:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("tweet") or data.get("status")
    except Exception as e:
        logging.error(f"Error fetching FxTwitter data ({lang_suffix or 'original'}) for {tweet_id}: {e}")
    return None


def build_reply_line(tweet: dict) -> str | None:
    """'↩️ Replying to @user' small line when the tweet is a reply."""
    replying_to = tweet.get("replying_to")
    if not replying_to:
        return None
    reply_status = tweet.get("replying_to_status")
    href = (f"https://x.com/{replying_to}/status/{reply_status}" if reply_status
            else f"https://x.com/{replying_to}")
    return f"-# ↩️ Replying to [@{replying_to}]({href})"


def build_action_row(read_post_url: str) -> dict:
    """Separate action row placed OUTSIDE the container (V2 style)."""
    read_button = {
        "type": 2, "style": 5, "label": READ_POST_LABEL,
        "url": read_post_url, "emoji": READ_POST_EMOJI,
    }
    buttons = [read_button]
    for btn in STATIC_BUTTONS:
        b = {"type": 2, "style": 5, "label": btn["label"], "url": btn["url"]}
        if btn.get("emoji"):
            b["emoji"] = btn["emoji"]
        buttons.append(b)
    return {"type": 1, "components": buttons[:5]}


def build_v2_payload(account: str, tweet: dict, read_post_url: str,
                     display_text: str | None = None, posted_ts: int | None = None,
                     gallery_items: list | None = None, media_notes: list | None = None,
                     quote_components: list | None = None, reply_line: str | None = None,
                     lead_gallery_items: list | None = None,
                     repost_account: str | None = None) -> dict:
    """Constructs the V2 layout; the action row sits OUTSIDE the container."""
    author = tweet.get("author", {}) or {}
    author_name = (author.get("name") or "").strip() or account
    screen_name = author.get("screen_name") or account

    # chunk FIRST, then linkify each chunk (so markdown links never straddle
    # a component boundary). Empty text -> zero text components (no 400s).
    text_source = display_text if display_text is not None else tweet.get("text", "")
    chunks = [linkify_text(c) for c in chunk_text(text_source)]

    if gallery_items is None:
        gallery_items, media_notes = [], []
        media = tweet.get("media", {}) or {}
        for v in media.get("videos", []) or []:
            if v.get("url"):
                gallery_items.append({"media": {"url": v["url"]}})
        for p in media.get("photos", []) or []:
            if p.get("url"):
                gallery_items.append({"media": {"url": p["url"]}})

    replies = tweet.get("replies", 0)
    retweets = tweet.get("retweets", 0)
    likes = tweet.get("likes", 0)
    views = tweet.get("views", "N/A")

    if repost_account:
        # round 13 (2026-09-17): repost — attribute the account that reposted
        # it (screen name as the label, per 2026-09-17), and keep the true
        # author visible on the line below.
        inner_components = [
            {"type": 10,
             "content": (f"### [{repost_account} reposted]"
                         f"(https://x.com/{repost_account})")},
            {"type": 10,
             "content": (f"-# 📌 Original: [{author_name} "
                         f"(@{screen_name})](https://x.com/{screen_name})")},
        ]
    else:
        inner_components = [
            {"type": 10, "content": f"### [{author_name}](https://x.com/{screen_name}) just tweeted:"},
        ]
    if reply_line:
        inner_components.append({"type": 10, "content": reply_line})
    if lead_gallery_items:  # ROUND 10: article cover, shown right under the header
        inner_components.append({"type": 12, "items": lead_gallery_items[:10]})
    for chunk in chunks:
        inner_components.append({"type": 10, "content": chunk})
    if gallery_items:
        inner_components.append({"type": 14, "divider": True, "spacing": 1})
        inner_components.append({"type": 12, "items": gallery_items[:10]})
    for note in media_notes or []:
        inner_components.append({"type": 10, "content": note})
    if quote_components:
        inner_components.extend(quote_components)

    if not chunks and not gallery_items and not quote_components:
        inner_components.append({
            "type": 10,
            "content": "-# 📄 This opens a long-form page on X — tap *Read Post* to view it.",
        })

    ts_suffix = f"   •   🕐 <t:{posted_ts}:f>" if posted_ts else ""
    inner_components.append({
        "type": 10,
        "content": f"-# 💬 {replies} 🔁 {retweets} ❤️ {likes} 👁️ {views}{ts_suffix}",
    })

    container = {
        "type": 17,
        "accent_color": accent_from_color(tweet.get("color")),
        "components": inner_components,
    }
    # V2 style: action row is a separate top-level component, outside the card
    return {"flags": IS_COMPONENTS_V2, "components": [container, build_action_row(read_post_url)]}


async def main():
    if not ACCOUNTS:
        return
    posted_urls = load_posted_urls()
    is_first_run = len(posted_urls) == 0
    now = time.time()

    async with aiohttp.ClientSession() as session:
        feeds_tasks = [fetch_working_feed(session, acc) for acc in ACCOUNTS]
        feeds = await asyncio.gather(*feeds_tasks)

        feed_pairs = list(zip(ACCOUNTS, feeds))

        # round 12 (backported from V3, 2026-09-17): TEST_TWEET_ID — rebuild
        # ONE specific tweet WITHOUT the nitter feed (the nitter fleet was
        # down/stale on 2026-09-17 and the monitor silently no-oped — see
        # Wuthering_Waves/2100555373073797461). Format: screen_name/tweet_id
        # (a full x.com status URL also works). It flows through the SAME
        # pipeline below (data fallback chain, media, card, webhook) and the
        # SAME cache — an already-posted tweet is skipped by the check further
        # down, so it is safe to run even if a normal run posts it meanwhile.
        test_tweet_id = os.getenv("TEST_TWEET_ID", "").strip()
        if test_tweet_id:
            t_account, t_id = parse_test_tweet_id(test_tweet_id)
            if not t_account or not t_id:
                logging.error(f"TEST_TWEET_ID malformed ({test_tweet_id!r}) — "
                              f"expected screen_name/tweet_id or a full status "
                              f"URL — ignored.")
            else:
                feed_pairs.append((t_account, _TestFeed([_TestFeedEntry(
                    f"https://x.com/{t_account}/status/{t_id}")])))
                logging.info(f"TEST TWEET: {t_account}/{t_id} — nitter feed "
                             f"bypassed for this one tweet.")

        if not any(feed and feed.entries for _, feed in feed_pairs):
            logging.error("ALL FEEDS FAILED this run — no nitter instance "
                          "answered for ANY account (per-instance lines "
                          "above). Nothing to check — use TEST_TWEET_ID to "
                          "rebuild a specific tweet.")

        for account, feed in feed_pairs:
            if not feed or not feed.entries:
                logging.info(f"[{account}] skipping — no feed / 0 entries.")
                continue
            webhook_url = get_webhook_for_account(account)
            if not webhook_url:
                logging.warning(f"[{account}] skipping — no webhook configured "
                                f"(set WEBHOOK_{re.sub(r'[^A-Za-z0-9]', '_', account).upper()}"
                                f" or DISCORD_WEBHOOK_URL).")
                continue
            entries = [feed.entries[0]] if is_first_run else feed.entries
            for entry in entries:
                match = re.search(r"/status/(\d+)", getattr(entry, "link", ""))
                if not match:
                    continue
                tweet_id = match.group(1)
                unique_key = f"{account}_{tweet_id}"
                if unique_key in posted_urls:
                    continue

                published_parsed = entry.get("published_parsed")
                rss_ts = time.mktime(published_parsed) if published_parsed else now

                # ROUND 11 (backported from V3, 2026-09-17): 4-step tweet-data
                # fallback chain — every result is normalized to the
                # FxTwitter shape, so the card pipeline below is unchanged no
                # matter which service answered (winner is logged: source=...).
                if twitter_proxy is not None:
                    tweet_data, tweet_source = await twitter_proxy.fetch_tweet_details_any(
                        session, account, tweet_id, label=unique_key)
                else:
                    # legacy direct FxTwitter call (module file missing)
                    tweet_data = await fetch_tweet_details(session, account, tweet_id)
                    tweet_source = "fxtwitter"
                if not tweet_data:
                    continue

                # --- canonical author/read-post URL from the payload ---
                author_data = tweet_data.get("author", {}) or {}
                author_screen = author_data.get("screen_name") or account
                read_post_url = f"https://fxtwitter.com/{author_screen}/status/{tweet_id}"

                # round 13 (2026-09-17): repost detection. A nitter feed for
                # an account contains only that account's own tweets and its
                # reposts — so when the TRUE author from the payload differs
                # from the feed account, this entry is a REPOST by the feed
                # account. (FxEmbed's payload does carry a `reposted_by` field,
                # but it is only populated when the RETWEET's own status id is
                # queried — the nitter RSS links point at the ORIGINAL
                # author's status, so the feed itself is the signal.)
                repost_account = (account
                                  if author_screen.lower() != account.lower()
                                  else None)
                status_page_url = f"https://x.com/{author_screen}/status/{tweet_id}"
                display_text = None

                # --- X ARTICLE detection (round 10) ---
                # The full article arrives as clean JSON in tweet.article;
                # tweet.text is empty for article tweets.
                article_data = (tweet_data.get("article")
                                if isinstance(tweet_data.get("article"), dict) else None)
                is_article = bool(article_data and (article_data.get("content") or {}).get("blocks"))

                # --- auto-translation for non-English tweets ---
                # (articles: FxTwitter doesn't translate article bodies -> skip /en)
                lang = (tweet_data.get("lang") or "").lower()
                # ROUND 11: /en is a FxTwitter feature — attempted only when
                # the data really came from FxTwitter (backup services in the
                # chain don't offer the /en endpoint).
                if lang and lang != "en" and not is_article and tweet_source == "fxtwitter":
                    translated_data = await fetch_tweet_details(session, account, tweet_id,
                                                                lang_suffix="/en")
                    if translated_data and translated_data.get("translation"):
                        translation = translated_data["translation"]
                        translated_text = translation.get("text", tweet_data.get("text", ""))
                        original_text = tweet_data.get("text", "")
                        # round 28 (2026-09-18): X's per-tweet `lang` is an
                        # automatic GUESS and it misfires (live case
                        # 2100794014630846965: "Maintenance 🩸" + hashtags
                        # from an ESP/ENG artist tagged lang=fr). When /en
                        # returns a translation IDENTICAL to the original,
                        # post as-is instead of a bogus "Translated from X"
                        # block. Real translations always differ -> unchanged.
                        if translation_is_identical(original_text, translated_text):
                            logging.info(f"[{unique_key}] /en translation is identical to the original — X language mis-detection (lang={lang}), posting as-is.")
                        else:
                            lang_name = LANGUAGE_NAMES.get(lang, lang.upper())
                            display_text = (
                                f"🌐 Translated from {lang_name}\n\n"
                                f"{translated_text}\n\n"
                                f"**Original text**\n{original_text}"
                            )
                            tweet_data = translated_data
                            read_post_url += "/en"
                # ------------------------------------------------

                # --- media gallery (videos probed/ported/GIF'd) ---
                lead_gallery = None
                if is_article:
                    # ROUND 10: full article — cover + **title** + body text,
                    # in-article media gets the same GIF/video handling.
                    title = (article_data.get("title") or "").strip()
                    article_text = article_body_text(article_data)
                    if not article_text:
                        article_text = (article_data.get("preview_text") or "").strip()
                    display_text = (f"**{title}**\n\n{article_text}"
                                    if title and article_text else (title or article_text))
                    cover = article_cover_item(article_data)
                    lead_gallery = [cover] if cover else None
                    a_items, a_notes = await article_media_items(session, article_data)
                    overflow = len(a_items) - 10
                    if overflow > 0:
                        a_items = a_items[:10]
                        a_notes = list(a_notes) + [
                            f"-# 📄 …and {overflow} more image(s) inside the article — tap *Read Post*."
                        ]
                    gallery_items = a_items
                    media_notes = a_notes
                else:
                    gallery_items, media_notes = await collect_media(session, tweet_data)

                # --- quoted tweet ---
                quote_components = None
                quote_data = tweet_data.get("quote")
                if isinstance(quote_data, dict) and quote_data.get("id"):
                    q_gallery, q_notes = await collect_media(session, quote_data)
                    quote_components = build_quote_components(quote_data, q_gallery, q_notes)

                # --- card image fallback (link cards) — articles already get
                #     their cover from the article JSON, so skip the OG fetch ---
                plain_text = (display_text if display_text is not None
                              else (tweet_data.get("text") or "")).strip()
                if not is_article and not gallery_items and not quote_components:
                    card_image = None
                    if not plain_text or first_external_link(plain_text):
                        card_image = await fetch_og_image(session, status_page_url)
                        if not card_image:
                            link = first_external_link(plain_text)
                            if link:
                                card_image = await fetch_og_image(session, link)
                    if card_image:
                        gallery_items.append({"media": {"url": card_image}})
                        media_notes = list(media_notes) + ["-# 🖼 Card image (from the post's page)"]

                reply_line = build_reply_line(tweet_data)
                posted_ts = resolve_tweet_ts(tweet_data, rss_ts)
                payload = build_v2_payload(account, tweet_data, read_post_url,
                                           display_text=display_text, posted_ts=posted_ts,
                                           gallery_items=gallery_items, media_notes=media_notes,
                                           quote_components=quote_components, reply_line=reply_line,
                                           lead_gallery_items=lead_gallery,
                                           repost_account=repost_account)
                target_url = f"{webhook_url}?with_components=true"
                async with session.post(target_url, json=payload) as resp:
                    if resp.status in (200, 204):
                        posted_urls.add(unique_key)
                        logging.info(f"V2 Posted: {unique_key} (lang={lang or 'en'}{' | article' if is_article else ''} | source={tweet_source})")
                        await asyncio.sleep(1.5)
                    else:
                        body = await resp.text()
                        logging.error(f"Discord error {resp.status} for {unique_key}: {body}")

    save_posted_urls(posted_urls)
    logging.info("Twitter V2 Monitor execution finished.")


if __name__ == "__main__":
    asyncio.run(main())
