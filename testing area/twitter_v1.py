# ---------------------------------------------------------------------------
# 📋 Plain Embed V1 Edition — twitter_v1.py
# ---------------------------------------------------------------------------
# To use it: set the workflow's run line to:  python "testing area/twitter_v1.py"
# Note: buttons are outside the embed v1
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
# 📋 ACCOUNTS TO TRACK
# ---------------------------------------------------------------------------
ACCOUNTS_STR = os.getenv("ACCOUNTS", "TYPEII_EN,PomPom_HonkaiSR,Wuthering_Waves,HonkaiNA,Ananta_EN")
ACCOUNTS = [acc.strip() for acc in ACCOUNTS_STR.split(",") if acc.strip()]

DEFAULT_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

CACHE_FILE = "posted_tweets.json"
MAX_CACHE_SIZE = 500
MAX_AGE_SECONDS = 3 * 3600

RSS_INSTANCES = [
    "https://nitter.perennialte.ch",
    "https://nitter.privacydev.net",
    "https://nitter.net",
    "https://xcancel.com",
]

FXTWITTER_API_BASE = "https://api.fxtwitter.com"

# ---------------------------------------------------------------------------
# 🌐 LANGUAGE NAMES (for the "Translated from X" note)
# ---------------------------------------------------------------------------
LANGUAGE_NAMES = {
    "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "fr": "French",
    "de": "German", "es": "Spanish", "pt": "Portuguese", "ru": "Russian",
    "ar": "Arabic", "it": "Italian", "id": "Indonesian", "th": "Thai",
    "vi": "Vietnamese", "tl": "Filipino", "hi": "Hindi", "tr": "Turkish",
    "nl": "Dutch", "pl": "Polish", "uk": "Ukrainian", "sv": "Swedish",
}

# ---------------------------------------------------------------------------
# 🔘 BUTTON CONFIGURATION
# ---------------------------------------------------------------------------
READ_POST_LABEL = "Read Post"
READ_POST_EMOJI = {"name": "🔗"}

STATIC_BUTTONS = [
    {
        "label": "Citlali News",
        "url": "https://discord.gg/HyrVP9wRXu",
        "emoji": {"name": "📰"},
    },
    {
        "label": "Donate",
        "url": "https://ko-fi.com/jieunlatte",
        "emoji": {"name": "☕"},
    },
]


def get_webhook_for_account(account: str) -> str | None:
    sanitized = re.sub(r"[^A-Za-z0-9]", "_", account).upper()
    env_key = f"WEBHOOK_{sanitized}"
    webhook = os.getenv(env_key)
    if webhook:
        return webhook
    if DEFAULT_WEBHOOK_URL:
        logging.warning(
            f"No dedicated webhook found for @{account} (expected secret {env_key}). "
            f"Falling back to DISCORD_WEBHOOK_URL."
        )
        return DEFAULT_WEBHOOK_URL
    return None


def load_posted_urls() -> set:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"Error reading cache file: {e}")
    return set()


def save_posted_urls(posted_urls: set):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(list(posted_urls)[-MAX_CACHE_SIZE:], f, indent=2)
    except Exception as e:
        logging.error(f"Error saving cache file: {e}")


async def fetch_working_feed(session: aiohttp.ClientSession, account: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*"
    }
    for instance in RSS_INSTANCES:
        feed_url = f"{instance}/{account}/rss"
        try:
            async with session.get(feed_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    content = await response.text()
                    feed = await asyncio.to_thread(feedparser.parse, content)
                    title = str(feed.feed.get("title", ""))
                    if feed.entries and "whitelisted" not in title.lower():
                        logging.info(f"Successfully fetched feed for @{account} from {instance}")
                        return feed
        except Exception as e:
            logging.debug(f"Error fetching from {instance} for @{account}: {e}")
    logging.warning(f"Could not fetch valid RSS feed for @{account} from any instance.")
    return None


async def fetch_tweet_lang(session: aiohttp.ClientSession, account: str, tweet_id: str) -> str | None:
    """Lightweight check of the tweet's source language via the FxTwitter API."""
    url = f"{FXTWITTER_API_BASE}/{account}/status/{tweet_id}"
    headers = {"User-Agent": "NewsFlashBot/1.0"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 200:
                data = await response.json()
                tweet = data.get("tweet") or data.get("status") or {}
                return (tweet.get("lang") or "").lower()
    except Exception as e:
        logging.debug(f"Could not determine language for {tweet_id}: {e}")
    return None


def build_components(read_post_url: str) -> list:
    read_button = {
        "type": 2,
        "style": 5,
        "label": READ_POST_LABEL,
        "url": read_post_url,
    }
    if READ_POST_EMOJI:
        read_button["emoji"] = READ_POST_EMOJI

    buttons = [read_button]
    for btn in STATIC_BUTTONS:
        button_obj = {
            "type": 2,
            "style": 5,
            "label": btn["label"],
            "url": btn["url"],
        }
        if btn.get("emoji"):
            button_obj["emoji"] = btn["emoji"]
        buttons.append(button_obj)

    return [{"type": 1, "components": buttons[:5]}]


async def send_discord_webhook(session: aiohttp.ClientSession, webhook_url: str,
                                 message_content: str, read_post_url: str) -> bool:
    payload = {
        "content": message_content,
        "components": build_components(read_post_url),
    }
    request_url = f"{webhook_url}?with_components=true"
    try:
        async with session.post(request_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status in (200, 204):
                logging.info("Successfully posted to Discord Webhook.")
                return True
            else:
                body = await response.text()
                logging.error(f"Discord Webhook returned status {response.status}: {body}")
                return False
    except Exception as e:
        logging.error(f"Error posting to Discord Webhook: {e}")
        return False


async def main():
    if not ACCOUNTS:
        logging.error("No accounts configured in ACCOUNTS environment variable.")
        return

    posted_urls = load_posted_urls()
    is_first_run = len(posted_urls) == 0
    now = time.time()

    account_tweets = {account: [] for account in ACCOUNTS}

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_working_feed(session, account) for account in ACCOUNTS]
        feeds = await asyncio.gather(*tasks)

        for account, feed in zip(ACCOUNTS, feeds):
            if not feed or not feed.entries:
                continue

            entries_to_inspect = [feed.entries[0]] if is_first_run else feed.entries

            for entry in entries_to_inspect:
                raw_link = getattr(entry, "link", "")
                match = re.search(r"/status/(\d+)", raw_link)
                if not match:
                    continue

                tweet_id = match.group(1)
                unique_key = f"{account}_{tweet_id}"

                if unique_key in posted_urls:
                    continue

                published_parsed = entry.get("published_parsed")
                published_ts = time.mktime(published_parsed) if published_parsed else now

                if not is_first_run and (now - published_ts > MAX_AGE_SECONDS):
                    logging.info(f"Skipping older tweet {unique_key} (published > 3h ago)")
                    continue

                account_tweets[account].append({
                    "account": account,
                    "tweet_id": tweet_id,
                    "unique_key": unique_key,
                    "published_ts": published_ts
                })

        total_found = sum(len(v) for v in account_tweets.values())
        if total_found == 0:
            logging.info("No new tweets to post.")
            save_posted_urls(posted_urls)
            return

        logging.info(f"Found {total_found} new tweets across {len(ACCOUNTS)} accounts. Posting per-channel...")

        for account, tweets in account_tweets.items():
            if not tweets:
                continue

            webhook_url = get_webhook_for_account(account)
            if not webhook_url:
                sanitized = re.sub(r"[^A-Za-z0-9]", "_", account).upper()
                logging.error(
                    f"No webhook configured for @{account} (checked WEBHOOK_{sanitized} "
                    f"and DISCORD_WEBHOOK_URL). Skipping {len(tweets)} tweet(s)."
                )
                continue

            tweets.sort(key=lambda item: item["published_ts"])

            for tweet_info in tweets:
                tweet_id = tweet_info["tweet_id"]
                unique_key = tweet_info["unique_key"]

                fxtwitter_url = f"https://fxtwitter.com/{account}/status/{tweet_id}"

                # 🌐 Check language; if not English, switch to the /en translated link
                lang = await fetch_tweet_lang(session, account, tweet_id)
                translation_note = ""
                if lang and lang != "en":
                    fxtwitter_url += "/en"
                    lang_name = LANGUAGE_NAMES.get(lang, lang.upper())
                    translation_note = f"📑 *Translated from {lang_name}*\n"

                message = f"📰 **New update from @{account}**\n{translation_note}{fxtwitter_url}"

                success = await send_discord_webhook(session, webhook_url, message, fxtwitter_url)
                if success:
                    posted_urls.add(unique_key)
                    await asyncio.sleep(1.5)

    save_posted_urls(posted_urls)
    logging.info("RSS Feed Monitor execution finished successfully.")


if __name__ == "__main__":
    asyncio.run(main())
