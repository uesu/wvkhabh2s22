# ---------------------------------------------------------------------------
# ■ Reddit RSS Feed Monitor — V2 (Components V2 rich card via EmbedEZ API)
# ---------------------------------------------------------------------------
# Same monitoring as reddit_main.py (V1), but each post is rendered as a
# Discord Components V2 card (type 17 container, media gallery, stats,
# timestamp, buttons nested inside) using data from the EmbedEZ API.
#
# ■ EmbedEZ API (documented two-step flow, per https://embedez.com/docs):
#     1. GET /api/v1/providers/search?url=<post url>          -> returns a key
#     2. GET /api/v1/providers/preview?search_key=<key>       -> post data
#        (send header  Authorization: Bearer <EMBEDEZ_API_KEY> for full data)
#
# ■ IMPORTANT CONCERNS (please read):
#   1. API KEY / CREDITS — the EmbedEZ API key is NOT fully free. The dashboard
#      shows a credit balance (e.g. 100 credits); heavier use requires payment.
#      Every new post costs 2 API calls (search + preview). If you don't want
#      to pay, use reddit_main.py (V1) instead — no key needed.
#   2. WITHOUT a valid key, the preview endpoint still returns PUBLIC data
#      (author + title + thumbnail). This script detects that and posts a
#      limited card instead of skipping the post.
#   3. EmbedEZ officially warns that "large changes will be made to the API in
#      the future, and these might break the current API". Fields are parsed
#      defensively (media url OR media.source.url; HTML stripped everywhere),
#      but if the API changes shape again, check the Actions logs.
#   4. The old unofficial "/providers/combined?q=" endpoint from early docs is
#      no longer documented — this script uses only the documented endpoints.
#
# ■ ROUND 8 (2026-09-12): same RSS source fixes as V1 — dead
#   redlib.perennialte.ch removed (shut down 2026-08-31, answers HTTP 410);
#   fresh Redlib fallback list (official redlib-instances, checked 2026-09-12);
#   429 retry with backoff + staggered fetch starts.
#
# ■ ROUND 9 (2026-09-13):
#   • PRIMARY SOURCE IS NOW ONE COMBINED FEED:
#       https://www.reddit.com/r/sub1+sub2+.../new.rss?limit=100
#     verified live 2026-09-13 — 1 HTTP request covers ALL subreddits, which
#     fits inside Reddit's ~1 request/minute anonymous limit per datacenter IP
#     (introduced June 2026). Entries are routed to per-channel webhooks via
#     their permalinks (dedup keys unchanged, existing cache still valid).
#   • If the combined feed ever fails, the script falls back to the old
#     per-subreddit fetches (instance rotation + two 429 retries: 6s and 45s).
#   • REDDIT_FEED_TOKEN (repo secret): your personal feed token from
#     old.reddit.com -> Preferences -> Feeds. Appended as ?feed=<token> and
#     moves requests to the logged-in rate tier — the recommended setup.
#   • All public Redlib/Eddrit mirrors were live-probed 2026-09-13: every
#     official registry instance is behind Anubis/Cloudflare/gammaspectra bot
#     challenges (or dead). A few are kept ONLY as fallback lottery tickets.
# ---------------------------------------------------------------------------
import os
import re
import json
import time
import asyncio
import logging
import aiohttp
import feedparser
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
load_dotenv()

SUBREDDITS_STR = os.getenv("SUBREDDITS", "Zenlesszonezeroleaks_,Genshin_Impact_Leaks,HonkaiStarRail_leaks,WutheringWavesLeaks,HonkaiNexusAnimaLeaks,AnantaLeaks")
SUBREDDITS = [s.strip() for s in SUBREDDITS_STR.split(",") if s.strip()]

DEFAULT_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
EMBEDEZ_API_KEY = os.getenv("EMBEDEZ_API_KEY")

# Personal Reddit feed token (old.reddit.com -> Preferences -> Feeds).
# Recommended: anonymous Reddit RSS from datacenter IPs is limited to
# ~1 request/minute per IP since June 2026. Stored as REDDIT_FEED_TOKEN.
REDDIT_FEED_TOKEN = os.getenv("REDDIT_FEED_TOKEN", "").strip()

CACHE_FILE = "posted_reddit.json"
MAX_CACHE_SIZE = 500

# Wide window (48h) so posts approved from a subreddit's moderator queue a day
# or more later are still caught (approval bumps the RSS "updated" stamp).
MAX_AGE_SECONDS = 48 * 3600

# ---------------------------------------------------------------------------
# ■ RSS SOURCES
#
#   The COMBINED feed (see _combined_feed_url) is the PRIMARY source and
#   always uses www.reddit.com — one request covers all subreddits.
#
#   These are only used in FALLBACK mode (if the combined feed fails):
#
#   • www.reddit.com   -> native Reddit RSS (+ ?feed= token when set)
#   • old.reddit.com   -> usually an HTML "Welcome to Reddit" interstitial to
#                         datacenter IPs (0 entries) — fallback only
#   • Redlib mirrors   -> ALL official registry instances live-probed
#                         2026-09-13: behind Anubis/Cloudflare/gammaspectra
#                         bot challenges or dead — unusable for headless RSS.
#                         Kept only in case a challenge ever relaxes.
#                         Refresh candidates at github.com/redlib-org/redlib-instances
# The script validates that the response actually CONTAINS reddit permalinks
# before accepting it, and logs why each source was skipped.
# ---------------------------------------------------------------------------
REDDIT_RSS_INSTANCES = [
    "https://www.reddit.com",
    "https://old.reddit.com",
    "https://safereddit.com",           # 2026-09-13: Anubis bot check (fallback lottery)
    "https://red.artemislena.eu",       # 2026-09-13: Anubis bot check (fallback lottery)
    "https://redlib.privacyredirect.com",  # 2026-09-13: Anubis bot check (fallback lottery)
]

# 429 (rate limit) retry waits: first retry after 6s (short bursts), second
# after 45s (rides out the ~1/minute anonymous window refill — verified).
RATE_LIMIT_RETRY_DELAY_1 = 6
RATE_LIMIT_RETRY_DELAY_2 = 45
# Stagger between per-sub fetch starts in fallback mode (avoids same-second bursts).
FEED_FETCH_STAGGER = 1.2
# How many entries the combined feed returns (default 25, max 100 — verified).
COMBINED_FEED_LIMIT = 100

EMBEDEZ_SEARCH_URL = "https://embedez.com/api/v1/providers/search"
EMBEDEZ_PREVIEW_URL = "https://embedez.com/api/v1/providers/preview"

IS_COMPONENTS_V2 = 1 << 15

STATIC_BUTTONS = [
    {"label": "Citlali News", "url": "https://discord.gg/HyrVP9wRXu", "emoji": {"id": "1439878792653832253", "name": "starward11", "animated": True}},
    {"label": "Donate", "url": "https://ko-fi.com/jieunlatte", "emoji": {"id": "1509026327548657914", "name": "starwardfans", "animated": True}},
]

YOUTUBE_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^\s\"'<>)\]]+|shorts/[^\s\"'<>)\]]+)"
    r"|youtu\.be/[^\s\"'<>)\]]+)",
    re.IGNORECASE,
)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}


def get_webhook_for_subreddit(subreddit: str) -> str | None:
    sanitized = re.sub(r"[^A-Za-z0-9]", "_", subreddit).upper()
    env_key = f"WEBHOOK_REDDIT_{sanitized}"
    webhook = os.getenv(env_key)
    if webhook:
        return webhook
    if DEFAULT_WEBHOOK_URL:
        logging.warning(f"No dedicated webhook for r/{subreddit} (expected {env_key}). Using fallback.")
        return DEFAULT_WEBHOOK_URL
    return None


def load_posted() -> set:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"Error reading cache: {e}")
    return set()


def save_posted(posted: set):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(list(posted)[-MAX_CACHE_SIZE:], f, indent=2)
    except Exception as e:
        logging.error(f"Error saving cache: {e}")


def normalize_reddit_path(link: str) -> str | None:
    match = re.search(r"(/r/[^\s?]+)", link)
    return match.group(1).rstrip("/") + "/" if match else None


def extract_subreddit(path: str) -> str | None:
    """/r/<name>/comments/... -> <name>  (used to route combined-feed entries)."""
    match = re.match(r"/r/([^/]+)/", path or "")
    return match.group(1) if match else None


def extract_post_id(path: str) -> str | None:
    match = re.search(r"/comments/([a-zA-Z0-9]+)/", path)
    return match.group(1) if match else None


def _subreddit_by_name(name: str) -> str | None:
    """Case-insensitive match of a name from a permalink against our config."""
    for sub in SUBREDDITS:
        if sub.lower() == (name or "").lower():
            return sub
    return None


def extract_youtube_url(entry) -> str | None:
    html_parts = []
    if entry.get("content"):
        html_parts.extend(c.get("value", "") for c in entry.content)
    if entry.get("summary"):
        html_parts.append(entry.summary)
    for html in html_parts:
        match = YOUTUBE_RE.search(html or "")
        if match:
            return match.group(0)
    return None


def strip_html(value: str | None) -> str:
    """
    Removes tags (<a>,
...) from EmbedEZ's HTML-ish strings.
    Applied to EVERY text field (content.title/description/text AND
    preview.title/description) because authorized responses contain HTML too.
    """
    if not value:
        return ""
    value = re.sub(r"(?i)<br\s*/?>", " ", value)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s{2,}", " ", value).strip()


def resolve_post_timestamp(content: dict, fallback_ts: float) -> int:
    """Best-effort post time for the Discord <t:...> timestamp."""
    stamp = (content or {}).get("postedDate") or (content or {}).get("posted_date")
    if isinstance(stamp, (int, float)) and stamp > 0:
        # postedDate is documented as milliseconds in early EmbedEZ docs
        return int(stamp / 1000) if stamp > 10**12 else int(stamp)
    return int(fallback_ts)


async def _fetch_feed(session: aiohttp.ClientSession, feed_url: str, label: str):
    """
    GETs one feed URL with two 429 retries (6s, then 45s).
    A response is only accepted if it is HTTP 200, contains real feedparser
    entries, and those entries carry reddit /comments/ permalinks (this
    rejects HTML interstitial/bot-check/"loading takes a moment" pages that
    return 200 with junk). Every rejection is logged for the Actions log.
    """
    headers = {
        **BROWSER_HEADERS,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    retry_delays = [RATE_LIMIT_RETRY_DELAY_1, RATE_LIMIT_RETRY_DELAY_2]
    attempt = 0
    while True:
        attempt += 1
        try:
            async with session.get(feed_url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 429:
                    if attempt - 1 < len(retry_delays):
                        delay = retry_delays[attempt - 1]
                        logging.info(f"[{label}] HTTP 429 (rate limited) — retry {attempt} in {delay}s.")
                        await asyncio.sleep(delay)
                        continue
                    logging.info(f"[{label}] still HTTP 429 after {attempt - 1} retries — giving up.")
                    return None
                if response.status != 200:
                    logging.info(f"[{label}] HTTP {response.status} — rejected.")
                    return None
                content = await response.text()
                feed = await asyncio.to_thread(feedparser.parse, content)
                if not feed.entries:
                    logging.info(f"[{label}] no RSS entries (bot-check/interstitial/HTML page?) — rejected.")
                    return None
                if not any("/comments/" in str(getattr(e, "link", "")) for e in feed.entries[:5]):
                    logging.info(f"[{label}] entries contain no reddit /comments/ links — rejected.")
                    return None
                return feed
        except Exception as e:
            logging.info(f"[{label}] error: {e}")
            return None


def _combined_feed_url() -> str:
    """ONE feed for all tracked subreddits: /r/a+b+c/new.rss?limit=100 (+ token)."""
    url = "https://www.reddit.com/r/" + "+".join(SUBREDDITS) + f"/new.rss?limit={COMBINED_FEED_LIMIT}"
    if REDDIT_FEED_TOKEN:
        url += f"&feed={REDDIT_FEED_TOKEN}"
    return url


async def fetch_combined_feed(session: aiohttp.ClientSession):
    logging.info(f"Fetching combined feed for {len(SUBREDDITS)} subreddits in 1 request...")
    feed = await _fetch_feed(session, _combined_feed_url(), "combined")
    if feed:
        covered = set()
        for e in feed.entries:
            path = normalize_reddit_path(str(getattr(e, "link", "")))
            sub = _subreddit_by_name(extract_subreddit(path)) if path else None
            if sub:
                covered.add(sub)
        logging.info(f"Combined feed OK: {len(feed.entries)} entries covering {len(covered)} subreddit(s).")
    return feed


async def fetch_working_reddit_feed(session: aiohttp.ClientSession, subreddit: str):
    """Fallback mode: tries each RSS source in order for one subreddit."""
    for instance in REDDIT_RSS_INSTANCES:
        feed_url = f"{instance}/r/{subreddit}/new/.rss"
        # The personal feed token only applies to Reddit's own hosts.
        if REDDIT_FEED_TOKEN and "reddit.com" in instance:
            feed_url += f"?feed={REDDIT_FEED_TOKEN}"
        feed = await _fetch_feed(session, feed_url, instance)
        if feed:
            logging.info(f"Successfully fetched r/{subreddit} from {instance}")
            return feed
    logging.warning(f"Could not fetch valid RSS feed for r/{subreddit} from any instance.")
    return None


async def fetch_embedez_data(session: aiohttp.ClientSession, reddit_url: str) -> dict | None:
    """
    Documented two-step EmbedEZ flow:
      step 1: /providers/search?url=...    -> data.key
      step 2: /providers/preview?search_key=... (+ Authorization: Bearer <key>)
    Returns the preview `data` dict, or None on hard failure.
    Note: without a valid API key the response still succeeds but only contains
    public fields (type / user / preview) — no `content`/`media`/`statistics`.
    """
    headers = dict(BROWSER_HEADERS)
    if EMBEDEZ_API_KEY:
        headers["Authorization"] = f"Bearer {EMBEDEZ_API_KEY}"
    try:
        # — step 1: search for a key —
        async with session.get(EMBEDEZ_SEARCH_URL, headers=headers,
                               params={"url": reddit_url},
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logging.error(f"EmbedEZ search returned {resp.status}: {await resp.text()}")
                return None
            search = await resp.json()
        if not search.get("success"):
            logging.error(f"EmbedEZ search error: {search.get('message')}")
            return None
        key = (search.get("data") or {}).get("key")
        if not key:
            logging.error("EmbedEZ search returned no key.")
            return None

        # — step 2: preview with the key —
        async with session.get(EMBEDEZ_PREVIEW_URL, headers=headers,
                               params={"search_key": key},
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logging.error(f"EmbedEZ preview returned {resp.status}: {await resp.text()}")
                return None
            preview = await resp.json()
        if not preview.get("success"):
            logging.error(f"EmbedEZ preview error: {preview.get('message')}")
            return None
        return preview.get("data") or {}
    except Exception as e:
        logging.error(f"Error calling EmbedEZ API for {reddit_url}: {e}")
        return None


def extract_media_urls(data: dict) -> list:
    """
    Pulls media URLs defensively from the EmbedEZ payload:
      • full (authorized) data: content.media[].url            (current docs)
      • legacy combined shape:  content.media[].source.url     (early docs)
      • public (no-key) data:   preview.img.url / preview.img (str)
    """
    urls = []
    content = data.get("content") or {}
    for m in (content.get("media") or []):
        if not isinstance(m, dict):
            continue
        u = m.get("url")
        if not u and isinstance(m.get("source"), dict):
            u = m["source"].get("url")
        if isinstance(u, dict):  # some payloads nest {url: {url: ...}}
            u = u.get("url")
        if u:
            urls.append(u)
    if urls:
        return urls
    img = (data.get("preview") or {}).get("img")
    if isinstance(img, dict):
        img = img.get("url")
    if isinstance(img, str) and img:
        urls.append(img)
    return urls


def build_nested_action_row(reddit_url: str, youtube_url: str | None = None) -> dict:
    buttons = [
        {"type": 2, "style": 5, "label": "Read Post", "url": reddit_url, "emoji": {"id": "1472388018689282261", "name": "starwardhmm", "animated": True}},
    ]
    if youtube_url:
        buttons.append({"type": 2, "style": 5, "label": "YouTube", "url": youtube_url,
                        "emoji": {"id": "1483083423290490891", "name": "starwardspark3", "animated": True}})
    for btn in STATIC_BUTTONS:
        b = {"type": 2, "style": 5, "label": btn["label"], "url": btn["url"]}
        if btn.get("emoji"):
            b["emoji"] = btn["emoji"]
        buttons.append(b)
    return {"type": 1, "components": buttons[:5]}


def build_v2_payload(subreddit: str, data: dict, reddit_url: str,
                     youtube_url: str | None = None, posted_ts: int | None = None) -> dict:
    content = data.get("content") or {}
    user = data.get("user") or {}
    preview = data.get("preview") or {}
    limited = not bool(content)  # True when no (valid) API key -> public data only

    # — text fields (HTML stripped everywhere — authorized content has HTML too) —
    title = strip_html(content.get("title")) or strip_html(preview.get("title")) \
        or f"New post in r/{subreddit}"
    description = strip_html(content.get("description") or content.get("text")) \
        or strip_html(preview.get("description"))
    author_name = user.get("displayName") or user.get("name") or f"r/{subreddit}"

    inner_components = [
        {"type": 10, "content": f"### [{title}]({reddit_url})\n*by {author_name} in r/{subreddit}*"},
    ]
    if description:
        inner_components.append({"type": 10, "content": description})

    # — media gallery —
    media_urls = extract_media_urls(data)
    if media_urls:
        items = [{"media": {"url": u}} for u in media_urls[:10]]
        inner_components.append({"type": 14, "divider": True, "spacing": 1})
        inner_components.append({"type": 12, "items": items})

    # — stats line (full data) or limited-preview note + Discord timestamp —
    ts_suffix = f"   •   🕐 <t:{posted_ts}:f>" if posted_ts else ""
    if limited:
        inner_components.append({
            "type": 10,
            "content": f"-# ℹ️ Limited preview — set a valid EMBEDEZ_API_KEY for full stats & media{ts_suffix}",
        })
    else:
        stats = content.get("statistics") or {}
        comments = stats.get("comments", 0)
        shares = stats.get("shares", 0)
        likes = stats.get("likes", 0)
        views = stats.get("views", "N/A")
        inner_components.append({
            "type": 10,
            "content": f"-# 💬 {comments} 🔁 {shares} ❤️ {likes} 👁️ {views}{ts_suffix}",
        })

    # — YouTube callout (bare URL stays clickable; auto-embed only works in V1) —
    if youtube_url:
        inner_components.append({
            "type": 10,
            "content": f"**YouTube:** {youtube_url}",
        })

    inner_components.append({"type": 14, "divider": True, "spacing": 1})
    inner_components.append(build_nested_action_row(reddit_url, youtube_url))

    container = {
        "type": 17,
        "accent_color": 16729344,  # 0xFF4500 — Reddit orange
        "components": inner_components,
    }
    return {"flags": IS_COMPONENTS_V2, "components": [container]}


async def main():
    if not SUBREDDITS:
        logging.error("No subreddits configured in SUBREDDITS environment variable.")
        return
    if not EMBEDEZ_API_KEY:
        logging.warning("EMBEDEZ_API_KEY is missing — cards will use limited public preview data. "
                        "For full media/stats, add the EMBEDEZ_API_KEY secret (watch your credit balance), "
                        "or switch the workflow to testing area/reddit_main.py (V1, free, no key).")
    if not REDDIT_FEED_TOKEN:
        logging.warning("REDDIT_FEED_TOKEN not set — running anonymously. The combined feed "
                        "(1 request/run) usually fits the ~1 req/min limit, but add your feed "
                        "token (old.reddit.com -> Preferences -> Feeds) to be bulletproof.")

    posted = load_posted()
    is_first_run = len(posted) == 0
    now = time.time()
    subreddit_posts = {sub: [] for sub in SUBREDDITS}

    async with aiohttp.ClientSession() as session:
        # ---- PRIMARY: one combined request for all subreddits -------------
        combined = await fetch_combined_feed(session)
        if combined and combined.entries:
            entries = [combined.entries[0]] if is_first_run else combined.entries
            for entry in entries:
                raw_link = str(getattr(entry, "link", ""))
                path = normalize_reddit_path(raw_link)
                if not path:
                    continue
                sub = _subreddit_by_name(extract_subreddit(path))
                if not sub:
                    continue
                post_id = extract_post_id(path)
                if not post_id:
                    continue
                unique_key = f"{sub}_{post_id}"
                if unique_key in posted:
                    continue

                # --- mod-queue safe age check (approval bumps 'updated') ---
                published_parsed = entry.get("published_parsed")
                updated_parsed = entry.get("updated_parsed")
                published_ts = time.mktime(published_parsed) if published_parsed else now
                updated_ts = time.mktime(updated_parsed) if updated_parsed else published_ts
                activity_ts = max(published_ts, updated_ts)
                if not is_first_run and (now - activity_ts > MAX_AGE_SECONDS):
                    continue
                # ------------------------------------------------------------

                subreddit_posts[sub].append({
                    "path": path,
                    "unique_key": unique_key,
                    "published_ts": published_ts,
                    "activity_ts": activity_ts,
                    "youtube_url": extract_youtube_url(entry),
                })
        else:
            # ---- FALLBACK: per-subreddit feeds (staggered) ----------------
            logging.info("Combined feed unavailable — falling back to per-subreddit feeds...")

            async def _staggered_fetch(sub: str, index: int):
                await asyncio.sleep(index * FEED_FETCH_STAGGER)
                return await fetch_working_reddit_feed(session, sub)

            tasks = [_staggered_fetch(sub, i) for i, sub in enumerate(SUBREDDITS)]
            feeds = await asyncio.gather(*tasks)

            for subreddit, feed in zip(SUBREDDITS, feeds):
                if not feed or not feed.entries:
                    continue
                entries = [feed.entries[0]] if is_first_run else feed.entries
                for entry in entries:
                    raw_link = str(getattr(entry, "link", ""))
                    path = normalize_reddit_path(raw_link)
                    if not path:
                        continue
                    post_id = extract_post_id(path)
                    if not post_id:
                        continue
                    unique_key = f"{subreddit}_{post_id}"
                    if unique_key in posted:
                        continue

                    # --- mod-queue safe age check (approval bumps 'updated') ---
                    published_parsed = entry.get("published_parsed")
                    updated_parsed = entry.get("updated_parsed")
                    published_ts = time.mktime(published_parsed) if published_parsed else now
                    updated_ts = time.mktime(updated_parsed) if updated_parsed else published_ts
                    activity_ts = max(published_ts, updated_ts)
                    if not is_first_run and (now - activity_ts > MAX_AGE_SECONDS):
                        continue
                    # ------------------------------------------------------------

                    subreddit_posts[subreddit].append({
                        "path": path,
                        "unique_key": unique_key,
                        "published_ts": published_ts,
                        "activity_ts": activity_ts,
                        "youtube_url": extract_youtube_url(entry),
                    })
        # --------------------------------------------------------------------

        total_found = sum(len(v) for v in subreddit_posts.values())
        if total_found == 0:
            logging.info("No new Reddit posts to post.")
            save_posted(posted)
            return

        logging.info(f"Found {total_found} new Reddit posts. Fetching EmbedEZ cards...")

        for subreddit, posts in subreddit_posts.items():
            if not posts:
                continue
            webhook_url = get_webhook_for_subreddit(subreddit)
            if not webhook_url:
                logging.error(f"No webhook configured for r/{subreddit}. Skipping.")
                continue
            posts.sort(key=lambda p: p["published_ts"])
            for post in posts:
                path = post["path"]
                reddit_url = f"https://www.reddit.com{path}"
                youtube_url = post["youtube_url"]

                embedez_data = await fetch_embedez_data(session, reddit_url)
                if embedez_data is None:
                    logging.warning(f"Skipping {post['unique_key']}: could not fetch EmbedEZ data.")
                    continue

                posted_ts = resolve_post_timestamp(embedez_data.get("content") or {}, post["activity_ts"])
                payload = build_v2_payload(subreddit, embedez_data, reddit_url,
                                           youtube_url=youtube_url, posted_ts=posted_ts)
                target_url = f"{webhook_url}?with_components=true"
                async with session.post(target_url, json=payload) as resp:
                    if resp.status in (200, 204):
                        posted.add(post["unique_key"])
                        logging.info(f"Reddit V2 Posted: {post['unique_key']}")
                        await asyncio.sleep(1.5)
                    else:
                        body = await resp.text()
                        logging.error(f"Discord error {resp.status} for {post['unique_key']}: {body}")

    save_posted(posted)
    logging.info("Reddit V2 Monitor execution finished.")


if __name__ == "__main__":
    asyncio.run(main())
