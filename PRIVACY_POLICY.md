# Privacy Policy

**Effective date:** September 15, 2026 (updated for Reddit V3 round 12; updated September 17, 2026 for the X V3 tweet-data fallback chain and the Arctic Shift search backup; updated September 20, 2026 for the round-31 feed-token .json probe gate)
**Applies to:** the *News Express* / *Citlali News* X (Twitter) + Reddit → Discord monitor
("the Service"), an open-source, self-hosted automation tool.

---

## 1. Overview

The Service is a set of Python scripts that run on **your own GitHub repository** (GitHub Actions)
and post new public X/Twitter and Reddit items into **your own Discord channels** via Discord
webhooks. There is no hosted bot account, no gateway connection, no dashboard, and no central server
operated by the author.

**Short version: the Service collects, stores, and transmits no personal data.**

---

## 2. Data the Service Processes

| Data | Where it lives | Why | Shared with anyone? |
|---|---|---|---|
| Post/tweet IDs (e.g. `TYPEII_EN_2098063866303504597`) | `posted_tweets.json` / `posted_reddit.json` **inside your own repository**, committed by `github-actions[bot]` | Deduplication only — prevents posting the same item twice | No |
| Webhook URLs, API keys & the optional Reddit feed token | GitHub **encrypted repository secrets** in *your* repository | Authentication to Discord / EmbedEZ / Reddit | No (GitHub stores them; they are never logged or transmitted elsewhere — the feed token is sent only to reddit.com, never to any mirror or third party) |
| Public post content (titles, text, media URLs, stats) | Processed **in memory** during a run, discarded immediately after | Rendering the Discord message | Only sent to the Discord webhook(s) *you* configured |

No analytics, no tracking, no cookies, no databases, no telemetry, no advertising.

---

## 3. Data the Service Does NOT Collect

* Discord user information (usernames, IDs, messages of server members) — the Service cannot read
  any of it; webhooks are **one-way, post-only**.
* X/Twitter or Reddit account credentials — only **public** posts are fetched via public RSS/API
  endpoints; no login is used or stored. (The optional Reddit *feed token* is a public-read
  credential for RSS only — it grants no access to your account, DMs, or profile, and can be
  regenerated at any time.)
* IP addresses or device information of anyone.
* Any payment information.

---

## 4. Third-Party Services

When the Service runs, it makes outbound requests to the following third parties. Their own privacy
policies apply to whatever they can technically observe (typically just the requesting IP and the
public URL being looked up):

| Service | Purpose | Their policy |
|---|---|---|
| **GitHub Actions** | Hosts and runs the scripts | https://docs.github.com/en/site-policy/privacy-policies |
| **Discord (webhooks)** | Delivers the generated messages | https://discord.com/privacy |
| **Nitter mirrors** | Public RSS feeds for X/Twitter | per-instance |
| **Redlib** | Reddit RSS fallback mirrors, and (Reddit V3) best-effort **post-page** fetches for multi-photo galleries and on-demand test posts — same instances as the RSS fallback, probed in parallel; all current instances sit behind anti-bot challenges (verified 2026-09-13), so requests typically fail fast and transfer no content | per-instance |
| **FxTwitter / FxEmbed API** (incl. sister host **api.fixupx.com**) | **Primary** tweet metadata, media, translation; if the primary host can't answer, the same-engine fixupx host is tried (round 11) | https://fxtwitter.com |
| **video.twimg.com / x.com (X CDN & post pages)** | HTTP HEAD probes of public video file sizes (X V2/V3 "smart video" check) and OpenGraph image lookups on public post pages — only meta tags are read, no content is downloaded | https://x.com |
| **gif.fxtwitter.com** | 1st of the GIF converter chain (round 11): one HEAD probe per X GIF to check the animated WebP rendition exists before using it | https://fxtwitter.com |
| **api.vxtwitter.com** (BetterTwitFix) | 3rd fallback for tweet metadata/media (round 11) — public tweet data only; its direct `video.twimg.com` media URLs are what the bot then probes | https://vxtwitter.com |
| **gifconvert.vxtwitter.com** | 2nd/3rd of the GIF converter chain (round 11): one HEAD probe per X GIF (`.webp`, then `.gif`, with a browser Referer) to check a converted animated rendition exists before Discord uses it | https://vxtwitter.com |
| **fastgif-production.up.railway.app** | Last probe of the GIF chain (round 11; offline since 2026-09-17 — retained so it is used automatically again if it returns): one HEAD probe per X GIF to check the converted animated GIF exists; if it answers, Discord fetches that converted GIF when rendering the post. Independent third-party service, unaffiliated with this project or FxTwitter | https://railway.app |
| **EmbedEZ API** | Reddit V2: Reddit post metadata, media. X V3 (round 11, **last-resort only**): tweet metadata via the keyless search API + one bot-page og: tag read per tweet when fxtwitter/fixupx/vxtwitter all fail; the media URLs are then fetched by **Discord's servers** (animated WebP for GIFs), and any embedded promo/ad line is stripped before posting | https://embedez.com |
| **Arctic Shift** (arctic-shift.photon-reddit.com) | Reddit V3 archive: post-lookup JSON for crosspost originals, and — only for subreddits that returned no new RSS posts — one `/api/posts/search` per missing subreddit (public post data only, 48-hour window); soft-fails back to the RSS path on any error | https://arctic-shift.photon-reddit.com |
| **redditez.com** (Reddit V1 default mirror) | No direct contact: the Service only *constructs* the mirror link from the public post path; **Discord's servers** fetch that URL to render the unfurled embed | https://www.redditez.com |
| **embeddit.deltandy.me / vxreddit.com** (only if you switch `REDDIT_MIRROR`) | Same as above — link construction only; Discord fetches the mirror when rendering. Both are independent community projects, unaffiliated with this project | https://embeddit.deltandy.me |
| **reddit.com** | Public subreddit RSS — all tracked subreddits in **one combined feed request per run** (`/r/a+b+c/new.rss?limit=100`), optionally carrying your personal feed token (sent only to reddit.com); plus per-post `.json` lookups in Reddit V3 (OAuth app token when an app is configured, or the feed token as a best-effort workaround — round 31: only when an app exists or `REDDIT_JSON_PROBE=force`) — public post data + public top-level comments only | https://www.reddit.com/policies/privacy-policy |
| **i.redd.it / preview.redd.it / v.redd.it** (Reddit's own CDNs) | V3 media verification only: one short HTTP range probe per candidate media file (images/videos) to check it exists and is playable; the signed `packaged-media.redd.it` masters (which expire in hours) are never used | https://www.reddit.com/policies/privacy-policy |
| **proxy.embedez.com / vxreddit.com** | V3 video fallback only, and only when Reddit's own `v.redd.it` DASH files are unavailable: the *public video file URL* (no account data) is passed so they can return a muxed (video+audio) mp4 for the card | https://embedez.com / per-instance |
| **seaof.glass** (quartz) | Only if you enable `YOUTUBE_MEDIA_EMBED=1` (default off): one range probe per YouTube video id to see if a playable mp4 exists — the video id only | per-service |
| **i.ytimg.com** (YouTube thumbnails) | One probe per YouTube post to pick the best available thumbnail (maxres/hq/mq) | https://policies.google.com/privacy |
| **discohook.app** (optional, on by default) | Per-card **share-link preview** of the public card payload only (POST `/api/v1/share`, keyless). **No `targets` are sent, so your webhook URL and any tokens never leave the repository.** Share links are public for their 7-day TTL; disable with `DISCOHOOK_PREVIEW=0` | https://discohook.app |
| **GitHub (Dependabot, optional)** | If you keep `.github/dependabot.yml`, GitHub's built-in Dependabot opens **pull requests only** (never pushes to `main`); it sees your dependency files, nothing else — see `docs/DEPENDABOT.md` | https://docs.github.com/en/site-policy/privacy-policies |
| **cron-job.org** (optional) | External schedule trigger | https://cron-job.org/en/privacy/ |

The Service never sends these third parties anything about your Discord server's members or content —
only the identifiers of the **public** posts being rendered.

---

## 5. Data Retention

* Post-ID caches live in your repository until you delete them (they self-trim to the latest 500
  entries).
* Workflow run logs are retained by GitHub according to your repository's log-retention settings.
* Nothing is retained anywhere else, because nothing else exists.

---

## 6. Your Responsibilities & Rights

Because you operate your own instance, **you** are the data controller for it. You may inspect,
modify, or delete every byte it holds (the two JSON cache files) at any time. Server members who
want messages removed can ask you (or any admin with *Manage Messages*) to delete them from Discord.

---

## 7. Children's Privacy

The Service is not directed at children under 13 and collects no personal data from anyone of any
age.

---

## 8. Changes to this Policy

Changes are committed to this repository's `PRIVACY_POLICY.md` with an updated effective date.
Material changes will also be announced in the project's Discord server.

---

## 9. Contact

* GitHub: open an issue on this repository
* Discord: https://discord.gg/HyrVP9wRXu
* Support/dev: https://ko-fi.com/jieunlatte
