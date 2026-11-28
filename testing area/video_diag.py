#!/usr/bin/env python3
"""
video_diag.py — Round-10 diagnostic for the "image not found" video bug
=========================================================================
Standalone one-shot tool (no dedup cache, no commits — safe to re-run).

WHAT IT DOES
  1. Fetches each target tweet from the FxTwitter API.
  2. Collects every MP4 variant (skips m3u8) and probes its REAL file size.
  3. Posts ONE components-v2 card per tweet to DISCORD_WEBHOOK_URL with a
     single gallery of up to 10 LABELED test tiles:
       - direct URL, with ?tag   (the exact URL the bot currently embeds)
       - direct URL, no ?tag     (tests whether the query string is the issue)
       - fxtwitter proxy URL     (302 -> same file, different origin)
     Each tile's description tells you resolution + size + URL style, so you
     can see exactly which play and which show "image not found".

WHY
  We already verified video.twimg.com happily serves Discord's own bot user
  agent for the failing 171 MB / 234 MB files, so the block happens inside
  Discord's media proxy. This card tells us whether it's:
    (a) the ?tag=21 query string, (b) a size threshold, or (c) the origin —
  which decides the exact fix.

USAGE
  python "testing area/video_diag.py"
  python "testing area/video_diag.py" <webhook_url>
  python "testing area/video_diag.py" <webhook_url> <tweet_id1,tweet_id2>

  .env:  DISCORD_WEBHOOK_URL   target channel (use the Ananta channel)
         DIAG_TWEET_IDS        optional, comma-separated (default: the 2
                               failing Ananta amplify tweets)

AFTER RUNNING
  Wait 30-60 seconds (Discord proxies gallery tiles asynchronously), then
  tell me which tiles PLAY and which say "image not found" — e.g.
  "270p and 360p play in every style, 720p and 1080p all fail".
"""

import os
import re
import sys
import asyncio
import logging
import urllib.parse

import aiohttp
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("video-diag")

FXTWITTER_API_BASE = "https://api.fxtwitter.com"
FXTWITTER_PROXY = "https://api.fxtwitter.com/2/go?url="
IS_COMPONENTS_V2 = 1 << 15
PROD_MAX_MB = 256  # keep in sync with VIDEO_SIZE_LIMIT in twitter_v3.py
DEFAULT_TWEET_IDS = "1971001706466115893,1970307131074355593"  # the 2 failing Ananta amplify tweets


async def fetch_tweet(session: aiohttp.ClientSession, tweet_id: str) -> dict | None:
    url = f"{FXTWITTER_API_BASE}/status/{tweet_id}"
    headers = {"User-Agent": "NewsFlashBot/3.0"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                log.warning("fxtwitter %s -> HTTP %s", tweet_id, r.status)
                return None
            data = await r.json()
        return data.get("tweet") or data.get("status")
    except Exception as e:
        log.warning("fxtwitter %s failed: %s", tweet_id, e)
        return None


def video_variants(tweet: dict) -> list:
    """All mp4 URLs of the tweet, with resolution parsed from the URL."""
    out = []
    media = tweet.get("media") or {}
    for v in (media.get("videos") or []):
        cands = [v.get("url")] + [f.get("url") for f in (v.get("formats") or [])]
        for cand in cands:
            if cand and re.search(r"\.mp4(\?|$)", cand):
                m = re.search(r"/(\d+)x(\d+)/", cand)
                w, h = (int(m.group(1)), int(m.group(2))) if m else (v.get("width"), v.get("height"))
                out.append({"url": cand, "width": w, "height": h})
    # de-dupe by URL
    seen, unique = set(), []
    for it in out:
        if it["url"] not in seen:
            seen.add(it["url"])
            unique.append(it)
    return unique


async def probe_size(session: aiohttp.ClientSession, url: str):
    """Real file size in MB (1-byte ranged GET), or None if unknown."""
    try:
        async with session.get(url, headers={"Range": "bytes=0-0"},
                               timeout=aiohttp.ClientTimeout(total=20), allow_redirects=True) as r:
            if r.status in (200, 206):
                cr = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range", ""))
                if cr:
                    return int(cr.group(1)) / (1024 * 1024)
                cl = r.headers.get("Content-Length")
                if cl and r.status == 200:
                    return int(cl) / (1024 * 1024)
    except Exception as e:
        log.debug("size probe failed for %s: %s", url, e)
    return None


def pick_tiers(tiers: list) -> list:
    """A short, informative size ladder: smallest / production pick / first-too-big / largest."""
    tiers = [t for t in tiers]
    tiers.sort(key=lambda t: t.get("size_mb") or 0)
    chosen = []
    def add(t):
        if t not in chosen:
            chosen.append(t)
    if tiers:
        add(tiers[0])  # smallest
    safe = [t for t in tiers if (t.get("size_mb") or 0) <= PROD_MAX_MB]
    if safe:
        add(safe[-1])  # what production currently embeds (largest <= 256 MB)
    big = [t for t in tiers if (t.get("size_mb") or 0) > PROD_MAX_MB]
    if big:
        add(big[0])  # smallest "too big" tier
    if len(tiers) > 1:
        add(tiers[-1])  # largest
    return chosen


def build_card(screen_name: str, tweet_id: str, items: list, summary_lines: list) -> dict:
    inner = [
        {"type": 10, "content": f"### 🎬 Video diagnostic — {screen_name} (tweet {tweet_id})"},
        {"type": 10, "content":
         "Each tile is the same video at a different resolution/size and URL style. "
         "**Play each tile** — tiles that play are fine for Discord's proxy; "
         "tiles showing *image not found* are what breaks.\n\n"
         "`★` = the exact URL the bot currently embeds."},
        {"type": 14, "divider": True, "spacing": 1},
        {"type": 12, "items": items[:10]},
        {"type": 14, "divider": True, "spacing": 1},
        {"type": 10, "content": "Variants found:\n" + "\n".join(f"• {s}" for s in summary_lines)},
    ]
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [{"type": 17, "accent_color": 0x1DA1F2, "components": inner}],
    }


async def run(webhook_url: str, tweet_ids: list) -> None:
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for tid in tweet_ids:
            log.info("Fetching tweet %s ...", tid)
            tweet = await fetch_tweet(session, tid)
            if not tweet:
                log.warning("Skipping %s (could not fetch).", tid)
                continue
            screen_name = (tweet.get("author") or {}).get("screen_name", "?")
            variants = video_variants(tweet)
            if not variants:
                log.warning("Tweet %s has no MP4 video variants — skipping.", tid)
                continue

            log.info("Probing %d variant sizes ...", len(variants))
            for v in variants:
                v["size_mb"] = await probe_size(session, v["url"])
                v["untagged"] = v["url"].split("?", 1)[0]
                v["proxy"] = FXTWITTER_PROXY + urllib.parse.quote(v["url"], safe="")

            tiers = pick_tiers(variants)
            summary_lines = []
            for t in tiers:
                res = f"{t['width']}x{t['height']}" if t.get("width") else "?"
                size = f"{t['size_mb']:.1f} MB" if t.get("size_mb") is not None else "size unknown"
                summary_lines.append(f"{res} — {size}")
                log.info("  candidate %s (%s)", res, size)

            prod_pick = None
            safe = [t for t in tiers if (t.get("size_mb") or 0) <= PROD_MAX_MB]
            if safe:
                prod_pick = safe[-1]

            items = []
            for t in tiers:
                res = f"{t['width']}x{t['height']}" if t.get("width") else "?"
                size = f"{t['size_mb']:.1f} MB" if t.get("size_mb") is not None else "? MB"
                base = {"width": t.get("width"), "height": t.get("height"), "content_type": "video/mp4"}

                def add_item(url: str, style: str, star: bool) -> None:
                    media = dict(base)
                    media["url"] = url
                    items.append({
                        "media": media,
                        "description": f"{'★ ' if star else ''}{res} · {size} · {style}",
                        "spoiler": False,
                    })

                add_item(t["url"], "direct +tag", t is prod_pick)
                add_item(t["untagged"], "direct, no ?tag", False)
                if t is prod_pick:
                    add_item(t["proxy"], "fxtwitter proxy", False)
                if len(items) >= 9:  # leave room, max 10
                    break

            payload = build_card(screen_name, tid, items, summary_lines)
            log.info("Posting diagnostic card for %s (%d tiles) ...", screen_name, len(items))
            # Components V2 requires the with_components=true query parameter
            # (same as twitter_v3.py in production).
            target_url = f"{webhook_url}?with_components=true"
            async with session.post(target_url, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status in (200, 204):
                    log.info("Posted tweet %s diagnostic OK.", tid)
                else:
                    log.error("Webhook HTTP %s: %s", r.status, (await r.text())[:300])
            await asyncio.sleep(2)
    log.info("Done. Wait 30-60s for tiles to proxy, then check which play.")


def main() -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    tweet_ids = [t.strip() for t in os.getenv("DIAG_TWEET_IDS", DEFAULT_TWEET_IDS).split(",") if t.strip()]
    args = [a for a in sys.argv[1:] if a.strip()]
    if args:
        webhook_url = args[0]
    if len(args) > 1:
        tweet_ids = [t.strip() for t in args[1].split(",") if t.strip()]
    if not webhook_url:
        print("Set DISCORD_WEBHOOK_URL in .env or pass it as the first argument.")
        return
    log.info("Target webhook set. Tweets: %s", ", ".join(tweet_ids))
    asyncio.run(run(webhook_url, tweet_ids))


if __name__ == "__main__":
    main()
