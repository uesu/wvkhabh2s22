# ---------------------------------------------------------------------------
# ■ Reddit RSS Feed Monitor — V3 (Components V2 rich card — NATIVE media, NO EmbedEZ)
# ---------------------------------------------------------------------------
# Same monitoring as V1/V2 (combined feed, feed token, 429 retries, mod-queue
# safe 48h window, per-channel webhooks, dedup cache, auto-commit), but the
# card media comes from REDDIT'S OWN URLs — no EmbedEZ API, no mirror links,
# no credits. Round 12 (2026-09-15).
#
# ■ DATA PATHS (every fact below was live-verified 2026-09-15 from a
#   datacenter IP with the Discordbot/2.0 UA):
#
#   FULL MODE — activates automatically when post JSON is reachable:
#     a) OAuth app: REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET secrets (create at
#        reddit.com/prefs/apps, type "script", redirect http://localhost).
#        2026 note: new API access may require Reddit's approval form
#        (Responsible Builder Policy) — not guaranteed, so V3 never depends
#        on it. If you get one, just add the two secrets; no code change.
#     b) FEED TOKEN ON .json (workaround, tried automatically):
#        www.reddit.com/comments/<id>.json?…&feed=<REDDIT_FEED_TOKEN>.
#        Anonymous .json is 403 from datacenters (verified); whether your
#        feed token lifts that is answered by your FIRST test run — the log
#        line says exactly which path worked. Tried once per run; a 403
#        result is remembered for that run.
#     FULL MODE adds: every photo (20-photo posts → 2nd container),
#     upvotes/comments stats, video fallback_url (with audio), and true
#     crosspost full-embeds (fetches the original post, 1 level).
#
#   NATIVE MODE — default, zero setup, works when JSON is unavailable:
#     • photo(s): EVERY redd.it image in the RSS content, in post order,
#       best rendition each: i.redd.it "swap" (full-res, unsigned — verified
#       206 for jpg/jpeg; slug-prefixed preview names reduced to the bare
#       file id; PNGs 404 there → largest signed preview URL as-is)
#     • GIF: i.redd.it .gif (unsigned) / signed preview .gif as-is
#     • video (video posts show the video ONLY, never a dup thumb):
#           1. v.redd.it/<id>/DASH_<q>.mp4 (720→1080→480→360) — self-
#              contained mp4 WITH audio, straight from Reddit, open, no sig
#              (round 12 — replaces the old proxy-first chain)
#           2. proxy.embedez.com/render/video.mp4?videoUrl=…&audioUrl=…
#              (CMAF video + CMAF audio -> muxed mp4; keyless, verified
#              h264 720p + AAC out)
#           3. vxreddit.com/redditvideo.mp4?video_url=…&audio_url=…
#     • NEVER a silent video: if the chain fails → first-frame thumbnail +
#       Read Post button.
#   • signed preview.redd.it URLs MUST be used verbatim — changing ANY query
#     param (even width) breaks the signature (403, verified).
#   • YouTube: i.ytimg.com thumbnail (no sig) + official oembed title +
#     "YouTube" button with the starwardspark3 animated emoji; when
#     YOUTUBE_MEDIA_EMBED is True it also tries seaof.glass/yt/<id>.mp4
#     (playable mp4, verified 206 with Discordbot UA; 502 on cold start once —
#     handled by range-check + 1 retry, else thumb+button). youtube.com/live
#     links → thumb+button only.
#   • redgifs link posts: media.redgifs.com/<name>.mp4 (open, verified).
#
# ■ DISCORD COMPONENTS-V2 LIMITS (verified against Discord docs, 2026-09-15):
#   40 components per message, 10 per container, 10 items per media gallery,
#   5 buttons per row, 4000 chars total text. → 20 photos = 2 containers × 10.
#
# ■ ROUND 12 FIXES (2026-09-15, from the live test-channel review):
#   1. Multi-photo posts now show ALL photos: single-image posts get every
#      redd.it image from the RSS content in post order; multi-image GALLERY
#      posts (whose RSS content carries no image links — verified in the
#      2026-09-15 workflow log) get a best-effort REDLIB post-page harvest
#      (same instances as the feed fallback, probed in parallel, ~10s worst
#      case; on failure the single-thumbnail card is kept). FULL MODE had
#      all already.
#   2. Photos use the BEST rendition: i.redd.it full-res swap (jpg/jpeg) or
#      the largest signed preview URL — never the 140px feed thumbnail.
#   3. Stray redd.it image URLs no longer linger in the body text.
#   4. Video posts show the VIDEO ONLY — no duplicate first-frame thumbnail;
#      external-preview.redd.it thumbs (YouTube/external media screenshots)
#      are dropped whenever the post resolves a video.
#   5. Native reddit video chain now prefers v.redd.it DASH_<q>.mp4 — a
#      SELF-CONTAINED mp4 (h264 + AAC, with audio) straight from Reddit, no
#      proxy, no signature, no expiry — before the embedez/vxreddit CMAF
#      muxing proxies. (The signed packaged-media.redd.it DASH master links
#      expire within hours — the e=… query param — so they are NOT used.)
#   6. YouTube: default is now thumbnail + the animated starwardspark3
#      button (deterministic, no third-party transcoder in the hot path).
#      Set YOUTUBE_MEDIA_EMBED=1 to also try seaof.glass playback.
#   7. OP comment: FULL MODE fetches the stickied/top OP comment and shows
#      it in the card ("💬 OP comment:", capped 500 chars + full-comment
#      link). REDDIT_OP_COMMENT=0 disables.
#   8. Discohook: after each successful post a keyless share-link preview of
#      the exact card is created (discohook.app public API, /api/v1/share)
#      and the URL is logged to the workflow run. The share data contains
#      ONLY the public card payload — NO targets, so the webhook URL never
#      leaves the repo. DISCOHOOK_PREVIEW=0 disables.
#   9. Test tools: TEST_POST_ID=<sub>/<post_id> rebuilds one specific post;
#      DRY_RUN=1 builds + logs payloads without touching Discord or the
#      cache (use both from workflow_dispatch to verify before promoting).
#   10. (round 12c) TEST POST works in NATIVE MODE too: when the post JSON
#       is unavailable, the base is built from the post's RSS feed entry
#       (100-entry window) or, failing that, a redlib post page scrape.
#   11. (round 12e) TEST POST gains a 2nd native source: the post's OWN RSS
#       feed (/comments/<id>/.rss) — works for ANY post age, not just the
#       combined feed's 100-entry window. Native photo path now logs how
#       many media URLs the RSS content carries (gallery diagnostics).
#   12. (round 13) PROXY MEDIA: native-mode card media now comes FIRST from
#       the public proxy services — redditez.com (EmbedEZ) -> vxreddit.com
#       -> embeddit.deltandy.me, in that priority order (see testing
#       area/reddit_proxy.py). Each service's own URLs are used verbatim in
#       the components-v2 card: full-res photos, EVERY gallery photo (up to
#       20 = 2 containers), videos WITH audio, GIFs, plus stats. A per-run
#       warm-up probes all three with one known post and writes
#       proxy_health.json (auto-committed); services proven dead are
#       skipped for the run. When a redditez page shows "Failed to Get Post
#       | EmbedEZ" its backend (the part that fetches the post from Reddit
#       for us) is down or unavailable at that moment — a service-side
#       failure, detected per post — and the post falls through to the next
#       service.
#       If every proxy fails for a post, the round-12 native RSS path is
#       used unchanged. PROXY_MEDIA=0 disables the proxy path.
#   13. (round 13) YouTube posts also send a SECOND, plain message
#       containing ONLY the YouTube link (Discord's official preview) after
#       the card lands — waits for the first post (YOUTUBE_LINK_MESSAGE=0
#       disables; the card's own thumb + button stay).
#   14. (round 17, 2026-09-17) ARCTIC SHIFT SEARCH BACKUP: subreddits that
#       RSS left empty (missing from the combined feed, dead per-sub feeds,
#       quiet subs) are re-checked against the archive's /api/posts/search —
#       same post shape as the crosspost lookup. Archive posts are wrapped
#       as feedparser-style entries and run through the SAME collect()
#       (dedup + 48h window unchanged); soft-fail on any error; shares the
#       3-strike circuit breaker with the crosspost archive lookup.
#   15. (round 18, 2026-09-17) SOFT-REMOVED / DELETED POST FILTER: posts the
#       moderators soft-removed or the author deleted — the archive (and
#       occasionally RSS) still carries them with a removal-notice body:
#       "[deleted]", "[removed]", "[ Removed by moderator ]", "Sorry, this
#       post has been removed by the moderators of r/...", "Sorry, this
#       post was deleted by the person who originally posted it" — are
#       detected, skipped and NOT added to the dedup cache: if the post is
#       approved later it surfaces again and posts normally. Explicit TEST
#       POST rebuilds are unaffected. (Follow-up to the round-17 live run,
#       which posted a few removal-notice cards.)
#   16. (round 19, 2026-09-17) 'LABEL LINE + BARE URL' MANGLE REPAIR
#       (follow-up to the round-18 live run — post 1whe2tr): the feed's
#       auto-linker mangles bodies made of "label line + bare URL line"
#       pairs (e.g. "Firefly video" / "https://b23.tv/..." / blank / ...) —
#       it doubles or triples the opening '[' of the URL-labelled link,
#       glues '](U](U))' tail fragments on, and duplicates the next label's
#       first word as a dangling 'Word](U](U)' line. A pre-pass in
#       _line_stage (BEFORE repair_mangled_link_lines, mirrored in
#       reddit_proxy.py's clean_proxy_body) repairs the family to one label
#       line + one clickable URL line per pair. Every other line shape is
#       untouched; the round 15/16 mangle repairs are unchanged.
#   17. (round 20, 2026-09-17) TWO FOLLOW-UPS: (a) the link mangle fix is
#       now the SIMPLE one — a mangled line (the feed's doubled/nested
#       '[[U](U)](U](U))' garbage, its '[U](U](U))' plain face, or any
#       deeper nesting) collapses to ONE plain line with the URL exactly
#       once, 'label [U](U)', exactly like the original post (replaces
#       the round-19 rule; clean lines and the round 15/16 shapes stay
#       byte-identical). (b) ARCHIVE LIVENESS GATE: the Arctic archive
#       (round 17) keeps posts that are no longer live on reddit —
#       removed by moderators, deleted by the author, or still pending
#       approval — often with the ORIGINAL body stored, which the
#       removal-notice filter cannot see. An archive-sourced post is now
#       only posted when a live source (redditez -> vxreddit -> embeddit,
#       then redlib) can actually retrieve it; otherwise it is skipped
#       and NOT cached, so it posts normally once approved or restored.
#       RSS posts and TEST POST rebuilds are unaffected.
#   18. (round 21, 2026-09-17) RAW PLAIN LINKS — the card body now matches
#       the original post as RAW text: (a) the round-20 mangle fix
#       outputs 'label + bare URL' (no markdown wrapping); (b) a bare
#       URL line under a plain label line (the original post's
#       "label / URL" pair, with or without a blank line between)
#       becomes ONE plain 'label https://...' line; (c) standalone
#       bare URL lines are no longer wrapped in markdown — they stay
#       raw and Discord auto-links them in the component v2 container.
#       Clean markdown links, prose and all round 15/16 shapes stay
#       byte-identical.
#   19. (round 22, 2026-09-17) CLEAN AUTO-LINKED 'PLAIN LINK' FACE:
#       the post's bare URLs can also arrive from a source that
#       AUTO-LINKS them, as a clean markdown link whose text IS the
#       URL: 'Firefly video [https://b23.tv/...](https://b23.tv/...)'.
#       Round 20/21 left clean links untouched — but the card body
#       renders as PLAIN TEXT, so such a link showed its literal
#       brackets. URL-labelled links now collapse to the bare URL
#       ('Firefly video https://b23.tv/...') — the original line; the
#       round-15 cascade repair also outputs the bare URL now.
#       Descriptive links ('[text](URL)', text != URL), prose and
#       lines with mangle residue stay byte-identical.
#   20. (round 24, 2026-09-18) MININGTCUP REDLIB: redlib.miningtcup.me
#       joins REDDIT_RSS_INSTANCES (RSS + post pages everywhere the
#       redlib instances already run). It sits behind the operator's
#       DogWAF anti-bot, so the miningtcup token (repo secret
#       NITTER_RSS_TOKEN — same value the Twitter monitor uses) is
#       appended as ?token= on this host via _with_miningtcup_token
#       (both chokepoints: _fetch_feed + _fetch_redlib_post_page). If
#       the WAF doesn't accept it the instance logs a bot-check miss
#       and the chain moves on — no behavior change. inv.miningtcup
#       .me (Invidious) was checked the same day: up (v2026.09.13) but
#       video fetch broken (Invidious error page on /watch) — NOT
#       wired in; revisit once playback is fixed.
#   21. (round 25, 2026-09-18) MOST-COMPLETE-MEDIA-WINS (1wj0p83):
#       proxy chain keeps the largest media list, ties keep priority,
#       and the returned list is capped at 20 (card capacity). The chain
#       stops early at capacity. Archive posts with a known
#       larger gallery count skip without caching and retry within the
#       48h window. Unknown counts and video cards are exempt.
#       This reduces partial galleries; it cannot prove completeness
#       when every source is partial and the archive count is unknown.
#
# ■ WORKFLOW: identical to V1/V2. Run line:
#   run: python "testing area/reddit_main_v3.py"
# ---------------------------------------------------------------------------
import os
import re
import json
import time
import base64
import asyncio
import logging
import html as html_lib
import aiohttp
import feedparser
from urllib.parse import quote
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
load_dotenv()

# ---------------------------------------------------------------------------
# ■ SUBREDDITS TO TRACK
# ---------------------------------------------------------------------------
SUBREDDITS_STR = os.getenv("SUBREDDITS", "Zenlesszonezeroleaks_,Genshin_Impact_Leaks,HonkaiStarRail_leaks,WutheringWavesLeaks,HonkaiNexusAnimaLeaks,AnantaLeaks")
SUBREDDITS = [s.strip() for s in SUBREDDITS_STR.split(",") if s.strip()]

DEFAULT_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Personal Reddit feed token (old.reddit.com -> Preferences -> Feeds).
# Still used for the RSS feed (rate tier) AND now also attempted on the
# .json endpoints (workaround for the 2026 datacenter JSON wall — see header).
REDDIT_FEED_TOKEN = os.getenv("REDDIT_FEED_TOKEN", "").strip()

# round 24 (2026-09-18): miningtcup's DogWAF token — the SAME value as the
# Twitter repo's NITTER_RSS_TOKEN (their nitter RSS token is a DogWAF
# "pass" and the operator says passes from one instance are valid on the
# others — unverified on redlib, so if it doesn't pass the WAF the instance
# just logs a bot-check miss and the chain moves on). Appended as ?token=
# on redlib.miningtcup.me requests only.
MININGTCUP_TOKEN = os.getenv("NITTER_RSS_TOKEN", "").strip()

# round 15 (2026-09-20): eddrit.com — public Reddit frontend with basic
# RSS (github.com/corenting/eddrit). Token-free: no REDDIT_FEED_TOKEN, no
# bot check — plain client verified live 2026-09-20 on ALL 6 tracked subs.
# Feed URL: /r/<sub>/.rss (per-post: /r/<sub>/comments/<id>/.rss).
# Media URLs are the originals (preview.redd.it / i.redd.it / v.redd.it);
# only post permalinks are rewritten to eddrit.com — the post id still
# comes from the same /comments/<id>/ path every other frontend uses.
EDDRIT_BASE = "https://eddrit.com"

# OPTIONAL (FULL MODE, path a): Reddit script app credentials.
# reddit.com/prefs/apps -> create another app -> type "script" ->
# redirect http://localhost. Client ID = under the app name; secret via the
# "get secret" button. Add as repo secrets when/ if you get an app approved.
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "").strip()
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
# Reddit requires a descriptive User-Agent for OAuth API calls.
REDDIT_API_USER_AGENT = "python:uesu.news-express:v3 (personal rss monitor)"

CACHE_FILE = "posted_reddit.json"
MAX_CACHE_SIZE = 500

# Wide window (48h) so posts approved from a subreddit's moderator queue a day
# or more later are still caught (approval bumps the RSS "updated" stamp).
MAX_AGE_SECONDS = 48 * 3600

# ---------------------------------------------------------------------------
# ■ PENDING-POST RECHECK CACHE (round 30, 2026-09-19)
# Posts skipped by the removed/deleted/pending-approval gates (rounds 18/20)
# or the Arctic media-wait gates (rounds 23/25) are tracked in
# pending_reddit.json and re-verified at most once every
# PENDING_RECHECK_SECONDS instead of EVERY run. Without this, every 5-minute
# run re-runs the full live-check chain (proxy services + redlib + media
# resolution) for each still-pending post — the dominant cost of quiet runs
# (2.5-3.5 min) and the window in which overlapping runs could double-post.
# ---------------------------------------------------------------------------
PENDING_FILE = "pending_reddit.json"
PENDING_RECHECK_SECONDS = int(os.getenv("PENDING_RECHECK_SECONDS", "1800"))  # 30 min
PENDING_MAX_AGE_SECONDS = 48 * 3600  # matches the 48h posting window

# ---------------------------------------------------------------------------
# ■ RSS SOURCES (unchanged from V1/V2 — see round 8/9 notes)
# ---------------------------------------------------------------------------
REDDIT_RSS_INSTANCES = [
    "https://www.reddit.com",
    "https://old.reddit.com",
    # round 24 (2026-09-18): miningtcup's redlib (the operator behind the
    # nitter.miningtcup.me RSS token). Behind their DogWAF anti-bot — the
    # miningtcup token below is appended as ?token= on this host; if the
    # operator scoped it to nitter only, it logs a bot-check miss and the
    # chain moves on, exactly like the Anubis instances below.
    "https://redlib.miningtcup.me",
    "https://safereddit.com",           # 2026-09-13: Anubis bot check (fallback lottery)
    "https://red.artemislena.eu",       # 2026-09-13: Anubis bot check (fallback lottery)
    "https://redlib.privacyredirect.com",  # 2026-09-13: Anubis bot check (fallback lottery)
]

RATE_LIMIT_RETRY_DELAY_1 = 6
RATE_LIMIT_RETRY_DELAY_2 = 45
FEED_FETCH_STAGGER = 1.2
COMBINED_FEED_LIMIT = 100

# ---------------------------------------------------------------------------
# ■ V3 — NATIVE MEDIA SETTINGS
# ---------------------------------------------------------------------------
IS_COMPONENTS_V2 = 1 << 15

# Media gallery / container layout (Discord limits: 10 items per gallery,
# 10 components per container, 40 total per message).
MEDIA_PER_GALLERY = 10
MAX_GALLERIES = 2                      # -> max 20 photos per post (Reddit's own cap)

# Video: which CMAF quality to request from the proxies (360 / 720 / 1080).
CMAF_QUALITY = 720
# Skip any candidate media file larger than this (bytes) — same 256 MiB+ headroom
# logic proven on the X engines (234 MB plays, 521 MB+ fails in tiles).
MAX_MEDIA_BYTES = 256 * 1024 * 1024

# Audio video proxy chain (both verified keyless 2026-09-15, muxed h264+AAC out):
VIDEO_PROXY_EMBEDEZ = ("https://proxy.embedez.com/render/video.mp4"
                       "?videoUrl={video_url}&audioUrl={audio_url}&headers=%7B%7D")
VIDEO_PROXY_VXREDDIT = ("https://vxreddit.com/redditvideo.mp4"
                        "?video_url={video_url}&audio_url={audio_url}")

def _env_flag(name: str, default: str) -> bool:
    """Env bool: anything in 0/false/no/off/"" is False, everything else True."""
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off", "")


# YouTube: default = thumbnail + animated starwardspark3 button (deterministic,
# no third-party transcoder in the hot path). Set YOUTUBE_MEDIA_EMBED=1 to ALSO
# try to play the video via seaof.glass (quartz) — range-checked with 1 retry,
# degrades to thumbnail + button automatically.
YOUTUBE_MEDIA_EMBED = _env_flag("YOUTUBE_MEDIA_EMBED", "0")
YOUTUBE_MP4_TEMPLATE = "https://seaof.glass/yt/{video_id}.mp4"

# FULL MODE only: include the OP's (stickied first, else first top-level)
# comment in the card.
INCLUDE_OP_COMMENT = _env_flag("REDDIT_OP_COMMENT", "1")
OP_COMMENT_MAX_CHARS = 500

# Test tools (round 12):
#   TEST_POST_ID=<sub>/<post_id>  -> process exactly that post (bypasses feed)
#   DRY_RUN=1                     -> build + log payloads, never touch Discord/cache
TEST_POST_ID = os.getenv("TEST_POST_ID", "").strip()
DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in ("1", "true", "yes", "on")

# Discohook share-link preview (public API, no key — see the function).
DISCOHOOK_PREVIEW = _env_flag("DISCOHOOK_PREVIEW", "1")
DISCOHOOK_SHARE_ENDPOINT = "https://discohook.app/api/v1/share"
DISCOHOOK_SHARE_TTL = 7 * 24 * 3600  # 7 days (API max: 28)
DISCOHOOK_USER_AGENT = "python:uesu.news-express:v3 (discohook share preview)"

# ---------------------------------------------------------------------------
# ■ PROXY MEDIA SERVICES (round 13) — see testing area/reddit_proxy.py
# ---------------------------------------------------------------------------
# Native-mode card media now comes from the public proxy services FIRST:
# redditez.com (EmbedEZ) -> vxreddit.com -> embeddit.deltandy.me, in that
# priority order. The winning service's own URLs are used verbatim in the
# card (full-res photos, every gallery photo, videos WITH audio, GIFs).
PROXY_MEDIA = _env_flag("PROXY_MEDIA", "1")        # '0' disables the proxy path entirely
YOUTUBE_LINK_MESSAGE = _env_flag("YOUTUBE_LINK_MESSAGE", "1")
# '0' stops the SECOND plain YouTube-link message (the card's own YouTube
# thumb + button are unaffected).

try:
    import reddit_proxy
except Exception as _proxy_import_error:
    # A missing/corrupt module must never break the run — the native RSS
    # media path (round 12) still works on its own.
    reddit_proxy = None
    logging.warning(f"reddit_proxy module unavailable — native media only: {_proxy_import_error}")

_proxy_health = None   # per-run warm-up result (set in main(), read in resolve_post_media)

# Native reddit video ladder: v.redd.it DASH_<q>.mp4 files are self-contained
# mp4s (h264 + AAC). 404s answer instantly, so the ladder is cheap.
DASH_QUALITIES = (720, 1080, 480, 360)

# Feed-token .json attempt: anonymous .json is ~1 req/min from datacenters, so
# this is the polite sleep before each attempt (lower it ONLY if your token
# reliably works on .json).
FEEDTOKEN_JSON_STAGGER = int(os.getenv("FEEDTOKEN_JSON_STAGGER", "65"))

# Text display budget (Discord: 4000 chars total per message across all
# text components; we keep header+body+stats comfortably under it).
MAX_BODY_CHARS = 3000

# Buttons (style 5 = Link). YouTube button uses the animated starwardspark3.
READ_POST_EMOJI = {"id": "1472388018689282261", "name": "starwardhmm", "animated": True}
YOUTUBE_EMOJI = {"id": "1483083423290490891", "name": "starwardspark3", "animated": True}
STATIC_BUTTONS = [
    {"label": "Citlali News", "url": "https://discord.gg/HyrVP9wRXu", "emoji": {"id": "1439878792653832253", "name": "starward11", "animated": True}},
    {"label": "Support", "url": "https://ko-fi.com/jieunlatte", "emoji": {"id": "1509026327548657914", "name": "starwardfans", "animated": True}},
]

# ---------------------------------------------------------------------------
# ■ URL PATTERNS
# ---------------------------------------------------------------------------
# watch / shorts / youtu.be / live (live = no mp4 possible -> thumb+button)
YOUTUBE_RE = re.compile(
    r"""https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^"'<>)\]\s]*v=|shorts/|live/)[^"'<>)\]\s]*"""
    r"""|youtu\.be/[^"'<>)\]\s]+)""",
    re.IGNORECASE,
)
YOUTUBE_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|live/)([A-Za-z0-9_-]{11})")
VREDDIT_RE = re.compile(r"https?://v\.redd\.it/([a-z0-9]+)")
REDGIFS_RE = re.compile(r"https?://(?:www\.)?redgifs\.com/(?:watch|gallery|redeyes?)/([A-Za-z0-9]+)")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}

# ---------------------------------------------------------------------------
# ■ RUNTIME STATE (per run)
# ---------------------------------------------------------------------------
_oauth_token = None          # cached for the run
_feedtoken_json_failed = False  # set True after a 403 on ?feed= .json (per run)


# ---------------------------------------------------------------------------
# ■ SMALL HELPERS (same as V1/V2 unless noted)
# ---------------------------------------------------------------------------
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
            # Keep identical sets byte-identical so quiet runs do not create
            # commits from process-dependent set iteration order.
            json.dump(sorted(posted)[-MAX_CACHE_SIZE:], f, indent=2)
    except Exception as e:
        logging.error(f"Error saving cache: {e}")


def load_pending() -> dict:
    try:
        if os.path.exists(PENDING_FILE):
            with open(PENDING_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception as e:
        logging.error(f"Error reading pending cache: {e}")
    return {}


def save_pending(pending: dict) -> None:
    # Prune entries older than PENDING_MAX_AGE_SECONDS (the 48h posting
    # window has passed — collect() can no longer pick the post up anyway).
    try:
        now = time.time()
        pruned = {k: v for k, v in pending.items()
                  if isinstance(v, dict)
                  and now - float(v.get("first_seen") or 0) <= PENDING_MAX_AGE_SECONDS}
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(pruned.items())), f, indent=2)
    except Exception as e:
        logging.error(f"Error saving pending cache: {e}")


def mark_pending(pending: dict, key: str, reason: str, now: float) -> None:
    entry = pending.get(key)
    if isinstance(entry, dict):
        entry["last_checked"] = now
        entry["reason"] = reason
    else:
        pending[key] = {"first_seen": now, "last_checked": now, "reason": reason}


def pending_due(pending: dict, key: str, now: float) -> bool:
    """True when the pending post is due for a live re-check (never seen,
    or PENDING_RECHECK_SECONDS have passed since the last check)."""
    entry = pending.get(key)
    if not isinstance(entry, dict):
        return True
    return now - float(entry.get("last_checked") or 0) >= PENDING_RECHECK_SECONDS


def normalize_reddit_path(link: str) -> str | None:
    match = re.search(r"(/r/[^\s?]+)", link)
    return match.group(1).rstrip("/") + "/" if match else None


def extract_subreddit(path: str) -> str | None:
    match = re.match(r"/r/([^/]+)/", path or "")
    return match.group(1) if match else None


def extract_post_id(path: str) -> str | None:
    match = re.search(r"/comments/([a-zA-Z0-9]+)/", path)
    return match.group(1) if match else None


CROSSPOST_PERMALINK_RE = re.compile(r"/r/[^/\s?<>]+/comments/[a-zA-Z0-9]+/")


def find_crosspost_original_path(text: str | None, own_path: str | None = None) -> str | None:
    """
    Round 14 crosspost detection: a crosspost's content (RSS content, redlib
    post area, proxy body) contains the word 'crosspost' AND a permalink to
    the ORIGINAL post ("u/x crossposted this from r/Y — original post").
    Returns the original path '/r/<sub>/comments/<id>/' or None.
    """
    if not text or "crosspost" not in text.lower():
        return None
    own = (own_path or "").rstrip("/")
    for m in CROSSPOST_PERMALINK_RE.finditer(text):
        p = m.group(0).rstrip("/")
        if p and p == own:
            continue  # the post's own permalink, not the original
        return p + "/"
    return None


def _crosspost_notice_clean(body: str | None, title: str | None) -> tuple:
    """Round 29 (2026-09-19): a crosspost's own selftext is EMPTY — the
    mirror fills the gap with a CROSSPOST NOTICE instead of content, e.g.
    redditez/EmbedEZ served og:description
    "Original PostPosted in r/AnantaStationLemon Recording Studio via Dremka"
    (notice fragments glued without spaces + the original's title; live
    2026-09-19, crosspost 1wjv962). That notice is not post content: strip
    it, and return the original's subreddit when the notice names one, so
    the card can still show its "🔁 Crosspost of" line when no permalink was
    available (the RSS text carried no 'crosspost' link and Arctic hadn't
    captured the post yet). Returns (cleaned_body, subreddit_or_None).
    """
    if not body or not body.strip():
        return body, None
    low = body.lower()
    norm_title = re.sub(r"\s+", " ", str(title or "")).strip().lower()

    # "posted in r/…" (greedy) — in glued text the run swallows the start of
    # the title ("…r/AnantaStation" + "Lemon …" with no separator); peel the
    # longest title-prefix off the subreddit match and remember it so the
    # strip step can put it back into the text.
    sub_raw = None
    sub_true = None
    m = re.search(r"(?i)posted\s+in\s+r/([A-Za-z0-9_]{2,21})", body)
    if m:
        sub_raw = m.group(1)
        sub_true = sub_raw
        if norm_title:
            for L in range(min(len(norm_title), len(sub_raw) - 1), 1, -1):
                if sub_raw.lower().endswith(norm_title[:L]):
                    sub_true = sub_raw[:len(sub_raw) - L]
                    break
    sub_mark = None
    m2 = re.search(r"(?i)crosspost\w*\s+(?:of|from)\s+"
                   r"(?:\[([^\]]+)\]\([^)]*\)|r/?\s*([A-Za-z0-9_]{2,21})\b)", body)
    if not sub_true:
        if m2:
            sub_mark = (m2.group(1) or m2.group(2) or "").strip().lstrip("r/")

    def _strip(b: str) -> str:
        s = b
        if sub_raw is not None:
            stolen = sub_raw[len(sub_true or sub_raw):]
            s = re.sub(r"(?i)posted\s+in\s+r/" + re.escape(sub_raw),
                       " " + stolen, s, count=1)
        else:
            s = re.sub(r"(?i)posted\s+in\s+r/[A-Za-z0-9_]{2,21}", " ", s)
        s = re.sub(r"(?i)original\s*post(?![a-z])", " ", s)
        s = re.sub(r"(?i)crosspost\w*\s+(?:of|from)\s+"
                   r"(?:\[[^\]]+\]\([^)]*\)|r/?\s*[A-Za-z0-9_]{2,21})\s*(?:subreddit)?",
                   " ", s)
        return re.sub(r"\s{2,}", " ", s).strip()

    specific = ("posted in r/" in low
                or re.search(r"crosspost\w*\s+(?:of|from)\s+(?:\[|r/?)", low))
    # a weak "original post" mention alone never triggers — only when what
    # remains after stripping is exactly the original's title echo.
    if not specific and not (norm_title and "original post" in low
                             and _strip(body) == norm_title):
        return body, None
    orig_sub = sub_true if sub_true is not None else sub_mark
    cleaned = _strip(body)
    if norm_title:
        if cleaned.lower() == norm_title:
            cleaned = ""  # all that was left was the original's title echo
        elif specific and cleaned.lower().startswith(norm_title):
            # notice + title + real text glued together: drop the notice and
            # the title echo, keep the real text
            cleaned = cleaned[len(norm_title):].strip(" \t,.;:-")
    return cleaned, orig_sub


def _subreddit_by_name(name: str) -> str | None:
    for sub in SUBREDDITS:
        if sub.lower() == (name or "").lower():
            return sub
    return None


# Arctic Shift is an optional archive, not a live Reddit API. Missing posts,
# stale records, malformed responses and outages must leave existing fallbacks usable.
ARCTIC_POSTS_URL = "https://arctic-shift.photon-reddit.com/api/posts/ids"
_arctic_fail_count = 0


async def fetch_arctic_post(session, post_id: str, label: str = "") -> dict | None:
    global _arctic_fail_count
    if not re.fullmatch(r"[a-z0-9]+", post_id or "") or _arctic_fail_count >= 3:
        return None
    try:
        async with session.get(ARCTIC_POSTS_URL, params={"ids": post_id},
                               headers=dict(BROWSER_HEADERS),
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            data = await resp.json(content_type=None)
        posts = data.get("data") if isinstance(data, dict) else None
        if not isinstance(posts, list):
            raise ValueError("invalid archive response")
        _arctic_fail_count = 0
        # Empty results are normal for posts not yet archived. Match the ID,
        # rather than accidentally attaching another post's media.
        return next((post for post in posts if isinstance(post, dict)
                     and post.get("id") == post_id), None)
    except Exception as exc:
        _arctic_fail_count += 1
        logging.info(f"[{label or post_id}] Arctic Shift unavailable: {exc}")
        return None


def arctic_crosspost_orig(post) -> dict | None:
    parents = post.get("crosspost_parent_list") if isinstance(post, dict) else None
    if isinstance(parents, list) and parents and isinstance(parents[0], dict):
        return parents[0]
    return None


def arctic_video_info(post) -> tuple:
    """Candidate video URL; the existing resolver must still validate it.

    Reddit fallback_url is NOT guaranteed to include audio.
    """
    media = post.get("media") if isinstance(post, dict) else None
    video = media.get("reddit_video") if isinstance(media, dict) else None
    url = video.get("fallback_url") if isinstance(video, dict) else None
    if not isinstance(url, str) or not re.match(r"https://v\.redd\.it/", url):
        return None, None
    return extract_vreddit_id(url), html_lib.unescape(url)


def arctic_gallery_items(post) -> list:
    if not isinstance(post, dict):
        return []
    gallery = post.get("gallery_data")
    metadata = post.get("media_metadata")
    if not isinstance(gallery, dict) or not isinstance(metadata, dict):
        return []
    items = gallery.get("items")
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict) or item.get("is_deleted"):
            continue
        key = item.get("media_id")
        meta = metadata.get(key) if isinstance(key, str) else None
        if not isinstance(meta, dict) or meta.get("status") not in (None, "valid"):
            continue
        src = meta.get("s")
        if not isinstance(src, dict):
            continue
        animated = meta.get("e") == "AnimatedImage" or meta.get("m") == "image/gif"
        url = src.get("gif" if animated else "u")
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        url = html_lib.unescape(url)
        if not animated:
            match = re.fullmatch(r"https://(?:i|preview)\.redd\.it/([\w.-]+\.jpe?g)",
                                 url.split("?", 1)[0], re.I)
            if match:
                url = f"https://i.redd.it/{_reddit_media_key(match.group(1))}"
        out.append({"kind": "gif" if animated else "image", "url": url})
    return out


# ---------------------------------------------------------------------------
# ■ ROUND 17 (2026-09-17): ARCTIC SHIFT SEARCH BACKUP
# When RSS yields NO new posts for a subreddit, re-check the Arctic Shift
# archive's /api/posts/search (same post JSON shape as the /api/posts/ids
# lookup above). Archive posts are wrapped as feedparser-style entries and
# run through the SAME collect() in main() — dedup cache + 48h freshness
# window apply unchanged. Soft-fail on any error; shares the 3-strike
# _arctic_fail_count circuit breaker with the crosspost archive lookup.
# Arctic's score/num_comments are stale for ~36h after posting, so they are
# NEVER read here (stats come from the post JSON / proxy services instead).
# ---------------------------------------------------------------------------
ARCTIC_SEARCH_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"


async def fetch_arctic_subreddit_posts(session, subreddit: str,
                                       after_epoch: float | None = None,
                                       limit: int = 100, label: str = "") -> list:
    """Newest posts for one subreddit from the Arctic Shift search API.

    Returns [] on ANY failure (429/timeout/bad shape) — the RSS path has
    already run, so a search error must never block posting. `after_epoch`
    (unix seconds) restricts the window to match MAX_AGE_SECONDS.
    """
    global _arctic_fail_count
    if not re.fullmatch(r"[A-Za-z0-9_]{2,50}", subreddit or "") or _arctic_fail_count >= 3:
        return []
    params = {"subreddit": subreddit, "limit": str(min(int(limit), 100)),
              "sort": "desc", "md2html": "true"}
    if after_epoch:
        params["after"] = str(int(after_epoch))
    try:
        async with session.get(ARCTIC_SEARCH_URL, params=params,
                               headers=dict(BROWSER_HEADERS),
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            data = await resp.json(content_type=None)
        posts = data.get("data") if isinstance(data, dict) else None
        if not isinstance(posts, list):
            raise ValueError("invalid search response")
        _arctic_fail_count = 0
        return [p for p in posts if isinstance(p, dict) and p.get("id")]
    except Exception as exc:
        _arctic_fail_count += 1
        logging.info(f"[{label or subreddit}] Arctic Shift search unavailable: {exc}")
        return []


class _ArcticEntry:
    """Minimal feedparser-style entry wrapping an Arctic Shift post, so
    archive posts flow through the SAME collect() as RSS entries (dedup
    cache + 48h freshness window apply unchanged). entry_to_base_data()
    reads title/author/link as attributes and content/summary/
    media_thumbnail via .get() — exactly like a feedparser entry."""

    def __init__(self, post: dict):
        pid = post.get("id") or ""
        sub = post.get("subreddit") or ""
        self.link = f"https://www.reddit.com/r/{sub}/comments/{pid}/"
        self.title = post.get("title") or ""
        self.author = post.get("author") or ""
        # round 23 (2026-09-18): keep the raw post for _arctic_media_hint
        # (the media-wait gate in main). No other behavior changes.
        self._arctic_post = post
        created = post.get("created_utc")
        updated = post.get("updated_utc") or created
        self.published_parsed = (time.localtime(created)
                                 if isinstance(created, (int, float)) and created > 0 else None)
        self.updated_parsed = (time.localtime(updated)
                               if isinstance(updated, (int, float)) and updated > 0 else None)
        self._d = {
            "content": [{"value": post.get("body") or ""}],
            "summary": "",
            "media_thumbnail": [],
            "published_parsed": self.published_parsed,
            "updated_parsed": self.updated_parsed,
        }

    def get(self, k, d=None):
        return self._d.get(k, d)


def _clean_author_name(value) -> str:
    for line in reversed(str(value or "").splitlines()):
        candidate = re.sub(r"\]\(.*$", "", line).strip(" []*")
        candidate = re.sub(r"^(?:submitted\s+)?by\)?\s+", "", candidate, flags=re.I)
        candidate = re.sub(r"^/?u/", "", candidate)
        if re.fullmatch(r"[A-Za-z0-9_-]{2,25}", candidate):
            return candidate
    return "unknown"


def _clean_post_title(value) -> str:
    title = str(value or "").strip()
    # Only the observed appended byline artifact, not legitimate title punctuation.
    title = re.sub(r"\]\(https?://[^\n]*\)\s*\n\*?by.*$", "", title, flags=re.S)
    return re.sub(r"\s+", " ", title)[:400]


# ---------------------------------------------------------------------------
# ■ ROUND 18 (2026-09-17): SOFT-REMOVED / DELETED POST DETECTION
# The Arctic Shift archive (round 17) — and occasionally the RSS feed —
# still carries posts the moderators soft-removed or the author deleted.
# Their pages show a removal notice instead of the real content. Such
# posts are skipped (not posted) and NOT cached, so a post that gets
# approved later is caught and posted normally.
# ---------------------------------------------------------------------------
# round 23 (2026-09-18): a whole title of "[ Removed by moderator ]" is a
# removal marker too (reddit.com renders it as the post title); the
# longer alternative comes FIRST so it is tried before the bare "removed".
_REMOVED_TITLE_RE = re.compile(
    r"^\s*\*{0,2}\[ ?(?:removed ?by ?(?:the )?moderators?|removed|deleted) ?\]\*{0,2}\s*$",
    re.I)
_REMOVED_WHOLE_BODY_RE = re.compile(
    r"^\*{0,2}\[ ?(?:deleted|removed) ?\]\*{0,2}$"
    r"|^\*{0,2}\[ ?removed ?by ?moderator ?\]\*{0,2}$", re.I)
_REMOVED_NOTICE_RES = (
    (re.compile(r"sorry,? (?:this|the) post (?:has been|was) (?:removed|deleted)", re.I), "removal notice"),
    (re.compile(r"\[ ?removed ?by ?moderator ?\]", re.I), "removed by moderator"),
    (re.compile(r"removed by (?:the )?(?:moderators?|reddit)", re.I),
     "removed by moderators/filters"),
    (re.compile(r"(?:was|has been) deleted by the person who originally posted it", re.I), "deleted by author"),
)


def removed_post_reason(title: str | None, body: str | None) -> str | None:
    """Returns a short reason when the post looks soft-removed or deleted
    (still pending approval), else None. Observed notices (2026-09-17 live
    run): "[deleted]", "[removed]", "**[ Removed by moderator ]**",
    "Sorry, this post has been removed by the moderators of r/...",
    "Sorry, this post was deleted by the person who originally posted it".
    Only the first 400 chars of the body are inspected — a legitimate post
    that merely mentions a deletion later in its text must not be caught.
    An EMPTY body is NOT treated as removed (legitimate image/link posts
    have empty bodies)."""
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


def _arctic_media_hint(post) -> bool:
    """Round 23 (2026-09-18): does the Arctic record POSITIVELY say this
    post has media (a gallery, a rich link, or a redd.it media URL)?

    Only a positive hint triggers the media-wait gate in main(): text
    and link posts (no gallery flags, no redd.it media URL) have no
    hint and post exactly as before. Arctic fills gallery_data /
    media_metadata asynchronously after capture, so a fresh gallery
    post can carry its text here without its media — the hint is
    what makes that detectable (1wj38fc posted media-less because
    none of that was visible at the time).
    """
    if not isinstance(post, dict):
        return False
    if post.get("is_gallery") or post.get("gallery_data") or post.get("media_metadata"):
        return True
    if post.get("post_hint") in ("image", "rich_link"):
        return True
    if post.get("secure_media_domain") in (
            "i.redd.it", "preview.redd.it", "external-preview.redd.it", "v.redd.it"):
        return True
    for key in ("url", "url_overridden_by_dest"):
        u = post.get(key) or ""
        if re.match(r"https?://(?:i|preview|external-preview)\.redd\.it/", u) \
                or "v.redd.it/" in u:
            return True
    return False


def _arctic_media_count(post) -> int:
    """Round 25: count valid, filled gallery entries; unknown/non-gallery is 0.

    Reuse the ordered Arctic extractor so invalid/deleted entries do not
    delay a post forever. Only a positive count can gate partial media.
    """
    return len(arctic_gallery_items(post))


def _clean_plain_body(text) -> str:
    if text in ("[removed]", "[deleted]"):
        return ""
    return _collapse_blanks(_line_stage([line.strip() for line in str(text or "").splitlines()]))


def _drop_youtube_line(body: str, youtube_url: str | None) -> str:
    video_id, _ = extract_youtube_id(youtube_url)
    if not video_id:
        return body
    kept = []
    for line in body.splitlines():
        candidate = line.strip()
        link = re.fullmatch(r"\[([^\]]+)\]\((https?://[^\s]+)\)", candidate)
        # Only remove URL-labelled links, not descriptive links in prose.
        if link and link.group(1) == link.group(2):
            candidate = link.group(2)
        if re.fullmatch(r"https?://\S+", candidate):
            other_id, _ = extract_youtube_id(candidate)
            if other_id == video_id:
                continue
        kept.append(line)
    return _collapse_blanks(kept)


def strip_html(value: str | None) -> str:
    """Removes tags from HTML-ish strings (used for JSON selftext etc.)."""
    if not value:
        return ""
    value = re.sub(r"(?i)<br\s*/?>", " ", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html_lib.unescape(value)
    return re.sub(r"\s{2,}", " ", value).strip()



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


# ---------------------------------------------------------------------------
# ■ ROUND 20 (2026-09-17): ARCHIVE POST LIVENESS GATE
# The Arctic Shift archive (round 17) keeps posts that are no longer
# live on reddit: ones the moderators removed ("[ Removed by moderator
# ]"), ones the author deleted ("[deleted]" / "Sorry, this post was
# deleted by the person who originally posted it."), and ones still
# pending approval in a moderator queue (not accepted yet). The archive
# stores the ORIGINAL content from capture time, so the round-18/19
# removal-notice filter cannot see the removal. Live sources can: a post
# that is removed / deleted / not approved yet is invisible to the proxy
# services (redditez -> vxreddit -> embeddit) and to redlib. So an
# archive-sourced post is only posted when at least one live source can
# actually retrieve it; otherwise it is skipped and NOT cached — once it
# is approved or restored it becomes visible and posts normally on a
# later run. RSS-sourced posts and TEST POST rebuilds are unaffected.
# ---------------------------------------------------------------------------

async def verify_archive_post_live(session, path: str, label: str = ""):
    """Verify an archive-sourced post is still LIVE on reddit.
    Returns (live: bool, why: str)."""
    health = _proxy_health or {}
    proxy_down = (reddit_proxy is None or all(
        isinstance(health.get(s), dict) and health[s].get("ok") is False
        for s in ("redditez", "vxreddit", "embeddit")))
    if reddit_proxy is not None:
        try:
            result = await reddit_proxy.fetch_proxy_post(session, path, label=label)
        except Exception:
            result = None
        if result:
            _reason = removed_post_reason(result.get("title"), result.get("body"))
            if _reason:
                return False, (f"live source {result.get('service')} still "
                               f"shows a removal notice ({_reason})")
            return True, f"live via {result.get('service')}"
    # redlib (the "other means"): the post page only exists while the
    # post is live on reddit.
    try:
        base = await fetch_test_post_base(session, path, label)
    except Exception:
        base = None
    if base and (base.get("title") or base.get("body")):
        return True, "live via redlib"
    if proxy_down:
        return False, ("all live sources are down this run — cannot "
                       "verify; skipped for safety")
    return False, ("no live source (redditez/vxreddit/embeddit/redlib) "
                   "can retrieve the post — appears removed, deleted or "
                   "still pending approval")


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

def clean_rss_body(value: str | None) -> str:
    """
    Turn the RSS entry's content HTML into clean card text (Discord markdown).
    The feed wraps post HTML in <table><tr><td>…</td></tr></table> and appends
    a 'submitted by /u/… to r/…' footer plus [link]/[comments] spans — all of
    that is stripped; paragraphs are kept on separate lines.
    Round 14: links become clickable markdown — <a href="U">T</a> -> [T](U)
    (and raw-markdown RSS links pass through untouched) — while redd.it
    MEDIA URLs (bare or as links) are removed from the text, since the media
    already sits in the gallery (fixes the "…s=…dc0Seems like the…" bug).
    """
    if not value:
        return ""
    text = value
    # [link]/[comments] nav spans first (before the generic anchor rule)
    text = re.sub(r"(?i)<span>\s*<a[^>]*>\[(?:link|comments)\]</a>\s*</span>", " ", text)
    # links stay clickable: <a href="U">T</a> -> [T](U)
    text = re.sub(r'(?is)<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r'[\2](\1)', text)
    # round 16: blockquotes -> Discord quote markers (applied per line below)
    text = re.sub(r"(?is)<blockquote[^>]*>", "\x01", text)
    text = re.sub(r"(?i)</blockquote>", "\x02", text)
    # round 16: bold tags -> Discord bold (only when present in the source)
    text = re.sub(r"(?is)<(?:b|strong)\s*>(.*?)</(?:b|strong)>", r"**\1**", text)
    # round 16: list items -> Discord bullet lines
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</li\s*>", " ", text)
    text = re.sub(r"(?i)</?(?:ul|ol)[^>]*>", "\n", text)
    text = re.sub(r"(?i)<\s*(br|p\b|/p|/div|/li|/table|/tr|/td|h[1-6])[^>]*>", "\n", text)
    text = re.sub(r"(?i)<img[^>]*>", " ", text)
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
    text = re.sub(r"submitted by\s+/u/\S+\s+to\s+r/\S+", " ", text)
    # redd.it media link -> gone (the gallery has the media)
    text = re.sub(r"\[[^\]]*\]\(\s*https?://(?:i\.|preview\.|external-preview\.)?redd\.it/[^\s)]*\s*\)", " ", text)
    # bare redd.it URL -> gone (never touch a markdown link target)
    text = re.sub(r"(?<!\]\()https?://(?:i\.|preview\.|external-preview\.)?redd\.it/[^\s<>)\]]+(?!\))", " ", text)
    # RSS nav leftovers that survived as markdown links: [link](…) [comments](…)
    text = re.sub(r"(?i)\[(?:link|comments)\]\(\s*[^\s)]*\s*\)", " ", text)
    lines = [re.sub(r"\s{2,}", " ", ln).strip() for ln in text.splitlines()]
    lines = _apply_quote_markers(lines)
    return _collapse_blanks(_line_stage(lines))


# ---------------------------------------------------------------------------
# ■ RSS FEED FETCHING (identical logic to V1/V2 — combined feed primary)
# ---------------------------------------------------------------------------
def _with_miningtcup_token(url: str) -> str:
    """round 24: append the DogWAF token for miningtcup hosts — their WAF
    reads ?token= (same convention the operator confirmed for nitter).
    No-op for other hosts or when the token is empty."""
    if MININGTCUP_TOKEN and "miningtcup.me" in url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}token={MININGTCUP_TOKEN}"
    return url


async def _fetch_feed(session: aiohttp.ClientSession, feed_url: str, label: str):
    """
    GETs one feed URL with two 429 retries (6s, then 45s). A response is only
    accepted if it is HTTP 200, contains real feedparser entries, and those
    entries carry reddit /comments/ permalinks. Every rejection is logged.
    """
    feed_url = _with_miningtcup_token(feed_url)   # round 24: DogWAF token
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
        if REDDIT_FEED_TOKEN and "reddit.com" in instance:
            feed_url += f"?feed={REDDIT_FEED_TOKEN}"
        feed = await _fetch_feed(session, feed_url, instance)
        if feed:
            logging.info(f"Successfully fetched r/{subreddit} from {instance}")
            return feed
        # round 15 (2026-09-20): eddrit.com — token-free RSS fallback.
        # Sits AFTER redlib.miningtcup.me (token-gated, tried first) and
        # BEFORE the Arctic Shift search backup (archive, laggier).
        # Same session/headers/timeout/success handoff as the redlib
        # branch: _fetch_feed is that path (BROWSER_HEADERS + RSS Accept,
        # ClientTimeout(total=15), 429 retries, /comments/ validation).
        if instance == "https://redlib.miningtcup.me":
            try:
                eddrit_url = f"{EDDRIT_BASE}/r/{subreddit}/.rss"
                logging.info(f"trying eddrit feed: {eddrit_url}")
                eddrit_feed = await _fetch_feed(session, eddrit_url, EDDRIT_BASE)
                if eddrit_feed:
                    logging.info(
                        f"eddrit feed OK: r/{subreddit} -> "
                        f"{len(eddrit_feed.entries)} entries")
                    logging.info(f"Successfully fetched r/{subreddit} from {EDDRIT_BASE}")
                    return eddrit_feed
            except Exception as e:
                logging.error(
                    f"eddrit feed failed for r/{subreddit}: {e} — falling through")
    logging.warning(f"Could not fetch valid RSS feed for r/{subreddit} from any instance.")
    return None


def extract_youtube_url(*html_parts: str | None) -> str | None:
    """Finds a YouTube link (watch / shorts / youtu.be / live) in HTML text."""
    for html in html_parts:
        match = YOUTUBE_RE.search(html or "")
        if match:
            return match.group(0)
    return None


def extract_youtube_id(url: str | None) -> tuple[str | None, bool]:
    """Returns (video_id, is_live)."""
    if not url:
        return None, False
    if "youtube.com/live/" in url:
        m = re.search(r"live/([A-Za-z0-9_-]{11})", url)
        return (m.group(1) if m else None), True
    m = YOUTUBE_ID_RE.search(url)
    return (m.group(1) if m else None), False


# ---------------------------------------------------------------------------
# ■ POST JSON — full-mode data (OAuth, then feed-token workaround)
# ---------------------------------------------------------------------------
async def get_oauth_token(session: aiohttp.ClientSession) -> str | None:
    """
    Application-only OAuth token (grant_type=client_credentials) — the same
    flow Embeddit uses. No user login, no password. Returns None if the
    secrets are missing/invalid.
    """
    global _oauth_token
    if _oauth_token:
        return _oauth_token
    if not (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET):
        return None
    basic = base64.b64encode(f"{REDDIT_CLIENT_ID}:{REDDIT_CLIENT_SECRET}".encode()).decode()
    try:
        async with session.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": REDDIT_API_USER_AGENT,
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logging.info(f"OAuth token request HTTP {resp.status} — {await resp.text()[:200]}")
                return None
            data = await resp.json()
        _oauth_token = data.get("access_token")
        if _oauth_token:
            logging.info("OAuth application token acquired (FULL MODE path a).")
        else:
            logging.info(f"OAuth token response had no access_token: {str(data)[:200]}")
    except Exception as e:
        logging.info(f"OAuth token request error: {e}")
    return _oauth_token


async def fetch_post_json(session: aiohttp.ClientSession, post_id: str, use_oauth: bool) -> dict | None:
    """
    Fetches ONE post's JSON (the full data: all media, stats, crosspost link).
    Paths: OAuth token -> oauth.reddit.com ; else feed token on www .json
    (workaround — 403 result remembered for the run). Returns the post dict
    or None (caller falls back to native/RSS-only data).
    """
    global _feedtoken_json_failed
    # limit=25: we need the TOP-LEVEL comments (data[1]) for the OP comment.
    url = f"https://oauth.reddit.com/comments/{post_id}.json?limit=25&raw_json=1"
    headers = {"User-Agent": REDDIT_API_USER_AGENT}
    token = await get_oauth_token(session) if use_oauth else None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif not _feedtoken_json_failed and REDDIT_FEED_TOKEN:
        # Workaround attempt: the personal feed token on a .json endpoint.
        # Anonymous .json is 403 from datacenters; the token MIGHT lift it.
        url = (f"https://www.reddit.com/comments/{post_id}.json"
               f"?limit=25&raw_json=1&feed={REDDIT_FEED_TOKEN}")
        headers = dict(BROWSER_HEADERS)
        await asyncio.sleep(FEEDTOKEN_JSON_STAGGER)  # anonymous tier ~1 req/min
    else:
        return None

    try:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status == 429:
                logging.info(f"[{post_id}] post JSON 429 — one retry in 45s.")
                await asyncio.sleep(45)
                async with session.get(url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=20)) as resp2:
                    if resp2.status != 200:
                        logging.info(f"[{post_id}] post JSON retry HTTP {resp2.status} — native mode.")
                        return None
                    body = await resp2.text()
            elif resp.status != 200:
                if "feed=" in url:
                    _feedtoken_json_failed = True
                logging.info(f"[{post_id}] post JSON HTTP {resp.status} — native mode "
                             f"({'feed token will not be retried this run' if 'feed=' in url else 'no JSON path'})")
                return None
            else:
                body = await resp.text()
        data = json.loads(body)
        post = data[0]["data"]["children"][0]["data"]
        try:
            post["_top_comments"] = [
                c for c in (data[1].get("data", {}).get("children") or [])
                if isinstance(c, dict) and c.get("data", {}).get("body")
            ]
        except Exception:
            post["_top_comments"] = []
        source = "oauth" if token else "feed-token"
        logging.info(f"[{post_id}] post JSON OK via {source} (FULL MODE).")
        return post
    except Exception as e:
        logging.info(f"[{post_id}] post JSON error: {e} — native mode.")
        return None


# ---------------------------------------------------------------------------
# ■ MEDIA VERIFICATION + RESOLUTION (V3 core)
# ---------------------------------------------------------------------------
async def media_url_ok(session: aiohttp.ClientSession, url: str, timeout: int = 20,
                       video: bool = False) -> tuple[bool, int]:
    """
    Range-checks a media URL the way a fetch would. Returns (ok, total_bytes).
    ok = 200/206 with a sane content-type and size under MAX_MEDIA_BYTES.
    """
    try:
        async with session.get(url, headers={"User-Agent": "Discordbot/2.0", "Range": "bytes=0-0"},
                               timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status in (200, 206):
                ctype = (resp.headers.get("Content-Type") or "").lower()
                want = ("video" in ctype or "octet-stream" in ctype) if video else (
                    "image" in ctype or "video" in ctype or "octet-stream" in ctype)
                size = 0
                cr = resp.headers.get("Content-Range")  # e.g. "bytes 0-0/12345"
                if cr and "/" in cr:
                    try:
                        size = int(cr.rsplit("/", 1)[1])
                    except ValueError:
                        size = 0
                cl = resp.headers.get("Content-Length")
                if not size and cl and resp.status == 200:
                    size = int(cl)
                if want and (size == 0 or size <= MAX_MEDIA_BYTES):
                    return True, size
                logging.info(f"media check rejected {url[:90]} — type={ctype} size={size}")
                return False, size
            logging.info(f"media check {resp.status} for {url[:90]}")
            return False, 0
    except Exception as e:
        logging.info(f"media check error for {url[:90]}: {e}")
        return False, 0


def cmaf_urls(vid: str, quality: int = CMAF_QUALITY) -> dict:
    base = f"https://v.redd.it/{vid}"
    return {
        "video_mp4": f"{base}/CMAF_{quality}.mp4",
        "video_m3u8": f"{base}/CMAF_{quality}.m3u8",
        "audio_mp4": f"{base}/CMAF_AUDIO_128.mp4",
        "audio_m3u8": f"{base}/CMAF_AUDIO_128.m3u8",
    }


async def resolve_video_url(session: aiohttp.ClientSession, vid: str,
                            fallback_url: str | None = None) -> str | None:
    """
    Audio video chain (NO silent fallback):
      1. fallback_url (from post JSON, if it serves an mp4)
      2. proxy.embedez.com  (CMAF mp4 + audio -> muxed mp4)
      3. vxreddit.com       (CMAF m3u8 pair -> muxed mp4, cached by them)
    Qualities tried: CMAF_QUALITY, then 1080 (not every video has 720p).
    -> None means the caller uses thumbnail + button.
    """
    if fallback_url and "v.redd.it" in fallback_url and fallback_url.lower().split("?")[0].endswith(".mp4"):
        ok, size = await media_url_ok(session, fallback_url, timeout=60, video=True)
        if ok:
            logging.info(f"video url OK via fallback_url ({size} bytes).")
            return fallback_url
    # Native ladder FIRST: v.redd.it DASH_<q>.mp4 = self-contained mp4
    # (h264 + AAC, WITH audio) straight from Reddit — no proxy, no sig, no
    # expiry. (The signed packaged-media.redd.it masters expire in hours.)
    for quality in DASH_QUALITIES:
        dash = f"https://v.redd.it/{vid}/DASH_{quality}.mp4"
        ok, size = await media_url_ok(session, dash, timeout=30, video=True)
        if ok:
            logging.info(f"video url OK via native v.redd.it DASH_{quality} ({size} bytes).")
            return dash
    for quality in (CMAF_QUALITY, 1080):
        c = cmaf_urls(vid, quality)
        em = VIDEO_PROXY_EMBEDEZ.format(video_url=quote(c["video_mp4"], safe=""),
                                        audio_url=quote(c["audio_mp4"], safe=""))
        ok, size = await media_url_ok(session, em, timeout=120, video=True)
        if ok:
            logging.info(f"video url OK via embedez-proxy q{quality} ({size} bytes).")
            return em
        if quality == CMAF_QUALITY:
            vx = VIDEO_PROXY_VXREDDIT.format(video_url=quote(c["video_m3u8"], safe=""),
                                             audio_url=quote(c["audio_m3u8"], safe=""))
            ok, size = await media_url_ok(session, vx, timeout=120, video=True)
            if ok:
                logging.info(f"video url OK via vxreddit-proxy ({size} bytes).")
                return vx
    logging.info("video chain exhausted — using thumbnail + button (no silent video).")
    return None


def photo_urls_from_metadata(mm: dict) -> list[str]:
    """
    media_metadata (JSON) -> ordered signed photo URLs.
    Order = the order of `preview.images` (each entry's id keys into media_metadata).
    Prefers the full-res `p` variant, falls back to `s`, then `source`.
    URLs are used VERBATIM (the s= signature covers the query params).
    """
    if not mm:
        return []
    urls = []
    seen = set()
    for key, meta in mm.items():
        if not isinstance(meta, dict) or meta.get("is_video") or meta.get("is_gif"):
            continue
        url = None
        for variant in ("p", "s"):
            v = meta.get(variant)
            if isinstance(v, dict) and v.get("url"):
                url = v["url"]
                break
        if not url:
            src = meta.get("source")
            if isinstance(src, dict) and src.get("url"):
                url = src["url"]
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def gif_url_from_metadata(mm: dict) -> str | None:
    for meta in (mm or {}).values():
        if isinstance(meta, dict) and meta.get("is_gif"):
            for variant in ("u", "s"):
                v = meta.get(variant)
                if isinstance(v, dict) and v.get("url"):
                    return v["url"]
    return None


def i_reddit_swap(url: str) -> str | None:
    """
    preview.redd.it/<file>.jpg|jpeg (signed) -> i.redd.it/<file>.jpg|jpeg
    (unsigned full-res; verified 2026-09-15 for jpg/jpeg, PNGs 404 there).
    Slug-prefixed preview names (<postslug>-v0-<id>.jpg) are reduced to the
    bare <id>.jpg first — i.redd.it only serves the bare file id.
    Returns the candidate URL or None.
    """
    m = re.match(r"https?://preview\.redd\.it/([\w.-]+\.(?:jpe?g))\?", url or "", re.I)
    if not m:
        return None
    return f"https://i.redd.it/{_reddit_media_key(m.group(1))}"


def extract_vreddit_id(text: str | None) -> str | None:
    m = VREDDIT_RE.search(text or "")
    return m.group(1) if m else None


def extract_redgifs_url(text: str | None) -> str | None:
    m = REDGIFS_RE.search(text or "")
    if not m:
        return None
    url = f"https://media.redgifs.com/{m.group(1)}.mp4"
    return url


def _is_external_preview(url: str | None) -> bool:
    """external-preview.redd.it = screenshots of EXTERNAL media (mostly
    YouTube embeds). They are video byproducts, not post photos."""
    return bool(url) and "external-preview.redd.it" in url


# ---------------------------------------------------------------------------
# ■ NATIVE MODE MEDIA EXTRACTION (round 12)
# ---------------------------------------------------------------------------
REDDIT_MEDIA_URL_RE = re.compile(
    r"""https?://(?:i\.redd\.it|preview\.redd\.it|external-preview\.redd\.it)"""
    r"""/[\w.-]+\.(?:jpe?g|png|gif|webp)(?:\?[^"'<>\s]*)?""",
    re.I,
)


def _reddit_media_key(url: str) -> str:
    """Group key so all renditions of ONE photo collapse to a single item:
    preview.redd.it/<slug>-v0-<id>.png  and  i.redd.it/<id>.png  -> <id>.png
    """
    base = url.split("?", 1)[0].rsplit("/", 1)[-1].lower()
    return re.sub(r"^.+-v\d+-", "", base)


def extract_native_media(content_html: str | None) -> list[dict]:
    """
    ALL redd.it media from the RSS content HTML, in post order, one item per
    photo with its BEST rendition: i.redd.it (unsigned, full-res) > the
    signed preview URL with the largest ?width=. external-preview.redd.it
    thumbs are kept here and dropped by the caller when a video resolves.
    """
    if not content_html:
        return []
    best: dict[str, tuple[int, int, str, str]] = {}
    for order, m in enumerate(REDDIT_MEDIA_URL_RE.finditer(content_html)):
        url = m.group(0).rstrip(".,;")
        host = url.split("/")[2].lower()
        key = _reddit_media_key(url)
        ext = key.rsplit(".", 1)[-1]
        kind = "gif" if ext == "gif" else "image"
        score = 0
        if host == "i.redd.it" and "?" not in url:
            score += 100000  # unsigned full-res wins
        wm = re.search(r"[?&]width=(\d+)", url)
        if wm:
            score += int(wm.group(1))
        prev = best.get(key)
        if prev is None or score > prev[0]:
            best[key] = (score, order, kind, url)
    items = sorted(best.values(), key=lambda t: t[1])
    out = []
    for _, _, kind, u in items:
        if kind == "image":
            # Signed preview jpg/jpeg -> unsigned full-res i.redd.it
            # (verified 2026-09-15; PNGs are never swapped — i.redd.it 404s).
            swapped = i_reddit_swap(u)
            if swapped:
                u = swapped
        out.append({"kind": kind, "url": u})
    return out


# ---------------------------------------------------------------------------
# ■ OP COMMENT (FULL MODE, round 12)
# ---------------------------------------------------------------------------
def extract_op_comment(post_json: dict) -> dict | None:
    """
    Finds the OP's top-level comment (stickied first, else the first
    top-level comment by the post author). Returns
    {"text", "permalink", "stickied"} or None.
    """
    top = post_json.get("_top_comments") or []
    author = post_json.get("author")
    if not top or not author:
        return None

    def _finish(d: dict) -> dict | None:
        body = strip_html(d.get("body"))
        if not body:
            return None
        permalink = (f"https://www.reddit.com{post_json.get('permalink', '').rstrip('/')}"
                     f"/comment/{d.get('id', '')}/")
        return {"text": body, "permalink": permalink, "stickied": bool(d.get("stickied"))}

    fallback = None
    for c in top:
        d = c.get("data") if isinstance(c, dict) else None
        if not d or d.get("author") != author or not (d.get("body") or "").strip():
            continue
        if d.get("stickied"):
            return _finish(d)
        if fallback is None:
            fallback = d
    return _finish(fallback) if fallback else None


def op_comment_text(op: dict) -> str:
    """Single-line card text: label + capped comment + full-comment link."""
    t = op["text"].strip()
    if len(t) > OP_COMMENT_MAX_CHARS:
        cut = t[:OP_COMMENT_MAX_CHARS]
        cut = cut.rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
    else:
        cut = t
    label = "💬 OP comment" + (" (stickied)" if op.get("stickied") else "")
    return f"**{label}:** {cut} — [full comment]({op['permalink']})"


# ---------------------------------------------------------------------------
# ■ REDLIB GALLERY ENRICHMENT (round 12, best-effort, NATIVE MODE)
# ---------------------------------------------------------------------------
# Reddit RSS does NOT expose gallery images for multi-image posts (they live
# in the post JSON's media_metadata, which is 403-walled from datacenters —
# verified in the 2026-09-15 workflow log: "post JSON HTTP 403 — native
# mode"). Single-image posts DO carry their image link in the RSS content.
# Redlib post pages render the full gallery, so as a fallback lottery (the
# SAME instances as the feed fallback) we fetch the post page and harvest
# every redd.it media URL in the post area. All instances are probed in
# parallel; first success wins; if none answer, the single-thumbnail card
# is kept (no error, no retry).

REDDIL_POST_TITLE_RE = re.compile(r"<h1[^>]*post_title[^>]*>", re.I)


def _redlib_post_area(page_html: str) -> str:
    """HTML slice of the POST AREA (post title -> first comment) so sidebar,
    related posts and comment thumbnails can't leak into the harvest.
    Prefers the post_content element when the theme has one; the cut at the
    first comment is made on the tag boundary (not mid-tag)."""
    m = REDDIL_POST_TITLE_RE.search(page_html)
    if m:
        rest = page_html[m.end():]
        close = rest.find("</h1>")
        page_html = rest[close + 5:] if close != -1 else rest
    start = re.search(r'<(?:div|section)\s+class="post_content', page_html, re.I)
    if start:
        page_html = page_html[start.start():]
    for marker in ('id="comment-', 'class="comment"', '<section class="comments"'):
        idx = page_html.lower().find(marker.lower())
        if idx != -1:
            lt = page_html.rfind("<", 0, idx)
            page_html = page_html[:lt if lt != -1 else idx]
            break
    return page_html


def extract_redlib_gallery(page_html: str | None) -> list[dict]:
    """
    Harvest redd.it media from a redlib post page (post area only), then
    apply the same dedupe/best-rendition rules as extract_native_media.
    """
    if not page_html:
        return []
    return extract_native_media(_redlib_post_area(page_html))


def base_from_redlib_page(page_html: str | None, path: str) -> dict | None:
    """
    Round 12c: native base for a TEST POST when the post JSON is unavailable
    AND the post is not in the current RSS feed — title/author/body/media
    links scraped from a redlib post page (post area only). Best-effort:
    returns None for bot-challenge pages or unparseable layouts.
    """
    if not page_html:
        return None
    area = _redlib_post_area(page_html)
    tm = re.search(r"<h1[^>]*post_title[^>]*>.*?<a[^>]*>([^<]+)</a>", page_html, re.S | re.I)
    title = html_lib.unescape(tm.group(1)).strip() if tm else ""
    if not title:
        return None
    author = "unknown"
    am = re.search(r'class="post_author[^"]*"[^>]*>\s*(?:<[^>]+>\s*)?u?/?\s*([A-Za-z0-9_]{2,20})',
                   page_html, re.I)
    if am:
        author = am.group(1)
    og = (re.search(r'property="og:image"\s+content="([^"]+)"', page_html, re.I) or
          re.search(r'content="([^"]+)"\s+property="og:image"', page_html, re.I))
    return {
        "title": _clean_post_title(title),
        "author": _clean_author_name(author),
        "content_html": area,
        "thumb": og.group(1) if og else None,
        "body": clean_rss_body(area),
        "crosspost_orig_path": find_crosspost_original_path(area, path),
        "vred_id": extract_vreddit_id(area),
        "redgifs_url": extract_redgifs_url(area),
        "youtube_url": extract_youtube_url(area),
    }

async def _fetch_post_rss(session: aiohttp.ClientSession, path: str):
    """
    Round 12e: the post's OWN RSS feed (/r/<sub>/comments/<id>/.rss).
    Works for ANY post age — unlike the combined feed, which only carries
    the newest 100 entries across all subreddits. The post itself is an
    entry whose link is its permalink; comment entries are ignored by the
    caller's permalink match.
    """
    for instance in REDDIT_RSS_INSTANCES:
        url = f"{instance}{path}.rss"
        if REDDIT_FEED_TOKEN and "reddit.com" in instance:
            url += f"?feed={REDDIT_FEED_TOKEN}"
        feed = await _fetch_feed(session, url, f"post-rss {instance}")
        if feed:
            return feed
    return None
    
async def fetch_test_post_base(session: aiohttp.ClientSession, path: str,
                               label: str) -> dict | None:
    """
    Round 12c: NATIVE fallback for TEST POST mode (post JSON 403'd / no
    OAuth app). Source 1: the combined RSS feed (post must be inside the
    100-entry window — true for anything from the last day or two).
    Source 2: the redlib post page (same parallel lottery as the gallery
    enrichment). None when neither source has the post.
    """
    target_sub = extract_subreddit(path)
    target_pid = extract_post_id(path)
    feed = await fetch_combined_feed(session)
    if feed:
        # match on subreddit + post id (feed permalinks carry a slug suffix,
        # the test-post path does not)
        for entry in feed.entries:
            p = normalize_reddit_path(str(getattr(entry, "link", "")))
            if not p:
                continue
            if (extract_post_id(p) == target_pid
                    and (extract_subreddit(p) or "").lower() == (target_sub or "").lower()):
                logging.info(f"[{label}] test post found in the RSS feed — "
                             f"native base built from it.")
                return entry_to_base_data(entry)
    logging.info(f"[{label}] test post not in the combined feed window — "
                 f"trying its own RSS feed...")
    post_feed = await _fetch_post_rss(session, path)
    if post_feed:
        for entry in post_feed.entries:
            p = normalize_reddit_path(str(getattr(entry, "link", "")))
            if not p:
                continue
            if (extract_post_id(p) == target_pid
                    and (extract_subreddit(p) or "").lower() == (target_sub or "").lower()):
                logging.info(f"[{label}] test post found in its own RSS feed — "
                             f"native base built from it.")
                return entry_to_base_data(entry)
    logging.info(f"[{label}] not in its own RSS feed either — "
                 f"trying the redlib post page...")
    pages = await asyncio.gather(
        *[_fetch_redlib_post_page(session, inst, path) for inst in REDDIT_RSS_INSTANCES]
    )
    for instance, html in zip(REDDIT_RSS_INSTANCES, pages):
        base = base_from_redlib_page(html, path)
        if base:
            logging.info(f"[{label}] test post base via redlib ({instance}).")
            return base
    return None


async def _fetch_redlib_post_page(session: aiohttp.ClientSession, instance: str,
                                  path: str, timeout: int = 10) -> str | None:
    try:
        async with session.get(_with_miningtcup_token(f"{instance}{path}"),
                               headers=BROWSER_HEADERS,
                               timeout=aiohttp.ClientTimeout(total=timeout),
                               allow_redirects=True) as resp:
            if resp.status == 200 and "html" in (resp.headers.get("Content-Type") or "").lower():
                return await resp.text()
    except Exception:
        pass
    return None


async def enrich_gallery_redlib(session: aiohttp.ClientSession, path: str,
                                label: str) -> list[dict]:
    """
    Parallel best-effort redlib gallery harvest. Returns [] when no instance
    answers (caller keeps the single-thumbnail fallback).
    """
    pages = await asyncio.gather(
        *[_fetch_redlib_post_page(session, inst, path) for inst in REDDIT_RSS_INSTANCES]
    )
    for instance, html in zip(REDDIT_RSS_INSTANCES, pages):
        if not html:
            continue
        items = extract_redlib_gallery(html)
        if items:
            logging.info(f"[{label}] gallery via redlib ({instance}) — "
                         f"{len(items)} media item(s).")
            return items
    logging.info(f"[{label}] redlib gallery enrichment failed (all instances) — "
                 f"proxy/native media used instead.")
    return []


async def resolve_youtube_media(session: aiohttp.ClientSession, vid: str, is_live: bool):
    """
    Returns (media_url_or_None, thumb_url). YOUTUBE_MEDIA_EMBED tries
    seaof.glass .mp4 (1 retry for cold start). Thumbs: maxres -> hqdefault.
    """
    media_url = None
    if YOUTUBE_MEDIA_EMBED and not is_live:
        mp4 = YOUTUBE_MP4_TEMPLATE.format(video_id=vid)
        for attempt in (1, 2):
            ok, size = await media_url_ok(session, mp4, timeout=90, video=True)
            if ok:
                media_url = mp4
                logging.info(f"YouTube mp4 OK via seaof.glass ({size} bytes).")
                break
            if attempt == 1:
                logging.info("YouTube mp4 check failed — retrying once (cold transcode?).")
                await asyncio.sleep(5)
    # maxresdefault 404s for videos without 4K; hqdefault always exists.
    thumb = None
    for name in ("maxresdefault", "hqdefault", "mqdefault"):
        cand = f"https://i.ytimg.com/vi/{vid}/{name}.jpg"
        ok, _ = await media_url_ok(session, cand, timeout=10)
        if ok:
            thumb = cand
            break
    if thumb is None:
        thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    return media_url, thumb


async def fetch_yt_oembed(session: aiohttp.ClientSession, yt_url: str) -> dict | None:
    try:
        async with session.get(
            "https://www.youtube.com/oembed",
            params={"url": yt_url, "format": "json"},
            headers=dict(BROWSER_HEADERS),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# ■ POST DATA ASSEMBLY
# ---------------------------------------------------------------------------
def entry_to_base_data(entry) -> dict:
    """What we can always get from the RSS entry (native-mode base)."""
    content_html = ""
    for c in entry.get("content") or []:
        if c.get("value"):
            content_html += c["value"]
    if not content_html and entry.get("summary"):
        content_html = entry["summary"]
    thumb = None
    for m in entry.get("media_thumbnail") or []:
        if isinstance(m, dict) and (m.get("url") or m.get("href")):
            thumb = m.get("url") or m.get("href")
            break
    author = str(getattr(entry, "author", "") or "")
    if author.startswith("/u/"):
        author = author[3:]
    return {
        "title": _clean_post_title(getattr(entry, "title", "")),
        "author": _clean_author_name(author),
        "content_html": content_html,
        "thumb": thumb,
        "body": clean_rss_body(content_html),
        "crosspost_orig_path": find_crosspost_original_path(
            content_html, normalize_reddit_path(str(getattr(entry, "link", "")))),
        "vred_id": extract_vreddit_id(content_html),
        "redgifs_url": extract_redgifs_url(content_html),
        "youtube_url": extract_youtube_url(content_html),
    }


async def _dedupe_media_final_urls(session: aiohttp.ClientSession, media: list,
                                   resolve=None) -> list:
    """Round 29 (2026-09-19): drop media items that are the SAME file served
    under different wrapper URLs. Mirrors can list one photo twice — e.g.
    redditez/EmbedEZ emitted og:image redirects for BOTH content.media.0.source
    and content.media.1.source of the same single-image post (live 2026-09-19:
    crosspost 1wjv962 posted the same 1247x932 collage twice). The i.redd.it
    file-id dedupe (reddit_proxy.dedupe_proxy_media) can't see through
    redirect wrappers, so each redirect-style URL is resolved to its final
    target (HEAD, follow redirects, best-effort) and only the first item per
    final file is kept. A URL that fails to resolve is kept — a network
    hiccup must never drop media. `resolve` is injectable for offline tests;
    only redirect-style URLs are resolved (reddit CDN URLs are canonical).
    """
    if len(media) <= 1:
        return list(media)

    def _file_key(u: str):
        m2 = re.match(r"https?://(?:i|preview)\.redd\.it/([\w.-]+\.(?:jpe?g|png|gif|webp))",
                      u or "", re.I)
        if not m2:
            return None
        return re.sub(r"^.+-v\d+-", "", m2.group(1).lower())

    async def _default_resolve(url: str):
        try:
            async with session.head(url, allow_redirects=True,
                                    timeout=aiohttp.ClientTimeout(total=8),
                                    headers={"User-Agent": "Discordbot/2.0"}) as resp:
                return str(resp.url)
        except Exception:
            return None

    resolver = resolve or _default_resolve
    out = []
    seen = set()
    for m in media:
        url = m.get("url", "")
        key = _file_key(url)
        if key is None and ("/redirect" in url or "search_" in url):
            try:
                final = await resolver(url)
            except Exception:
                final = None
            if final:
                key = _file_key(final) or final
        if key is None:
            key = url
        if key in seen:
            logging.info(f"media dedupe: dropped {url[:120]!r} — same file as an "
                         f"earlier gallery item (mirror listed it twice).")
            continue
        seen.add(key)
        out.append(m)
    return out


async def resolve_post_media(session: aiohttp.ClientSession, base: dict,
                             post_json: dict | None,
                             path: str | None = None, label: str = "") -> dict:
    """
    Builds the final media list + meta for one post.
    media = [{"kind": "image"|"gif"|"video", "url": ...}, ...]
    Round-12/13 rules:
      • video posts -> the VIDEO tile ONLY (no first-frame / external thumb)
      • photo posts -> ALL photos, best rendition each (never the 140px thumb)
      • round 13 (native mode): the proxy services (redditez/vxreddit/
        embeddit) are tried FIRST — they provide every gallery photo and
        videos WITH audio; on any failure the round-12 RSS/redlib path runs
      • stats: post JSON (FULL MODE) or the proxy services (round 13 native)
      • external-preview.redd.it screenshots dropped whenever a video resolves
    """
    media: list[dict] = []
    stats = None
    crosspost = None
    op_comment = None
    body = base["body"]
    title = base["title"]
    author = base["author"]
    yt_url = base["youtube_url"]
    vid: str | None = None
    fallback_url: str | None = None
    video_poster: str | None = None

    if post_json:
        # ---- FULL MODE ----
        title = str(post_json.get("title") or title)[:400]
        author = str(post_json.get("author") or author)
        if post_json.get("selftext"):
            body = strip_html(post_json["selftext"]) or body
        stats = {"comments": post_json.get("num_comments", 0),
                 "ups": post_json.get("ups", 0)}
        if INCLUDE_OP_COMMENT:
            op_comment = extract_op_comment(post_json)
        # video id / youtube — resolved BEFORE the photo loop (round 12)
        m = post_json.get("media")
        if isinstance(m, dict) and isinstance(m.get("reddit_video"), dict):
            rv = m["reddit_video"]
            fallback_url = rv.get("fallback_url")
            if fallback_url:
                vid = extract_vreddit_id(fallback_url)
        if not vid:
            vid = base["vred_id"] or extract_vreddit_id(str(post_json.get("url") or ""))
        if not yt_url:
            yt_url = extract_youtube_url(str(post_json.get("selftext") or ""),
                                         str(post_json.get("url") or ""))
        yt_vid_early, _ = extract_youtube_id(yt_url)
        has_video = bool(vid or yt_vid_early or base.get("redgifs_url"))
        if post_json.get("crosspost_post_link"):
            crosspost = {"url": post_json["crosspost_post_link"],
                         "path": normalize_reddit_path(post_json["crosspost_post_link"])}
        # crosspost full-embed: fetch the ORIGINAL post (1 level only)
        if crosspost:
            orig_id = extract_post_id(crosspost["path"] or "")
            if orig_id:
                try:
                    # fetch_post_json already falls back internally
                    # (oauth -> feed token -> None)
                    orig = await fetch_post_json(session, orig_id, use_oauth=True)
                    if orig:
                        mm = orig.get("media_metadata") or {}
                        photos = photo_urls_from_metadata(mm)
                        g = gif_url_from_metadata(mm)
                        for u in photos[:MAX_GALLERIES * MEDIA_PER_GALLERY]:
                            media.append({"kind": "image", "url": u})
                        if g:
                            media.append({"kind": "gif", "url": g})
                        if orig.get("selftext"):
                            orig_body = strip_html(orig["selftext"])
                            if orig_body and len(orig_body) > len(body or ""):
                                body = orig_body
                        logging.info(f"crosspost: fetched original {orig_id} "
                                     f"({len(media)} media items).")
                except Exception as e:
                    logging.info(f"crosspost original fetch failed: {e}")
        # own media
        mm = post_json.get("media_metadata") or {}
        preview_images = (post_json.get("preview") or {}).get("images") or []
        # ordered by preview.images; fall back to metadata order if absent
        ordered_keys = [p.get("id") for p in preview_images if isinstance(p, dict) and p.get("id") in mm]
        if ordered_keys:
            for key in ordered_keys:
                meta = mm[key]
                if not isinstance(meta, dict):
                    continue
                if meta.get("is_gif") and not any(x["kind"] == "gif" for x in media):
                    g = (meta.get("u") or meta.get("s") or {}).get("url")
                    if g:
                        media.append({"kind": "gif", "url": g})
                    continue
                if meta.get("is_video"):
                    s = meta.get("s")
                    if video_poster is None and isinstance(s, dict) and s.get("url"):
                        video_poster = s["url"]
                    continue
                url = None
                for variant in ("p", "s"):
                    v = meta.get(variant)
                    if isinstance(v, dict) and v.get("url"):
                        url = v["url"]
                        break
                # external-preview screenshots belong to a video, not the gallery
                if url and not (has_video and _is_external_preview(url)):
                    media.append({"kind": "image", "url": url})
        else:
            for u in photo_urls_from_metadata(mm):
                if not (has_video and _is_external_preview(u)):
                    media.append({"kind": "image", "url": u})
            g = gif_url_from_metadata(mm)
            if g:
                media.append({"kind": "gif", "url": g})
            for meta in mm.values():
                if isinstance(meta, dict) and meta.get("is_video"):
                    s = meta.get("s")
                    if isinstance(s, dict) and s.get("url"):
                        video_poster = s["url"]
                        break
    else:
        # ---- NATIVE MODE (RSS only) ----
        vid = base["vred_id"]
        yt_vid_early, _ = extract_youtube_id(yt_url)
        has_video = bool(vid or yt_vid_early or base.get("redgifs_url"))

    # ---- round 14: crossposts fetch the ORIGINAL post's media ------------
    # A crosspost's own pages carry little (vxreddit shows one thumbnail,
    # the RSS content no media), so media + stats come from the ORIGINAL
    # post; the card keeps the crosspost URL + the "🔁 Crosspost of" line.
    fetch_path = path
    if not post_json and base.get("crosspost_orig_path"):
        fetch_path = base["crosspost_orig_path"]
        crosspost = {"url": f"https://www.reddit.com{fetch_path}", "path": fetch_path}
        logging.info(f"[{label or 'native'}] crosspost — media/stats from the "
                     f"original post {fetch_path}")

    # Resolve archive crossposts BEFORE asking proxies for media. Keep the
    # crosspost thread/title/author on the card; media comes from its original.
    arctic = None
    if not post_json and fetch_path:
        arctic = await fetch_arctic_post(session, extract_post_id(fetch_path) or "", label)
        original = arctic_crosspost_orig(arctic)
        if original and not crosspost:
            original_path = original.get("permalink")
            if (isinstance(original_path, str)
                    and re.fullmatch(r"/r/[^/]+/comments/[a-z0-9]+/[^?#]*", original_path)):
                fetch_path = original_path
                crosspost = {"url": f"https://www.reddit.com{fetch_path}", "path": fetch_path}
                arctic = original
                logging.info(f"[{label}] crosspost detected via Arctic Shift: {fetch_path}")
    arctic_items = arctic_gallery_items(arctic)
    a_vid, a_fb = arctic_video_info(arctic)
    arctic_is_video = bool(a_vid)
    if a_vid:
        vid, fallback_url = a_vid, a_fb
        has_video = True
    # Structured text wins over flattened og descriptions. Archive text is
    # used only when the RSS/redlib body is empty.
    if arctic and not (body or "").strip():
        body = _clean_plain_body(arctic.get("selftext"))

    # ---- round 13: PROXY media services (native mode only) ---------------
    # redditez.com -> vxreddit.com -> embeddit.deltandy.me, in that priority
    # order (see testing area/reddit_proxy.py). The winning service's own
    # URLs are used verbatim: full-res photos, EVERY gallery photo (20 ->
    # 2 containers), videos WITH audio, GIFs. The per-run warm-up
    # (proxy_health.json) skips services already proven dead this run.
    # Round 14: the redlib post-page harvest runs IN PARALLEL with the proxy
    # fetch (it is the only source that lists EVERY gallery item incl. GIFs
    # in order — the redditez og tags omit GIFs). Skipped for real video
    # posts (the DASH chain handles those). Any failure here simply falls
    # through to the round-12 native path.
    proxy_media_used = False
    redlib_items: list[dict] = []
    redlib_done = False
    if not post_json and PROXY_MEDIA and reddit_proxy is not None and fetch_path:
        tasks = [reddit_proxy.fetch_proxy_post(session, fetch_path,
                                               label=label, health=_proxy_health,
                                               need_video=bool(has_video))]
        if not (base.get("vred_id") or base.get("redgifs_url")):
            tasks.append(enrich_gallery_redlib(session, fetch_path, label or "gallery"))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        redlib_done = len(tasks) > 1
        if redlib_done and isinstance(results[1], list):
            redlib_items = results[1]
        proxy = results[0] if isinstance(results[0], dict) else None
        if not proxy and isinstance(results[0], Exception):
            logging.info(f"[{label or 'proxy'}] proxy fetch error: {results[0]}")
        if proxy:
            proxy_video = next((m for m in proxy["media"]
                                if m["kind"] == "video"), None)
            proxy_images = [m for m in proxy["media"]
                            if m["kind"] != "video"]
            video_ok = False
            if proxy_video:
                # range-check the muxed mp4 (has audio)
                video_ok, size = await media_url_ok(session, proxy_video["url"],
                                                    timeout=90, video=True)
                if video_ok:
                    logging.info(f"[{label or 'proxy'}] proxy video OK via "
                                 f"{proxy['service']} ({size} bytes).")
                else:
                    logging.info(f"[{label or 'proxy'}] {proxy['service']} video URL "
                                 f"failed the range check — native video chain will run.")

            def _use_proxy_text():
                nonlocal stats, body
                stats = proxy.get("stats") or stats
                if proxy.get("body") and not (body or "").strip():
                    body = proxy["body"][:MAX_BODY_CHARS]

            video_dead = bool(proxy_video) and not video_ok
            if proxy_video and video_ok:
                # video post: the proxy's muxed mp4 tile ONLY (never a dup
                # first-frame thumbnail)
                media = [proxy_video]
                _use_proxy_text()
                proxy_media_used = True
                logging.info(f"[{label or 'proxy'}] card media via {proxy['service']} "
                             f"— 1 video tile.")
            elif (not video_dead and not arctic_is_video and arctic_items
                  and len(arctic_items) >= max(1, len(proxy_images))):
                media = [dict(item) for item in arctic_items]
                _use_proxy_text()
                proxy_media_used = True
            elif (not video_dead and not arctic_is_video and redlib_items
                  and len(redlib_items) >= max(1, len(proxy_images))):
                # image post: the COMPLETE ordered gallery from the redlib
                # harvest — it lists EVERY item incl. GIFs (the redditez og
                # tags omit GIFs); body/stats still come from the proxy
                media = [dict(x) for x in redlib_items
                         if not (has_video and _is_external_preview(x["url"]))]
                _use_proxy_text()
                proxy_media_used = True
                logging.info(f"[{label or 'proxy'}] card media via redlib harvest "
                             f"— {len(media)} item(s) (body/stats via "
                             f"{proxy['service']}).")
            elif not video_dead and not arctic_is_video and proxy_images:
                media = proxy_images
                _use_proxy_text()
                proxy_media_used = True
                logging.info(f"[{label or 'proxy'}] card media via {proxy['service']} "
                             f"— {len(media)} item(s).")
            else:
                # no usable proxy media (a dead proxy video counts too — the
                # native DASH chain has audio)
                logging.info(f"[{label or 'proxy'}] no usable proxy media — "
                             f"falling back to the native RSS path.")
        # round 14: redditez og pages often lack the stats line (they live in
        # the oembed, not the og tags) — backfill from the Embeddit JSON
        # (one extra request, ~1 s, not bot-gated)
        if proxy_media_used and stats is None:
            try:
                stats = await reddit_proxy.fetch_embeddit_stats(session, fetch_path,
                                                                label or "stats")
                if stats:
                    logging.info(f"[{label or 'stats'}] stats via embeddit — {stats}")
            except Exception as e:
                logging.info(f"[{label or 'stats'}] embeddit stats fetch failed: {e}")

    if stats is None and arctic and all(isinstance(arctic.get(key), int)
                                      for key in ("ups", "num_comments")):
        stats = {"ups": arctic["ups"], "comments": arctic["num_comments"], "archived": True}

    # ---- VIDEO FIRST (round 12): the tile is the video, never a dup thumb
    video_url = None
    if (not proxy_media_used and vid
            and not any(x["kind"] in ("video", "gif") for x in media)):
        video_url = await resolve_video_url(session, vid, fallback_url)

    if video_url:
        media.append({"kind": "video", "url": video_url})
        # drop the video's own screenshot (external-preview) if one slipped in
        media = [x for x in media
                 if x["kind"] in ("video", "gif") or not _is_external_preview(x["url"])]
    else:
        if proxy_media_used and any(x["kind"] == "video" for x in media):
            # round 13: the proxy video tile only (no first-frame / poster dup)
            media = [x for x in media if x["kind"] in ("video", "gif")]
        elif not post_json:
            # ---- NATIVE MODE media (no reddit video resolved) ----
            if base.get("redgifs_url"):
                ok, _ = await media_url_ok(session, base["redgifs_url"], timeout=30, video=True)
                if ok:
                    media.append({"kind": "video", "url": base["redgifs_url"]})
            if (not any(x["kind"] == "video" for x in media)
                    and not proxy_media_used):
                # round 14: skipped entirely when the proxy path already
                # filled the gallery — a shorter redlib/RSS list must never
                # downgrade it. The `not media` fallbacks below still run
                # when the winning branch produced an empty list.
                if arctic_items:
                    media = [dict(item) for item in arctic_items]
                elif redlib_items:
                    # round 14: the COMPLETE ordered gallery (incl. GIFs,
                    # full-res i.redd.it) from the parallel redlib harvest —
                    # takes priority over the RSS content (which omits
                    # gallery items + GIFs)
                    media = [dict(x) for x in redlib_items
                             if not (has_video and _is_external_preview(x["url"]))]
                    logging.info(f"[{label or 'native'}] card media via redlib "
                                 f"harvest — {len(media)} item(s).")
                else:
                    # ALL photos from the RSS content, best rendition each
                    native_items = extract_native_media(base.get("content_html"))
                    logging.info(f"[{label or 'native'}] native media in RSS content: "
                                 f"{len(native_items)} url(s).")
                    for p in native_items:
                        if has_video and _is_external_preview(p["url"]):
                            continue  # it's the video's screenshot
                        if p["kind"] == "image":
                            swap = i_reddit_swap(p["url"])
                            if swap:
                                ok, _ = await media_url_ok(session, swap, timeout=15)
                                if ok:
                                    p["url"] = swap
                                    logging.info(f"photo via i.redd.it full-res swap ({swap}).")
                        media.append(p)
            if not media and not redlib_done and fetch_path:
                # (legacy, e.g. video posts where the harvest was skipped):
                # best-effort redlib harvest of the post page (parallel,
                # ~10s worst case, no retry).
                media = await enrich_gallery_redlib(session, fetch_path, label or "gallery")
            if not media and base.get("thumb"):
                # legacy fallback: the feed's single thumbnail
                thumb = base["thumb"]
                if ".gif" in thumb:
                    media.append({"kind": "gif", "url": thumb})
                else:
                    swap = i_reddit_swap(thumb)
                    if swap:
                        ok, _ = await media_url_ok(session, swap, timeout=15)
                        if ok:
                            media.append({"kind": "image", "url": swap})
                        else:
                            media.append({"kind": "image", "url": thumb})
                    else:
                        media.append({"kind": "image", "url": thumb})
        elif video_poster and not media:
            # FULL MODE, video chain failed -> first frame (never a silent video)
            media.append({"kind": "image", "url": video_poster})
            logging.info("video unavailable -> using first-frame poster + button.")

    # youtube tile / thumbnail (both modes)
    yt_vid, yt_live = extract_youtube_id(yt_url)
    if yt_vid:
        yt_media_url, yt_thumb = await resolve_youtube_media(session, yt_vid, yt_live)
        if yt_media_url and not any(x["kind"] == "video" for x in media):
            media.append({"kind": "video", "url": yt_media_url})
        if not any(x["kind"] == "video" for x in media):
            # thumbnail + button (round-12 default): the YT thumb is the
            # canonical preview — replace external screenshots, add if absent
            media = [x for x in media if not _is_external_preview(x["url"])]
            if not any(x["kind"] == "image" for x in media):
                media.append({"kind": "image", "url": yt_thumb})

    body = _drop_youtube_line(body, yt_url)
    # round 29 (2026-09-19): a crosspost with an empty selftext gets the
    # mirror's crosspost NOTICE as its "body" (e.g. "Original PostPosted in
    # r/AnantaStation" + the original's title, glued — redditez's og:
    # description, live 1wjv962). Strip it; when no permalink was available
    # (RSS text had no 'crosspost' link, Arctic not captured yet), the
    # notice's subreddit still yields the "🔁 Crosspost of" line.
    body, _notice_sub = _crosspost_notice_clean(body, title)
    if not crosspost and _notice_sub:
        crosspost = {"url": "", "path": "", "subreddit": _notice_sub}
        logging.info(f"[{label or 'native'}] crosspost detected via the mirror's "
                     f"notice (original subreddit r/{_notice_sub}) — notice text "
                     f"removed from the body.")
    # round 29 (2026-09-19): mirrors can list ONE photo under two different
    # wrapper URLs (two og:image redirects resolving to the same file) —
    # collapse to one tile. Applies to EVERY source (proxy/redlib/RSS/Arctic).
    media = await _dedupe_media_final_urls(session, media)
    media = media[:MAX_GALLERIES * MEDIA_PER_GALLERY]
    return {
        "title": title,
        "author": _clean_author_name(author),
        "body": body[:MAX_BODY_CHARS] + ("…" if len(body) > MAX_BODY_CHARS else ""),
        "media": media,
        "stats": stats,
        "crosspost": crosspost,
        "op_comment": op_comment,
        "youtube_url": yt_url,
        "youtube_id": yt_vid,
        "youtube_live": yt_live,
        "full_mode": bool(post_json),
    }


# ---------------------------------------------------------------------------
# ■ CARD BUILDING (components v2)
# ---------------------------------------------------------------------------
def build_action_row(reddit_url: str, youtube_url: str | None) -> dict:
    buttons = [
        {"type": 2, "style": 5, "label": "Read Post", "url": reddit_url, "emoji": READ_POST_EMOJI},
    ]
    if youtube_url:
        buttons.append({"type": 2, "style": 5, "label": "YouTube", "url": youtube_url,
                        "emoji": YOUTUBE_EMOJI})
    for btn in STATIC_BUTTONS:
        b = {"type": 2, "style": 5, "label": btn["label"], "url": btn["url"]}
        if btn.get("emoji"):
            b["emoji"] = btn["emoji"]
        buttons.append(b)
    return {"type": 1, "components": buttons[:5]}


def build_v3_payload(subreddit: str, data: dict, reddit_url: str, posted_ts: int) -> dict:
    """
    <=10 media items -> single container (V2 look):
        header / body / divider / gallery / stats / divider / buttons
    11-20 media items -> two containers:
        container 1: header / body / divider / gallery(10)
        container 2: divider / gallery(rest) / stats / divider / buttons
    (Discord: 10 items per gallery, 10 components per container, 40 total.)
    """
    header = f"### [{_clean_post_title(data['title'])}]({reddit_url})\n*by {_clean_author_name(data['author'])} in r/{subreddit}*"
    if data["crosspost"]:
        _cp = data["crosspost"]
        _cp_url = _cp.get("url") or ""
        # round 29 (2026-09-19): a mirror can hand the permalink back already
        # wrapped as a markdown link "[url](url)" — the card link needs the
        # BARE url (a nested link breaks Discord's markdown).
        _m = re.search(r"https?://[^\s\)\]]+", _cp_url)
        if _m:
            _cp_url = _m.group(0)
        _cp_sub = re.search(r"/r/([^/]+)/", _cp.get("path") or "")
        if not _cp_sub:
            _cp_sub = re.search(r"/r/([^/]+)/", _cp_url)
        if not _cp_sub and _cp.get("subreddit"):
            # round 29: notice-only detection (no permalink available anywhere)
            _sub = _cp["subreddit"]
            header += (f"\n*🔁 Crosspost of [r/{_sub}]"
                       f"(https://www.reddit.com/r/{_sub}/) Subreddit*")
        elif _cp_sub and _cp_url:
            header += (f"\n*🔁 Crosspost of [{_cp_sub.group(1)}]"
                       f"({_cp_url}) Subreddit*")
        elif _cp_sub:
            header += (f"\n*🔁 Crosspost of [r/{_cp_sub.group(1)}]"
                       f"(https://www.reddit.com/r/{_cp_sub.group(1)}/) Subreddit*")
        else:
            header += f"\n*🔁 Crosspost of {_cp_url or 'the original post'}*"

    media = data["media"]
    stats = data["stats"]
    ts_suffix = f"   •   🕐 <t:{posted_ts}:f>"
    if stats:
        stats_line = f"-# 💬 {stats['comments']} 👍 {stats['ups']}{ts_suffix}"
        if stats.get("archived"):
            stats_line += " · archived counts"
    else:
        stats_line = f"-# 🕐 <t:{posted_ts}:f>"

    row = build_action_row(reddit_url, data["youtube_url"])

    # Char budget: Discord caps TOTAL text at 4000 across all components.
    op_line = op_comment_text(data["op_comment"]) if data.get("op_comment") else ""
    body_budget = max(300, 3800 - len(header) - len(op_line) - len(stats_line))
    body_out = data["body"][:body_budget]
    if len(data["body"]) > body_budget:
        body_out = body_out.rsplit(" ", 1)[0].rstrip() + "…"

    def gallery(items: list) -> dict:
        return {"type": 12, "items": [{"media": {"url": m["url"]}} for m in items]}

    if len(media) > MEDIA_PER_GALLERY:
        first, second = media[:MEDIA_PER_GALLERY], media[MEDIA_PER_GALLERY:]
        container1 = {"type": 17, "accent_color": 16729344, "components": [
            {"type": 10, "content": header},
        ]}
        if body_out:
            container1["components"].append({"type": 10, "content": body_out})
        if op_line:
            container1["components"].append({"type": 10, "content": op_line})
        container1["components"].append({"type": 14, "divider": True, "spacing": 1})
        container1["components"].append(gallery(first))
        container2 = {"type": 17, "accent_color": 16729344, "components": [
            {"type": 14, "divider": True, "spacing": 1},
            gallery(second),
            {"type": 10, "content": stats_line},
            {"type": 14, "divider": True, "spacing": 1},
            row,
        ]}
        return {"flags": IS_COMPONENTS_V2,
                "components": [container1, container2]}

    inner = [{"type": 10, "content": header}]
    if body_out:
        inner.append({"type": 10, "content": body_out})
    if op_line:
        inner.append({"type": 10, "content": op_line})
    if media:
        inner.append({"type": 14, "divider": True, "spacing": 1})
        inner.append(gallery(media))
    inner.append({"type": 10, "content": stats_line})
    inner.append({"type": 14, "divider": True, "spacing": 1})
    inner.append(row)
    return {"flags": IS_COMPONENTS_V2,
            "components": [{"type": 17, "accent_color": 16729344, "components": inner}]}


# ---------------------------------------------------------------------------
# ■ DISCOHOOK SHARE-LINK PREVIEW (optional, keyless, round 12)
# ---------------------------------------------------------------------------
async def create_discohook_share(session: aiohttp.ClientSession, payload: dict,
                                 label: str) -> str | None:
    """
    Creates a public Discohook share link (discohook.app) that renders the
    EXACT card we just posted — handy for verifying in the browser before
    promoting. Keyless public API (POST /api/v1/share), best-effort: any
    failure only logs, posting is never blocked.

    PRIVACY: the share data contains ONLY the public card payload. NO
    `targets` are sent, so the webhook URL (and any token) never leaves this
    repo. Share IDs are reused after the TTL, so treat links as 7-day temp.
    """
    if not DISCOHOOK_PREVIEW:
        return None
    query_data = {"version": "d2", "messages": [{"data": payload}]}
    try:
        async with session.post(
            DISCOHOOK_SHARE_ENDPOINT,
            json={"data": query_data, "ttl": DISCOHOOK_SHARE_TTL},
            headers={"User-Agent": DISCOHOOK_USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                url = data.get("url")
                logging.info(f"Discohook preview for {label}: {url}")
                return url
            logging.warning(f"Discohook share HTTP {resp.status} for {label} — preview skipped.")
    except Exception as e:
        logging.warning(f"Discohook share error for {label} — preview skipped: {e}")
    return None


def test_post_entries(now: float) -> list:
    """
    TEST_POST_ID=<subreddit>/<post_id> -> one synthetic entry so a specific
    post can be rebuilt on demand (workflow_dispatch input `test_post`).
    Bypasses the feed and the dedup cache on purpose (re-testing is the
    point). Data source: post JSON when reachable (FULL MODE); otherwise
    the RSS feed entry / redlib post page (round 12c native fallback).
    """
    parts = TEST_POST_ID.split("/", 1)
    sub_name, pid = (parts + [""])[:2] if len(parts) < 2 else parts
    sub = _subreddit_by_name(sub_name) if sub_name else None
    if not sub or not pid:
        logging.error(f"TEST_POST_ID: '{TEST_POST_ID}' — use <subreddit>/<post_id>, "
                      f"e.g. AnantaLeaks/1wgvcz7 (subreddit must be in SUBREDDITS).")
        return []
    path = f"/r/{sub}/comments/{pid}/"
    return [(sub, path, f"{sub}_{pid}", now, now, None)]


# ---------------------------------------------------------------------------
# ■ MAIN
# ---------------------------------------------------------------------------
async def main():
    if not SUBREDDITS:
        logging.error("No subreddits configured in SUBREDDITS environment variable.")
        return
    if not REDDIT_FEED_TOKEN:
        logging.warning("REDDIT_FEED_TOKEN not set — running anonymously. The combined feed "
                        "(1 request/run) usually fits the ~1 req/min limit, but add your feed "
                        "token (old.reddit.com -> Preferences -> Feeds) as REDDIT_FEED_TOKEN "
                        "to be bulletproof. (V3 also tries it on .json for FULL MODE.)")
    if not (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET):
        logging.info("No Reddit OAuth app secrets — FULL MODE may still work via the feed "
                     "token on .json (tested once per run); otherwise cards use native RSS data.")

    posted = load_posted()
    pending = load_pending()
    is_first_run = len(posted) == 0
    now = time.time()

    use_oauth = bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)

    async with aiohttp.ClientSession() as session:
        # ---- round 13: proxy warm-up (writes proxy_health.json) ----------
        global _proxy_health
        if PROXY_MEDIA and reddit_proxy is not None:
            _proxy_health = await reddit_proxy.proxy_warmup(session)
        else:
            logging.info("Proxy media off (PROXY_MEDIA=0 or module missing) — "
                         "native media only.")

        # ---- TEST POST mode (round 12): rebuild one specific post --------
        if TEST_POST_ID:
            logging.info(f"TEST POST mode: {TEST_POST_ID} (dry_run={DRY_RUN}) — "
                         f"feed + dedup cache bypassed on purpose.")
            new_posts = test_post_entries(now)
        else:
            # ---- PRIMARY: one combined request for all subreddits ---------
            combined = await fetch_combined_feed(session)
            new_posts = []  # (subreddit, path, unique_key, published_ts, activity_ts, entry)

            def collect(entries, known_subreddit: str | None):
                for entry in entries:
                    raw_link = str(getattr(entry, "link", ""))
                    path = normalize_reddit_path(raw_link)
                    if not path:
                        continue
                    sub = known_subreddit or _subreddit_by_name(extract_subreddit(path))
                    if not sub:
                        continue
                    post_id = extract_post_id(path)
                    if not post_id:
                        continue
                    unique_key = f"{sub}_{post_id}"
                    if unique_key in posted:
                        continue
                    published_parsed = entry.get("published_parsed")
                    updated_parsed = entry.get("updated_parsed")
                    published_ts = time.mktime(published_parsed) if published_parsed else now
                    updated_ts = time.mktime(updated_parsed) if updated_parsed else published_ts
                    activity_ts = max(published_ts, updated_ts)
                    if not is_first_run and (now - activity_ts > MAX_AGE_SECONDS):
                        continue
                    new_posts.append((sub, path, unique_key, published_ts, activity_ts, entry))

            if combined and combined.entries:
                entries = [combined.entries[0]] if is_first_run else combined.entries
                collect(entries, None)
            else:
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
                    collect(entries, subreddit)

            # ---- round 17: Arctic Shift search backup ---------------------
            # Subreddits RSS left EMPTY (missing from the combined 100-entry
            # feed, dead/bot-walled per-sub feeds, quiet subs) are re-checked
            # against the Arctic Shift archive search. Archive posts are
            # wrapped as feedparser-style entries and run through the SAME
            # collect() above — dedup cache + 48h freshness window apply
            # unchanged; crossposts found in the archive get the original
            # post's media via the existing native crosspost path. Soft-fail:
            # any search error just leaves that sub at zero.
            covered_subs = {p[0] for p in new_posts}
            for sub in SUBREDDITS:
                if sub in covered_subs:
                    continue
                try:
                    arc_posts = await fetch_arctic_subreddit_posts(
                        session, sub, after_epoch=now - MAX_AGE_SECONDS,
                        label=sub)
                except Exception:
                    arc_posts = []
                if not arc_posts:
                    continue
                logging.info(f"[arctic {sub}] RSS had no new posts — trying "
                             f"{len(arc_posts)} post(s) from the archive.")
                arc_entries = [_ArcticEntry(p) for p in arc_posts]
                if is_first_run:
                    arc_entries = arc_entries[:1]  # anti-flood (same as RSS)
                collect(arc_entries, sub)

        total_found = len(new_posts)
        if total_found == 0:
            logging.info("No new Reddit posts to post.")
            save_posted(posted)
            save_pending(pending)
            return

        logging.info(f"Found {total_found} new Reddit posts. Building V3 cards...")

        # newest first per sub for stable ordering
        new_posts.sort(key=lambda p: p[3])

        for subreddit, path, unique_key, published_ts, activity_ts, entry in new_posts:
            webhook_url = get_webhook_for_subreddit(subreddit)
            if not webhook_url:
                logging.error(f"No webhook configured for r/{subreddit}. Skipping {unique_key}.")
                continue

            reddit_url = f"https://www.reddit.com{path}"

            # ---- round 30 (2026-09-19): pending-post recheck throttle ----
            # A post an earlier run skipped (removed/deleted, still pending
            # approval, media not filled yet) is re-verified at most once
            # every PENDING_RECHECK_SECONDS — not every 5-minute run. When it
            # IS due, the full pipeline below runs unchanged, so a
            # newly-approved / restored / media-complete post is caught and
            # posted on its next due run. TEST POST + DRY RUN bypass this
            # (manual tools must always exercise the full pipeline).
            if (not TEST_POST_ID and not DRY_RUN
                    and unique_key in pending
                    and not pending_due(pending, unique_key, now)):
                _pend = pending[unique_key]
                _due_in = int(PENDING_RECHECK_SECONDS
                              - (now - float(_pend.get("last_checked") or 0)))
                logging.info(f"[{unique_key}] pending ({_pend.get('reason')}) — "
                             f"skipping recheck, due again in ~{_due_in}s.")
                continue

            if entry is not None:
                base = entry_to_base_data(entry)
                post_json = await fetch_post_json(session, extract_post_id(path) or "",
                                                  use_oauth=use_oauth)
            else:
                # TEST POST mode: no RSS entry (feed bypassed on purpose).
                # Try the post JSON (FULL MODE); when JSON is unavailable
                # (native mode), build the base from the RSS feed entry or
                # the redlib post page (round 12c).
                post_json = await fetch_post_json(session, extract_post_id(path) or "",
                                                  use_oauth=use_oauth)
                if post_json:
                    st = str(post_json.get("selftext") or "")
                    base = {
                        "title": _clean_post_title(post_json.get("title")),
                        "author": _clean_author_name(post_json.get("author")),
                        "content_html": st,
                        "thumb": None,
                        "body": strip_html(st),
                        "crosspost_orig_path": normalize_reddit_path(
                            str(post_json.get("crosspost_post_link") or "")),
                        "vred_id": extract_vreddit_id(st) or extract_vreddit_id(str(post_json.get("url") or "")),
                        "redgifs_url": extract_redgifs_url(st),
                        "youtube_url": extract_youtube_url(st, str(post_json.get("url") or "")),
                    }
                else:
                    base = await fetch_test_post_base(session, path, TEST_POST_ID)
                    if not base and PROXY_MEDIA and reddit_proxy is not None:
                        # round 13: build a minimal base from a proxy service
                        # (title/author/body) so the test post still works
                        # when no RSS/redlib source has the post — media is
                        # then resolved from the same service in
                        # resolve_post_media.
                        proxy = await reddit_proxy.fetch_proxy_post(session, path,
                                                                    label=TEST_POST_ID,
                                                                    health=_proxy_health)
                        if proxy and proxy.get("title"):
                            base = {
                                "title": _clean_post_title(proxy["title"]),
                                "author": _clean_author_name(proxy.get("author")),
                                "content_html": proxy.get("body") or "",
                                "thumb": None,
                                "body": proxy.get("body") or "",
                                "crosspost_orig_path": find_crosspost_original_path(
                                    proxy.get("body") or "", path),
                                # the proxy body can carry the post's video
                                # link (e.g. a crosspost of a video shows the
                                # original's v.redd.it URL)
                                "vred_id": extract_vreddit_id(proxy.get("body") or ""),
                                "redgifs_url": extract_redgifs_url(proxy.get("body") or ""),
                                "youtube_url": extract_youtube_url(proxy.get("body") or ""),
                            }
                            logging.info(f"[{TEST_POST_ID}] test post base built from "
                                         f"proxy service {proxy['service']}.")
                    if not base:
                        logging.error(f"TEST POST {TEST_POST_ID}: post JSON unavailable "
                                      f"(native mode), and the post is neither in the "
                                      f"combined feed (100-entry window), its own RSS "
                                      f"feed, any redlib instance, nor any proxy "
                                      f"service (redditez/vxreddit/embeddit) — cannot "
                                      f"build the test post. Add REDDIT_CLIENT_ID/"
                                      f"SECRET secrets for reliable testing.")
                        continue

            # ---- round 18: skip soft-removed / deleted / pending-approval
            # posts. The archive (round 17) and occasionally the RSS feed
            # still carries posts the moderators removed or the author
            # deleted — their pages show a removal notice instead of the
            # real content. They are NOT posted and NOT added to the dedup
            # cache: if the post is approved later it surfaces again and
            # posts normally. Explicit TEST POST rebuilds are unaffected.
            if not TEST_POST_ID:
                _r_title = str((post_json or {}).get("title") or base.get("title") or "")
                _r_body = (strip_html(str((post_json or {}).get("selftext") or ""))
                           or str(base.get("body") or ""))
                _removed = removed_post_reason(_r_title, _r_body)
                if _removed:
                    mark_pending(pending, unique_key, _removed, now)
                    logging.info(f"[{unique_key}] post appears removed/deleted "
                                 f"({_removed}) — skipping, not cached (will post "
                                 f"once approved).")
                    continue

            # ---- round 20: liveness gate for archive-sourced posts ----
            # The Arctic archive can carry posts that are no longer live on
            # reddit (removed / deleted / still pending approval). Verify a
            # live source (proxy chain, then redlib) can actually retrieve
            # the post before posting it; otherwise skip + don't cache.
            if (not TEST_POST_ID and entry is not None
                    and isinstance(entry, _ArcticEntry)):
                _live, _why = await verify_archive_post_live(session, path,
                                                             label=unique_key)
                if not _live:
                    mark_pending(pending, unique_key,
                                 "removal_notice" if "removal notice" in _why
                                 else "sources_down" if "all live sources are down" in _why
                                 else "not_live", now)
                    logging.info(f"[{unique_key}] archive post not verified "
                                 f"live ({_why}) — skipping, not cached (will "
                                 f"post once approved/restored).")
                    continue

            try:
                data = await resolve_post_media(session, base, post_json,
                                                path=path, label=unique_key)
                # ---- round 23 (2026-09-18): Arctic media-hint gate ------
                # Arctic fills gallery_data / media_metadata ASYNCHRONOUSLY
                # after capture, so a brand-new media post can be archived
                # with its text but not its media for a while. If the record
                # positively says the post has media (see _arctic_media_hint)
                # but no source served any this run, never post a media-less
                # card (1wj38fc: posted once without images, then cached
                # forever). Skip + don't cache: the next run retries, and
                # once the media is available (Arctic fields filled, or a
                # proxy renders the og: tags) the full gallery posts. The
                # 48h freshness window bounds the retries.
                if (not TEST_POST_ID and not DRY_RUN and post_json is None
                        and isinstance(entry, _ArcticEntry)
                        and not data["media"]
                        and _arctic_media_hint(getattr(entry, "_arctic_post", None))):
                    mark_pending(pending, unique_key, "media_wait", now)
                    logging.info(f"[{unique_key}] archive record says this post "
                                 f"has media (gallery/media_metadata) but no "
                                 f"source served any yet (Arctic fills those "
                                 f"asynchronously) — skipping, not cached "
                                 f"(retries next run).")
                    continue
                # Round 25: known partial archive galleries wait without caching.
                # Unknown counts cannot gate; video cards intentionally use one tile.
                if (not TEST_POST_ID and not DRY_RUN and post_json is None
                        and isinstance(entry, _ArcticEntry)
                        and data["media"]
                        and not any(m["kind"] == "video" for m in data["media"])):
                    _arctic_n = _arctic_media_count(getattr(entry, "_arctic_post", None))
                    if _arctic_n > len(data["media"]):
                        mark_pending(pending, unique_key, "partial_gallery", now)
                        logging.info(f"[{unique_key}] archive record lists "
                                     f"{_arctic_n} gallery items but the sources "
                                     f"served only {len(data['media'])} this run — "
                                     f"skipping, not cached (retries next run).")
                        continue
                posted_ts = int(max(published_ts, activity_ts))
                payload = build_v3_payload(subreddit, data, reddit_url, posted_ts)

                if DRY_RUN:
                    kinds = ",".join(sorted({m["kind"] for m in data["media"]})) or "text"
                    mode = "full" if data["full_mode"] else "native"
                    logging.info(f"DRY RUN (Discord NOT touched): {unique_key} "
                                 f"(media={kinds} | {mode} | {len(data['media'])} item(s))")
                    logging.info(f"DRY RUN payload for {unique_key}:\n"
                                 f"{json.dumps(payload, indent=2, ensure_ascii=False)}")
                    if data.get("youtube_url") and YOUTUBE_LINK_MESSAGE:
                        logging.info(f"DRY RUN 2nd message for {unique_key} "
                                     f"(YouTube link only): {data['youtube_url']}")
                    continue

                target_url = f"{webhook_url}?with_components=true"
                async with session.post(target_url, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status in (200, 204):
                        posted.add(unique_key)
                        pending.pop(unique_key, None)  # posted — no longer pending
                        kinds = ",".join(sorted({m["kind"] for m in data["media"]})) or "text"
                        mode = "full" if data["full_mode"] else "native"
                        logging.info(f"Reddit V3 Posted: {unique_key} (media={kinds} | {mode} | "
                                     f"{len(data['media'])} item(s))")
                        await create_discohook_share(session, payload, unique_key)
                        # round 13: YouTube posts get a SECOND, plain message
                        # containing ONLY the YouTube link (Discord shows the
                        # official preview for a bare link). It waits for the
                        # card above to land first; the card's own YouTube
                        # thumb + animated button stay as they are.
                        if data.get("youtube_url") and YOUTUBE_LINK_MESSAGE:
                            try:
                                async with session.post(
                                    webhook_url,
                                    json={"content": data["youtube_url"]},
                                    timeout=aiohttp.ClientTimeout(total=15),
                                ) as yt_resp:
                                    if yt_resp.status in (200, 204):
                                        logging.info(f"YouTube link message posted: "
                                                     f"{data['youtube_url']}")
                                    else:
                                        logging.error(f"YouTube link message "
                                                      f"HTTP {yt_resp.status}: "
                                                      f"{(await yt_resp.text())[:200]}")
                            except Exception as yt_e:
                                logging.error(f"YouTube link message failed: {yt_e}")
                            await asyncio.sleep(1.0)
                        await asyncio.sleep(1.5)
                    else:
                        body = await resp.text()
                        logging.error(f"Discord error {resp.status} for {unique_key}: {body}")
            except Exception as e:
                logging.error(f"Failed building/posting {unique_key}: {e}")

    if DRY_RUN:
        logging.info("DRY RUN finished: cache NOT saved, Discord NOT touched.")
    else:
        save_posted(posted)
        save_pending(pending)
        logging.info("Reddit V3 Monitor execution finished.")


if __name__ == "__main__":
    asyncio.run(main())
