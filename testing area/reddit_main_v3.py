# ---------------------------------------------------------------------------
# ■ Reddit RSS Feed Monitor — V3 (Components V2 rich card — NATIVE media + proxy services)
# ---------------------------------------------------------------------------
# Same monitoring as V1/V2 (combined feed, feed token, 429 retries, mod-queue
# safe 48h window, per-channel webhooks, dedup cache, auto-commit), but the
# card media comes from REDDIT'S OWN URLs and keyless public proxy services
# (vxreddit, redditez/EmbedEZ, Embeddit) — no EmbedEZ API key, no credits.
# Round 12 (2026-09-15); proxy-service round 13; EmbedEZ video-quality
# candidate round 67 (see PROXY MEDIA SERVICES below).
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
#     b) FEED TOKEN ON .json (ROUND 31: only when the OAuth app above exists,
#        or REDDIT_JSON_PROBE=force — see JSON_PROBE_MODE below):
#        www.reddit.com/comments/<id>.json?…&feed=<REDDIT_FEED_TOKEN>.
#        Anonymous .json is 403 from datacenters (verified 2026-09-20), and
#        the feed token's documented role is the RSS rate tier — so without
#        an OAuth app the attempt is pure 65 s + a 403 log line per run with
#        new posts, and auto mode skips it entirely. A 403 result is
#        remembered for that run.
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
#           ROUND 67 (REDDIT_VIDEO_QUALITY, default balanced): EmbedEZ 1080p
#           and vxreddit are attempted together — a validated EmbedEZ 1080p
#           result may replace the vxreddit result, else EmbedEZ 720p, else
#           the durable v.redd.it DASH ladder below. The whole resolver is
#           bounded (candidate timeouts + per-post and per-run budgets) and
#           decides on validated facts, never on a quality string in a URL.
#           1. v.redd.it/<id>/DASH_<q>.mp4 (720→1080→480→360) — self-
#              contained mp4 WITH audio, straight from Reddit, open, no sig
#              (round 12; the durable reliability floor in every mode)
#           2. proxy.embedez.com/render/video.mp4?videoUrl=…&audioUrl=…
#              (CMAF video + CMAF audio -> muxed mp4; keyless)
#           3. vxreddit.com/redditvideo.mp4?video_url=…&audio_url=…
#           REDDIT_VIDEO_QUALITY=compatibility restores the pre-round-67
#           order exactly (native ladder → EmbedEZ 720 → vxreddit →
#           EmbedEZ 1080).
#           Signed packaged-media.redd.it render URLs EXPIRE: they are never
#           card URLs and are never persisted (round 67: refused outright).
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
#       the public proxy services (original round-13 order; round 49 replaced
#       the serial chain with bounded waves, tie-break vxreddit -> redditez
#       (EmbedEZ) -> embeddit — see PROXY MEDIA SERVICES below). See testing
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
#       only posted when a live source (vxreddit, redditez, embeddit in
#       tie-break order, then redlib) can actually retrieve it; otherwise it is skipped
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
import urllib.parse
import base64
import asyncio
import logging
import html as html_lib
import importlib.util
import aiohttp
import feedparser
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, quote, unquote, urlparse
from dotenv import load_dotenv

try:
    import reddit_signals
except Exception:
    _signals_path = os.path.join(os.path.dirname(__file__), "reddit_signals.py")
    _signals_spec = importlib.util.spec_from_file_location("reddit_signals", _signals_path)
    reddit_signals = importlib.util.module_from_spec(_signals_spec)
    _signals_spec.loader.exec_module(reddit_signals)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
load_dotenv()

# ---------------------------------------------------------------------------
# ■ SUBREDDITS TO TRACK
# ---------------------------------------------------------------------------
# Round 53: an unset-but-wired GitHub secret/Variable arrives as an EMPTY
# STRING, which os.getenv(name, default) treats as a real value — the
# documented default then never applies. Empty means "not configured" here.
SUBREDDITS_STR = (os.getenv("SUBREDDITS") or "").strip() or "Zenlesszonezeroleaks_,Genshin_Impact_Leaks,HonkaiStarRail_leaks,WutheringWavesLeaks,HonkaiNexusAnimaLeaks,AnantaLeaks"
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
# Round 34 (2026-09-28): the old single global cap (MAX_CACHE_SIZE below) was
# enforced as `sorted(posted)[-MAX_CACHE_SIZE:]` — a LEXICOGRAPHIC eviction.
# Keys are `{sub}_{post_id}`, so once the six tracked subs crossed 500 total
# keys, the alphabetically-FIRST sub's keys — AnantaLeaks_* sorts before
# Genshin_Impact_Leaks / Honkai* / WutheringWavesLeaks /
# Zenlesszonezeroleaks_ — were evicted on EVERY save: added right after each
# post, dropped in the same save, missing from the next run's dedup check,
# re-posted (live incident 2026-09-28: AnantaLeaks_1wqhmw1 / 1wqphp3 /
# 1wriz86 on every 5-min cron run). Retention is now PER-SUBREDDIT,
# newest-first (leet post ids sort chronologically WITHIN a subreddit), with
# a generous global backstop. All three are overridable via repo Variables.
# Round 36 hotfix / round 49b: an unset repo Variable arrives as "",
# so every numeric env read goes through this guard.
def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or str(default))
    except ValueError:
        return default


MAX_CACHE_SIZE_PER_SUB = _env_int("MAX_CACHE_SIZE_PER_SUB", 250)
MAX_CACHE_SIZE_TOTAL = _env_int("MAX_CACHE_SIZE_TOTAL", 10000)
# Round 34: retired pre-round-34 global cap (kept so old references — e.g.
# tests/README — still resolve; the save no longer uses it).
MAX_CACHE_SIZE = 500
# Round 34: per-run flood cap — one run posts at most this many NEW posts
# (the newest ones); the rest retry next run inside the 48 h window. A loud
# log line instead of a mass re-post storm if the dedup cache ever loses
# keys again.
MAX_POSTS_PER_RUN = _env_int("MAX_POSTS_PER_RUN", 25)

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
PENDING_RECHECK_SECONDS = _env_int("PENDING_RECHECK_SECONDS", 1800)  # 30 min
PENDING_MAX_AGE_SECONDS = 48 * 3600  # matches the 48h posting window

# Round 44: delivered-post retraction/tombstone. Disabled by default; when on
# the webhook is called with wait=true so Discord returns a message ID.
RETRACT_DEAD_POSTS = os.getenv("RETRACT_DEAD_POSTS", "0").strip().lower() in ("1", "true", "yes", "on")
RETRACT_MODE = os.getenv("RETRACT_MODE", "edit").strip().lower() or "edit"
RETRACT_WINDOW_SECONDS = _env_int("RETRACT_WINDOW_SECONDS", 21600)
POSTED_MESSAGES_FILE = "posted_messages.json"

# ---------------------------------------------------------------------------
# ■ ROUND 64 ("Pristine Listing Protocol", 2026-10-05)
# A new post is delivered only when a pristine, cache-busted read of the
# subreddit's /new listing proves it is actually live on the subreddit RIGHT
# NOW, plus a flat minimum age computed from the post's own creation
# timestamp. Both are stateless: neither writes a pending entry, and
# "not there yet" is a this-tick skip, never a permanent block — the first
# tick where the post appears (and clears the age floor) it posts instantly.
# No AutoModerator comment detection of any kind: no phrase matching, no
# inference, no tracker state.
#   FRESH_HOLD_SECONDS — section A: flat minimum post age (seconds) before a
#     candidate is even considered; 0 disables the floor entirely.
#   LISTING_CONFIRM — section B: 1 (default) requires the pristine /new
#     listing to confirm the candidate before it posts; 0 disables the
#     listing-confirmation gate (the FRESH_HOLD_SECONDS floor still applies
#     independently; set both to 0 to fully restore pre-round-64 delivery).
# ---------------------------------------------------------------------------
FRESH_HOLD_SECONDS = _env_int("FRESH_HOLD_SECONDS", 30)
_LISTING_CONFIRM_RAW = os.getenv("LISTING_CONFIRM")
if not _LISTING_CONFIRM_RAW or _LISTING_CONFIRM_RAW.strip().lower() in ("1", "true", "yes", "on"):
    LISTING_CONFIRM = True
else:
    LISTING_CONFIRM = _LISTING_CONFIRM_RAW.strip().lower() not in ("0", "false", "no", "off")

# ---------------------------------------------------------------------------
# ■ ROUND 66 (2026-10-05): LONG-POST CONTINUATIONS + TABLE RENDERING
# A body that no longer fits the card's text budget is no longer truncated
# and dropped: the main card keeps the first chunk (same layout, gallery,
# stats, buttons), and the remainder is delivered as up to
# MAX_CONTINUATIONS follow-up messages to the same webhook — text-only
# Components V2 containers, same accent color, each opening with a small
# "-# (continued)" line. Beyond the cap the last message ends with
# "… full post on Reddit" (the Read Post button already links the post).
# Retraction covers every part: continuation message IDs are recorded
# alongside the main card's, so the tombstone/delete pass hits them all.
# Every split (main budget AND continuation boundaries) lands on a safe
# point — paragraph break, then line break, then space — and NEVER inside
# a markdown link, a bare URL, ||spoiler||, or a bold/italic marker pair
# (live 1wxfuj5: the old budget cut sliced a link mid-URL, rendering a
# broken "[Set](https://…" fragment). Markdown tables (Discord renders no
# table markdown) are converted to per-row bold-label bullets at card
# build — one choke point, every body path; malformed or ambiguous tables
# are left byte-identical, and nothing is date-converted (ranges like
# "October 9/10" cannot become Discord timestamps without guessing — the
# card's posted-time 🕐 stays the only timestamp).
# ---------------------------------------------------------------------------

# Round 63 ("The Great Simplification"): the settle window, the mod-queue
# gate, the duplicate-media gate, the repost gate and the page-identity
# check are gone. A native Reddit post is delivered as-is — the only things
# that can still stop a card are the dedup cache (below) and the opt-in
# retraction seatbelt above. Delivery latency is now simply cron interval +
# runtime overhead. (Round 67 removed the content-advisory gate as well —
# see docs/history/ROUND_67.md: the engine makes no content-based posting
# decision, on any path.)
#
# The /new listing fetch below is kept ONLY for the retraction seatbelt
# (it needs to know whether a delivered post has dropped out of the
# subreddit's listing). It uses one plain, hardcoded aiohttp timeout — no
# Variable, no HTML-vs-RSS "outage vs information" racing (that complexity
# existed solely to feed the now-removed mod-queue gate's release proof).
_LISTING_TIMEOUT_SECONDS = 8
_REDLIB_AGE_TEXT = r"(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\s+ago"
_REDLIB_CREATED_AGE_RE = re.compile(
    rf"class\s*=\s*[\"'][^\"']*\bcreated\b[^\"']*[\"'][^>]*>\s*{_REDLIB_AGE_TEXT}",
    re.I,
)
_REDLIB_BARE_AGE_RE = re.compile(rf">\s*{_REDLIB_AGE_TEXT}\s*<", re.I)
_REDLIB_AGE_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}
_LISTING_ID_RE = re.compile(r"/comments/([a-z0-9]+)/", re.I)
_listing_cache: dict = {}
_redlib_post_page_cache: dict = {}

# ---------------------------------------------------------------------------
# ■ ROUND 49 (2026-10-01): REDLIB REACHABILITY TELEMETRY
# The prod run of 11:55 logged "redlib gallery enrichment failed (all
# instances)" and "MODQUEUE-LISTING: r/AnantaLeaks /new listing unavailable
# (all sources)" — every mirror failed from the GitHub runner. "all" tells us
# nothing about WHICH mirrors are dead, so the instance list cannot be pruned
# or refreshed on evidence. One counter per instance, one summary line per
# run; zero extra requests, zero latency.
# ---------------------------------------------------------------------------
_redlib_reach: dict = {}


def log_redlib_reachability() -> str:
    """One REDLIB-REACH line per run: ok/total per instance, worst first."""
    if not _redlib_reach:
        return ""
    parts = []
    for instance, (ok, total) in sorted(_redlib_reach.items(),
                                        key=lambda kv: (kv[1][0] / kv[1][1] if kv[1][1] else 0,
                                                        kv[0])):
        parts.append(f"{instance.replace('https://', '')} {ok}/{total}")
    alive = sum(1 for ok, _t in _redlib_reach.values() if ok)
    line = (f"REDLIB-REACH: {alive}/{len(_redlib_reach)} instance(s) answered "
            f"this run — " + ", ".join(parts))
    logging.info(line)
    return line


_proxy_post_cache: dict = {}

def _redlib_ages_seconds(page_html):
    """Return visible relative ages in seconds, in page order.

    Redlib normally labels them with a ``created`` class. A bare-element
    fallback supports the equivalent markup emitted by other listing sources.
    Parsing never guesses: an unparseable page simply returns no age.
    """
    html = page_html or ""
    pairs = _REDLIB_CREATED_AGE_RE.findall(html)
    if not pairs:
        pairs = _REDLIB_BARE_AGE_RE.findall(html)
    return [int(value) * _REDLIB_AGE_UNITS[unit.lower()]
            for value, unit in pairs]

def _listing_oldest_age(page_html):
    ages = _redlib_ages_seconds(page_html)
    return max(ages) if ages else None

def _listing_post_ids(page_html):
    return set(m.lower() for m in _LISTING_ID_RE.findall(page_html or ""))

# The token-authenticated public RSS listing is a fallback source for the
# retraction seatbelt's /new snapshot. GitHub Actions datacenter IPs
# frequently cannot read the HTML /new pages, while the monitor's normal RSS
# path already works with REDDIT_FEED_TOKEN. RSS entry links carry the same
# /comments/<id>/ shape and pubDate gives an exact listing-span boundary
# without parsing relative-age text.
_RSS_LISTING_INSTANCES = ("https://www.reddit.com", "https://old.reddit.com")
_RSS_DOCUMENT_RE = re.compile(r"<(?:rss|feed|rdf:rdf)\b", re.I)
_RSS_PUBDATE_RE = re.compile(
    r"<pubDate\b[^>]*>\s*(?:<!\[CDATA\[)?\s*([^<]*?)\s*(?:\]\]>)?\s*</pubDate>",
    re.I | re.S,
)


def _rss_oldest_age(xml, now=None):
    """Return the oldest usable RSS entry age in seconds.

    A missing, malformed, or future-dated pubDate returns ``None`` so the
    retraction seatbelt fails open. ``now`` is injectable for the offline
    smoke test; production callers use the current wall clock.
    """
    clock = time.time() if now is None else now
    best = None
    for raw in _RSS_PUBDATE_RE.findall(xml or ""):
        try:
            timestamp = parsedate_to_datetime(raw.strip()).timestamp()
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        age = clock - timestamp
        if age >= 0 and (best is None or age > best):
            best = age
    return best

async def _fetch_new_listing(session, subreddit):
    """Return ``(post_ids, oldest_age_seconds)`` for r/<subreddit>/new.

    Round 63: the only remaining consumer was the retraction seatbelt
    (``listing_absence_proves_dead``) — it just needs a snapshot of which
    post IDs are currently listed and how far back that listing reaches.
    Every mirror/RSS source is tried concurrently and the first usable
    answer wins; one flat, hardcoded timeout bounds the whole step. There is
    no HTML-vs-RSS provenance distinction any more — that existed solely to
    feed the mod-queue gate's release proof, which round 63 removed.

    Round 64 ("Pristine Listing Protocol"): this is now ALSO the proof
    the main delivery loop's listing-confirmation gate relies on, so every
    request here is made PRISTINE — a unique ``_fresh=<unix ms>`` cache-buster
    query param (CDN cache keys include the query string, so a unique value
    guarantees an uncached read) plus ``Cache-Control``/``Pragma`` no-cache
    request headers, on every mirror/RSS attempt. The per-subreddit,
    once-per-run memoization below is unchanged — a fresh process (one per
    cron tick) means that memo is itself never reused across runs.
    """
    if subreddit in _listing_cache:
        return _listing_cache[subreddit]

    _timeout = aiohttp.ClientTimeout(total=_LISTING_TIMEOUT_SECONDS)
    # Round 64: one cache-buster value per _fetch_new_listing() call — every
    # mirror/RSS attempt this call makes shares it (they fire within
    # milliseconds of each other), but it is freshly computed on every call,
    # so two listing reads (different subreddits, or different cron ticks)
    # never share a value and no CDN/proxy can serve a cached response.
    _fresh = int(time.time() * 1000)
    _pristine_headers = {**BROWSER_HEADERS, "Cache-Control": "no-cache, no-store",
                         "Pragma": "no-cache"}

    async def _html_listing(instance):
        try:
            # miningtcup sits behind its DogWAF and rejects untokenized
            # requests. The helper is a no-op for every non-miningtcup URL
            # (same helper the post page uses).
            url = _with_miningtcup_token(f"{instance}/r/{subreddit}/new?limit=100&_fresh={_fresh}")
            async with session.get(url, headers=_pristine_headers, timeout=_timeout,
                                   allow_redirects=True) as resp:
                if resp.status != 200 or "html" not in (resp.headers.get("Content-Type") or "").lower():
                    return None
                html = await resp.text()
        except Exception:
            return None
        ids = _listing_post_ids(html or "")
        if not ids:
            return None
        return (ids, _listing_oldest_age(html))

    async def _rss_listing(instance):
        try:
            url = f"{instance}/r/{subreddit}/new.rss?limit=100&_fresh={_fresh}"
            if REDDIT_FEED_TOKEN:
                # Feed tokens are credentials; quote them as a query value and
                # never include the resulting URL in a log message.
                url += f"&feed={quote(REDDIT_FEED_TOKEN, safe='')}"
            async with session.get(url, headers=_pristine_headers, timeout=_timeout,
                                   allow_redirects=True) as resp:
                xml = await resp.text() if resp.status == 200 else None
        except Exception:
            return None
        if not xml or not _RSS_DOCUMENT_RE.search(xml):
            return None
        ids = _listing_post_ids(xml)
        if not ids:
            return None
        return (ids, _rss_oldest_age(xml))

    loop = asyncio.get_running_loop()
    deadline = loop.time() + _LISTING_TIMEOUT_SECONDS
    pending = {asyncio.ensure_future(_html_listing(inst)) for inst in REDDIT_RSS_INSTANCES}
    pending |= {asyncio.ensure_future(_rss_listing(inst)) for inst in _RSS_LISTING_INSTANCES}
    result = None
    try:
        while pending:
            budget = deadline - loop.time()
            if budget <= 0:
                break
            done, pending = await asyncio.wait(
                pending, timeout=budget, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                break                      # budget expired
            for task in done:
                try:
                    answer = task.result()
                except Exception:
                    answer = None
                if answer:
                    result = answer
            if result is not None:
                break
    finally:
        for task in pending:
            task.cancel()

    if result is not None:
        logging.info(f"LISTING: r/{subreddit} pristine /new ({len(result[0])} entries).")
    else:
        logging.info(f"LISTING: r/{subreddit} pristine /new listing unavailable this run "
                     f"(all sources) — an outage, not absence: the round-64 listing-confirmation "
                     f"gate and the retraction seatbelt both fail open (skip/retry next tick).")
    _listing_cache[subreddit] = result
    return result

# ---------------------------------------------------------------------------
# ■ RSS SOURCES
#
# PRIMARY: one combined native Reddit RSS request covers all subreddits.
# FALLBACK: native Reddit/old Reddit are followed by the token-gated miningtcup
# Redlib host, then the current official registry's token-free, non-Cloudflare
# candidates (catsarch US, nadeko CL, privadency DE), then retained legacy
# mirrors. Round 39 (2026-09-30) deliberately excludes r4fo because the
# registry marks it Cloudflare-fronted. Every response is validated for Reddit
# permalinks before it is accepted, so a challenge or HTML shell fails closed.
# ---------------------------------------------------------------------------
# Round 59 (2026-10-03): body-audit record. The exact /r/<sub>/new.rss
# response was read for each public Redlib candidate. Current responses are
# RSS-disabled, challenge-walled, or unavailable; no confirmed replacement
# was found, so the list is intentionally unchanged and fails closed via
# permalink validation. Re-audit bodies rather than trusting status codes or
# the instance registry before promoting a mirror.
REDDIT_RSS_INSTANCES = [
    "https://www.reddit.com",
    "https://old.reddit.com",
    # Token-gated fallback; harmlessly skipped when NITTER_RSS_TOKEN is empty.
    "https://redlib.miningtcup.me",
    "https://redlib.catsarch.com",
    "https://redlib.nadeko.net",
    "https://redlib.privadency.com",
    "https://safereddit.com",
    "https://red.artemislena.eu",
    "https://redlib.privacyredirect.com",
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
    """Read a boolean option, treating an unset/empty GitHub Variable as default."""
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        value = default.strip().lower()
    return value not in ("0", "false", "no", "off")


def _env_mode(name: str, default: str, allowed: tuple) -> str:
    """Read a string-mode option; unset/empty/garbage means the default.

    GitHub passes an unset repository Variable as an empty string, and a
    typo must never silently pick an unintended mode — both fall back to the
    documented default (same contract as round 53's empty-string handling).
    """
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    if value in allowed:
        return value
    logging.warning(f"{name}={value!r} is not one of {allowed} — using {default!r}.")
    return default


# ---------------------------------------------------------------------------
# ■ ROUND 67 (2026-10-07): REDDIT VIDEO QUALITY SELECTION
# Live Discord/Discohook tests on multiple Reddit videos (evidence:
# docs/REDDIT_VIDEO_MEDIA_REPORT.md) established three facts that shape this
# resolver:
#   • EmbedEZ 1080p — playable with audio and visibly sharper than 720p.
#   • vxreddit      — playable with audio, but its own output stayed at its
#                     own quality even when handed a CMAF_1080 input. A URL
#                     containing "1080" is therefore NEVER proof of quality;
#                     the resolver decides on validated response facts.
#   • signed native packaged-media.redd.it URLs play at original quality but
#     EXPIRE — they are never card URLs and never persisted (see
#     media_url_is_durable).
# Reliability order is unchanged: vxreddit is the winner path and the
# immediate fallback, and EmbedEZ 1080p is a bounded quality opportunity —
# never a single point of failure. A timeout, an HTTP error, an HTML error
# page, an oversized file or a failed mux simply keeps vxreddit.
#   REDDIT_VIDEO_QUALITY (empty-safe; wired in reddit_monitor.yml):
#     balanced (default) — EmbedEZ 1080p may replace a validated vxreddit
#                          result; EmbedEZ 720p is only tried when vxreddit
#                          itself is unusable.
#     compatibility      — the pre-round-67 ladder, byte-order identical
#                          (native DASH first, no 1080p EmbedEZ attempt):
#                          the instant rollback, no code change needed.
#     highest            — balanced with a wider quality window; still bound
#                          by the same size and latency limits.
# The resolver is also bounded end to end: every candidate has a timeout, the
# 1080p opportunity holds the decision for at most
# VIDEO_QUALITY_WINDOW_SECONDS, one post may not exceed
# VIDEO_RESOLVE_BUDGET_SECONDS, and a run may not exceed
# VIDEO_RUN_BUDGET_SECONDS of quality attempts (after that the durable native
# ladder is used directly — a slow proxy can never stall the monitor). The
# image, GIF, photo and gallery paths do not use any of this.
# ---------------------------------------------------------------------------
VIDEO_QUALITY_MODES = ("balanced", "compatibility", "highest")
VIDEO_QUALITY_MODE = _env_mode("REDDIT_VIDEO_QUALITY", "balanced", VIDEO_QUALITY_MODES)
# One muxing-proxy candidate (pre-round-67 code allowed 120 s per attempt).
VIDEO_CANDIDATE_TIMEOUT_SECONDS = 20.0
# One durable v.redd.it candidate (Reddit answers 404 / 206 quickly).
VIDEO_NATIVE_TIMEOUT_SECONDS = 10.0
# How long a validated EmbedEZ 1080p result may hold the decision open.
VIDEO_QUALITY_WINDOW_SECONDS = 8.0
# The same window in "highest" mode (still bounded — this is not "wait
# indefinitely"; a canary run may widen it deliberately).
VIDEO_QUALITY_WINDOW_HIGHEST_SECONDS = 15.0
# The whole resolver, wall clock, for one post.
VIDEO_RESOLVE_BUDGET_SECONDS = 40.0
# Per-run ceiling for quality attempts; afterwards the resolver goes straight
# to the durable native ladder.
VIDEO_RUN_BUDGET_SECONDS = 180.0
# Body prefix pulled for the MP4 evidence probe (dimensions + audio track).
VIDEO_PROBE_BYTES = 64 * 1024


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
# Native-mode card media comes from the public proxy services FIRST. Their
# tie-break priority is vxreddit.com -> redditez.com (EmbedEZ) ->
# embeddit.deltandy.me, dispatched as bounded concurrent waves (round 49), not
# as a serial chain: the first two eligible services start together and
# Embeddit is held back. The winning service's own URLs are used verbatim in
# the card (full-res photos, every gallery photo, videos WITH audio, GIFs).
# EmbedEZ is also an optional, validated 1080p video-quality candidate (round
# 67) that can only replace a validated proxy video — see
# docs/REDDIT_VIDEO_QUALITY.md.
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


async def fetch_proxy_post_memo(session, path: str, label: str = "", health=None, need_video: bool = False):
    """Per-run memo around reddit_proxy.fetch_proxy_post.

    The live/liveness path and media resolver often need the same proxy chain.
    Cache only successful results; failures remain retryable later in the run.
    """
    if reddit_proxy is None:
        return None
    key = (id(reddit_proxy), str(path or ""), bool(need_video))
    use_cache = hasattr(reddit_proxy, "__file__")
    if use_cache and key in _proxy_post_cache:
        return _proxy_post_cache[key]
    result = await reddit_proxy.fetch_proxy_post(session, path, label=label,
                                                 health=health, need_video=need_video)
    # ROUND 47: a PROFILE post ("/user/<name>/comments/<id>/") is the same
    # post as "/r/u_<name>/comments/<id>/", and the proxy services disagree
    # about which route they serve. Only a profile post HAS an alias, so an
    # ordinary subreddit post never makes this second call.
    if not (isinstance(result, dict) and result.get("media")):
        for alias in _path_aliases(path)[1:]:
            alt = await reddit_proxy.fetch_proxy_post(session, alias, label=label,
                                                      health=health,
                                                      need_video=need_video)
            if isinstance(alt, dict) and alt.get("media"):
                logging.info(f"[{label or 'proxy'}] profile post resolved via "
                             f"its subreddit alias {alias}")
                result = alt
                break
    if use_cache and result:
        _proxy_post_cache[key] = result
    return result

# Native reddit video ladder: v.redd.it DASH_<q>.mp4 files are self-contained
# mp4s (h264 + AAC). 404s answer instantly, so the ladder is cheap.
DASH_QUALITIES = (720, 1080, 480, 360)

# Feed-token .json attempt: anonymous .json is ~1 req/min from datacenters, so
# this is the polite sleep before each attempt (lower it ONLY if your token
# reliably works on .json).
FEEDTOKEN_JSON_STAGGER = _env_int("FEEDTOKEN_JSON_STAGGER", 65)

# ---------------------------------------------------------------------------
# ■ ROUND 31 (2026-09-20): FEED-TOKEN .json FULL-MODE PROBE GATE
# Live-verified: Reddit 403s .json from datacenter IPs, so without an OAuth
# app the round-12 probe was 65 s of guaranteed 403 log noise per run with
# new posts (first attempt only — a 403 is remembered for the rest of the
# run; steady-state runs never reached it).
#   auto (default; unset/empty = auto) — probe runs only when BOTH
#          REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are set (a real app
#          exists — then this is the legacy fallback under a working
#          OAuth path);
#   force — legacy behavior: always try (65 s wait) on a run with new posts;
#   off   — never.
# Repo Variable REDDIT_JSON_PROBE (wired in reddit_monitor.yml).
# ---------------------------------------------------------------------------
JSON_PROBE_MODE = os.getenv("REDDIT_JSON_PROBE", "auto").strip().lower()


def feedtoken_probe_enabled() -> bool:
    """Round 31: should the feed-token .json FULL-MODE probe run?"""
    if JSON_PROBE_MODE == "off":
        return False
    if JSON_PROBE_MODE == "force":
        return True
    # auto (default; also any unrecognized value): only with a real app.
    return bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)

# Text display budget (Discord: 4000 chars total per message across all
# text components; we keep header+body+stats comfortably under it).
# Round 66: this is now the MAIN CARD's body budget — a longer body is
# continued in follow-up messages (MAX_CONTINUATIONS × ~3800 chars more;
# see build_v3_payload/build_continuation_payloads) instead of truncated.
MAX_BODY_CHARS = 16000
# Round 66: follow-up messages carrying the body remainder (see the
# ROUND 66 block above). 3800 leaves room under Discord's 4000-char
# message limit for the "-# (continued)" heading line.
MAX_CONTINUATIONS = 3
CONTINUATION_CHAR_LIMIT = 3800
CONTINUATION_HEADER = "-# (continued)"
CONTINUATION_TAIL = "… full post on Reddit"

# Buttons (style 5 = Link). YouTube button uses the animated starwardspark3.
READ_POST_EMOJI = {"id": "1472388018689282261", "name": "starwardhmm", "animated": True}
YOUTUBE_EMOJI = {"id": "1483083423290490891", "name": "starwardspark3", "animated": True}
STATIC_BUTTONS = [
    {"label": "Citlali News", "url": "https://discord.gg/HyrVP9wRXu", "emoji": {"id": "1439878792653832253", "name": "starward11", "animated": True}},
    {"label": "Donate", "url": "https://ko-fi.com/jieunlatte", "emoji": {"id": "1509026327548657914", "name": "starwardfans", "animated": True}},
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


def _shrink_posted_cache(posted: set, keep_newest: frozenset = frozenset()) -> set:
    """Round 34 (2026-09-28): cap the dedup cache WITHOUT lexicographic
    eviction (see the MAX_CACHE_SIZE_PER_SUB note).

    Pre-round-34 the save wrote `sorted(posted)[-MAX_CACHE_SIZE:]`: at the
    cap that dropped the ALPHABETICALLY-FIRST keys — AnantaLeaks' keys, every
    time. Retention now keeps the NEWEST MAX_CACHE_SIZE_PER_SUB keys of EACH
    subreddit (leet post ids sort chronologically within a sub), then applies
    the MAX_CACHE_SIZE_TOTAL backstop. Keys in `keep_newest` (posted THIS
    run) always survive, whatever else is trimmed.
    """
    by_sub: dict = {}
    for key in posted:
        by_sub.setdefault(key.partition("_")[0], []).append(key)

    def _trim(keys: list, quota: int) -> list:
        keys.sort()  # leet ids: chronological within a subreddit
        return keys[-quota:] if len(keys) > quota else keys

    kept: set = set()
    evicted: set = set()
    for keys in by_sub.values():
        kept.update(_trim(keys, MAX_CACHE_SIZE_PER_SUB))
        if len(keys) > MAX_CACHE_SIZE_PER_SUB:
            evicted.update(keys[:-MAX_CACHE_SIZE_PER_SUB])
    # Global backstop (never expected to trigger: 6 subs x 250 = 1500 << 10000).
    if len(kept) > MAX_CACHE_SIZE_TOTAL:
        per_sub = max(1, MAX_CACHE_SIZE_TOTAL // max(1, len(by_sub)))
        kept = set()
        for keys in by_sub.values():
            kept.update(_trim(keys, per_sub))
    # Belt and braces: keys posted THIS run always survive.
    kept |= set(keep_newest) & posted
    if evicted:
        logging.warning(f"dedup cache: evicted {len(evicted)} oldest key(s) "
                        f"(per-sub cap {MAX_CACHE_SIZE_PER_SUB}): "
                        f"{sorted(evicted)[:10]}")
    return kept


def _verify_dedup_save(posted_at_start: set, posted: set, saved: set) -> None:
    """Round 34: after a save, prove every key posted THIS run made it to
    disk. A missing key WILL be re-posted next run — that must be a loud log
    line, never a silent failure (the 2026-09-28 AnantaLeaks re-post loop
    ran silently for hours)."""
    missing = (posted - posted_at_start) - saved
    if missing:
        logging.error(f"DEDUP GUARD: {len(missing)} key(s) posted this run are "
                      f"MISSING from the saved dedup cache — they WILL be "
                      f"re-posted next run: {sorted(missing)[:10]}")


def cap_new_posts(new_posts: list, cap: int) -> list:
    """Round 34: bound one run's post count (MAX_POSTS_PER_RUN note).
    `new_posts` is sorted oldest-first; keep the NEWEST `cap` entries and
    log the dropped oldest (they retry next run inside the 48 h window)."""
    if len(new_posts) <= cap:
        return new_posts
    dropped = [p[2] for p in new_posts[:len(new_posts) - cap]]
    logging.warning(f"POST FLOOD GUARD: {len(new_posts)} new posts this run "
                    f"exceed the cap of {cap} — posting the newest {cap}; the "
                    f"{len(dropped)} oldest retry next run inside the 48 h "
                    f"window. Dropped: {dropped}")
    return new_posts[-cap:]


def save_posted(posted: set, keep_newest: frozenset = frozenset()) -> set:
    """Save the dedup cache and return the set actually written ({} on a
    failed write).

    Written sorted so identical sets stay byte-identical (quiet runs create
    no commits from process-dependent set iteration order). Round 34:
    retention is per-sub newest-first (_shrink_posted_cache) instead of the
    lexicographic `[-MAX_CACHE_SIZE:]` slice that evicted AnantaLeaks keys
    forever once 500 keys accumulated.
    """
    try:
        kept = _shrink_posted_cache(posted, keep_newest)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(kept), f, indent=2)
        return kept
    except Exception as e:
        logging.error(f"Error saving cache: {e}")
        return set()


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


def load_posted_messages() -> dict:
    if not RETRACT_DEAD_POSTS:
        return {}
    try:
        with open(POSTED_MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logging.error(f"Error reading posted-message cache: {e}")
        return {}


def save_posted_messages(messages: dict) -> None:
    if not RETRACT_DEAD_POSTS:
        return
    try:
        now = time.time()
        pruned = {
            k: v for k, v in (messages or {}).items()
            if isinstance(v, dict) and not v.get("retracted")
            and now - float(v.get("delivered_at") or 0) <= RETRACT_WINDOW_SECONDS
        }
        with open(POSTED_MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(pruned.items())), f, indent=2)
    except Exception as e:
        logging.error(f"Error saving posted-message cache: {e}")


def mark_pending(pending: dict, key: str, reason: str, now: float,
                 source: str | None = None, title: str | None = None,
                 published_ts: float | None = None) -> None:
    # Round 36 (2026-09-29): optional audit fields (source rss/arctic,
    # title, publish time) — purely additive; existing callers and existing
    # pending_reddit.json files keep working unchanged.
    entry = pending.get(key)
    if isinstance(entry, dict):
        entry["last_checked"] = now
        entry["reason"] = reason
    else:
        entry = {"first_seen": now, "last_checked": now, "reason": reason}
        pending[key] = entry
    if source is not None:
        entry["source"] = source
    if title is not None:
        entry["title"] = title
    if published_ts is not None:
        entry["published_ts"] = int(published_ts)


def pending_due(pending: dict, key: str, now: float) -> bool:
    """True when the pending post is due for a live re-check (never seen,
    or PENDING_RECHECK_SECONDS have passed since the last check)."""
    entry = pending.get(key)
    if not isinstance(entry, dict):
        return True
    return now - float(entry.get("last_checked") or 0) >= PENDING_RECHECK_SECONDS


# ---------------------------------------------------------------------------
# ■ ROUND 32 (2026-09-20), simplified in round 63: REASON-AWARE PENDING
# RE-CHECK. Removed/deleted posts (the no-recheck set) are NEVER re-checked
# while the entry exists (self-expires at the 48 h window) — a removed post
# cannot change state while removed; if a mod RESTORES it, it re-enters via
# RSS with a fresh "updated" stamp, which bypasses the skip and the full
# pipeline posts it. Everything else (approval-pending, transient source
# outages, media-wait, source trouble, ...) shares the single round-30
# PENDING_RECHECK_SECONDS throttle — round 63 removed the mod-queue gate
# that justified a separate short interval.
# ---------------------------------------------------------------------------
_NO_RECHECK_REASONS = frozenset({
    "removal_notice",             # round-20 gate: proxy body had the notice
    "removal notice",             # round-18 gate: RSS/archive body had it
    "title marker",               # "[removed]" / "[deleted]" as whole title
    "whole-body marker",          # "[removed]" / "[deleted]" as whole body
    "removed by moderator",
    "removed by moderators/filters",
    "deleted by author",
})


def _pending_throttle_skip(pending: dict, key: str, now: float, entry) -> bool:
    """Round 32: should this pending post be skipped (not re-verified) now?

    Removed/deleted posts: always skipped — EXCEPT when the entry came from
    RSS (entry is not an _ArcticEntry), i.e. the post re-appeared in the
    feed with fresh content (a restore): the full pipeline runs and either
    posts it (now live) or re-queues it (still shows a notice).
    """
    pend = pending.get(key)
    reason = pend.get("reason") if isinstance(pend, dict) else None
    if reason in _NO_RECHECK_REASONS:
        # Arctic resurface -> skip (never re-verified); RSS re-appearance
        # (a restore, fresh "updated" stamp) -> run the full pipeline.
        return isinstance(entry, _ArcticEntry)
    last = float(pend.get("last_checked") or 0) if isinstance(pend, dict) else 0.0
    return now - last < PENDING_RECHECK_SECONDS


# ---------------------------------------------------------------------------
# ■ ROUND 47 (2026-10-01): PROFILE POSTS ("u/<name> posts", not subreddit ones)
# LIVE INCIDENT. r/AnantaLeaks 1wuv547 was a crosspost of a PROFILE post,
# https://www.reddit.com/user/aphotide/comments/1wuv4j8/ — a post that lives
# on a user's own page, not in a subreddit. Every crosspost helper below only
# recognised "/r/<sub>/comments/<id>/", so the original was never resolved:
# the card fell back to the crosspost's external-preview.redd.it POSTER and
# Discord showed a still image where a 1:26 video should have played.
#
# Reddit stores a profile post under the pseudo-subreddit "u_<name>", so
# /user/<name>/comments/<id>/  ==  /r/u_<name>/comments/<id>/
# Both forms are generated: mirrors and proxy services disagree about which
# one they route, so the media fetchers try the canonical form first and the
# alias second (see _path_aliases / fetch_proxy_post_memo).
# ---------------------------------------------------------------------------
PROFILE_POST_RE = re.compile(r"^/u(?:ser)?/([^/\s?]+)/comments/([a-zA-Z0-9]+)", re.I)
SUBREDDIT_PROFILE_RE = re.compile(r"^/r/u_([^/\s?]+)/comments/([a-zA-Z0-9]+)", re.I)


def normalize_reddit_path(link: str) -> str | None:
    """A reddit permalink -> '/r/<sub>/comments/<id>/...' or, for a profile
    post, the canonical '/user/<name>/comments/<id>/...' (round 47)."""
    match = re.search(r"(/r/[^\s?]+)", link or "")
    if match:
        return match.group(1).rstrip("/") + "/"
    # Round 47: profile posts ("/user/<name>/comments/<id>/", or the short
    # "/u/<name>/..." form some mirrors emit) are real posts too.
    match = re.search(r"/u(?:ser)?/([^/\s?]+)/comments/([a-zA-Z0-9]+)(/[^\s?]*)?", link or "")
    if match:
        tail = (match.group(3) or "").rstrip("/")
        return f"/user/{match.group(1)}/comments/{match.group(2)}{tail}/"
    return None


def is_profile_post_path(path: str | None) -> bool:
    """True for a post that lives on a user's page instead of a subreddit."""
    return bool(path) and bool(PROFILE_POST_RE.match(path or ""))


def _path_aliases(path: str | None) -> list:
    """Round 47: every equivalent route for one post, best-known first.

    A profile post answers on BOTH '/user/<name>/comments/<id>/' (reddit.com,
    redlib) and '/r/u_<name>/comments/<id>/' (the pseudo-subreddit Reddit
    files it under, which several proxy services and Arctic Shift index).
    Ordinary subreddit posts have exactly one route, so the list is a single
    entry and no caller pays anything extra.
    """
    if not path:
        return []
    out = [path]
    m = PROFILE_POST_RE.match(path)
    if m:
        alias = f"/r/u_{m.group(1)}/comments/{m.group(2)}/"
        if alias not in out:
            out.append(alias)
        return out
    m = SUBREDDIT_PROFILE_RE.match(path)
    if m:
        alias = f"/user/{m.group(1)}/comments/{m.group(2)}/"
        if alias not in out:
            out.append(alias)
    return out


def extract_subreddit(path: str) -> str | None:
    match = re.match(r"/r/([^/]+)/", path or "")
    return match.group(1) if match else None


def extract_post_id(path: str) -> str | None:
    match = re.search(r"/comments/([a-zA-Z0-9]+)/", path)
    return match.group(1) if match else None


# Round 47: profile posts ("/user/<name>/comments/<id>/") are crosspostable
# originals too — r/AnantaLeaks 1wuv547 was one, and before this its
# original (and therefore its video) was never found.
CROSSPOST_PERMALINK_RE = re.compile(
    r"/(?:r/[^/\s?<>]+|u(?:ser)?/[^/\s?<>]+)/comments/[a-zA-Z0-9]+/")


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
        # Round 47: '/u/<name>/comments/...' is the same post as
        # '/user/<name>/comments/...'; store the canonical form.
        return normalize_reddit_path(p + "/") or (p + "/")
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
        # Keep HTTP messages ("HTTP 422") and descriptive exception text; fall
        # back to the class name so a blank str(exc) never logs an empty reason.
        reason = str(exc).strip() or type(exc).__name__
        logging.info(
            f"[{label or subreddit}] Arctic Shift search unavailable: {reason}"
        )
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
# round 32 (2026-09-20): the mod-queue banner "Post is awaiting moderator
# approval." (observed live 2026-09-20, post 1wl41aj). A queued post is NOT
# removed: it is re-checked on the short approval interval and posts as
# soon as it is approved. Checked FIRST — the banner never co-occurs with a
# removal notice, so order is safe.
_REMOVED_NOTICE_RES = (
    (re.compile(r"awaiting (?:moderator )?approval", re.I), "pending approval"),
    (re.compile(r"sorry,? (?:this|the) post (?:has been|was) (?:removed|deleted)", re.I), "removal notice"),
    (re.compile(r"\[ ?removed ?by ?moderator ?\]", re.I), "removed by moderator"),
    (re.compile(r"removed by (?:the )?(?:moderators?|reddit)", re.I),
     "removed by moderators/filters"),
    (re.compile(r"(?:was|has been) deleted by the person who originally posted it", re.I), "deleted by author"),
)


def removed_post_reason(title: str | None, body: str | None) -> str | None:
    """Pure removal/pending classifier (implemented in reddit_signals)."""
    return reddit_signals.removed_post_reason(title, body)


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
    """Plain (non-HTML) selftext -> card body — the Arctic archive path.

    Round 65 (2026-10-05, live 1wxvl13): Arctic Shift — like Reddit's own
    JSON API — serves selftext with HTML entities escaped (&gt; &lt; &amp;),
    so the Arctic path unescapes EXACTLY ONCE here, before the cleaning
    (mirroring the JSON path's single unescape inside strip_html). Never a
    second pass: a post that genuinely writes "&amp;gt;" must render
    "&gt;", not ">".
    """
    if text in ("[removed]", "[deleted]"):
        return ""
    text = html_lib.unescape(str(text or ""))
    return _collapse_blanks(_line_stage([line.strip() for line in text.splitlines()]))


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


def _drop_gallery_media_lines(body: str, media: list) -> str:
    """Round 62 (2026-10-04, live 1wx9c77): a selftext whose only content is
    an inline image link rendered the SAME picture twice on the card — once
    as a raw preview.redd.it URL line in the body text, once in the media
    gallery right below it. Any body line that is ONLY a redd.it media link
    (bare URL, or a self-labelled [url](url) markdown link) whose file
    identity (_reddit_media_key: preview.redd.it/<slug>-v0-<id> and
    i.redd.it/<id> collapse to one key) is ALREADY a gallery item is
    dropped. Prose lines, caption-labelled links, and media links NOT in
    the gallery stay byte-identical; a body left empty simply omits the
    text component (the card keeps header/gallery/stats as before)."""
    if not body or not media:
        return body
    gallery_keys = {
        _reddit_media_key(str(m.get("url") or ""))
        for m in media if m.get("url")
    }
    if not gallery_keys:
        return body

    def _line_key(line: str) -> str | None:
        candidate = html_lib.unescape(line.strip())
        link = re.fullmatch(r"\[([^\]]+)\]\((https?://[^\s]+)\)", candidate)
        if link:
            label = html_lib.unescape(link.group(1).strip())
            url = link.group(2).strip()
            # self-labelled link ([url](url)) or both halves pointing at the
            # same media file — anything with a REAL caption is kept
            if label != url and not (
                    REDDIT_MEDIA_URL_RE.fullmatch(label.rstrip(".,;"))
                    and _reddit_media_key(label) == _reddit_media_key(url)):
                return None
            candidate = url
        candidate = candidate.rstrip(".,;")
        if REDDIT_MEDIA_URL_RE.fullmatch(candidate):
            return _reddit_media_key(candidate)
        return None

    kept = [line for line in body.splitlines()
            if _line_key(line) not in gallery_keys]
    return _collapse_blanks(kept)


# ---------------------------------------------------------------------------
# ■ ROUND 66 (2026-10-05): SAFE BODY SPLITTING + MARKDOWN TABLE RENDERING
# (see the ROUND 66 notes block near the top for the full design)
# ---------------------------------------------------------------------------
# Spans a body split must NEVER slice through — a sliced link renders
# broken markdown (live 1wxfuj5: "[Set](https://…").
_PROTECTED_SPAN_RES = (
    # markdown link [text](url) — the whole thing is one span
    re.compile(r"\[[^\[\]\n]*\]\(https?://[^)\s]*\)"),
    # bare URL (never extends past a ')' — that is punctuation in prose,
    # and a span bleeding past a link's closing paren would START inside
    # the link span and defeat the straddle rule below)
    re.compile(r"https?://[^\s<>\[\)]+"),
    # Discord spoiler
    re.compile(r"\|\|[^|\n]*\|\|"),
    # bold / italic marker pairs
    re.compile(r"\*\*[^*\n]*\*\*"),
    re.compile(r"(?<!\*)\*[^*\n]+\*(?!\*)"),
    re.compile(r"_[^_\n]+_"),
)


def _protected_spans(text: str) -> list:
    """Round 66: the (start, end) character ranges of a body that a split
    must never land inside — links, bare URLs, spoilers, marker pairs.
    Overlapping matches (a bare URL found inside a markdown link) are
    merged into one span so no span boundary can start inside another."""
    spans = []
    for rx in _PROTECTED_SPAN_RES:
        spans.extend(m.span() for m in rx.finditer(text))
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _safe_split_point(text: str, limit: int) -> int:
    """Round 66: the index at which to split `text` so text[:point] stays
    within `limit` characters and lands on a safe boundary — the largest
    paragraph break, else line break, else space — and never inside a
    markdown link, bare URL, spoiler, or bold/italic marker pair. A span
    straddling the limit pushes the split to BEFORE it (the live 1wxfuj5
    fix). Pathological no-boundary text (one giant token) falls back to
    the hard limit so delivery always makes progress."""
    spans = _protected_spans(text)

    def _inside(p: int) -> bool:
        return any(s < p < e for s, e in spans)

    for pattern in (r"\n\n", r"\n", r"[ \t]"):
        best = 0
        for m in re.finditer(pattern, text[:limit + 1]):
            p = m.end()
            if p <= limit and p > best and not _inside(p):
                best = p
        if best:
            return best
    # no paragraph/line/space boundary at all: never slice a protected
    # span that straddles the limit — split just before it starts
    for s, e in spans:
        if 0 < s <= limit < e:
            return s
    # a protected span starting at 0 whose end fits the small slack above
    # the limit: keep it whole instead of slicing it
    for s, e in spans:
        if s == 0 and e > limit and e <= limit + 200:
            return e
    return max(1, limit)


def split_card_body(text: str, limit: int) -> tuple:
    """Round 66: (head, tail) split of a card body at a safe boundary —
    paragraph break preferred, then line break, then space, never inside
    a link/URL/spoiler/marker pair (see _safe_split_point). A body within
    the limit is returned untouched ("", remainder)."""
    text = text or ""
    if len(text) <= limit:
        return text, ""
    point = _safe_split_point(text, limit)
    return text[:point].rstrip(), text[point:].lstrip()


_MD_TABLE_SEP_CELL_RE = re.compile(r"^:?-+:?$")


def _md_table_cells(line: str) -> "list | None":
    """Round 66: the pipe-separated cells of one markdown table row, or
    None when the line is not a table row (no pipe / fewer than 2 cells).
    Reddit escapes literal pipes as \\| — unescaped after splitting."""
    raw = (line or "").strip()
    if not raw or "|" not in raw:
        return None
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", raw)]
    return cells if len(cells) >= 2 else None


def _markdown_tables_to_bullets(body: str) -> str:
    """Round 66 (live 1wxfuj5): Discord renders no table markdown, so a
    markdown table in the card body (header row + |---| separator + data
    rows) is converted to per-row bullets:

        **<Header1> <cell1>**
        - **<Header2>:** <cell2>     (one bullet per remaining column,
                                      empty cells skipped; rows joined
                                      by a blank line)

    The bold row label COMPOSES the first header with the first cell —
    the live 1wxfuj5 table (header "Patch", cells "3.7"/"3.8") renders
    "**Patch 3.8**", matching the hand-checked example from the PR
    conversation. A cell that already carries the header word ("Patch
    3.8" under header "Patch") is used verbatim — never doubled.

    Conservative by design: a block only converts when every row agrees
    on the cell count, the separator row is well-formed, and no second
    separator-shaped row glues tables together — malformed or ambiguous
    shapes stay byte-identical, and nothing is date-converted (ranges
    like "October 9/10" cannot become Discord timestamps without
    guessing; the card's posted-time 🕐 stays the only timestamp)."""
    if not body or "|" not in body:
        return body
    lines = body.split("\n")
    out = []
    i, n = 0, len(lines)
    while i < n:
        header = _md_table_cells(lines[i])
        sep = _md_table_cells(lines[i + 1]) if i + 1 < n else None
        if not (header and sep and len(sep) == len(header)
                and all(_MD_TABLE_SEP_CELL_RE.match(c) for c in sep)):
            out.append(lines[i])
            i += 1
            continue
        # the data rows: consecutive rows sharing the header's cell count
        j = i + 2
        rows = []
        while j < n:
            cells = _md_table_cells(lines[j])
            if cells is None or len(cells) != len(header):
                break
            rows.append(cells)
            j += 1
        # full pipe-run end (for leaving malformed blocks byte-identical)
        j_end = j
        while j_end < n and _md_table_cells(lines[j_end]) is not None:
            j_end += 1
        # a separator-shaped row inside the data rows means two tables
        # were glued together (or worse); a pipe-run that ends on a row
        # with a DIFFERENT cell count is a malformed table — never guess
        glued = any(all(_MD_TABLE_SEP_CELL_RE.match(c) for c in r) for r in rows)
        malformed = (j < n and _md_table_cells(lines[j]) is not None
                     and len(_md_table_cells(lines[j])) != len(header))
        if rows and not glued and not malformed:
            blocks = []
            for cells in rows:
                block = []
                if cells[0]:
                    label = cells[0]
                    head1 = (header[0] or "").strip()
                    # compose "Header1 cell1" ("Patch" + "3.8" ->
                    # "Patch 3.8"); a cell already carrying the header
                    # word stays verbatim ("Patch 3.8" -> "Patch 3.8")
                    if head1 and head1.lower() not in label.lower():
                        label = f"{head1} {label}"
                    block.append(f"**{label}**")
                for head, cell in zip(header[1:], cells[1:]):
                    if cell:
                        block.append(f"- **{head}:** {cell}")
                if block:
                    blocks.append("\n".join(block))
            out.append("\n\n".join(blocks))
            i = j
        else:
            out.extend(lines[i:max(j, j_end)])
            i = max(j, j_end)
    return "\n".join(out)


def build_continuation_payloads(rest: str) -> list:
    """Round 66: the follow-up message payloads that carry a card body's
    remainder (see the ROUND 66 block above). Text-only Components V2
    containers, same accent color as the card, each opening with a small
    "-# (continued)" line; capped at MAX_CONTINUATIONS messages — any
    further remainder ends the last message with '… full post on Reddit'
    (the Read Post button already links the post)."""
    text = (rest or "").strip()
    if not text:
        return []
    budget = CONTINUATION_CHAR_LIMIT - len(CONTINUATION_HEADER) - 1
    chunks = []
    while text and len(chunks) < MAX_CONTINUATIONS:
        if len(text) <= budget:
            chunks.append(text)
            text = ""
            break
        point = _safe_split_point(text, budget)
        chunks.append(text[:point].rstrip())
        text = text[point:].lstrip()
    chunks = [c for c in chunks if c.strip()]
    if text and chunks:
        chunks[-1] = chunks[-1].rstrip() + f"\n\n{CONTINUATION_TAIL}"
    return [
        {"flags": IS_COMPONENTS_V2,
         "components": [{"type": 17, "accent_color": 16729344,
                         "components": [{"type": 10,
                                         "content": f"{CONTINUATION_HEADER}\n{chunk}"}]}]}
        for chunk in chunks
    ]


def strip_html(value: str | None) -> str:
    """Removes tags from HTML-ish strings (used for JSON selftext etc.).

    Round 65 audit: the html_lib.unescape below is the JSON body path's
    ONE unescape (JSON selftext, crosspost originals, OP comment all flow
    through here) — never add a second pass anywhere upstream.
    """
    if not value:
        return ""
    value = re.sub(r"(?is)<span\s+[^>]*\bclass=[\"\x27][^\"\x27]*\b(?:md-)?spoiler(?:-text)?\b[^\"\x27]*[\"\x27][^>]*>(.*?)</span>", r"||\1||", value)
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


# ---------------------------------------------------------------------------
# ■ ROUND 33 (2026-09-25): CLEAN URL-TEXTED MARKDOWN LINKS (1wpejir/1wpdxhs)
# A well-formed descriptive link whose LABEL is a bare URL and whose TARGET
# differs has no handler: rounds 15-22 intentionally leave descriptive
# links byte-identical (round 22 unwraps only the self-referential '[U](U)'
# class), and the card printed the raw link literally — label, brackets and
# the full ~330-char target included — when the target is a
# youtube.com/redirect wrapper the author copied from a YouTube video
# description (live 2026-09-25, r/Zenlesszonezeroleaks_ 1wpejir + 1wpdxhs,
# M0W1/M2W1 Phoenix showcase posts):
#   ...help of [https://zzzanalytics.vercel.app/](https://www.youtube.com/redirect?event=video_description&redir_token=...&q=https%3A%2F%2Fzzzanalytics.vercel.app%2F&v=...)
# On a CLEAN line (round 22's identical mangle-residue guard), collapse such
# a link to a display form:
#   * label is a bare URL -> the bare URL (Discord auto-links it; when the
#     target is a youtube.com/redirect wrapper the label IS the real site,
#     so the label is the better link to show);
#   * label is not a URL and the target is a youtube.com/redirect wrapper
#     -> 'label (decoded destination)' (the wrapper is an opaque copied
#     artifact; its q= / qp= param carries the real URL).
# Every other line stays byte-identical: prose-labelled descriptive links,
# mangle residue (the round-20/21 repairers'), '[U](U)' (round 22). The
# bare-URL line merge in _repair_label_url_mangle now also receives lines
# this rule turned into bare URLs (a whole-line '[U](redirect)' joins under
# its label exactly like the round-22 class).
# ---------------------------------------------------------------------------
_URL_TEXTED_RE = re.compile(r"\[([^\s\[\]]+)\]\((https?://[^\s()]+)\)")


def _decode_yt_redirect_target(url: str) -> str | None:
    """The real destination of a youtube.com/redirect wrapper: its q= (or
    qp=) param, percent-decoded. None when absent or not an http(s) URL."""
    m = re.search(r"[?&](?:q|qp)=([^&\s]+)", url or "")
    if not m:
        return None
    target = unquote(m.group(1))
    return target if re.match(r"https?://", target) else None


def _clean_url_texted_links(line: str) -> str:
    """Round 33 + Round 41: on a clean line, unwrap URL-labelled links
    (including domain-only labels without http/https scheme) into bare URLs,
    and decode youtube redirect wrappers."""
    if not _URL_TEXTED_RE.search(line):
        return line
    rest = _LINK_OK.sub("", line)
    if re.search(r"https?://", rest) or rest.count("[") != rest.count("]"):
        return line

    def _repl(m):
        label, url = m.group(1), m.group(2)
        if re.match(r"https?://", label):
            return label
        clean_url = re.sub(r"^https?://", "", url).rstrip("/")
        clean_label = label.rstrip("/")
        if clean_label == clean_url or re.match(r"^(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:/[^\s]*)?$", label):
            return url
        if "youtube.com/redirect?" in url:
            target = _decode_yt_redirect_target(url)
            if target:
                return f"{label} ({target})"
        return m.group(0)

    return _URL_TEXTED_RE.sub(_repl, line)


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
        line = _clean_url_texted_links(line)   # round 33
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
# services (vxreddit, redditez, embeddit) and to redlib. So an
# archive-sourced post is only posted when at least one live source can
# actually retrieve it; otherwise it is skipped and NOT cached — once it
# is approved or restored it becomes visible and posts normally on a
# later run. TEST POST rebuilds are unaffected. Round 32 (2026-09-20):
# RSS-sourced posts now run this gate too (live 1wl41aj: the feed delivers
# queued posts with real content) — with feed_ok=False, i.e. the redlib
# post pages only as the fallback, never the feed itself.
# ---------------------------------------------------------------------------

async def verify_archive_post_live(session, path: str, label: str = "",
                                   feed_ok: bool = True):
    """Verify a post is still LIVE on reddit.
    Returns (live: bool, why: str). Round 32: feed_ok=False when the
    candidate ITSELF came from the RSS feed — the feed cannot verify the
    feed (a queued post sits in the feed with real content, live 1wl41aj),
    so the fallback then uses the redlib post pages only."""
    health = _proxy_health or {}
    proxy_down = (reddit_proxy is None or all(
        isinstance(health.get(s), dict) and health[s].get("ok") is False
        for s in ("redditez", "vxreddit", "embeddit")))
    if reddit_proxy is not None:
        try:
            result = await fetch_proxy_post_memo(session, path, label=label)
        except Exception:
            result = None
        if result:
            _reason = removed_post_reason(result.get("title"), result.get("body"))
            if _reason:
                if _reason == "pending approval":
                    return False, (f"live source {result.get('service')} shows "
                                   f"the post is still awaiting moderator "
                                   f"approval")
                return False, (f"live source {result.get('service')} still "
                               f"shows a removal notice ({_reason})")
            return True, f"live via {result.get('service')}"
    # redlib (the "other means"): the post page only exists while the
    # post is live on reddit. Round 32: when the candidate itself came
    # from the RSS feed (feed_ok=False), the feed is NOT a verification
    # source — it is the very place the queued post sits with real
    # content (live 1wl41aj) — so the fallback uses the redlib post
    # pages ONLY (independent live sources).
    base = None
    if feed_ok:
        try:
            base = await fetch_test_post_base(session, path, label)
        except Exception:
            base = None
    else:
        for _inst in REDDIT_RSS_INSTANCES:
            try:
                _page = await _fetch_redlib_post_page(session, _inst, path)
            except Exception:
                _page = None
            if _page:
                base = base_from_redlib_page(_page, path)
                if base:
                    break
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
        # Round 65 (2026-10-05, live 1wxvl13): a line that is ONLY '>'
        # characters (+ whitespace) — ">", "> ", ">>" — is an empty markdown
        # blockquote separator with no content; it renders as a stray
        # literal '>' on the card. Lines with actual quoted content
        # ("> text") stay byte-identical.
        if re.fullmatch(r">[\s>]*", line.strip()):
            continue
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
    # Spoilers in HTML tags -> Discord spoiler ||...||
    text = re.sub(r"(?is)<span\s+[^>]*\bclass=[\"\x27][^\"\x27]*\b(?:md-)?spoiler(?:-text)?\b[^\"\x27]*[\"\x27][^>]*>(.*?)</span>", r"||\1||", text)
    # Round 41: ensure space before links attached directly to preceding text/punctuation
    # (a leading `>` in the class keeps tag-to-tag adjacency like <strong><a> untouched)
    text = re.sub(r"(?is)(?<=[^\s\[\(>])<a\s", " <a ", text)
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


# Round 60 (2026-10-03): Reddit link posts can now contain body text, so the
# RSS body can carry credit URLs before the post destination.  The destination
# itself is the trailing ``[link]`` anchor.  Keep this deliberately scoped to
# that anchor: other anchors in a post body must never become an outbound URL.
_OUTBOUND_LINK_RE = re.compile(
    r"<a\b(?P<attrs>[^>]*)>\s*\[link\]\s*</a\s*>", re.IGNORECASE | re.DOTALL)
_HREF_ATTR_RE = re.compile(
    r"(?<![\w-])href\s*=\s*(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)')",
    re.IGNORECASE)


def extract_outbound_url(content_html: str | None) -> str | None:
    """Return a link post's RSS ``[link]`` destination, if it is external.

    Text posts use the same anchor for their own Reddit permalink, which is
    intentionally returned as ``None`` so they retain the existing body-first
    YouTube selection behaviour.
    """
    # The RSS navigation anchor is trailing.  Choosing the last match avoids
    # treating a user-authored body link literally labelled "[link]" as the
    # post destination.
    anchors = list(_OUTBOUND_LINK_RE.finditer(content_html or ""))
    if not anchors:
        return None
    href = _HREF_ATTR_RE.search(anchors[-1].group("attrs"))
    if not href:
        return None
    url = html_lib.unescape(href.group("double") or href.group("single") or "")
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host in {"reddit.com", "redd.it"} or host.endswith((".reddit.com", ".redd.it")):
        return None
    return url or None


def extract_youtube_url(*html_parts: str | None) -> str | None:
    """Find a YouTube link and return its HTML-unescaped URL.

    RSS content is entity-escaped.  Unescaping here keeps ``&amp;`` out of
    every downstream consumer (button, thumbnail lookup, and follow-up link).
    """
    for html in html_parts:
        match = YOUTUBE_RE.search(html or "")
        if match:
            return html_lib.unescape(match.group(0))
    return None


def select_youtube_url(outbound_url: str | None,
                       *html_parts: str | None) -> str | None:
    """Prefer a YouTube link-post destination over YouTube URLs in its body."""
    destination = extract_youtube_url(outbound_url)
    video_id, _ = extract_youtube_id(destination)
    if video_id:
        return destination
    return extract_youtube_url(*html_parts)


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
    elif not _feedtoken_json_failed and REDDIT_FEED_TOKEN and feedtoken_probe_enabled():
        # Workaround attempt: the personal feed token on a .json endpoint.
        # Anonymous .json is 403 from datacenters; the token MIGHT lift it.
        # Round 31: only reached when REDDIT_JSON_PROBE allows it
        # (auto = only with an OAuth app; force = always; off = never).
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


# ---------------------------------------------------------------------------
# ■ ROUND 67: video candidate validation (no URL is trusted by its name)
# ---------------------------------------------------------------------------
# Native Reddit media that is signed or timestamped expires within hours and
# must never become the card's video URL or be persisted in cache/state.
_EXPIRING_MEDIA_HOSTS = ("packaged-media.redd.it",)
_SIGNATURE_QUERY_KEYS = frozenset({
    "s", "sig", "signature", "hmac", "token", "jwt", "policy",
    "expires", "expire", "e", "se", "sp", "sv",
    "x-amz-signature", "x-amz-credential", "x-amz-expires", "x-amz-date",
})
_VIDEO_QUALITY_HINT_RE = re.compile(r"(?:^|[^0-9])(1080|720|480|360|240|144)(?:p)?(?:[^0-9]|$)")
# Round 67: per-run seconds spent on quality attempts. Once the ceiling is
# reached the resolver uses the durable native ladder directly, so a slow or
# transcode-queued proxy can never stall a whole monitor run.
_video_quality_budget_used = 0.0


def reset_video_quality_budget() -> None:
    """Called once per run (main()); keeps the quality ceiling per-run."""
    global _video_quality_budget_used
    _video_quality_budget_used = 0.0


def media_url_is_durable(url: str) -> bool:
    """True only for a media URL with no signature and no expiry parameter.

    Round 67 policy: signed/expiring native Reddit media (today:
    ``packaged-media.redd.it``, whose render URLs carry ``s=``/``e=``
    parameters) may be used as a short-lived *input* to a proxy during the
    run, but never as the card's own media URL and never persisted.
    Unsigned v.redd.it CMAF/DASH files do not expire and remain usable.
    """
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if any(host == bad or host.endswith("." + bad) for bad in _EXPIRING_MEDIA_HOSTS):
        return False
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except Exception:
        return False
    for key, _value in pairs:
        if str(key).strip().lower() in _SIGNATURE_QUERY_KEYS:
            return False
    return True


def video_quality_hint(url: str) -> int | None:
    """Ordering hint read from a URL *path* — never proof of quality.

    ``.../CMAF_1080.mp4`` -> 1080. The query string is ignored on purpose: a
    muxing URL carries its INPUT quality there, and the live tests showed a
    CMAF_1080 input still producing the service's own output. The resolver
    ranks candidates with this hint and decides with validated facts.
    """
    try:
        path = urlparse(str(url or "")).path
    except Exception:
        return None
    match = _VIDEO_QUALITY_HINT_RE.search(path or "")
    return int(match.group(1)) if match else None


def _mp4_boxes(buf: bytes, start: int, end: int):
    """Yield (type, payload_start, payload_end) for boxes at one level."""
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(buf[pos:pos + 4], "big")
        box_type = buf[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = int.from_bytes(buf[pos + 8:pos + 16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield box_type, pos + header, min(pos + size, end)
        pos += size


def _mp4_tkhd_dims(buf: bytes, start: int, end: int):
    """(width, height) from a tkhd payload, or None. Bounds-checked.

    Layout (ISO/IEC 14496-12): version+flags, creation, modification, track
    id, reserved, duration, then 8+2+2+2+2 reserved/layer/group/volume
    bytes and the 36-byte matrix — so the 16.16 fixed-point width/height
    start at byte 76 (v0) / 88 (v1) of the box payload.
    """
    version = buf[start] if end > start else 0
    offset = start + (88 if version == 1 else 76)
    if offset + 8 > end:
        return None
    width = int.from_bytes(buf[offset:offset + 4], "big") / 65536.0
    height = int.from_bytes(buf[offset + 4:offset + 8], "big") / 65536.0
    if width <= 0 or height <= 0:
        return None
    return int(round(width)), int(round(height))


def _mp4_hdlr_type(buf: bytes, start: int, end: int) -> bytes | None:
    if end - start < 12:
        return None
    return buf[start + 8:start + 12]


def mp4_media_facts(prefix: bytes | None) -> dict | None:
    """Best-effort facts from an MP4 byte prefix; None when there is no moov.

    Returns ``{"width", "height", "has_video", "has_audio"}`` when a readable
    ``moov`` box is inside the prefix (faststart files and Reddit's own
    renditions). A fragmented/truncated prefix, a moov at the end of the file
    or any malformed box simply yields None — never an exception, so this can
    never fail a resolution.
    """
    if not isinstance(prefix, (bytes, bytearray)) or len(prefix) < 16:
        return None
    buf = bytes(prefix)
    width = height = None
    has_video = has_audio = False
    found = False
    for box_type, p_start, p_end in _mp4_boxes(buf, 0, len(buf)):
        if box_type != b"moov":
            continue
        found = True
        for sub_type, s_start, s_end in _mp4_boxes(buf, p_start, p_end):
            if sub_type != b"trak":
                continue
            handler = None
            dims = None
            for leaf_type, l_start, l_end in _mp4_boxes(buf, s_start, s_end):
                if leaf_type == b"tkhd":
                    dims = _mp4_tkhd_dims(buf, l_start, l_end)
                elif leaf_type == b"mdia":
                    for mdia_type, m_start, m_end in _mp4_boxes(buf, l_start, l_end):
                        if mdia_type == b"hdlr":
                            handler = _mp4_hdlr_type(buf, m_start, m_end)
            if handler == b"vide":
                has_video = True
                if dims and not (width and height):
                    width, height = dims
            elif handler == b"soun":
                has_audio = True
    if not found:
        return None
    return {"width": width, "height": height,
            "has_video": has_video, "has_audio": has_audio}


def video_orientation(facts: dict | None) -> str | None:
    """'portrait' / 'landscape' / 'square' / None from validated dimensions."""
    if not isinstance(facts, dict):
        return None
    width, height = facts.get("width"), facts.get("height")
    if not width or not height:
        return None
    if height > width:
        return "portrait"
    if width > height:
        return "landscape"
    return "square"


def _looks_like_error_body(prefix: bytes | None) -> bool:
    """True for the HTML/JSON error pages a proxy returns on success codes."""
    if not isinstance(prefix, (bytes, bytearray)) or not prefix:
        return False
    head = bytes(prefix[:512]).lstrip().lower()
    return head[:1] in (b"<", b"{") or head.startswith(b"\xef\xbb\xbf<")


async def video_candidate(session: aiohttp.ClientSession, url: str, *,
                          timeout: float | None = None,
                          quality_hint: int | None = None,
                          label: str = "") -> dict | None:
    """Range-probe ONE video URL; return validated facts, or None.

    Usable only when: the request completes inside the candidate timeout, the
    status is 200/206, the body prefix is not an HTML/JSON error page, the
    content type is compatible (or the body carries real MP4 structure), and
    the known size is under MAX_MEDIA_BYTES. Never raises, never blocks past
    ``timeout``. Signed/expiring URLs are refused outright.
    """
    url = str(url or "").strip()
    if not url:
        return None
    tag = label or "video"
    if not media_url_is_durable(url):
        logging.info(f"[{tag}] signed/expiring media URL refused as a card "
                     f"candidate: {url[:90]}")
        return None
    limit = VIDEO_CANDIDATE_TIMEOUT_SECONDS if timeout is None else min(
        float(timeout), float(VIDEO_CANDIDATE_TIMEOUT_SECONDS))
    limit = max(1.0, limit)
    facts = None
    try:
        async with session.get(
                url,
                headers={"User-Agent": "Discordbot/2.0",
                         "Range": f"bytes=0-{max(1, int(VIDEO_PROBE_BYTES)) - 1}"},
                timeout=aiohttp.ClientTimeout(total=limit)) as resp:
            status = resp.status
            ctype = (resp.headers.get("Content-Type") or "").lower()
            size = 0
            content_range = resp.headers.get("Content-Range") or ""
            if "/" in content_range:
                try:
                    size = int(content_range.rsplit("/", 1)[1])
                except (TypeError, ValueError, IndexError):
                    size = 0
            if not size and status == 200:
                try:
                    size = int(resp.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    size = 0
            if status not in (200, 206):
                logging.info(f"[{tag}] candidate rejected — HTTP {status}: {url[:90]}")
                return None
            if size and size > MAX_MEDIA_BYTES:
                logging.info(f"[{tag}] candidate rejected — {size} bytes exceeds "
                             f"the {MAX_MEDIA_BYTES}-byte media limit: {url[:90]}")
                return None
            try:
                prefix = await resp.content.read(max(1, int(VIDEO_PROBE_BYTES)))
            except Exception:
                prefix = b""
            if _looks_like_error_body(prefix):
                logging.info(f"[{tag}] candidate rejected — HTML/JSON error body "
                             f"despite HTTP {status}: {url[:90]}")
                return None
            ctype_ok = (not ctype) or any(token in ctype for token in
                                          ("video", "octet-stream", "mp4"))
            mp4_facts = mp4_media_facts(prefix)
            if not ctype_ok:
                logging.info(f"[{tag}] candidate rejected — content type "
                             f"{ctype!r} is not a video type: {url[:90]}")
                return None
            facts = mp4_facts
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logging.info(f"[{tag}] candidate failed ({exc.__class__.__name__}: "
                     f"{exc}) — {url[:90]}")
        return None
    return {
        "url": url,
        "size": size,
        "width": (facts or {}).get("width"),
        "height": (facts or {}).get("height"),
        "has_video": (facts or {}).get("has_video"),
        "has_audio": (facts or {}).get("has_audio"),
        "quality_hint": quality_hint if quality_hint is not None
                        else video_quality_hint(url),
        "label": tag,
    }


def _video_evidence(candidate: dict | None) -> str:
    """Conservative evidence: URL quality hints are never probe evidence."""
    if not isinstance(candidate, dict):
        return "none"
    bits = [f"bytes={candidate['size']}" if candidate.get("size") else "bytes=unknown"]
    if candidate.get("width") and candidate.get("height"):
        bits.append(f"dimensions={candidate['width']}x{candidate['height']} (verified)")
    else:
        bits.append("dimensions=unverified; URL quality hint was not used as proof")
    if candidate.get("has_audio") is True:
        bits.append("audio=present")
    elif candidate.get("has_audio") is False:
        bits.append("audio=absent")
    else:
        bits.append("audio=unknown")
    if candidate.get("quality_hint"):
        bits.append(f"url_quality_hint={candidate['quality_hint']}p (not evidence)")
    return ", ".join(bits)


def _provider_for_url(url: str) -> str:
    """Return a safe, stable provider label (never expose the full URL)."""
    host = urllib.parse.urlparse(str(url or "")).netloc.lower()
    if "embedez" in host or "redditez" in host:
        return "embedez"
    if "vxreddit" in host:
        return "vxreddit"
    if "embeddit" in host:
        return "embeddit"
    if "v.redd.it" in host:
        return "native-dash"
    return "unknown"


def prefer_quality_candidate(quality: dict | None, base: dict | None) -> dict | None:
    """Pick between the EmbedEZ 1080p opportunity and the vxreddit base.

    The quality candidate wins only when it passed validation AND the probe
    evidence does not contradict the quality claim: no orientation flip
    (portrait media is never replaced by a landscape output, or vice versa),
    no resolution downgrade, and no loss of an audio track the base carries.
    """
    if quality is None:
        return base
    if base is None:
        return quality
    q_orient, b_orient = video_orientation(quality), video_orientation(base)
    if q_orient and b_orient and q_orient != b_orient:
        logging.info(f"[video] EmbedEZ 1080p candidate is {q_orient} while the "
                     f"vxreddit result is {b_orient} — keeping vxreddit.")
        return base
    q_pixels = (quality.get("width") or 0) * (quality.get("height") or 0)
    b_pixels = (base.get("width") or 0) * (base.get("height") or 0)
    if q_pixels and b_pixels and q_pixels < b_pixels:
        logging.info(f"[video] EmbedEZ 1080p candidate returns fewer pixels "
                     f"({q_pixels}) than the vxreddit result ({b_pixels}) — "
                     f"keeping vxreddit.")
        return base
    if quality.get("has_audio") is False and base.get("has_audio") is True:
        logging.info("[video] EmbedEZ 1080p candidate has no audio track while "
                     "the vxreddit result does — keeping vxreddit.")
        return base
    return quality


async def probe_video_facts(session: aiohttp.ClientSession, url: str, *,
                            timeout: float = 10.0) -> dict | None:
    """Best-effort MP4 facts for a media URL that is ALREADY validated.

    Round 67 proxy-path comparison helper. It never gates the URL itself —
    the caller has already range-checked it as card media — it only gives
    ``prefer_quality_candidate`` real dimension/audio evidence with which to
    compare the established proxy video against the EmbedEZ 1080p
    opportunity. Never raises; None when the prefix cannot be read.
    """
    url = str(url or "").strip()
    if not url:
        return None
    try:
        async with session.get(
                url,
                headers={"User-Agent": "Discordbot/2.0",
                         "Range": f"bytes=0-{max(1, int(VIDEO_PROBE_BYTES)) - 1}"},
                timeout=aiohttp.ClientTimeout(total=max(1.0, float(timeout)))) as resp:
            if resp.status not in (200, 206):
                return None
            prefix = await resp.content.read(max(1, int(VIDEO_PROBE_BYTES)))
    except asyncio.CancelledError:
        raise
    except Exception:
        return None
    if _looks_like_error_body(prefix):
        return None
    return mp4_media_facts(prefix)


async def proxy_video_quality_upgrade(session: aiohttp.ClientSession, vid: str | None,
                                      base_url: str, label: str = "") -> str:
    """One bounded EmbedEZ 1080p opportunity for a PROXY-delivered video.

    The proxy chain's already-validated muxed mp4 stays the HELD base: this
    only replaces it when a validated EmbedEZ 1080p candidate survives
    ``prefer_quality_candidate`` against the base's own probe facts (never an
    orientation flip, never fewer pixels, never an audio-track loss). Any
    failure — timeout, HTTP error, error body, oversize, unreadable probe, a
    spent per-run budget, or compatibility mode — returns ``base_url``
    unchanged, so a proxy video is never lost to the quality attempt and the
    extra probe happens only on Reddit-video posts (never on image, GIF,
    photo or gallery paths).
    """
    global _video_quality_budget_used
    if not vid or not base_url:
        return base_url
    mode = VIDEO_QUALITY_MODE if VIDEO_QUALITY_MODE in VIDEO_QUALITY_MODES else "balanced"
    if mode == "compatibility":
        return base_url
    if _video_quality_budget_used >= float(VIDEO_RUN_BUDGET_SECONDS):
        logging.info(f"[{label or 'proxy'}] per-run quality budget spent — keeping "
                     f"the validated proxy video.")
        return base_url
    started = time.monotonic()
    cmaf1080 = cmaf_urls(vid, 1080)
    embedez_1080 = VIDEO_PROXY_EMBEDEZ.format(
        video_url=quote(cmaf1080["video_mp4"], safe=""),
        audio_url=quote(cmaf1080["audio_mp4"], safe=""))
    window = (float(VIDEO_QUALITY_WINDOW_HIGHEST_SECONDS) if mode == "highest"
              else float(VIDEO_QUALITY_WINDOW_SECONDS))
    try:
        quality = await asyncio.wait_for(
            video_candidate(session, embedez_1080,
                            timeout=float(VIDEO_CANDIDATE_TIMEOUT_SECONDS),
                            quality_hint=1080,
                            label=f"{label or 'proxy'} embedez-1080p"),
            timeout=max(0.05, window))
    except asyncio.CancelledError:
        raise
    except Exception:
        quality = None
    _video_quality_budget_used += max(0.0, time.monotonic() - started)
    if not quality:
        logging.info(f"[{label or 'proxy'}] quality fallback: candidate=embedez; "
                     f"retaining validated base provider={_provider_for_url(base_url)}; "
                     "reason=quality_candidate_unavailable")
        return base_url
    facts = await probe_video_facts(session, base_url,
                                   timeout=float(VIDEO_NATIVE_TIMEOUT_SECONDS))
    base = {"url": base_url,
            "width": (facts or {}).get("width"),
            "height": (facts or {}).get("height"),
            "has_audio": (facts or {}).get("has_audio")}
    winner = prefer_quality_candidate(quality, base)
    if isinstance(winner, dict) and str(winner.get("url") or "") == str(quality.get("url")):
        content_type = (quality.get("content_type") or quality.get("mime_type")
                       or "validated")
        logging.info(f"[{label or 'proxy'}] EmbedEZ 1080p candidate accepted: "
                     f"provider=embedez, content_type={content_type}, "
                     f"{_video_evidence(quality)}, reason=validated quality improvement.")
        return quality["url"]
    logging.info(f"[{label or 'proxy'}] quality fallback: candidate=embedez; "
                 f"retaining validated base provider={_provider_for_url(base_url)}; "
                 "reason=quality_candidate_not_superior_or_unvalidated")
    return base_url


def _task_video_facts(task) -> dict | None:
    """Validated facts from a finished task, else None (never raises)."""
    if task is None or not task.done() or task.cancelled():
        return None
    try:
        result = task.result()
    except Exception:
        return None
    if isinstance(result, dict) and result.get("url"):
        return result
    return None


async def _race_quality_candidates(session, vx_url: str, ez_url: str, window: float,
                                   budget: float, label: str):
    """Run the vxreddit base and the EmbedEZ 1080p opportunity concurrently.

    vxreddit is started FIRST and is never starved: the wait is bounded by
    ``window`` (and by the caller's remaining budget), so a slow or hung
    EmbedEZ request can only ever cost the window — never the video.
    Returns ``(embedez_facts, vxreddit_facts)``, each None when that
    candidate did not validate.
    """
    tag = label or "video"
    vx_task = asyncio.ensure_future(video_candidate(
        session, vx_url, timeout=budget, quality_hint=CMAF_QUALITY,
        label=f"{tag} vxreddit"))
    ez_task = asyncio.ensure_future(video_candidate(
        session, ez_url, timeout=budget, quality_hint=1080,
        label=f"{tag} embedez-1080p"))
    tasks = (vx_task, ez_task)
    hold_until = time.monotonic() + max(0.0, float(window))
    try:
        while True:
            pending = [task for task in tasks if not task.done()]
            if not pending:
                break
            if _task_video_facts(ez_task) is not None:
                break  # the quality candidate is in — stop holding the post
            if _task_video_facts(vx_task) is not None and ez_task.done():
                break  # base validated and EmbedEZ already failed
            remaining = hold_until - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.wait(pending, timeout=remaining,
                               return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return _task_video_facts(ez_task), _task_video_facts(vx_task)


async def resolve_video_url(session: aiohttp.ClientSession, vid: str,
                            fallback_url: str | None = None, *,
                            label: str = "") -> str | None:
    """Resolve the card's video URL — quality-aware, never a silent video.

    Round 67 order (REDDIT_VIDEO_QUALITY):

      balanced (default) — EmbedEZ 1080p and vxreddit are attempted together;
        a validated EmbedEZ 1080p result replaces vxreddit only when the probe
        evidence supports it (see prefer_quality_candidate). If neither
        validates, EmbedEZ 720p is tried, and the durable native v.redd.it
        DASH ladder is the reliability floor. Nothing usable -> None, and the
        caller shows thumbnail + Read Post.

      compatibility — the pre-round-67 ladder, unchanged in order (native
        DASH first, then EmbedEZ 720p, vxreddit, EmbedEZ 1080p): the
        zero-code rollback.

      highest — as balanced, with the wider quality window.

    Every candidate is validated (HTTP status, content type, error-body
    sniff, size limit, candidate timeout, and — when the byte prefix allows
    it — real MP4 dimensions and audio-track evidence). A URL containing
    "1080" is never accepted as proof of quality.
    """
    global _video_quality_budget_used
    started = time.monotonic()
    deadline = started + max(1.0, float(VIDEO_RESOLVE_BUDGET_SECONDS))
    mode = VIDEO_QUALITY_MODE if VIDEO_QUALITY_MODE in VIDEO_QUALITY_MODES else "balanced"
    tag = label or "video"

    def _budget() -> float:
        return max(0.0, deadline - time.monotonic())

    def _native_timeout() -> float:
        return min(_budget(), float(VIDEO_NATIVE_TIMEOUT_SECONDS))

    def _proxy_timeout() -> float:
        return min(_budget(), float(VIDEO_CANDIDATE_TIMEOUT_SECONDS))

    native_ladder = []
    if fallback_url:
        fallback_url = str(fallback_url)
        if ("v.redd.it" in fallback_url
                and fallback_url.lower().split("?")[0].endswith(".mp4")):
            native_ladder.append((fallback_url, video_quality_hint(fallback_url)))
        elif "packaged-media.redd.it" in fallback_url:
            logging.info(f"[{tag}] signed packaged-media fallback_url is not a "
                         f"durable candidate — using the CMAF ladder instead.")
    if vid:
        native_ladder.extend((f"https://v.redd.it/{vid}/DASH_{quality}.mp4", quality)
                             for quality in DASH_QUALITIES)

    async def _try_urls(candidates, timeout: float, kind: str) -> dict | None:
        for url, hint in candidates:
            if _budget() <= 0:
                logging.info(f"[{tag}] resolution budget exhausted before {kind} "
                             f"candidate {url[:70]}.")
                return None
            facts = await video_candidate(session, url, timeout=timeout,
                                          quality_hint=hint, label=tag)
            if facts:
                logging.info(f"[{tag}] video url OK via {kind} "
                             f"({_video_evidence(facts)}).")
                return facts
        return None

    # ---- compatibility: the exact pre-round-67 ladder --------------------
    if mode == "compatibility":
        native = await _try_urls(native_ladder,
                                 float(VIDEO_NATIVE_TIMEOUT_SECONDS), "native v.redd.it")
        if native:
            return native["url"]
        if vid:
            cmaf720 = cmaf_urls(vid, CMAF_QUALITY)
            embedez_720 = VIDEO_PROXY_EMBEDEZ.format(
                video_url=quote(cmaf720["video_mp4"], safe=""),
                audio_url=quote(cmaf720["audio_mp4"], safe=""))
            facts = await video_candidate(session, embedez_720,
                                          timeout=_proxy_timeout(),
                                          quality_hint=CMAF_QUALITY, label=tag)
            if facts:
                logging.info(f"[{tag}] video url OK via embedez-proxy "
                             f"q{CMAF_QUALITY} ({_video_evidence(facts)}).")
                return facts["url"]
            vx_url = VIDEO_PROXY_VXREDDIT.format(
                video_url=quote(cmaf720["video_m3u8"], safe=""),
                audio_url=quote(cmaf720["audio_m3u8"], safe=""))
            vx_facts = await video_candidate(session, vx_url,
                                             timeout=_proxy_timeout(),
                                             quality_hint=CMAF_QUALITY, label=tag)
            if vx_facts:
                logging.info(f"[{tag}] video url OK via vxreddit-proxy "
                             f"({_video_evidence(vx_facts)}).")
                return vx_facts["url"]
            cmaf1080 = cmaf_urls(vid, 1080)
            embedez_1080 = VIDEO_PROXY_EMBEDEZ.format(
                video_url=quote(cmaf1080["video_mp4"], safe=""),
                audio_url=quote(cmaf1080["audio_mp4"], safe=""))
            facts = await video_candidate(session, embedez_1080,
                                          timeout=_proxy_timeout(),
                                          quality_hint=1080, label=tag)
            if facts:
                logging.info(f"[{tag}] video url OK via embedez-proxy q1080 "
                             f"({_video_evidence(facts)}).")
                return facts["url"]
        logging.info(f"[{tag}] video chain exhausted — using thumbnail + button "
                     f"(no silent video).")
        return None

    # ---- balanced / highest: bounded quality opportunity -----------------
    quality_allowed = _video_quality_budget_used < float(VIDEO_RUN_BUDGET_SECONDS)
    if not quality_allowed:
        logging.info(f"[{tag}] per-run quality budget spent — using the durable "
                     f"native ladder.")
    elif vid and _budget() > 0:
        cmaf1080 = cmaf_urls(vid, 1080)
        embedez_1080 = VIDEO_PROXY_EMBEDEZ.format(
            video_url=quote(cmaf1080["video_mp4"], safe=""),
            audio_url=quote(cmaf1080["audio_mp4"], safe=""))
        vx_1080 = VIDEO_PROXY_VXREDDIT.format(
            video_url=quote(cmaf1080["video_m3u8"], safe=""),
            audio_url=quote(cmaf1080["audio_m3u8"], safe=""))
        window = (float(VIDEO_QUALITY_WINDOW_HIGHEST_SECONDS) if mode == "highest"
                  else float(VIDEO_QUALITY_WINDOW_SECONDS))
        window = min(window, _budget())
        ez_facts, vx_facts = await _race_quality_candidates(
            session, vx_1080, embedez_1080, window, _proxy_timeout(), tag)
        _video_quality_budget_used += max(0.0, time.monotonic() - started)
        winner = prefer_quality_candidate(ez_facts, vx_facts)
        if winner is not None:
            source = "embedez-1080p" if winner is ez_facts else "vxreddit"
            logging.info(f"[{tag}] video url OK via {source} "
                         f"({_video_evidence(winner)}).")
            return winner["url"]
        # 720p is a lower-quality fallback: it is only reached when neither
        # the vxreddit base nor the EmbedEZ 1080p opportunity validated, so
        # it can never displace a healthy vxreddit result.
        cmaf720 = cmaf_urls(vid, CMAF_QUALITY)
        embedez_720 = VIDEO_PROXY_EMBEDEZ.format(
            video_url=quote(cmaf720["video_mp4"], safe=""),
            audio_url=quote(cmaf720["audio_mp4"], safe=""))
        facts = await video_candidate(session, embedez_720, timeout=_proxy_timeout(),
                                      quality_hint=CMAF_QUALITY, label=tag)
        if facts:
            logging.info(f"[{tag}] video url OK via embedez-720p "
                         f"({_video_evidence(facts)}).")
            return facts["url"]

    # ---- durable native ladder: the reliability floor --------------------
    native = await _try_urls(native_ladder, _native_timeout(), "native v.redd.it")
    if native:
        return native["url"]

    logging.info(f"[{tag}] video chain exhausted — using thumbnail + button "
                 f"(no silent video).")
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
        # Round 65 audit: the OP comment body unescapes exactly once —
        # inside strip_html (never again downstream).
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


# Round 38c (2026-09-30): a gallery post can carry a reddit-hosted VIDEO
# item (1wt59lm: image + clip). The image harvest above never sees it —
# themes either inline a v.redd.it URL or link the instance's player page
# (/link/<post>/video/<id>); both carry the same base62 id the DASH chain
# resolves. (Markup verified live on safereddit, 2026-09-30.)
REDDIL_PLAYER_VIDEO_RE = re.compile(r"/link/[a-z0-9]+/video/([a-z0-9]{5,20})")


def extract_redlib_video_id(page_html: str | None) -> str | None:
    """The reddit video id of a GALLERY video item on a redlib post page
    (post area only), or None when the page has no video."""
    if not page_html:
        return None
    area = _redlib_post_area(page_html)
    vid = extract_vreddit_id(area)
    if vid:
        return vid
    pm = REDDIL_PLAYER_VIDEO_RE.search(area)
    return pm.group(1) if pm else None


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
        # Round 65 audit: this body's HTML entities are unescaped exactly
        # once — inside clean_rss_body (the round-16 fixpoint the feed's
        # double-escaping requires).
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
    # Round 42: per-run memo. Successful page fetches are reused by the
    # liveness gate and media enrichment; misses
    # are deliberately not cached so a transient failure stays retryable.
    key = (str(instance or ""), str(path or ""))
    if key in _redlib_post_page_cache:
        return _redlib_post_page_cache[key]
    _redlib_reach.setdefault(str(instance or ""), [0, 0])[1] += 1
    try:
        async with session.get(_with_miningtcup_token(f"{instance}{path}"),
                               headers=BROWSER_HEADERS,
                               timeout=aiohttp.ClientTimeout(total=timeout),
                               allow_redirects=True) as resp:
            if resp.status == 200 and "html" in (resp.headers.get("Content-Type") or "").lower():
                text = await resp.text()
                if text:
                    _redlib_post_page_cache[key] = text
                    _redlib_reach.setdefault(str(instance or ""), [0, 0])[0] += 1
                return text
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
    # Round 47: profile posts route under BOTH /user/<name>/... and
    # /r/u_<name>/...; try the alias when no mirror rendered the first form.
    if not any(pages):
        for alias in _path_aliases(path)[1:]:
            pages = await asyncio.gather(
                *[_fetch_redlib_post_page(session, inst, alias)
                  for inst in REDDIT_RSS_INSTANCES]
            )
            if any(pages):
                logging.info(f"[{label}] profile post pages via alias {alias}")
                path = alias
                break
    video_vid = None
    for instance, html in zip(REDDIT_RSS_INSTANCES, pages):
        if not html:
            continue
        if video_vid is None:
            video_vid = extract_redlib_video_id(html)
        items = extract_redlib_gallery(html)
        if items:
            # Round 38c: a gallery can carry a reddit-hosted VIDEO item
            # (1wt59lm: image + clip) the image harvest above never sees —
            # the redlib page renders it as a player link. Resolve it
            # through the same DASH chain the native video path uses (no
            # silent fallback — only a range-checked mp4 ever lands on the
            # card); "gallery" marks it so the round-13 video-tile-only
            # rule keeps the post's photos.
            if video_vid and not any(i["kind"] == "video" for i in items):
                _vurl = await resolve_video_url(session, video_vid, None,
                                            label=f"{label} gallery video")
                if _vurl:
                    items.append({"kind": "video", "url": _vurl, "gallery": True})
                    logging.info(f"[{label}] redlib gallery video {video_vid} "
                                 f"resolved — {len(items)} media item(s).")
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
        # Round 65 audit: this body's HTML entities are unescaped exactly
        # once — inside clean_rss_body (the round-16 fixpoint the feed's
        # double-escaping requires).
        "body": clean_rss_body(content_html),
        "crosspost_orig_path": find_crosspost_original_path(
            content_html, normalize_reddit_path(str(getattr(entry, "link", "")))),
        "vred_id": extract_vreddit_id(content_html),
        "redgifs_url": extract_redgifs_url(content_html),
        # Round 60: a link post's destination is the main video; body links
        # are often credits and must not outrank it.
        "youtube_url": select_youtube_url(extract_outbound_url(content_html),
                                          content_html),
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
    body = base["body"]  # round 65 audit: RSS/redlib/mirror base bodies are unescaped exactly once at build time (clean_rss_body / reddit_proxy.clean_proxy_body)
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
            # Round 65 audit: the JSON selftext unescapes exactly once —
            # inside strip_html (no second pass anywhere downstream).
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
        # Round 60: post_json["url"] is the link-post destination.  It
        # outranks a YouTube credit URL that RSS or selftext exposed first.
        _dest_yt = extract_youtube_url(str(post_json.get("url") or ""))
        if _dest_yt:
            yt_url = _dest_yt
        elif not yt_url:
            yt_url = extract_youtube_url(str(post_json.get("selftext") or ""))
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
                            # Round 65 audit: the crosspost original's
                            # selftext unescapes exactly once — inside
                            # strip_html (never again downstream).
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
                    and re.fullmatch(r"/(?:r|user)/[^/]+/comments/[a-z0-9]+/[^?#]*",
                                     original_path)):
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
        # Round 65 (2026-10-05, live 1wxvl13): the Arctic body path now
        # unescapes HTML entities EXACTLY ONCE, inside _clean_plain_body —
        # completing the round-65 body-entity audit: JSON selftext / crosspost
        # originals / OP comment unescape inside strip_html, RSS + redlib
        # content inside clean_rss_body (round-16 fixpoint), proxy bodies
        # inside reddit_proxy.clean_proxy_body, and Arctic selftext HERE.
        # No path unescapes twice ("&amp;gt;" must render "&gt;", not ">").
        body = _clean_plain_body(arctic.get("selftext"))

    # ---- round 13: PROXY media services (native mode only) ---------------
    # Tie-break priority vxreddit.com -> redditez.com (EmbedEZ) ->
    # embeddit.deltandy.me, dispatched as bounded concurrent waves (see
    # testing area/reddit_proxy.py). The winning service's own
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
        tasks = [fetch_proxy_post_memo(session, fetch_path,
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
                    # Round 65 audit: proxy bodies arrive pre-unescaped
                    # (exactly once) from reddit_proxy.py's parsers
                    # (clean_proxy_body & friends) — no second unescape here.
                    body = proxy["body"][:MAX_BODY_CHARS]

            video_dead = bool(proxy_video) and not video_ok
            if proxy_video and video_ok:
                # Round 67: one bounded quality opportunity for a PROXY-muxed
                # Reddit video — the validated proxy result is the held base,
                # so this can only ever improve it (video-only; the helper
                # returns the base unchanged on any failure).
                _q_url = await proxy_video_quality_upgrade(
                    session, vid, proxy_video["url"], label or "proxy")
                if _q_url and _q_url != proxy_video["url"]:
                    proxy_video = dict(proxy_video, url=_q_url)
                # video post: the proxy's muxed mp4 tile ONLY (never a dup
                # first-frame thumbnail)
                media = [proxy_video]
                _use_proxy_text()
                proxy_media_used = True
                logging.info(f"[{label or 'proxy'}] card media via "
                             f"{_provider_for_url(proxy_video['url'])} — 1 video tile.")
            elif (not video_dead and not arctic_is_video and redlib_items
                  and any(x["kind"] == "video" for x in redlib_items)):
                # round 38c: a redlib gallery WITH a video item is the most
                # complete source — every photo + the video tile (1wt59lm)
                media = [dict(x) for x in redlib_items
                         if not (has_video and _is_external_preview(x["url"]))]
                _use_proxy_text()
                proxy_media_used = True
                logging.info(f"[{label or 'proxy'}] card media via redlib harvest "
                             f"(gallery video) — {len(media)} item(s).")
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
        video_url = await resolve_video_url(session, vid, fallback_url,
                                   label=label or "video")

    if video_url:
        media.append({"kind": "video", "url": video_url})
        # drop the video's own screenshot (external-preview) if one slipped in
        media = [x for x in media
                 if x["kind"] in ("video", "gif") or not _is_external_preview(x["url"])]
    else:
        if proxy_media_used and any(x["kind"] == "video" and not x.get("gallery")
                                    for x in media):
            # round 13: the proxy video tile only (no first-frame / poster dup)
            # (round 38c: a GALLERY video item keeps the post's photos)
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
                if arctic_items and not any(x["kind"] == "video" for x in redlib_items):
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

    # Round 38a (2026-09-30): link posts carry the YouTube URL as the
    # DESTINATION (youtu.be/... as the post URL, a bare URL line in the
    # final body) — base-time extraction only saw the RSS content, which
    # is empty for link posts (live 2026-09-30: 1wtmbmh went out as a
    # bare URL line with no button). The final body is the last place
    # the URL can be — extract it before the tile/button build.
    if not yt_url:
        yt_url = extract_youtube_url(body)
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


def build_v3_payload(subreddit: str, data: dict, reddit_url: str, posted_ts: int,
                     *, return_remainder: bool = False):
    """
    <=10 media items -> single container (V2 look):
        header / body / divider / gallery / stats / divider / buttons
    11-20 media items -> two containers:
        container 1: header / body / divider / gallery(10)
        container 2: divider / gallery(rest) / stats / divider / buttons
    (Discord: 10 items per gallery, 10 components per container, 40 total.)

    Round 66: a body longer than the card's text budget is SPLIT at a safe
    boundary instead of truncated — the main card keeps the first chunk
    (layout identical), and return_remainder=True also hands back the rest
    as (payload, rest) so the posting loop can deliver it as follow-up
    continuation messages (build_continuation_payloads).
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
        _cp_profile = (re.search(r"/u(?:ser)?/([^/\s?]+)/comments/", _cp.get("path") or "")
                       or re.search(r"reddit\.com/u(?:ser)?/([^/\s?]+)/comments/", _cp_url))
        if not _cp_profile and _cp_sub and _cp_sub.group(1).lower().startswith("u_"):
            _cp_profile_name = _cp_sub.group(1)[2:]
        elif _cp_profile:
            _cp_profile_name = _cp_profile.group(1)
        else:
            _cp_profile_name = None
        if _cp_profile_name:
            header += (f"\n*🔁 Crosspost of [u/{_cp_profile_name}]"
                       f"(https://www.reddit.com/user/{_cp_profile_name}/) Profile*")
        elif not _cp_sub and _cp.get("subreddit"):
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
    # Round 38c (2026-09-30): a video tile renders in its OWN media block —
    # photos in one gallery, the video in a separate one below it (Discord
    # renders every type-12 as its own block). No video -> identical card.
    _vid_media = [m for m in media if m["kind"] == "video"]
    _photo_media = [m for m in media if m["kind"] != "video"]
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
    # Round 62: an inline-image link that is ALSO a gallery item never
    # renders twice — the raw-URL body line is scrubbed (live 1wx9c77)
    # Round 66: markdown tables become per-row bold-label bullets (Discord
    # renders no table markdown — live 1wxfuj5), and the budget split lands
    # on a safe boundary — never inside a link/URL/spoiler/marker pair. The
    # remainder is carried by follow-up continuation messages; no ellipsis.
    body_src = _markdown_tables_to_bullets(
        _drop_gallery_media_lines(data["body"], media))
    body_out, body_rest = split_card_body(body_src, body_budget)

    def gallery(items: list) -> dict:
        return {"type": 12, "items": [{"media": {"url": m["url"]}} for m in items]}

    # Round 38c hardening: the split MUST key on the PHOTO count — with
    # exactly 10 photos + a gallery video, len(media)=11 would open the
    # two-container branch with an EMPTY second photo gallery (a type-12
    # with 0 items is a Discord 400 — the whole card would be rejected).
    if len(_photo_media) > MEDIA_PER_GALLERY:
        first, second = _photo_media[:MEDIA_PER_GALLERY], _photo_media[MEDIA_PER_GALLERY:]
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
        ]}
        if _vid_media:
            # round 38c: the video tile keeps its OWN media block
            container2["components"].append(gallery(_vid_media))
        container2["components"].append({"type": 10, "content": stats_line})
        container2["components"].append({"type": 14, "divider": True, "spacing": 1})
        container2["components"].append(row)
        payload = {"flags": IS_COMPONENTS_V2,
                   "components": [container1, container2]}
        if return_remainder:
            return payload, body_rest
        return payload

    inner = [{"type": 10, "content": header}]
    if body_out:
        inner.append({"type": 10, "content": body_out})
    if op_line:
        inner.append({"type": 10, "content": op_line})
    if media:
        # Round 38c hardening: the divider precedes ANY media (a video-only
        # card — the round-13 tile — keeps its legacy divider too).
        inner.append({"type": 14, "divider": True, "spacing": 1})
    if _photo_media:
        inner.append(gallery(_photo_media))
    if _vid_media:
        # round 38c: the video tile gets its OWN media block, below the photos
        inner.append(gallery(_vid_media))
    inner.append({"type": 10, "content": stats_line})
    inner.append({"type": 14, "divider": True, "spacing": 1})
    inner.append(row)
    payload = {"flags": IS_COMPONENTS_V2,
               "components": [{"type": 17, "accent_color": 16729344, "components": inner}]}
    if return_remainder:
        return payload, body_rest
    return payload


def tombstone_payload(payload: dict, reason: str | None = None) -> dict:
    return reddit_signals.tombstone_payload(payload, reason)


def tombstone_continuation_payload(reason: str | None = None) -> dict:
    """Round 66: the tombstone replacement for a body-continuation message
    (the main card keeps its full tombstone via tombstone_payload)."""
    return reddit_signals.tombstone_continuation_payload(reason)


async def live_removal_reason(session, path: str, label: str = "") -> str | None:
    """Return a live removal verdict, not just absence/outage."""
    if reddit_proxy is not None:
        try:
            result = await fetch_proxy_post_memo(session, path, label=label)
        except Exception:
            result = None
        if result:
            reason = removed_post_reason(result.get("title"), result.get("body"))
            if reason:
                return reason
    for instance in REDDIT_RSS_INSTANCES:
        page = await _fetch_redlib_post_page(session, instance, path)
        if not page:
            continue
        base = base_from_redlib_page(page, path)
        if base:
            reason = removed_post_reason(base.get("title"), base.get("body"))
            if reason:
                return reason
    return None


async def retract_dead_posts(session, posted_messages: dict, now: float) -> None:
    if not RETRACT_DEAD_POSTS or not posted_messages:
        return
    for unique_key, record in list(posted_messages.items()):
        if not isinstance(record, dict) or record.get("retracted"):
            continue
        if now - float(record.get("delivered_at") or 0) > RETRACT_WINDOW_SECONDS:
            continue
        subreddit = record.get("subreddit") or unique_key.rsplit("_", 1)[0]
        path = record.get("path") or ""
        post_id = extract_post_id(path) or unique_key.rsplit("_", 1)[-1]
        listing = await _fetch_new_listing(session, subreddit)
        listing_ids, listing_oldest_age = listing if listing else (None, None)
        reason = await live_removal_reason(session, path, label=f"retract {unique_key}")
        decision = reddit_signals.retraction_decision(
            enabled=RETRACT_DEAD_POSTS,
            mode=RETRACT_MODE,
            post_id=post_id,
            published_ts=float(record.get("published_ts") or record.get("delivered_at") or now),
            now=now,
            listing_ids=listing_ids,
            listing_oldest_age=listing_oldest_age,
            live_removal_reason=reason,
            tail_margin_seconds=RETRACT_WINDOW_SECONDS,
        )
        if not decision:
            continue
        webhook_url = get_webhook_for_subreddit(subreddit)
        # Round 66: retraction covers EVERY part of a delivered post — the
        # main card plus its body-continuation messages (message_ids);
        # older records fall back to the single message_id.
        main_id = str(record.get("message_id") or "")
        part_ids = [str(m) for m in (record.get("message_ids") or []) if str(m)]
        ids = part_ids or ([main_id] if main_id else [])
        if not webhook_url or not ids:
            continue
        try:
            if decision == "delete":
                deleted = True
                for mid in ids:
                    async with session.delete(
                        f"{webhook_url}/messages/{mid}",
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status in (200, 204):
                            logging.warning(f"RETRACT: {unique_key} deleted dead Discord message "
                                            f"{mid} ({reason}).")
                        else:
                            deleted = False
                            logging.error(f"RETRACT: Discord delete HTTP {resp.status} for "
                                          f"{unique_key} (message {mid}): {(await resp.text())[:200]}")
                if deleted:
                    record["retracted"] = True
                continue
            payload = tombstone_payload(record.get("payload") or {}, reason)
            edited = True
            for idx, mid in enumerate(ids):
                part = payload if idx == 0 else tombstone_continuation_payload(reason)
                async with session.patch(
                    f"{webhook_url}/messages/{mid}?with_components=true",
                    json=part,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status in (200, 204):
                        if idx:
                            logging.warning(f"RETRACT: {unique_key} tombstoned continuation "
                                            f"message {mid} ({reason}).")
                    else:
                        edited = False
                        logging.error(f"RETRACT: Discord edit HTTP {resp.status} for "
                                      f"{unique_key} (message {mid}): {(await resp.text())[:200]}")
            if edited:
                record["retracted"] = True
                logging.warning(f"RETRACT: {unique_key} tombstoned dead Discord card "
                                f"({len(ids)} message(s), {reason}).")
        except Exception as exc:
            logging.error(f"RETRACT: failed for {unique_key}: {exc}")


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
                        "to be bulletproof. (V3 also uses it on .json for FULL MODE — "
                        "round 31: only when an OAuth app exists or REDDIT_JSON_PROBE=force.)")
    if not (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET):
        if feedtoken_probe_enabled():
            logging.info("No Reddit OAuth app secrets — V3 uses native RSS data; the feed "
                         "token .json FULL-MODE probe runs FORCED (REDDIT_JSON_PROBE=force — "
                         "legacy behavior: 65 s wait + one attempt per run with new posts, "
                         "403 expected from datacenter IPs).")
        else:
            if JSON_PROBE_MODE == "off":
                _probe_why = "REDDIT_JSON_PROBE=off"
            else:
                _probe_why = ("no OAuth app secrets — datacenter IPs 403 the .json route "
                              "anyway, so the probe would only cost 65 s + a 403 log line "
                              "per run with new posts")
            logging.info("No Reddit OAuth app secrets — V3 uses native RSS data; the feed "
                         "token .json FULL-MODE probe is skipped (" + _probe_why + "). "
                         "Set REDDIT_JSON_PROBE=force to re-enable, or add "
                         "REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET for FULL MODE "
                         "(reddit.com/prefs/apps -> type 'script') — no code change.")

    # Round 67: the video-quality ceiling is per RUN — every run starts with
    # the full quality budget available.
    reset_video_quality_budget()

    posted = load_posted()
    pending = load_pending()
    posted_messages = load_posted_messages()
    _listing_cache.clear()
    _redlib_post_page_cache.clear()
    _proxy_post_cache.clear()
    is_first_run = len(posted) == 0
    now = time.time()
    # Round 34: remember the pre-run cache so every save can be verified
    # (a just-posted key missing from the saved file = it WILL re-post).
    posted_at_start = set(posted)
    # Round 34: saturation warning — the pre-round-34 cap failure was
    # completely silent (lexicographic eviction logged nothing). Warn when a
    # sub is within 10 keys of its per-sub quota or the total is within 10%
    # of the backstop, so the next eviction burst is visible in the log.
    _sub_counts: dict = {}
    for _k in posted:
        _pfx = _k.partition("_")[0]
        _sub_counts[_pfx] = _sub_counts.get(_pfx, 0) + 1
    _hot = {s: n for s, n in _sub_counts.items() if n >= MAX_CACHE_SIZE_PER_SUB - 10}
    if _hot or len(posted) >= MAX_CACHE_SIZE_TOTAL * 9 // 10:
        logging.warning(f"dedup cache near cap: {len(posted)} key(s) (per-sub "
                        f"quota {MAX_CACHE_SIZE_PER_SUB}, backstop "
                        f"{MAX_CACHE_SIZE_TOTAL})"
                        + (f"; hot: " + ", ".join(f"{s}={n}" for s, n in sorted(_hot.items()))
                           if _hot else ""))

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
            if RETRACT_DEAD_POSTS and not DRY_RUN:
                await retract_dead_posts(session, posted_messages, now)
            saved = save_posted(posted, frozenset(posted - posted_at_start))
            _verify_dedup_save(posted_at_start, posted, saved)
            save_pending(pending)
            save_posted_messages(posted_messages)
            return

        logging.info(f"Found {total_found} new Reddit posts. Building V3 cards...")

        # newest first per sub for stable ordering
        new_posts.sort(key=lambda p: p[3])

        # Round 34: per-run flood cap (see MAX_POSTS_PER_RUN) — any future
        # cache loss causes ONE capped run with a loud log line, not a mass
        # re-post storm.
        new_posts = cap_new_posts(new_posts, MAX_POSTS_PER_RUN)

        for subreddit, path, unique_key, published_ts, activity_ts, entry in new_posts:
            # Round 34: double dedup (belt and braces) — collect() already
            # skips keys in the loaded cache, but a key can surface twice in
            # ONE run's feed (new + mod-queue listings) or via any future
            # code path; re-check the live set before the expensive pipeline.
            # TEST POST rebuilds are exempt (re-testing a cached post is
            # their whole point).
            if not TEST_POST_ID and unique_key in posted:
                continue
            webhook_url = get_webhook_for_subreddit(subreddit)
            if not webhook_url:
                logging.error(f"No webhook configured for r/{subreddit}. Skipping {unique_key}.")
                continue

            reddit_url = f"https://www.reddit.com{path}"
            _entry_source36 = ("arctic" if isinstance(entry, _ArcticEntry)
                               else "rss")

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
                    and _pending_throttle_skip(pending, unique_key, now, entry)):
                _pend = pending[unique_key]
                if _pend.get("reason") in _NO_RECHECK_REASONS:
                    logging.info(f"[{unique_key}] pending ({_pend.get('reason')}) — "
                                 f"not re-checking (removed posts re-enter via RSS "
                                 f"when restored; entry expires at the 48 h window).")
                else:
                    _due_in = int(PENDING_RECHECK_SECONDS
                                  - (now - float(_pend.get("last_checked") or 0)))
                    logging.info(f"[{unique_key}] pending ({_pend.get('reason')}) — "
                                 f"skipping recheck, due again in ~{_due_in}s.")
                continue

            # ---- round 64: stateless 30s fresh hold (section A) ---------
            # A flat floor computed from the post's OWN creation timestamp —
            # no first-seen tracking, no pending entry. A candidate still
            # under the floor simply re-qualifies from the feed next tick.
            if not TEST_POST_ID and FRESH_HOLD_SECONDS > 0:
                _post_age = now - float(published_ts or now)
                if _post_age < FRESH_HOLD_SECONDS:
                    logging.info(f"[{unique_key}] age {_post_age:.1f}s < "
                                 f"FRESH_HOLD_SECONDS ({FRESH_HOLD_SECONDS}s) — "
                                 f"skipped this tick (stateless, not cached), "
                                 f"re-evaluated next tick.")
                    continue

            # ---- round 64: pristine listing confirmation (section B) ----
            # Delivers only when a cache-busted, pristine read of the
            # subreddit's /new listing proves the post is live right now.
            # Absent -> skip this tick (never cached); appears -> posts on
            # the very next tick. A candidate older than the listing's own
            # span (scrolled past limit=100) cannot be proven OR disproven,
            # so confirmation is inapplicable and it falls through normally.
            # An unreadable listing is an OUTAGE, never read as absence —
            # every new candidate for that subreddit is skipped and retried.
            if not TEST_POST_ID and LISTING_CONFIRM:
                _r64_listing = await _fetch_new_listing(session, subreddit)
                if _r64_listing is None:
                    logging.info(f"[{unique_key}] pristine /new listing unavailable "
                                 f"for r/{subreddit} this tick (outage, not absence) "
                                 f"— skipped (not cached), retried next tick.")
                    continue
                _r64_ids, _r64_oldest_age = _r64_listing
                _r64_candidate_id = (extract_post_id(path) or "").lower()
                _r64_post_age = now - float(published_ts or now)
                if _r64_oldest_age is not None and _r64_post_age > _r64_oldest_age:
                    logging.info(f"[{unique_key}] older than the pristine listing's own "
                                 f"span — confirmation inapplicable, falling through to "
                                 f"normal delivery.")
                elif _r64_candidate_id not in _r64_ids:
                    logging.info(f"[{unique_key}] not in pristine listing — skipped "
                                 f"this tick (not cached), re-checked next tick.")
                    continue

            if (unique_key in pending
                    and str(pending[unique_key].get("reason") or "") in _NO_RECHECK_REASONS
                    and not isinstance(entry, _ArcticEntry)):
                logging.info(f"RESTORED: {unique_key} pending "
                             f"({pending[unique_key].get('reason')}) reappeared via RSS — "
                             f"running full pipeline (posts if live).")

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
                        proxy = await fetch_proxy_post_memo(session, path,
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
                    mark_pending(pending, unique_key, _removed, now,
                                 source=_entry_source36, title=_r_title[:200],
                                 published_ts=published_ts)
                    logging.info(f"[{unique_key}] post appears removed/deleted/"
                                 f"awaiting approval ({_removed}) — skipping, not "
                                 f"cached (will post once approved).")
                    continue

            # ---- round 20 + round 32: liveness gate for ALL sourced posts ----
            # The Arctic archive can carry posts that are no longer live on
            # reddit (removed / deleted / still pending approval) — and,
            # live-verified 2026-09-20 (post 1wl41aj, "Post is awaiting
            # moderator approval."): the RSS feed ALSO delivers queued posts
            # with their REAL content (no banner in the feed text), which the
            # round-18 text filter cannot see — the post was built + posted
            # to Discord while still in the mod queue. So the gate now runs
            # for RSS-sourced posts too, with feed_ok=False (the feed cannot
            # verify the feed — the redlib post pages are the independent
            # fallback). Otherwise skip + don't cache: a queued post
            # re-checks on the short approval interval and posts the moment
            # a live source can see it.
            if not TEST_POST_ID and entry is not None:
                _live, _why = await verify_archive_post_live(
                    session, path, label=unique_key,
                    feed_ok=not isinstance(entry, _ArcticEntry))
                if not _live:
                    mark_pending(pending, unique_key,
                                 "pending approval" if "awaiting moderator approval" in _why
                                 else "removal_notice" if "removal notice" in _why
                                 else "sources_down" if "all live sources are down" in _why
                                 else "not_live", now,
                                 source=_entry_source36,
                                 title=str(base.get("title") or "")[:200],
                                 published_ts=published_ts)
                    logging.info(f"[{unique_key}] post not verified live "
                                 f"({_why}) — skipping, not cached (will "
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
                # Round 66: the body beyond the card budget is continued in
                # follow-up messages (see build_continuation_payloads).
                payload, body_rest = build_v3_payload(subreddit, data, reddit_url,
                                                      posted_ts, return_remainder=True)
                continuations = build_continuation_payloads(body_rest)

                if DRY_RUN:
                    kinds = ",".join(sorted({m["kind"] for m in data["media"]})) or "text"
                    mode = "full" if data["full_mode"] else "native"
                    logging.info(f"DRY RUN (Discord NOT touched): {unique_key} "
                                 f"(media={kinds} | {mode} | {len(data['media'])} item(s))")
                    logging.info(f"DRY RUN payload for {unique_key}:\n"
                                 f"{json.dumps(payload, indent=2, ensure_ascii=False)}")
                    if continuations:
                        logging.info(f"DRY RUN: {unique_key} body continues in "
                                     f"{len(continuations)} follow-up message(s).")
                        for _ci, _cont in enumerate(continuations, 1):
                            logging.info(f"DRY RUN continuation {_ci} for {unique_key}:\n"
                                         f"{json.dumps(_cont, indent=2, ensure_ascii=False)}")
                    if data.get("youtube_url") and YOUTUBE_LINK_MESSAGE:
                        logging.info(f"DRY RUN 2nd message for {unique_key} "
                                     f"(YouTube link only): {data['youtube_url']}")
                    continue

                target_url = f"{webhook_url}?with_components=true"
                if RETRACT_DEAD_POSTS:
                    target_url += "&wait=true"
                async with session.post(target_url, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status in (200, 204):
                        message_json = None
                        if RETRACT_DEAD_POSTS and resp.status == 200:
                            try:
                                message_json = await resp.json(content_type=None)
                            except Exception:
                                message_json = None
                        posted.add(unique_key)
                        # ROUND 48b: delivery telemetry. Every post logs how
                        # long it took from Reddit creation to Discord, and —
                        # when it had been held — what held it and for how
                        # long. This is the only way to calibrate the gate
                        # against a subreddit's real approval latency without
                        # guessing (grep the Actions log for LATENCY).
                        _pend_entry = pending.pop(unique_key, None)  # posted — no longer pending
                        if RETRACT_DEAD_POSTS and isinstance(message_json, dict) and message_json.get("id"):
                            posted_messages[unique_key] = {
                                "subreddit": subreddit,
                                "path": path,
                                "message_id": str(message_json["id"]),
                                "delivered_at": int(now),
                                "published_ts": int(published_ts),
                                "payload": payload,
                            }
                        kinds = ",".join(sorted({m["kind"] for m in data["media"]})) or "text"
                        mode = ("full" if data["full_mode"] else
                                ("proxy: " + _provider_for_url(data["media"][0].get("url", ""))
                                 if data["media"] and data["media"][0].get("kind") == "video"
                                 and _provider_for_url(data["media"][0].get("url", "")) != "thumbnail"
                                 else "native-dash" if data["media"] and
                                 _provider_for_url(data["media"][0].get("url", "")) == "native-dash"
                                 else "native"))
                        _latency = max(0.0, now - float(published_ts or now))
                        _held_note = ""
                        if isinstance(_pend_entry, dict):
                            _first = float(_pend_entry.get("first_seen") or 0)
                            _held = max(0.0, now - _first) if _first else 0.0
                            _held_note = (f" | held {_held / 60:.1f}min as "
                                          f"'{_pend_entry.get('reason')}'")
                        logging.info(f"Reddit V3 Posted: {unique_key} (media={kinds} | {mode} | "
                                     f"{len(data['media'])} item(s)) | LATENCY "
                                     f"{_latency / 60:.1f}min after creation{_held_note}")
                        # Round 66: a body that exceeded the card budget
                        # continues in follow-up messages — same webhook,
                        # same mechanism/ordering as the YouTube link
                        # message below. Message IDs are recorded so the
                        # retraction seatbelt covers EVERY part.
                        for _ci, _cont in enumerate(continuations, 1):
                            try:
                                _cont_url = f"{webhook_url}?with_components=true"
                                if RETRACT_DEAD_POSTS:
                                    _cont_url += "&wait=true"
                                async with session.post(_cont_url, json=_cont,
                                                        timeout=aiohttp.ClientTimeout(total=15)) as c_resp:
                                    if c_resp.status in (200, 204):
                                        if RETRACT_DEAD_POSTS and c_resp.status == 200:
                                            try:
                                                _c_json = await c_resp.json(content_type=None)
                                            except Exception:
                                                _c_json = None
                                            if isinstance(_c_json, dict) and _c_json.get("id"):
                                                _record = posted_messages.get(unique_key)
                                                if isinstance(_record, dict) and _record.get("message_id"):
                                                    _ids = _record.setdefault(
                                                        "message_ids", [_record["message_id"]])
                                                    if str(_c_json["id"]) not in _ids:
                                                        _ids.append(str(_c_json["id"]))
                                        logging.info(f"Body continuation posted for {unique_key} "
                                                     f"(part { _ci + 1 } of "
                                                     f"{len(continuations) + 1}).")
                                    else:
                                        logging.error(f"Body continuation HTTP {c_resp.status} for "
                                                      f"{unique_key}: {(await c_resp.text())[:200]}")
                            except Exception as c_e:
                                logging.error(f"Body continuation failed for {unique_key}: {c_e}")
                            await asyncio.sleep(1.0)
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

        if RETRACT_DEAD_POSTS and not DRY_RUN:
            await retract_dead_posts(session, posted_messages, now)

    log_redlib_reachability()   # round 49: which mirrors actually answered

    if DRY_RUN:
        logging.info("DRY RUN finished: cache NOT saved, Discord NOT touched.")
    else:
        saved = save_posted(posted, frozenset(posted - posted_at_start))
        _verify_dedup_save(posted_at_start, posted, saved)
        save_pending(pending)
        save_posted_messages(posted_messages)
        logging.info("Reddit V3 Monitor execution finished.")


if __name__ == "__main__":
    asyncio.run(main())
