# ---------------------------------------------------------------------------
# ■ Reddit RSS Feed Monitor — V1 (plain content + auto-embed via a mirror)
# ---------------------------------------------------------------------------
# Monitors subreddits via RSS and posts the configured mirror link (redditez
# by default; embeddit / vxreddit via the REDDIT_MIRROR env var — verified
# live 2026-09-12), letting Discord auto-unfurl it into a rich embed
# (same idea as fxtwitter for X).
#
# No API key required. 100% free.
#
# Features:
#   • Read Post button -> the ORIGINAL reddit.com permalink
#   • YouTube detection -> bare YouTube URL on its own line (auto-embeds a
#     playable video in Discord) + an optional "YouTube" link button
#   • Mod-queue safe -> approved posts resurface via the RSS "updated" stamp,
#     with a 48-hour catch window (see MAX_AGE_SECONDS below)
#   • ROUND 8 (2026-09-12): dead redlib.perennialte.ch removed; fresh Redlib
#     fallback list; 429 retry with backoff + staggered fetch starts.
#   • ROUND 9 (2026-09-13):
#       - PRIMARY SOURCE IS NOW ONE COMBINED FEED:
#           https://www.reddit.com/r/sub1+sub2+.../new.rss?limit=100
#         verified live 2026-09-13 — 1 HTTP request covers ALL subreddits,
#         which fits inside Reddit's ~1 request/minute anonymous limit per
#         datacenter IP (introduced June 2026). Entries are routed to
#         per-channel webhooks via their permalinks.
#       - If the combined feed fails, the script falls back to the old
#         per-subreddit fetches (instance rotation + two 429 retries: 6s and
#         45s).
#       - REDDIT_FEED_TOKEN (repo secret): your personal feed token from
#         old.reddit.com -> Preferences -> Feeds. Appended as ?feed=<token>
#         and moves requests to the logged-in rate tier — the recommended
#         setup.
#       - All public Redlib/Eddrit mirrors live-probed 2026-09-13: every
#         official registry instance is behind Anubis/Cloudflare/
#         gammaspectra bot challenges (or dead). A few are kept ONLY as
#         fallback lottery tickets.
#
# To use rich Components V2 version (V2 requires an EmbedEZ API key)
# change the workflow run line to: python "testing area/reddit_main_v2_embedez.py" or
# "testing area/reddit_main_v3.py" (Does not require API)
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

# ---------------------------------------------------------------------------
# ■ SUBREDDITS TO TRACK
# ---------------------------------------------------------------------------
SUBREDDITS_STR = os.getenv("SUBREDDITS", "Zenlesszonezeroleaks_,Genshin_Impact_Leaks,HonkaiStarRail_leaks,WutheringWavesLeaks,HonkaiNexusAnimaLeaks,AnantaLeaks")
SUBREDDITS = [s.strip() for s in SUBREDDITS_STR.split(",") if s.strip()]

# Optional fallback webhook used only if a subreddit has no dedicated secret
DEFAULT_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# ---------------------------------------------------------------------------
# ■ REDDIT FEED TOKEN (recommended)
# ---------------------------------------------------------------------------
# Since June 2026 Reddit rate-limits ANONYMOUS RSS from datacenter IPs to
# roughly 1 request/minute per IP. The combined feed (one request per run)
# already fits that, but your personal feed token makes it bulletproof.
# Get it: old.reddit.com -> Preferences -> Feeds -> copy the "?feed=..."
# value from any feed link (the token itself, without "?feed=").
# Store it as the REDDIT_FEED_TOKEN secret.
REDDIT_FEED_TOKEN = os.getenv("REDDIT_FEED_TOKEN", "").strip()

CACHE_FILE = "posted_reddit.json"
MAX_CACHE_SIZE = 500

# How old a post's latest activity may be before we skip it.
# Kept WIDE (48h) on purpose: subreddits with moderator approval queues can
# surface a post a day or more after submission. When a queued post gets
# approved, Reddit bumps its RSS "updated" timestamp — we use that (see
# activity_ts below), so newly-approved posts are still caught and never
# left out. If you track very slow subreddits, you can widen this further.
MAX_AGE_SECONDS = 48 * 3600

# ---------------------------------------------------------------------------
# ■ RSS SOURCES
#
# PRIMARY (round 9): ONE COMBINED feed request for ALL subreddits
# (see _combined_feed_url below) — always www.reddit.com.
#
# FALLBACK (only if the combined feed fails), tried in order:
#   • www.reddit.com  -> native Reddit RSS (+ ?feed= token when set).
#   • old.reddit.com  -> often returns an HTML "Welcome to Reddit" interstitial
#                        to datacenter IPs (0 entries) — fallback only.
#   • Redlib mirrors  -> ALL official registry instances live-probed
#                        2026-09-13: behind Anubis/Cloudflare/gammaspectra
#                        bot challenges, or dead (410/404/SSL). Kept only in
#                        case a challenge ever relaxes. Refresh candidates at
#                        github.com/redlib-org/redlib-instances.
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

# 429 (rate limit) retry waits: first retry after 6s (short bursts, verified
# effective on GitHub runners), second after 45s (rides out the ~1/minute
# anonymous window refill — verified live 2026-09-13).
RATE_LIMIT_RETRY_DELAY_1 = 6
RATE_LIMIT_RETRY_DELAY_2 = 45
# Start each subreddit's feed fetch this many seconds after the previous one
# (fallback mode only), so six subs don't all hit www.reddit.com in the same
# second (the burst that triggers 429s).
FEED_FETCH_STAGGER = 1.2
# How many entries the combined feed returns (Reddit default 25, max 100 —
# 100 verified working; covers quiet subreddits in busy runs).
COMBINED_FEED_LIMIT = 100

# ---------------------------------------------------------------------------
# ■ EMBED MIRROR — which service re-hosts the post link so Discord unfurls it.
# Switch by setting the REDDIT_MIRROR env var / repo VARIABLE (not secret):
#   • "redditez.com"          (default — EmbedEZ's mirror)
#   • "embeddit.deltandy.me"  (Embeddit by @DeltAndy123)
#   • "vxreddit.com"          (vxReddit by @dylanpdx)
# All three were verified live (2026-09-12): they accept the SAME
# /r/<sub>/comments/... path and serve og: meta to Discordbot. Scheme and
# any "www."/trailing slash are normalized away — just put the host.
# ---------------------------------------------------------------------------
_raw_mirror = os.getenv("REDDIT_MIRROR", "redditez.com").strip().lower()
_raw_mirror = re.sub(r"^https?://", "", _raw_mirror).strip("/")  # tolerate full URLs
MIRROR_HOST = _raw_mirror if _raw_mirror else "redditez.com"
# Discord button "style" 5 = Link button (MUST use "url", no "custom_id")
# Unicode emoji: {"name": "🔔"} | Custom emoji: {"id": "123", "name": "x", "animated": False}
# Note: click-to-rotate embed mirrors are NOT possible with plain webhooks
# (that needs a 24/7 bot answering Discord interactions), so the buttons are
# fixed link buttons only — Reddit-side mirrors were removed by request.
# ---------------------------------------------------------------------------
STATIC_BUTTONS = [
    {"label": "Citlali News", "url": "https://discord.gg/HyrVP9wRXu", "emoji": {"id": "1439878792653832253", "name": "starward11", "animated": True}},
    {"label": "Donate", "url": "https://ko-fi.com/jieunlatte", "emoji": {"id": "1509026327548657914", "name": "starwardfans", "animated": True}},
]

# Matches watch / shorts / youtu.be links inside the RSS entry HTML
YOUTUBE_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^\s\"'<>)\]]+|shorts/[^\s\"'<>)\]]+)"
    r"|youtu\.be/[^\s\"'<>)\]]+)",
    re.IGNORECASE,
)


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
    """Extracts the /r/subreddit/comments/.../ path regardless of source domain."""
    match = re.search(r"(/r/[^\s?]+)", link)
    return match.group(1).rstrip("/") + "/" if match else None


def extract_subreddit(path: str) -> str | None:
    """/r/<name>/comments/... -> <name>  (routes combined-feed entries)."""
    match = re.match(r"/r/([^/]+)/", path or "")
    return match.group(1) if match else None


def extract_post_id(path: str) -> str | None:
    match = re.search(r"/comments/([a-zA-Z0-9]+)/", path)
    return match.group(1) if match else None


def extract_youtube_url(entry) -> str | None:
    """Finds a YouTube link (watch / shorts / youtu.be) in the RSS entry HTML."""
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


def _subreddit_by_name(name: str) -> str | None:
    """Case-insensitive match of a permalink name against our SUBREDDITS."""
    for sub in SUBREDDITS:
        if sub.lower() == (name or "").lower():
            return sub
    return None


async def _fetch_feed(session: aiohttp.ClientSession, feed_url: str, label: str):
    """
    GETs one feed URL with two 429 retries (6s, then 45s). A response is only
    accepted if it is HTTP 200, contains real feedparser entries, and those
    entries carry reddit /comments/ permalinks (rejects HTML
    interstitial/bot-check/"loading takes a moment" pages that return 200
    with junk). Every rejection is logged for the Actions log.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
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


def collect_new_posts(entries, posted: set, is_first_run: bool, now: float,
                      known_subreddit: str | None) -> dict:
    """
    Filters feed entries (dedup + mod-queue-safe age check) and returns
    {subreddit: [post dict, ...]}. If known_subreddit is None the subreddit
    is read from each entry's permalink (combined-feed mode).
    """
    found = {}
    for entry in entries:
        raw_link = str(getattr(entry, "link", ""))
        path = normalize_reddit_path(raw_link)
        if not path:
            continue
        if known_subreddit:
            sub = known_subreddit
        else:
            sub = _subreddit_by_name(extract_subreddit(path))
            if not sub:
                continue
        post_id = extract_post_id(path)
        if not post_id:
            continue
        unique_key = f"{sub}_{post_id}"
        if unique_key in posted:
            continue

        # --- mod-queue safe age check ---------------------------------
        # Pending posts can be APPROVED hours/days later; Reddit bumps
        # the entry's "updated" stamp on approval, so we age-check the
        # most RECENT of (published, updated) instead of publish time.
        published_parsed = entry.get("published_parsed")
        updated_parsed = entry.get("updated_parsed")
        published_ts = time.mktime(published_parsed) if published_parsed else now
        updated_ts = time.mktime(updated_parsed) if updated_parsed else published_ts
        activity_ts = max(published_ts, updated_ts)
        if not is_first_run and (now - activity_ts > MAX_AGE_SECONDS):
            continue
        # --------------------------------------------------------------

        found.setdefault(sub, []).append({
            "path": path,
            "unique_key": unique_key,
            "published_ts": published_ts,
            "youtube_url": extract_youtube_url(entry),
        })
    return found


def build_components(reddit_url: str, youtube_url: str | None = None) -> list:
    """Read Post (original reddit URL) [+ YouTube if detected] + static buttons (max 5/row)."""
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
    return [{"type": 1, "components": buttons[:5]}]


async def send_discord_webhook(session: aiohttp.ClientSession, webhook_url: str, content: str,
                               reddit_url: str, youtube_url: str | None = None) -> bool:
    payload = {
        "content": content,
        "components": build_components(reddit_url, youtube_url),
    }
    # Discord requires this query param or components are silently dropped
    request_url = f"{webhook_url}?with_components=true"
    try:
        async with session.post(request_url, json=payload,
                                timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status in (200, 204):
                logging.info("Successfully posted to Discord Webhook.")
                return True
            body = await response.text()
            logging.error(f"Discord Webhook returned status {response.status}: {body}")
            return False
    except Exception as e:
        logging.error(f"Error posting to Discord Webhook: {e}")
        return False


async def main():
    if not SUBREDDITS:
        logging.error("No subreddits configured in SUBREDDITS environment variable.")
        return
    if not REDDIT_FEED_TOKEN:
        logging.warning("REDDIT_FEED_TOKEN not set — running anonymously. The combined feed "
                        "(1 request/run) usually fits the ~1 req/min limit, but add your feed "
                        "token (old.reddit.com -> Preferences -> Feeds) as REDDIT_FEED_TOKEN "
                        "to be bulletproof.")

    posted = load_posted()
    is_first_run = len(posted) == 0
    now = time.time()
    found = {}

    async with aiohttp.ClientSession() as session:
        # ---- PRIMARY: one combined request for all subreddits -----------
        combined = await fetch_combined_feed(session)
        if combined and combined.entries:
            entries = [combined.entries[0]] if is_first_run else combined.entries
            found = collect_new_posts(entries, posted, is_first_run, now,
                                      known_subreddit=None)
        else:
            # ---- FALLBACK: per-subreddit feeds (staggered) --------------
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
                f = collect_new_posts(entries, posted, is_first_run, now,
                                      known_subreddit=subreddit)
                if f.get(subreddit):
                    found.setdefault(subreddit, []).extend(f[subreddit])
        # ------------------------------------------------------------------

        subreddit_posts = {sub: found.get(sub, []) for sub in SUBREDDITS}
        total_found = sum(len(v) for v in subreddit_posts.values())
        if total_found == 0:
            logging.info("No new Reddit posts to post.")
            save_posted(posted)
            return

        logging.info(f"Found {total_found} new Reddit posts. Posting per-channel...")

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
                reddit_url = f"https://www.reddit.com{path}"   # original permalink
                mirror_url = f"https://{MIRROR_HOST}{path}"    # rich auto-embed mirror
                youtube_url = post["youtube_url"]

                # Header + masked mirror link (auto-embeds) + bare YouTube URL
                # on its own line so Discord also unfurls a playable YT player.
                lines = [f"🔔 **New post in r/{subreddit}**", mirror_url]
                if youtube_url:
                    lines.append(youtube_url)
                message = "\n".join(lines)

                success = await send_discord_webhook(
                    session, webhook_url, message, reddit_url, youtube_url
                )
                if success:
                    posted.add(post["unique_key"])
                    await asyncio.sleep(1.5)

    save_posted(posted)
    logging.info("Reddit RSS Monitor execution finished successfully.")


if __name__ == "__main__":
    asyncio.run(main())
