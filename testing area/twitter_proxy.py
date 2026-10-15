# ---------------------------------------------------------------------------
# ■ TWEET DATA SERVICES (round 11, 2026-09-17) — twitter_proxy.py
# ---------------------------------------------------------------------------
# Tweet data (text, author, stats, media, quotes, articles) normally comes
# from FxTwitter's API (api.fxtwitter.com — the engine behind fxtwitter.com).
# When it cannot answer, this module tries the rest of the chain, in
# priority order:
#
#   1. fxtwitter   — primary. Most feature-complete: media, quoted tweets,
#                    X Articles, /en translation, view counts.
#   2. fixupx      — FxEmbed's sister client (fixupx.com, same engine and
#                    creator). The public FxEmbed API docs list only
#                    FxTwitter + FxBluesky, so api.fixupx.com is treated as
#                    OPTIONAL: if the host does not resolve, the request
#                    fails instantly (DNS) and the chain continues.
#   3. vxtwitter   — BetterTwitFix's public API (api.vxtwitter.com, see
#                    their api.md): text, media (including type=gif),
#                    quoted tweets (qrt), lang, epoch date, stats. No view
#                    counts, no X Article bodies.
#   4. twitterez   — the EmbedEZ backend behind twitterez.com (the same one
#                    redditez uses for Reddit): the keyless search API maps
#                    the tweet URL to a stable key, then the bot embed page
#                    (Discordbot UA) exposes og: tags — media as embedez
#                    redirect URLs (GIFs arrive as animated .webp, which
#                    Discord loops natively), the text + a stats line
#                    (💬 N  🔁 N  💜 N  👀 N) inside og:description.
#
# Every service's result is normalized to the FxTwitter "tweet" shape, so
# the card pipeline (media handling, GIF chain, quotes, payload) is
# unchanged. fetch_tweet_details_any() returns (tweet, service) — service is
# the winner's name, for logging. A missing or broken module never breaks
# the run: the caller falls back to the legacy direct FxTwitter call.
# ---------------------------------------------------------------------------
import re
import html as html_lib
import logging
from urllib.parse import quote as url_quote

import aiohttp

FXTWITTER_API_BASE = "https://api.fxtwitter.com"
FIXUPX_API_BASE = "https://api.fixupx.com"          # optional — see module notes
VXTWITTER_API_BASE = "https://api.vxtwitter.com"
TWITTEREZ_SEARCH_ENDPOINT = "https://embedez.com/api/v1/providers/search"
TWITTEREZ_EMBED_PAGE = "https://embedez.com/embed/{key}"

# The embedez bot page (og tags) is served to social-preview bots only — a
# normal browser UA is redirected to the /download landing page instead.
TWITTEREZ_BOT_UA = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"

USER_AGENT = "NewsFlashBot/3.0"
TIMEOUT = aiohttp.ClientTimeout(total=15)

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}

GIF_WEBP_BASE = "https://gif.fxtwitter.com/tweet_video/"
GIF_RAILWAY_BASE = "https://fastgif-production.up.railway.app/tweet_video/"
GIFCONVERT_BASE = "https://gifconvert.vxtwitter.com/convert."

# The first line of a twitterez og:description is a compact stats line,
# e.g.  **💬 2  🔁 140  💜 1K  👀 16.7K**  (replies/retweets/likes/views)
TWSTATS_RE = re.compile(
    r"💬\s*([\d.,]+[KkMm]?)\s*🔁\s*([\d.,]+[KkMm]?)\s*💜\s*([\d.,]+[KkMm]?)\s*👀\s*([\d.,]+[KkMm]?)")


def _compact_int(s) -> int:
    """'1.1K' -> 1100, '2M' -> 2000000, '1,234' -> 1234, '57' -> 57, junk -> 0."""
    s = (s or "").strip().replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KkMm])?", s)
    if not m:
        return 0
    n = float(m.group(1))
    if m.group(2):
        n *= 1_000_000 if m.group(2).upper() == "M" else 1_000
    return int(n)


def _og_meta(page_html: str) -> dict:
    """og:/twitter: meta tags -> dict. og:image collects ALL occurrences in
    order (galleries emit one tag per photo); every other key keeps the
    first value. HTML entities are unescaped.
    (Same logic as reddit_proxy._og_meta — kept local so this module has no
    cross-module dependency.)"""
    meta = {}
    for m in re.finditer(r"<meta[^>]+>", page_html or "", re.I):
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


async def _probe_image_url(session, url: str,
                           referer: str | None = None) -> bool:
    """HEAD-checks that a URL answers 200 with an image content type.
    ROUND 11: optional referer for header-gated converters (gifconvert)."""
    try:
        headers = dict(BROWSER_HEADERS)
        if referer:
            headers["Referer"] = referer
        async with session.head(url, headers=headers, allow_redirects=True,
                                timeout=aiohttp.ClientTimeout(total=8)) as resp:
            content_type = resp.headers.get("Content-Type") or ""
            return resp.status == 200 and content_type.startswith("image/")
    except Exception:
        return False


async def resolve_gif_image(session,
                            video_url: str) -> tuple[str | None, str]:
    """
    X GIFs ship as tweet_video/*.mp4. Community converters re-encode them
    into REAL animated images that Discord renders inline (better than an mp4
    player for tiny loops). Probed in order — never forced (round 11):

      1. gif.fxtwitter.com/tweet_video/<id>.webp — the official FxTwitter
         asset that V1 embeds use. Verified INTERMITTENTLY DOWN (Cloudflare
         530/1033), so it's only used when the probe succeeds.
      2. gifconvert.vxtwitter.com/convert.webp?url=<mp4> — BetterTwitFix's
         converter (round 11; the exact URL shape vxtwitter's own embeds
         use). Header-gated: probed with a browser Referer.
      3. gifconvert.vxtwitter.com/convert.gif?url=<mp4>
      4. fastgif-production.up.railway.app/tweet_video/<id>.gif — independent
         third-party converter (round 6; OFFLINE since 2026-09-17 — last
         probe only, auto-revives if it returns).

    Returns (url, source) with source "gif.fxtwitter" | "gifconvert" |
    "fastgif", or (None, "") when nothing answers — caller then keeps the
    mp4 (it still plays, just as a video).
    """
    match = re.search(r"tweet_video/([^./]+)\.mp4", video_url)
    if not match:
        return None, ""
    key = match.group(1)
    webp_url = GIF_WEBP_BASE + key + ".webp"
    if await _probe_image_url(session, webp_url):
        return webp_url, "gif.fxtwitter"
    vc_webp = GIFCONVERT_BASE + "webp?url=" + url_quote(video_url, safe="")
    if await _probe_image_url(session, vc_webp, referer="https://vxtwitter.com"):
        return vc_webp, "gifconvert"
    vc_gif = GIFCONVERT_BASE + "gif?url=" + url_quote(video_url, safe="")
    if await _probe_image_url(session, vc_gif, referer="https://vxtwitter.com"):
        return vc_gif, "gifconvert"
    gif_url = GIF_RAILWAY_BASE + key + ".gif"
    if await _probe_image_url(session, gif_url):
        return gif_url, "fastgif"
    return None, ""


# ---------------------------------------------------------------------------
# ■ NORMALIZERS — every service's result becomes the FxTwitter "tweet" shape
# ---------------------------------------------------------------------------
def normalize_vxtwitter(data: dict) -> dict | None:
    """vxtwitter JSON (api.md) -> FxTwitter "tweet" shape, or None."""
    if not isinstance(data, dict) or not data.get("tweetID"):
        return None
    videos, photos = [], []
    for m in data.get("media_extended") or []:
        if not isinstance(m, dict) or not m.get("url"):
            continue
        size = m.get("size") or {}
        if m.get("type") == "image":
            photos.append({"url": m["url"]})
        else:
            is_gif = m.get("type") == "gif" or "tweet_video" in str(m.get("url"))
            videos.append({
                "url": m["url"],
                "thumbnail_url": m.get("thumbnail_url"),
                "duration": round((m.get("duration_millis") or 0) / 1000),
                "width": size.get("width") or 0,
                "height": size.get("height") or 0,
                "type": "gif" if is_gif else "video",
                "formats": [{"url": m["url"], "container": "mp4", "bitrate": 0}],
            })
    qrt = data.get("qrt")
    return {
        "id": str(data.get("tweetID")),
        "url": data.get("tweetURL") or "",
        "text": data.get("text") or "",
        "lang": data.get("lang"),
        "color": None,
        "author": {"name": data.get("user_name") or "",
                   "screen_name": data.get("user_screen_name") or ""},
        "replies": data.get("replies") or 0,
        "retweets": data.get("retweets") or 0,
        "likes": data.get("likes") or 0,
        "views": "N/A",  # vxtwitter does not report views
        "created_at": data.get("date"),
        "created_timestamp": data.get("date_epoch"),
        "replying_to": data.get("replyingTo"),
        "replying_to_status": data.get("replyingToID"),
        "media": {"videos": videos, "photos": photos},
        "quote": normalize_vxtwitter(qrt) if isinstance(qrt, dict) and qrt else None,
        "article": None,  # vxtwitter does not expose X Article bodies
    }


def normalize_twitterez(meta: dict, tweet_id: str | None = None) -> dict | None:
    """twitterez (embedez) bot-page og: tags -> FxTwitter "tweet" shape.

    og:description starts with the stats line (💬 replies /  retweets /
    💜 likes / 👀 views — compact numbers like 16.7K or 1.5M); the rest is
    the tweet text, and EmbedEZ sometimes APPENDS a promo/ad line at the
    end, which is dropped. og:video(secure_url) -> video (tweet_video mp4
    = GIF); each og:image -> a photo (embedez redirect URLs, in gallery
    order — a GIF tweet's image is an animated .webp that Discord loops
    natively). For VIDEO tweets the og:image is the video poster, not a
    photo, so it is skipped.
    """
    if not isinstance(meta, dict):
        return None
    description = meta.get("og:description") or ""
    text = description
    replies = retweets = likes = 0
    views = "N/A"
    sm = TWSTATS_RE.search(description)
    if sm:
        replies = _compact_int(sm.group(1))
        retweets = _compact_int(sm.group(2))
        likes = _compact_int(sm.group(3))
        views = _compact_int(sm.group(4))
        text = description[:sm.start()] + description[sm.end():]
    text = re.sub(r"\*\*", "", text)
    videos, photos = [], []
    video_url = meta.get("og:video:secure_url") or meta.get("og:video")
    if video_url:
        is_gif = "tweet_video" in str(video_url)
        videos.append({"url": video_url, "thumbnail_url": None, "duration": 0,
                       "width": 0, "height": 0,
                       "type": "gif" if is_gif else "video",
                       "formats": [{"url": video_url, "container": "mp4", "bitrate": 0}]})
    raw_images = meta.get("og:image") or []
    if isinstance(raw_images, str):
        raw_images = [raw_images]
    for img in raw_images:
        s = str(img)
        # video tweets: a lone og:image is the video poster, not a photo
        if videos and ("proxy.embedez.com/thumbnail" in s or s.split("?")[0].endswith(".mp4")):
            continue
        photos.append({"url": s})
    if not videos and not photos and not text.strip():
        return None
    # Drop EmbedEZ promo/ad lines (verified in live og pages, e.g.
    # "[Add](https://embedez.com/t/test-bot) the EmbedEZ bot to your server
    #  *(ad)*" and "[Save](https://embedez.com/t/test-premium) any embed to
    #  your library with Premium *(ad)*").
    kept = [ln for ln in text.splitlines()
            if "embedez.com" not in ln.lower()
            and not ln.rstrip().lower().endswith("*(ad)*")
            and not ln.rstrip().lower().endswith("(ad)")]
    text = re.sub(r"[ \t]+", " ", "\n".join(kept)).strip()
    return {
        "id": tweet_id,
        "url": meta.get("og:url") or "",
        "text": text,
        "lang": None,
        "color": None,
        "author": {"name": meta.get("og:title") or "", "screen_name": ""},
        "replies": replies, "retweets": retweets, "likes": likes, "views": views,
        "created_at": None, "created_timestamp": None,  # -> RSS timestamp fallback
        "replying_to": None, "replying_to_status": None,
        "media": {"videos": videos, "photos": photos},
        "quote": None,
        "article": None,
    }


# ---------------------------------------------------------------------------
# ■ CHAIN FETCHERS (in priority order)
# ---------------------------------------------------------------------------
async def _fetch_fxtwitter(session, screen_name: str, tweet_id: str, label: str = ""):
    """1st in the chain. FxTwitter's API, PLAIN-ID path (/status/:id) — the
    same call the legacy direct fetch in twitter_v3.py makes."""
    url = f"{FXTWITTER_API_BASE}/status/{tweet_id}"
    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT},
                               timeout=TIMEOUT) as resp:
            if resp.status != 200:
                logging.info(f"[{label or tweet_id}] fxtwitter HTTP {resp.status}")
                return None
            data = await resp.json(content_type=None)
    except Exception as e:
        logging.info(f"[{label or tweet_id}] fxtwitter unavailable: {e}")
        return None
    if not isinstance(data, dict):
        return None
    return data.get("tweet") or data.get("status")


async def _fetch_fixupx(session, screen_name: str, tweet_id: str, label: str = ""):
    """2nd in the chain. FxEmbed's sister client (fixupx.com — same engine,
    same creator). NOT listed in the public FxEmbed API docs (FxTwitter +
    FxBluesky only), so the host may not exist: the request then fails
    instantly (DNS) and the chain continues. Expected shape: FxTwitter v1
    JSON ({"tweet": {...}} / {"status": {...}})."""
    url = f"{FIXUPX_API_BASE}/status/{tweet_id}"
    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT},
                               timeout=TIMEOUT) as resp:
            if resp.status != 200:
                logging.info(f"[{label or tweet_id}] fixupx HTTP {resp.status}")
                return None
            data = await resp.json(content_type=None)
    except Exception as e:
        logging.info(f"[{label or tweet_id}] fixupx unavailable: {e}")
        return None
    if not isinstance(data, dict):
        return None
    return data.get("tweet") or data.get("status")


async def _fetch_vxtwitter(session, screen_name: str, tweet_id: str, label: str = ""):
    """3rd in the chain. BetterTwitFix's public API — /<screen_name>/status/
    <id> (the screen-name path is required here)."""
    if not screen_name:
        return None
    url = f"{VXTWITTER_API_BASE}/{screen_name}/status/{tweet_id}"
    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT},
                               timeout=TIMEOUT) as resp:
            if resp.status != 200:
                logging.info(f"[{label or tweet_id}] vxtwitter HTTP {resp.status}")
                return None
            data = await resp.json(content_type=None)
    except Exception as e:
        logging.info(f"[{label or tweet_id}] vxtwitter unavailable: {e}")
        return None
    return normalize_vxtwitter(data)


async def _fetch_twitterez(session, screen_name: str, tweet_id: str, label: str = ""):
    """4th (last-resort) in the chain. The EmbedEZ backend behind
    twitterez.com — the same mechanism redditez uses for Reddit: keyless
    search API -> stable key -> bot embed page (Discordbot UA) -> og: tags."""
    if not screen_name:
        return None
    tweet_url = f"https://x.com/{screen_name}/status/{tweet_id}"
    try:
        async with session.get(TWITTEREZ_SEARCH_ENDPOINT, params={"url": tweet_url},
                               headers={"User-Agent": USER_AGENT},
                               timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except Exception as e:
        logging.info(f"[{label or tweet_id}] twitterez search unavailable: {e}")
        return None
    key = (data.get("data") or {}).get("key") if isinstance(data, dict) else None
    if not key:
        return None
    try:
        async with session.get(TWITTEREZ_EMBED_PAGE.format(key=key),
                               headers={"User-Agent": TWITTEREZ_BOT_UA,
                                        "Accept": "text/html"},
                               allow_redirects=True, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            page = (await resp.content.read(512 * 1024)).decode("utf-8",
                                                                errors="ignore")
    except Exception as e:
        logging.info(f"[{label or tweet_id}] twitterez page unavailable: {e}")
        return None
    tweet = normalize_twitterez(_og_meta(page), tweet_id)
    if tweet:
        # og:title is "Name (@screen)" — trim the handle we already know.
        name = (tweet["author"].get("name") or "").strip()
        suffix = f"(@{screen_name})"
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
        tweet["author"]["name"] = name or screen_name
        tweet["author"]["screen_name"] = screen_name
        if not tweet.get("url"):
            tweet["url"] = tweet_url
    return tweet


async def fetch_tweet_details_any(session, screen_name: str, tweet_id: str,
                                  label: str = "") -> tuple:
    """Runs the full chain and returns (tweet, service) — tweet normalized
    to the FxTwitter "tweet" shape, service = the winner's name (for
    logging), or (None, None) when every service fails. Each service is
    soft-timeout bounded; any failure just moves to the next in the chain.
    (The fetchers are referenced by name per call, so tests can monkeypatch
    them on this module.)"""
    fetchers = (
        ("fxtwitter", _fetch_fxtwitter),
        ("fixupx", _fetch_fixupx),
        ("vxtwitter", _fetch_vxtwitter),
        ("twitterez", _fetch_twitterez),
    )
    for name, fetch in fetchers:
        try:
            tweet = await fetch(session, screen_name, tweet_id, label)
        except Exception as e:
            logging.info(f"[{label or tweet_id}] {name} error: {e}")
            tweet = None
        if tweet:
            if name != "fxtwitter":
                logging.info(f"[{label or tweet_id}] tweet data via {name} (fallback).")
            return tweet, name
    return None, None
