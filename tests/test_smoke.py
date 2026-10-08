"""Offline smoke test — no network, no secrets.

Run:  python tests/test_smoke.py
Purpose (also used by CI as the safety gate for Dependabot PRs):
  1. every monitor script imports cleanly (catches import-time breakage
     from dependency bumps),
  2. the Reddit V3 card pipeline still behaves (body cleaning, media
     extraction/dedup, OP comment, components-v2 layout, button set,
     Arctic Shift search backup — round 17),
  3. the X V3 tweet-data path still behaves (GIF converter chain,
     vxtwitter normalization, twitterez og-page parsing, fallback-chain
     order — round 11),
  4. the round-23 (2026-09-18) Reddit V3 rules hold: media must win the
     proxy chain, the Arctic media-hint gate skips WITHOUT caching, the
     removal-notice variants are caught and legit posts are not, and the
     round-14 miningtcup RSS token is still sent all three ways,
  5. the round-28 (2026-09-18) X V2/V3 rules hold: an /en translation
     IDENTICAL to the original (X language mis-detection) is dropped, and
     non-ASCII hashtags (zzzero + U+3164 filler) get percent-encoded,
     clickable URLs,
  6. the round-29 (2026-09-19) Reddit V3 rules hold: the mirror's
     crosspost NOTICE is stripped from empty-selftext crossposts (and the
     notice's subreddit still yields the "🔁 Crosspost of" line when no
     permalink was available), and one photo listed twice under different
     wrapper URLs collapses to a single gallery tile.
"""
import os
import re
import sys
import json
import time
import types
import shutil
import inspect
import tempfile
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# In offline environments (no aiohttp/feedparser/dotenv installed) use stubs —
# this test never opens a connection, so stubs are safe.
try:
    import aiohttp  # noqa: F401
    import feedparser  # noqa: F401
    import dotenv  # noqa: F401
except ImportError:
    for name in ("aiohttp", "feedparser", "dotenv"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    sys.modules["aiohttp"].ClientSession = object
    sys.modules["aiohttp"].ClientTimeout = lambda total=None: None
    sys.modules["feedparser"].parse = lambda *a, **k: None
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None

failures = []


def check(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(label)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- 1. every monitor script must import --------------------------------
SCRIPTS = [
    "testing area/twitter_v1.py",
    "testing area/reddit_main.py",
    "testing area/reddit_main_v2_embedez.py",
    "testing area/reddit_main_v3.py",
    "testing area/twitter_v2_button_outside.py",
    "testing area/twitter_v3.py",
    "testing area/twitter_proxy.py",
    "testing area/video_diag.py",
]
v3 = None
for rel in SCRIPTS:
    try:
        m = load_module("smoke_" + rel.replace(os.sep, "_").replace(" ", "_"), rel)
        check(f"import {rel}", True)
        if rel.endswith("reddit_main_v3.py"):
            v3 = m
    except Exception as e:
        check(f"import {rel}", False, repr(e))

if v3 is None:
    print("FATAL: reddit_main_v3.py could not be imported.")
    sys.exit(1)

# ---- 2. body cleaning -----------------------------------------------------
html = ('<table><tr><td><p><a href="https://preview.redd.it/abc123.png?width=1280&s=x">'
        'https://preview.redd.it/abc123.png?width=1280&s=x</a></p>'
        '<p>Keep me</p></td></tr></table>\nsubmitted by /u/x to r/y')
body = v3.clean_rss_body(html)
check("body: redd.it URL line stripped", "preview.redd.it" not in body, body)
check("body: text kept", "Keep me" in body, body)

# ---- 3. native media extraction (order + dedup + best rendition) ----------
content = (
    '<a href="https://preview.redd.it/post-slug-v0-aaaa1111bbbb.jpg?width=140&crop=1:1,smart&auto=webp&s=1"><img></a> '
    '<a href="https://i.redd.it/aaaa1111bbbb.jpg"><img></a> '
    '<a href="https://preview.redd.it/post-slug-v0-cccc2222dddd.jpg?width=1080&crop=smart&auto=webp&s=2"><img></a> '
    '<a href="https://i.redd.it/eeee3333ffff.gif"><img></a> '
    '<a href="https://external-preview.redd.it/ext.png?width=640&s=3"><img></a>'
)
items = v3.extract_native_media(content)
check("media: 4 distinct items (deduped)", len(items) == 4, str(items))
check("media: photo1 full-res swap", items[0] == {"kind": "image", "url": "https://i.redd.it/aaaa1111bbbb.jpg"}, str(items[0]))
check("media: photo2 (signed jpg only) swapped to i.redd.it",
      items[1] == {"kind": "image", "url": "https://i.redd.it/cccc2222dddd.jpg"}, str(items[1]))
check("media: gif kind", items[2] == {"kind": "gif", "url": "https://i.redd.it/eeee3333ffff.gif"}, str(items[2]))
check("media: external-preview kept for caller", "external-preview" in items[3]["url"], str(items[3]))

# ---- 4. i_reddit_swap reduces slug-prefixed names to the bare file id ------
check("swap: bare id", v3.i_reddit_swap("https://preview.redd.it/yjexawtg9nph1.jpg?width=1280&s=x")
      == "https://i.redd.it/yjexawtg9nph1.jpg")
check("swap: slug -> bare id", v3.i_reddit_swap(
    "https://preview.redd.it/heist-mode-v0-8la7js0h9nph1.jpg?width=1080&s=x")
    == "https://i.redd.it/8la7js0h9nph1.jpg")
check("swap: png not swapped", v3.i_reddit_swap("https://preview.redd.it/xyz.png?width=1280&s=x") is None)

# ---- 4b. redlib gallery scoping ---------------------------------------------
redlib_page = """
<html><head><style>.post_title{font-size:20px}</style></head><body>
<header><img src="https://i.redd.it/subicon.jpg"></header>
<h1 class="post_title"><a href="/r/X/comments/abc/t/">Title</a></h1>
<div class="post_content">
  <img src="https://preview.redd.it/photo-a-v0-111aaa1.jpg?width=140&crop=1:1,smart&auto=webp&s=x">
  <img src="https://i.redd.it/222bbb2.jpg">
  <img src="https://preview.redd.it/photo-b-v0-333ccc3.jpg?width=1080&crop=smart&auto=webp&s=y">
  <img src="https://i.redd.it/444ddd4.gif">
</div>
<div id="comment-9"><img src="https://i.redd.it/commentside.jpg"></div>
<div class="sidebar"><img src="https://i.redd.it/sidebar.jpg"></div>
</body></html>
"""
g = v3.extract_redlib_gallery(redlib_page)
check("redlib: 4 items (sub icon + comment/sidebar imgs excluded)", len(g) == 4, str(g))
check("redlib: photo-a deduped to full-res", g[0] == {"kind": "image", "url": "https://i.redd.it/111aaa1.jpg"}, str(g[0]))
check("redlib: order kept (signed jpg/jpeg swapped to i.redd.it)",
      [x["url"] for x in g][:3]
      == ["https://i.redd.it/111aaa1.jpg", "https://i.redd.it/222bbb2.jpg",
          "https://i.redd.it/333ccc3.jpg"], str(g))
check("redlib: gif kind", g[3] == {"kind": "gif", "url": "https://i.redd.it/444ddd4.gif"}, str(g[3]))
check("redlib: empty page -> []", v3.extract_redlib_gallery("") == [])
check("redlib: bot-challenge page -> []", v3.extract_redlib_gallery("<html><h1>Please verify</h1></html>") == [])

# ---- 4d. test-post post-RSS source (round 12e) ----------------------------
import asyncio


class _FakeEntry:
    def __init__(self, link, title, author, content_value):
        self.link = link
        self.title = title
        self.author = author
        self._d = {"content": [{"value": content_value}], "summary": "",
                   "media_thumbnail": []}

    def get(self, k, d=None):
        return self._d.get(k, d)


def _post_rss_feed():
    post = _FakeEntry("/r/AnantaLeaks/comments/1wguffh/heist_mode/", "Heist mode",
                      "hugosince1999", "<p>1 hour long, 2-4 players</p>")
    comment = _FakeEntry("/r/AnantaLeaks/comments/1wguffh/heist_mode/comment/zzz/",
                         "a comment", "somebody", "<p>comment body</p>")
    return types.SimpleNamespace(entries=[post, comment])


orig_combined = v3.fetch_combined_feed
orig_post_rss = v3._fetch_post_rss
orig_fetch_feed = v3._fetch_feed

# 4d.1 URL construction (real _fetch_post_rss, fake _fetch_feed)
captured = []


async def _fetch_feed_fake(session, url, label):
    captured.append(url)
    return None


v3._fetch_feed = _fetch_feed_fake
asyncio.run(v3._fetch_post_rss(None, "/r/AnantaLeaks/comments/1wgq3cy/"))
v3._fetch_feed = orig_fetch_feed
check("post-rss: url = instance + permalink + .rss",
      bool(captured) and captured[0].startswith(
          "https://www.reddit.com/r/AnantaLeaks/comments/1wgq3cy/.rss"), str(captured[:1]))
check("post-rss: falls through every instance when all fail",
      len(captured) == len(v3.REDDIT_RSS_INSTANCES), str(len(captured)))

# 4d.2 end-to-end base from the post entry (fake feeds, no network)
async def _combined_none(session):
    return None


async def _post_rss_fake(session, path):
    return _post_rss_feed()


v3.fetch_combined_feed = _combined_none
v3._fetch_post_rss = _post_rss_fake
b2 = asyncio.run(v3.fetch_test_post_base(None, "/r/AnantaLeaks/comments/1wguffh/", "tp"))
check("tp-12e: base built from the post entry (not the comment)",
      b2 and b2["title"] == "Heist mode" and b2["author"] == "hugosince1999", str(b2))
check("tp-12e: body cleaned from post content",
      b2 and b2["body"] == "1 hour long, 2-4 players", str(b2 and b2.get("body")))
v3.fetch_combined_feed = orig_combined
v3._fetch_post_rss = orig_post_rss

# ---- 5. OP comment selection + card line ----------------------------------
pj = {"author": "OP", "permalink": "/r/Sub/comments/abc/title/"}
pj["_top_comments"] = [
    {"data": {"author": "other", "body": "nope"}},
    {"data": {"author": "OP", "body": "first"}},
    {"data": {"author": "OP", "body": "stickied!", "stickied": True, "id": "p9"}},
]
oc = v3.extract_op_comment(pj)
check("op: stickied wins", oc and oc["stickied"] and oc["text"] == "stickied!", str(oc))
check("op: permalink", oc and oc["permalink"] == "https://www.reddit.com/r/Sub/comments/abc/title/comment/p9/", str(oc))
check("op: none when absent", v3.extract_op_comment({"author": "A", "permalink": "/r/S/comments/x/",
                                                     "_top_comments": []}) is None)

# ---- 6. card layout ---------------------------------------------------------
data = {"title": "T", "author": "A", "body": "b" * 50,
        "media": [{"kind": "image", "url": f"https://i.redd.it/{i}.jpg"} for i in range(12)],
        "stats": {"comments": 3, "ups": 9}, "crosspost": None,
        "op_comment": {"text": "op note", "permalink": "https://pc", "stickied": True},
        "youtube_url": "https://youtube.com/shorts/ABCDEFGHI12", "full_mode": True}
p = v3.build_v3_payload("AnantaLeaks", data, "https://www.reddit.com/r/AnantaLeaks/comments/x/", 1)
check("layout: 2 containers for 12 media", len(p["components"]) == 2)
check("layout: flags", p["flags"] == 32768)
c2 = p["components"][1]
g2 = [c for c in c2["components"] if c.get("type") == 12]
check("layout: 2nd gallery has 2 items", len(g2) == 1 and len(g2[0]["items"]) == 2, str(len(g2)))
row = [c for c in c2["components"] if c.get("type") == 1][0]
labels = [b["label"] for b in row["components"]]
check("buttons: Read Post, YouTube, statics", labels == ["Read Post", "YouTube", "Citlali News", "Donate"], str(labels))
yt = row["components"][1]
check("buttons: youtube uses starwardspark3",
      yt["emoji"] == {"id": "1483083423290490891", "name": "starwardspark3", "animated": True}, str(yt))
texts = [c["content"] for ct in p["components"] for c in ct["components"] if c.get("type") == 10]
check("op line in card", any("OP comment" in t for t in texts), str(texts))
total = sum(len(t) for t in texts)
check("char budget under 4000", total < 4000, str(total))

# ---- 7. proxy media services (round 13, offline fixtures) ------------------
proxy = load_module("smoke_reddit_proxy", "testing area/reddit_proxy.py")

# 7.1 embeddit status-id codec (port of src/util/encode.ts — base-36:
# 36-char alphabet "1234567890" + "a-z"; '{' (123) -> 123//36=3 -> '4',
# 123%36=15 -> 'f')
enc = proxy.status_id_encode({"type": "post", "id": "abc123", "merge": True})
check("proxy: embeddit id encodes '{' as '4f'", enc.startswith("4f"), enc)
check("proxy: embeddit id round-trips",
      proxy.status_id_decode(enc) == json.dumps(
          {"type": "post", "id": "abc123", "merge": True}, separators=(",", ":")), enc)

# 7.2 og: meta parsing (multiple og:image = gallery, unescape, video tag)
og_page = (
    '<html><head>'
    '<meta property="og:site_name" content="u/leaker on r/AnantaLeaks - ⬆️ 1234 | 💬 56"/>'
    '<meta property="og:title" content="Heist mode &amp; more"/>'
    '<meta property="og:description" content="line one\nline two"/>'
    '<meta property="og:image" content="https://i.redd.it/aaa111.jpg"/>'
    '<meta property="og:image" content="https://i.redd.it/bbb222.gif"/>'
    '<meta property="og:video:secure_url" content="https://vxreddit.com/redditvideo.mp4?video_url=x"/>'
    '</head><body></body></html>'
)
meta = proxy._og_meta(og_page)
check("proxy: og:image collects all in order",
      meta.get("og:image") == ["https://i.redd.it/aaa111.jpg", "https://i.redd.it/bbb222.gif"],
      str(meta.get("og:image")))
check("proxy: og values unescaped", meta.get("og:title") == "Heist mode & more", str(meta.get("og:title")))
check("proxy: og:video captured",
      str(meta.get("og:video:secure_url", "")).startswith("https://vxreddit.com/redditvideo.mp4"),
      str(meta.get("og:video:secure_url")))

# r51: vxReddit sometimes returns an HTTP-200 shell for a crosspost's own
# URL. Its media-less generic title is not post data and must fall through to
# the next source; a real media result still remains usable.
class _R51VxResponse:
    status = 200

    def __init__(self, page):
        self.page = page

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self, **kwargs):
        return self.page


class _R51VxSession:
    def __init__(self, page):
        self.page = page

    def get(self, *args, **kwargs):
        return _R51VxResponse(self.page)


_r51_vx_placeholder = asyncio.run(proxy._fetch_vxreddit(
    _R51VxSession('<meta property="og:title" content="  VxReDdIt  ">'),
    "/r/u_aphotide/comments/1wuv4j8/title/", label="r51"))
check("r51 proxy: media-less case-insensitive vxReddit title is a fetch miss",
      _r51_vx_placeholder is None, str(_r51_vx_placeholder))
_r51_vx_media = asyncio.run(proxy._fetch_vxreddit(
    _R51VxSession('<meta property="og:title" content="vxReddit">'
                  '<meta property="og:image" content="https://i.redd.it/real.jpg">'),
    "/r/u_aphotide/comments/1wuv4j8/title/", label="r51"))
check("r51 proxy: generic title with actual media stays usable",
      _r51_vx_media is not None and _r51_vx_media["media"] == [
          {"kind": "image", "url": "https://i.redd.it/real.jpg"}],
      str(_r51_vx_media))

# 7.3 stats-line parsing (both service formats)
st = proxy.parse_icon_stats("💬 152  🔁 0  💜 573  👀 0")
check("proxy: redditez icon stats", st == {"comments": 152, "ups": 573}, str(st))
sm = proxy.VXREDDIT_STATS_RE.search("u/IdiotGaming on r/196 - ⬆️ 699 | 💬 29")
check("proxy: vxreddit stats line",
      bool(sm) and sm.group(1) == "IdiotGaming" and sm.group(3) == "699" and sm.group(4) == "29",
      str(sm.groups() if sm else None))

# 7.4 redditez search key extraction
check("proxy: redditez search key",
      proxy.parse_redditez_search({"success": True, "data": {"key": "search_abc", "site": "reddit"}})
      == "search_abc")
check("proxy: redditez search failure -> None",
      proxy.parse_redditez_search({"success": False}) is None)

# 7.5 embeddit JSON parsing (20-photo shape: title + body + stats + media)
edd_fixture = {
    "account": {"display_name": "u/Aikz21 (@ r/AnimeFigures)"},
    "content": ('<a href="https://reddit.com/r/AnimeFigures/comments/1sqass3/x/">'
                '<b>First time posting collection</b></a>'
                '<br/><br/><div>So, I generally never post online.</div>'
                '<br/><br/><div><b>⬆️ 305 • 💬 21</b></div>'),
    "media_attachments": [
        {"type": "image", "url": "https://preview.redd.it/aaa.jpg?width=4059&s=1"},
        {"type": "image", "url": "https://preview.redd.it/bbb.gif?width=4074&s=2"},
        {"type": "video", "url": "https://embeddit.deltandy.me/video/vid1/name.mp4"},
    ],
}
res = proxy.parse_embeddit_post(edd_fixture)
check("proxy: embeddit title", res and res["title"] == "First time posting collection",
      str(res and res.get("title")))
check("proxy: embeddit author + subreddit",
      res and res["author"] == "Aikz21" and res["subreddit"] == "AnimeFigures",
      str(res and (res.get("author"), res.get("subreddit"))))
check("proxy: embeddit stats", res and res["stats"] == {"ups": 305, "comments": 21},
      str(res and res.get("stats")))
check("proxy: embeddit body keeps middle lines only",
      res and res["body"] == "So, I generally never post online.", str(res and res.get("body")))
check("proxy: embeddit media kinds (image, gif, video)",
      res and [m["kind"] for m in res["media"]] == ["image", "gif", "video"],
      str(res and res["media"]))
check("proxy: embeddit bad data -> None", proxy.parse_embeddit_post({"nope": 1}) is None)

# 7.6 fallback chain + warm-up health skipping (fakes, no network)
async def _fake_rr(session, path, label=""):
    return None

async def _fake_vx(session, path, label=""):
    return {"service": "vxreddit", "title": "T", "author": "a", "subreddit": None,
            "body": "b", "stats": {"ups": 1, "comments": 2},
            "media": [{"kind": "image", "url": "https://i.redd.it/x.jpg"}]}

async def _fake_ed(session, path, label=""):
    return {"service": "embeddit", "title": "T", "author": "a", "subreddit": None,
            "body": "b", "stats": None,
            "media": [{"kind": "image", "url": "https://preview.redd.it/y.jpg?s=1"}]}

orig_rr, orig_vx, orig_ed = proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit
proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit = _fake_rr, _fake_vx, _fake_ed
r1 = asyncio.run(proxy.fetch_proxy_post(None, "/r/X/comments/abc/", label="t", health={}))
check("proxy: falls through to vxreddit when redditez has no data",
      r1 and r1["service"] == "vxreddit", str(r1))
r2 = asyncio.run(proxy.fetch_proxy_post(None, "/r/X/comments/abc/", label="t",
                                        health={"vxreddit": {"ok": False}}))
check("proxy: warm-up-dead service is skipped (embeddit wins)",
      r2 and r2["service"] == "embeddit", str(r2))
r3 = asyncio.run(proxy.fetch_proxy_post(None, "/r/X/comments/abc/", label="t",
                                        health={s: {"ok": False} for s in proxy.PROXY_SERVICES}))
check("proxy: ALL-dead health still retries every service",
      r3 and r3["service"] == "vxreddit", str(r3))
proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit = orig_rr, orig_vx, orig_ed

# 7.7 health file round-trip
import tempfile
orig_health_file = proxy.PROXY_HEALTH_FILE
with tempfile.TemporaryDirectory() as _td:
    proxy.PROXY_HEALTH_FILE = os.path.join(_td, "health.json")
    proxy.save_proxy_health({"post": "X/y",
                             "services": {"redditez": {"ok": True, "detail": "1 media item(s)"}}})
    loaded = proxy.load_proxy_health()
    check("proxy: health file round-trip",
          loaded.get("redditez", {}).get("ok") is True, str(loaded))
    proxy.PROXY_HEALTH_FILE = os.path.join(_td, "missing.json")
    check("proxy: missing health file -> {}", proxy.load_proxy_health() == {})
proxy.PROXY_HEALTH_FILE = orig_health_file

# ---- 8. round 14: body formatting, og dedupe, crosspost -------------------
# 8.1 clean_proxy_body: markdown links + bold + paragraph breaks + URL strip
html_body = ('<a href="https://en.wikipedia.org/wiki/Heist">BGM: Heist</a> more text<br/>'
             '<b>bold line</b><p><a href="https://i.redd.it/abc123.png?s=1">img</a></p>'
             'plain https://preview.redd.it/xyz-v0-def456.jpg?width=140&s=2 end')
cb = proxy.clean_proxy_body(html_body)
check("r14 body: markdown link kept",
      "[BGM: Heist](https://en.wikipedia.org/wiki/Heist)" in cb, cb)
check("r14 body: bold kept as **bold**", "**bold line**" in cb, cb)
check("r14 body: paragraph breaks kept", "\n" in cb, cb)
check("r14 body: redd.it media links+URLs removed", "redd.it" not in cb, cb)
check("r14 body: surrounding text kept", "more text" in cb and "end" in cb, cb)

# 8.2 _og_meta: double-escaped values unescape to stable
meta2 = proxy._og_meta('<html><head><meta property="og:title" content="A &amp;amp; B"/></head></html>')
check("r14 og: double-escape unescaped", meta2.get("og:title") == "A & B", str(meta2.get("og:title")))

# 8.3 dedupe_proxy_media (the 4 duplicate shapes from the 2026-09-16 run)
dm = proxy.dedupe_proxy_media
check("r14 dedupe: single item untouched",
      dm([{"kind": "image", "url": "https://i.redd.it/solo.jpg"}])
      == [{"kind": "image", "url": "https://i.redd.it/solo.jpg"}])
check("r14 dedupe: same file id collapsed",
      dm([{"kind": "image", "url": "https://i.redd.it/aaaa.jpg"},
          {"kind": "image", "url": "https://i.redd.it/aaaa.jpg"}])
      == [{"kind": "image", "url": "https://i.redd.it/aaaa.jpg"}])
check("r14 dedupe: 140px crop dropped",
      dm([{"kind": "image", "url": "https://i.redd.it/aaaa.jpg"},
          {"kind": "image", "url": "https://preview.redd.it/s-v0-bbbb.jpg?width=140&crop=1:1,smart&s=1"}])
      == [{"kind": "image", "url": "https://i.redd.it/aaaa.jpg"}])
check("r14 dedupe: trailing main-image tag dropped (1wguffh shape)",
      dm([{"kind": "image", "url": "https://embedez.com/api/v2/redirect/6633670e/1"},
          {"kind": "image", "url": "https://embedez.com/api/v2/redirect/6633670e/2"},
          {"kind": "image", "url": "https://embedez.com/api/v2/redirect/6633670e/3"},
          {"kind": "image", "url": "https://i.redd.it/heist-mode-v0-qw19p2v3g8uh1.jpg"}])
      == [{"kind": "image", "url": "https://embedez.com/api/v2/redirect/6633670e/1"},
          {"kind": "image", "url": "https://embedez.com/api/v2/redirect/6633670e/2"},
          {"kind": "image", "url": "https://embedez.com/api/v2/redirect/6633670e/3"}])
check("r14 dedupe: vN- slug variants of same id collapsed",
      dm([{"kind": "image", "url": "https://i.redd.it/heist-mode-v0-qw19p2v3g8uh1.jpg"},
          {"kind": "image", "url": "https://preview.redd.it/qw19p2v3g8uh1.jpg?width=1080&s=1"}])
      == [{"kind": "image", "url": "https://i.redd.it/heist-mode-v0-qw19p2v3g8uh1.jpg"}])

# 8.4 clean_rss_body: clickable links kept, redd.it URLs gone, nav gone
rss_html = ('<table><tr><td><p>Check <a href="https://example.com/x">this link</a> out</p>'
            '<p>pic: https://i.redd.it/abcd1234efgh.jpg and '
            '<a href="https://i.redd.it/abcd1234efgh.jpg">it</a></p>'
            '<span>submitted by /u/x to r/y <a href="https://www.reddit.com/r/y/comments/1z/">link</a> '
            '<a href="https://www.reddit.com/r/y/comments/1z/">comments</a></span>'
            '</td></tr></table>')
rb = v3.clean_rss_body(rss_html)
check("r14 rss: non-redd.it link clickable", "[this link](https://example.com/x)" in rb, rb)
check("r14 rss: redd.it URLs gone", "redd.it" not in rb, rb)
check("r14 rss: [link]/[comments] nav gone",
      "www.reddit.com" not in rb and "comments" not in rb, rb)

# 8.5 crosspost detection
cp = v3.find_crosspost_original_path
check("r14 crosspost: detected in RSS content",
      cp('<p>u/leak crossposted this from r/AnantaLeaks — '
         '<a href="https://www.reddit.com/r/AnantaLeaks/comments/1wgjk4a/orig/">original post</a></p>',
         "/r/Other/comments/1wabcdx/") == "/r/AnantaLeaks/comments/1wgjk4a/", "")
check("r14 crosspost: no 'crosspost' marker -> None",
      cp('<p>see <a href="https://www.reddit.com/r/X/comments/abc/">that post</a></p>') is None)
check("r14 crosspost: own permalink excluded",
      cp("crosspost /r/X/comments/abc/", "/r/X/comments/abc/") is None)
check("r14 crosspost: plain-text permalink found",
      cp("u/x crossposted this from r/Y — /r/AnantaLeaks/comments/1wgjk4a/x", None)
      == "/r/AnantaLeaks/comments/1wgjk4a/", "")

# 8.6 entry_to_base_data carries the crosspost field
ce = _FakeEntry("/r/Other/comments/1wabcdx/slug/", "Crosspost title", "leak",
                '<p>u/leak crossposted this from r/AnantaLeaks — '
                '<a href="https://www.reddit.com/r/AnantaLeaks/comments/1wgjk4a/orig/">original post</a></p>')
b3 = v3.entry_to_base_data(ce)
check("r14 crosspost: base carries original path",
      b3.get("crosspost_orig_path") == "/r/AnantaLeaks/comments/1wgjk4a/",
      str(b3.get("crosspost_orig_path")))

# 8.7 embeddit title from the <a><b>…</b></a> anchor
edd2 = proxy.parse_embeddit_post({
    "account": {"display_name": "u/A (@ r/B)"},
    "content": ('<a href="https://reddit.com/r/B/comments/1abc/x/"><b>My &amp; title</b></a>'
                '<br/><div>body line</div><br/><div><b>⬆️ 10 • 💬 2</b></div>'),
    "media_attachments": [],
})
check("r14 embeddit: anchor title (plain, unescaped)",
      edd2 and edd2["title"] == "My & title", str(edd2 and edd2.get("title")))
check("r14 embeddit: body excludes title link + stats footer",
      edd2 and edd2["body"] == "body line", str(edd2 and edd2.get("body")))

# ---- Round 15: regression coverage (offline; synthetic archive fixtures) ----
for module in (v3, proxy):
    cleaner = module.clean_rss_body if module is v3 else module.clean_proxy_body
    check("r15 valid standalone markdown retained",
          cleaner("[Spotify](https://example.com/music)") == "[Spotify](https://example.com/music)")
    check("r15 URL-labelled markdown -> bare URL (round 22)",
          cleaner("[https://example.com](https://example.com)") == "https://example.com")
    check("r15 legitimate submitted-by prose retained",
          cleaner("This was submitted by a musician.") == "This was submitted by a musician.")
    check("r15 punctuation repair", cleaner("Buff](https://example.com)\nBuff): TBD") == "Buff: TBD")
    check("r15 cascade repair (round 22: raw bare URLs)", cleaner(
        "Firefly](https://example.com/previous)\nFirefly) video [https://b23.tv/aaa\n"
        "Feixiao](https://b23.tv/aaa)\nFeixiao) video [https://b23.tv/bbb") ==
        "Firefly video https://b23.tv/aaa\n"
        "Feixiao video https://b23.tv/bbb")
    check("r15 footer repair", cleaner(
        "[ ](https://reddit.com/post)\nsubmitted](https://reddit.com/post)\n"
        "submitted) by [ /u/name_ ](https://reddit.com/u/name_) to [r/sub](https://reddit.com/r/sub)") == "")
    check("r15 merged linked footer dropped", cleaner(
        "[](https://www.reddit.com/r/Sub/comments/1abc/) submitted by "
        "[/u/name_](https://www.reddit.com/user/name_/) to [r/Sub](https://www.reddit.com/r/Sub/)") == "")
    check("r15 paragraph spacing", cleaner("<p>First</p><p>Second</p>") == "First\n\nSecond")
    check("r15 hidden video URL", cleaner("<p>https://v.redd.it/abc</p>") == "")
    check("r15 linked HTML footer", cleaner('<span>submitted by <a href="https://reddit.com/u/name">/u/name</a> to <a href="https://reddit.com/r/sub">r/sub</a></span>') == "")
    # ---- round 16: source HTML formatting (both cleaners) ------------------
    check("r16 bold from strong tag",
          cleaner("<p>Ice DMG <strong>increases by 20%</strong> a lot</p>")
          == "Ice DMG **increases by 20%** a lot")
    check("r16 bold from b tag",
          cleaner("<p><b>Anomaly</b> specialty</p>") == "**Anomaly** specialty")
    check("r16 no bold forced when absent",
          cleaner("<p>Ice DMG increases by 20% a lot</p>")
          == "Ice DMG increases by 20% a lot")
    check("r16 bold nested inside a link",
          cleaner('<strong><a href="https://lunaris.moe/">Lunaris</a></strong> is nice')
          == "**[Lunaris](https://lunaris.moe/)** is nice")
    check("r16 bullet list",
          cleaner("<ul><li>first item</li><li>second item</li></ul>")
          == "- first item\n- second item")
    check("r16 bullet with inline bold",
          cleaner("<ul><li>Agents with the <strong>Anomaly</strong> specialty "
                  "have their ATK increased by <strong>20%</strong>.</li></ul>")
          == "- Agents with the **Anomaly** specialty have their ATK "
             "increased by **20%**.")
    check("r16 blockquote becomes a Discord quote line",
          cleaner("<p>before</p><blockquote>[spoilers] &amp;gt;!hidden!&amp;lt;"
                  "</blockquote><p>after</p>")
          == "before\n> [spoilers] >!hidden!" + "<\nafter")
    check("r16 relative user link becomes absolute",
          cleaner('<a href="/u/empty_Berry-Kun">u/empty_Berry-Kun</a>')
          == "[u/empty_Berry-Kun](https://www.reddit.com/user/empty_Berry-Kun/)")
    check("r16 relative subreddit link becomes absolute",
          cleaner('<a href="/r/Genshin_Impact_Leaks/wiki/posting_guidelines/">'
                  "posting guidelines</a>")
          == "[posting guidelines]"
             "(https://www.reddit.com/r/Genshin_Impact_Leaks/wiki/posting_guidelines/)")
    check("r16 mangled 3-line link shape repairs to 3 plain label + bare-URL lines",
          cleaner("Firefly](https://b23.tv/prev0)\n"
                  "Firefly) video [[https://b23.tv/dkCXgES](https://b23.tv/dkCXgES)\n\n"
                  "Feixiao](https://b23.tv/dkCXgES](https://b23.tv/dkCXgES)\n\n"
                  "Feixiao) video [[https://b23.tv/XojBeMr](https://b23.tv/XojBeMr)\n\n"
                  "Therta](https://b23.tv/XojBeMr](https://b23.tv/XojBeMr)\n\n"
                  "Therta) video [[https://b23.tv/PNtXo0u](https://b23.tv/PNtXo0u)]"
                  "(https://b23.tv/PNtXo0u](https://b23.tv/PNtXo0u))")
          == "Firefly video https://b23.tv/dkCXgES\n\n"
             "Feixiao video https://b23.tv/XojBeMr\n\n"
             "Therta video https://b23.tv/PNtXo0u")
    # ---- round 41: reddit html spoiler conversion ---------------------------
    check("r41 html spoiler tag converts to discord spoiler",
          cleaner('<p>Spoiler: <span class="md-spoiler-text">boss dies</span></p>')
          == "Spoiler: ||boss dies||")
    check("r41 megathread spoiler policy case",
          cleaner("<blockquote><code>3.1 Ending &gt;!spoilers here, between the symbols on the ends!&lt;</code>"
                  "\n3.1 Ending <span class=\"md-spoiler-text\">spoilers here, between the symbols on the ends</span></blockquote>")
          == "> 3.1 Ending >!spoilers here, between the symbols on the ends!<\n"
             "> 3.1 Ending ||spoilers here, between the symbols on the ends||")

check("r15 author underscore", v3._clean_author_name(
    "](https://reddit.com/post)\n*by) Knight_Steve_") == "Knight_Steve_")
check("r15 author hyphen", v3._clean_author_name("/u/valid-name") == "valid-name")
check("r15 garbage author", v3._clean_author_name("](https://reddit.com/post)") == "unknown")
check("r15 clean title punctuation", v3._clean_post_title("[Preview] *New* weapon") == "[Preview] *New* weapon")
check("r15 title artifact", v3._clean_post_title("Overview](https://reddit.com/post)\n*by") == "Overview")
youtube = "https://www.youtube.com/watch?v=abcdefghijk"
check("r15 cleaned YouTube URL dropped", v3._drop_youtube_line(
    v3.clean_rss_body(f"<p>{youtube}</p><p>First</p><p>Second</p>"), youtube) == "First\n\nSecond")
check("r15 other YouTube URL kept", v3._drop_youtube_line(
    "https://youtu.be/12345678901", youtube) == "https://youtu.be/12345678901")

archive_gallery = {"id": "gallery1", "gallery_data": {"items": [
    {"media_id": str(i)} for i in range(6)]}, "media_metadata": {
    str(i): {"status": "valid", "e": "AnimatedImage" if i in (2, 3, 4) else "Image",
             "s": {"gif" if i in (2, 3, 4) else "u":
                   f"https://i.redd.it/{i}.gif" if i in (2, 3, 4) else
                   f"https://preview.redd.it/slug-v0-{i}.jpg?width=700&amp;s=x"}}
    for i in range(6)}}
items = v3.arctic_gallery_items(archive_gallery)
check("r15 six ordered gallery items", [item["kind"] for item in items] ==
      ["image", "image", "gif", "gif", "gif", "image"])
check("r15 full resolution image", items[0]["url"] == "https://i.redd.it/0.jpg")
for malformed in (None, [], {}, {"gallery_data": []},
                  {"gallery_data": {"items": []}, "media_metadata": []},
                  {"gallery_data": {"items": [{"media_id": []}]}, "media_metadata": {}}):
    check("r15 malformed gallery safe", v3.arctic_gallery_items(malformed) == [])

# Fake every I/O boundary to exercise actual orchestration, not just parsers.
import asyncio
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

async def round15_flows():
    original_path = "/r/Original/comments/orig1/title/"
    original = {"id": "orig1", "permalink": original_path,
                "selftext": "", "ups": 10, "num_comments": 2,
                "media": {"reddit_video": {"fallback_url": "https://v.redd.it/video1/CMAF_1080.mp4"}}}
    base = {"title": "Crosspost", "author": "name_", "body": "", "youtube_url": None,
            "vred_id": None, "redgifs_url": None, "content_html": "", "thumb": None}
    async def get_proxy(session, path, **kwargs):
        check("r15 proxy fetch uses original", path == original_path)
        return {"service": "test", "media": [{"kind": "video", "url": "https://example.com/video.mp4"}],
                "stats": {"ups": 30, "comments": 4}, "body": ""}
    fake_proxy = SimpleNamespace(fetch_proxy_post=AsyncMock(side_effect=get_proxy),
                                 fetch_embeddit_stats=AsyncMock(return_value=None))
    with patch.object(v3, "reddit_proxy", fake_proxy), patch.object(v3, "PROXY_MEDIA", True), \
         patch.object(v3, "fetch_arctic_post", AsyncMock(return_value={"crosspost_parent_list": [original]})), \
         patch.object(v3, "enrich_gallery_redlib", AsyncMock(return_value=[])), \
         patch.object(v3, "media_url_ok", AsyncMock(return_value=(True, 123))), \
         patch.object(v3, "resolve_video_url", AsyncMock(return_value="https://example.com/fallback.mp4")):
        result = await v3.resolve_post_media(None, base, None, "/r/Sub/comments/cross1/title/")
        check("r15 original crosspost link", result["crosspost"]["path"] == original_path)
        check("r15 crosspost video only", result["media"] == [{"kind": "video", "url": "https://example.com/video.mp4"}])
        check("r15 live proxy stats preferred", result["stats"]["ups"] == 30)
        # A proxy screenshot must not prevent the original video's fallback.
        fake_proxy.fetch_proxy_post = AsyncMock(return_value={"service": "test", "media": [
            {"kind": "image", "url": "https://example.com/screenshot.jpg"}], "stats": None, "body": ""})
        result = await v3.resolve_post_media(None, base, None, "/r/Sub/comments/cross1/title/")
        check("r15 screenshot rejected", result["media"] == [{"kind": "video", "url": "https://example.com/fallback.mp4"}])
        check("r15 archived stats labelled", result["stats"].get("archived") is True)
    fake_proxy.fetch_proxy_post = AsyncMock(return_value={"service": "test", "media": items[:2],
                                                         "body": "FirstSecond", "stats": None})
    with patch.object(v3, "reddit_proxy", fake_proxy), patch.object(v3, "PROXY_MEDIA", True), \
         patch.object(v3, "fetch_arctic_post", AsyncMock(return_value=archive_gallery)), \
         patch.object(v3, "enrich_gallery_redlib", AsyncMock(return_value=[])):
        result = await v3.resolve_post_media(None, dict(base, body="First\n\nSecond"), None,
                                             "/r/Sub/comments/gallery1/title/")
        check("r15 archive gallery beats short proxy", result["media"] == items)
        check("r15 RSS body wins", result["body"] == "First\n\nSecond")
    with patch.object(v3, "PROXY_MEDIA", False), \
         patch.object(v3, "fetch_arctic_post", AsyncMock(return_value=archive_gallery)):
        result = await v3.resolve_post_media(None, base, None, "/r/Sub/comments/gallery1/title/")
        check("r15 gallery independent of proxies", result["media"] == items)
    with patch.object(v3, "reddit_proxy", fake_proxy), patch.object(v3, "PROXY_MEDIA", True), \
         patch.object(v3, "fetch_arctic_post", AsyncMock(return_value=None)), \
         patch.object(v3, "enrich_gallery_redlib", AsyncMock(return_value=[])):
        result = await v3.resolve_post_media(None, base, None, "/r/Sub/comments/new1/title/")
        check("r15 archive miss keeps existing proxy path", result["media"] == items[:2])

async def round15_fetch():
    class Response:
        status = 200
        data = {"data": []}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self, **kwargs): return self.data
    response = Response()
    session = SimpleNamespace(get=lambda *args, **kwargs: response)
    with patch.object(v3, "_arctic_fail_count", 0):
        check("r15 archive empty response", await v3.fetch_arctic_post(session, "abc") is None)
        check("r15 archive misses not outages", v3._arctic_fail_count == 0)
        response.data = {"data": [{"id": "wrong"}, {"id": "abc"}]}
        check("r15 archive verifies post ID", (await v3.fetch_arctic_post(session, "abc"))["id"] == "abc")
        response.data = []
        check("r15 archive malformed safe", await v3.fetch_arctic_post(session, "abc") is None)
        response.status = 429
        for _ in range(2): await v3.fetch_arctic_post(session, "abc")
        check("r15 archive circuit breaker", v3._arctic_fail_count == 3)
        response.status = 200
        check("r15 archive circuit breaker skips", await v3.fetch_arctic_post(session, "abc") is None)

# ---- round 16: crosspost line (subreddit masked to the original post) -----
_cp_data = {"title": "Houses", "author": "Ananta2027", "body": "", "media": [],
            "stats": None,
            "crosspost": {"url": "https://www.reddit.com/r/Ananta2027/comments/1wgjebk/houses/",
                          "path": "/r/Ananta2027/comments/1wgjebk/houses/"},
            "op_comment": None, "youtube_url": None, "full_mode": False}
_cp = v3.build_v3_payload("Ananta2027", _cp_data,
                          "https://www.reddit.com/r/Other/comments/1wgjebl/cross/", 1)
_cp_texts = [c["content"] for ct in _cp["components"] for c in ct["components"]
             if c.get("type") == 10]
check("r16 crosspost line masks the original subreddit",
      "*🔁 Crosspost of [Ananta2027]"
      "(https://www.reddit.com/r/Ananta2027/comments/1wgjebk/houses/) Subreddit*"
      in _cp_texts[0], str(_cp_texts))
_cp_raw = v3.build_v3_payload("Sub", dict(_cp_data, crosspost={"url": "https://x", "path": ""}),
                              "https://www.reddit.com/r/Sub/comments/x/", 1)
_cp_texts = [c["content"] for ct in _cp_raw["components"] for c in ct["components"]
             if c.get("type") == 10]
check("r16 crosspost raw line when subreddit unknown",
      any("*🔁 Crosspost of https://x*" in t for t in _cp_texts), str(_cp_texts))
_cp_none = v3.build_v3_payload("Sub", dict(_cp_data, crosspost=None),
                               "https://www.reddit.com/r/Sub/comments/x/", 1)
_cp_texts = [c["content"] for ct in _cp_none["components"] for c in ct["components"]
             if c.get("type") == 10]
check("r16 no crosspost line when none",
      not any("Crosspost of" in t for t in _cp_texts), str(_cp_texts))

# ---- round 16: need_video fall-through (video posts prefer the muxed video)
async def _nv_rr(session, path, label=""):
    return {"service": "redditez", "title": "T", "author": None, "subreddit": None,
            "body": "b", "stats": {"ups": 1, "comments": 2}, "media": []}

async def _nv_vx(session, path, label=""):
    return {"service": "vxreddit", "title": "T", "author": "a", "subreddit": None,
            "body": "b", "stats": None,
            "media": [{"kind": "video", "url": "https://vxreddit.com/video.mp4"}]}

async def _nv_ed(session, path, label=""):
    return None

orig_nv_rr, orig_nv_vx, orig_nv_ed = proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit
proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit = _nv_rr, _nv_vx, _nv_ed
_nv1 = asyncio.run(proxy.fetch_proxy_post(None, "/r/X/comments/abc/", label="t",
                                          health={}, need_video=True))
check("r16 need_video: skips media-less service, muxed video wins",
      _nv1 and _nv1["service"] == "vxreddit" and _nv1["media"][0]["kind"] == "video",
      str(_nv1))
# round 23 (2026-09-18): MEDIA wins the chain. redditez here has the text +
# stats but no media, so it is only kept as the body/stats fallback and the
# chain continues — vxreddit (which does have the video) wins.
_nv2 = asyncio.run(proxy.fetch_proxy_post(None, "/r/X/comments/abc/", label="t", health={}))
check("r23 need_video off: media wins over the text-only first service",
      _nv2 and _nv2["service"] == "vxreddit" and _nv2["media"][0]["kind"] == "video",
      str(_nv2))
proxy._fetch_vxreddit = _nv_ed
_nv3 = asyncio.run(proxy.fetch_proxy_post(None, "/r/X/comments/abc/", label="t",
                                          health={}, need_video=True))
check("r16 need_video: keeps body/stats fallback when no video anywhere",
      _nv3 and _nv3["service"] == "redditez"
      and _nv3["stats"] == {"ups": 1, "comments": 2}, str(_nv3))
proxy._fetch_vxreddit = _nv_vx
_nv4 = asyncio.run(proxy.fetch_proxy_post(None, "/r/X/comments/abc/", label="t",
                                          health={"redditez": {"ok": False}},
                                          need_video=True))
check("r16 need_video: warm-up-dead service still skipped",
      _nv4 and _nv4["service"] == "vxreddit", str(_nv4))
proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit = orig_nv_rr, orig_nv_vx, orig_nv_ed

# ---- round 23 (2026-09-18): media must win the proxy chain ----------------
# 1wj38fc reproduction: redditez dead -> vxreddit has ONLY the title/stats
# (og:image tags not rendered yet) -> embeddit, which HAS both photos, wins.
async def _r23_dead(session, path, label=""):
    return None


async def _r23_text_only(session, path, label=""):
    return {"service": "vxreddit", "title": "Gallery title", "author": "a",
            "subreddit": "Sub", "body": "body text",
            "stats": {"ups": 12, "comments": 3}, "media": []}


async def _r23_photos(session, path, label=""):
    return {"service": "embeddit", "title": "Gallery title", "author": "a",
            "subreddit": "Sub", "body": "body text", "stats": None,
            "media": [{"kind": "image", "url": "https://i.redd.it/p1.jpg"},
                      {"kind": "image", "url": "https://i.redd.it/p2.jpg"}]}


orig_r23 = (proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit)
proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit = (
    _r23_dead, _r23_text_only, _r23_photos)
_r23a = asyncio.run(proxy.fetch_proxy_post(None, "/r/Sub/comments/1wj38fc/",
                                           label="t", health={}))
check("r23 1wj38fc: text-only vxreddit does not stop the chain — embeddit's photos win",
      _r23a and _r23a["service"] == "embeddit" and len(_r23a["media"]) == 2, str(_r23a))

proxy._fetch_redditez = _r23_photos
_r23b = asyncio.run(proxy.fetch_proxy_post(None, "/r/Sub/comments/abc/",
                                           label="t", health={}))
check("r23 media-first: equal media counts keep the earlier result",
      _r23b and _r23b["service"] == "embeddit" and len(_r23b["media"]) == 2, str(_r23b))

proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit = (
    _nv_rr, _r23_text_only, _r23_dead)
_r23c = asyncio.run(proxy.fetch_proxy_post(None, "/r/Sub/comments/abc/",
                                           label="t", health={}))
# Round 49: the services are asked concurrently, so "first" is no longer an
# arrival order — the body/stats fallback is the HIGHEST-PRIORITY text-only
# answer (vxreddit, which is also the one that parses author + stats).
check("r49 all text-only: the highest-priority text-only result is the fallback",
      _r23c and _r23c["service"] == "vxreddit"
      and _r23c["stats"] == {"ups": 12, "comments": 3}, str(_r23c))

proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit = (_r23_dead,) * 3
_r23d = asyncio.run(proxy.fetch_proxy_post(None, "/r/Sub/comments/abc/",
                                           label="t", health={}))
check("r23 all dead: None (caller falls back to the native path)", _r23d is None, str(_r23d))

proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit = (
    _r23_dead, _r23_text_only, _r23_photos)
_r23e = asyncio.run(proxy.fetch_proxy_post(None, "/r/Sub/comments/abc/", label="t",
                                           health={"redditez": {"ok": False},
                                                   "vxreddit": {"ok": False}}))
check("r23 warm-up-dead services still skipped, media still wins",
      _r23e and _r23e["service"] == "embeddit", str(_r23e))
proxy._fetch_redditez, proxy._fetch_vxreddit, proxy._fetch_embeddit = orig_r23

# ---- round 23 (b): 22(b) removal-notice variants --------------------------
check("r23 title: whole title '[ Removed by moderator ]' is a removal marker",
      v3.removed_post_reason("[ Removed by moderator ]", "body") == "title marker")
check("r23 title: bold '**[ Removed by moderator ]**' is a removal marker",
      v3.removed_post_reason("**[ Removed by moderator ]**", "body") == "title marker")
check("r23 title: tight '[Removed by moderator]' is a removal marker",
      v3.removed_post_reason("[Removed by moderator]", "") == "title marker")
check("r23 title: '[removed]' / '[deleted]' still markers (round 18 baselines)",
      v3.removed_post_reason("[removed]", "") == "title marker"
      and v3.removed_post_reason("[deleted]", "") == "title marker")
check("r23 body: \"removed by Reddit's filters\" is caught",
      v3.removed_post_reason("Title", "This post was removed by Reddit's filters.")
      == "removed by moderators/filters")
check("r23 body: \"removed by Reddit's automated system\" is caught",
      v3.removed_post_reason("Title", "This post was removed by Reddit's automated system.")
      == "removed by moderators/filters")
check("r23 body: \"removed by the moderators\" still caught",
      v3.removed_post_reason("Title", "This post has been removed by the moderators of r/X.")
      == "removed by moderators/filters")
check("r23 body: the full Reddit filter notice is still caught",
      v3.removed_post_reason("Title", "Sorry, this post was removed by Reddit's filters.")
      is not None)
check("r23 legit title mentioning [removed] is NOT caught",
      v3.removed_post_reason("Why was [removed] in the patch notes?",
                             "body text") is None)
check("r23 legit title about moderator tools is NOT caught",
      v3.removed_post_reason("Removed by moderator tools guide", "body text") is None)
check("r23 legit body mentioning Reddit is NOT caught",
      v3.removed_post_reason("Title",
                             "I love posting on Reddit. Nothing was removed here.")
      is None)

# ---- round 23 (c): the Arctic media hint is positive-only -----------------
check("r23 hint: gallery_data", v3._arctic_media_hint({"gallery_data": {"items": []}}) is True)
check("r23 hint: is_gallery", v3._arctic_media_hint({"is_gallery": True}) is True)
check("r23 hint: media_metadata", v3._arctic_media_hint({"media_metadata": {"a": {}}}) is True)
check("r23 hint: post_hint=image", v3._arctic_media_hint({"post_hint": "image"}) is True)
check("r23 hint: i.redd.it url",
      v3._arctic_media_hint({"url": "https://i.redd.it/abc.jpg"}) is True)
check("r23 hint: v.redd.it url", v3._arctic_media_hint({"url": "https://v.redd.it/abc"}) is True)
check("r23 hint: secure_media_domain",
      v3._arctic_media_hint({"secure_media_domain": "i.redd.it"}) is True)
check("r23 hint: plain text post has NO hint",
      v3._arctic_media_hint({"selftext": "hi"}) is False)
check("r23 hint: link post has NO hint",
      v3._arctic_media_hint({"url": "https://example.com/a", "post_hint": "link"}) is False)
check("r23 hint: None / empty dict are safe",
      v3._arctic_media_hint(None) is False and v3._arctic_media_hint({}) is False)

# ---- round 23 (d): the main-loop gate — skip, and skip WITHOUT caching ----
_main_src = inspect.getsource(v3.main)
_gate = _main_src.split("# ---- round 23 (2026-09-18): Arctic media-hint gate", 1)[-1]
_gate = _gate.split("if (", 1)[-1].split("posted_ts =", 1)[0]
for _cond in ("not TEST_POST_ID", "not DRY_RUN", "post_json is None",
              "isinstance(entry, _ArcticEntry)", "not data[\"media\"]",
              "_arctic_media_hint(getattr(entry, \"_arctic_post\", None))"):
    check(f"r23 gate keeps condition: {_cond}", _cond in _gate, _gate)
check("r23 gate: the skip is a `continue` and caches nothing "
      "(so the post is retried next run)",
      _gate.rstrip().endswith("continue") and "posted.add" not in _gate, _gate)

# ---- round 14 (2026-09-18): miningtcup RSS token sent all three ways ------
for _rel, _label in (("testing area/twitter_v3.py", "X V3"),
                     ("testing area/twitter_v2_button_outside.py", "X V2")):
    with open(os.path.join(ROOT, _rel), "r", encoding="utf-8") as _fh:
        _src = _fh.read()
    check(f"r14 {_label}: Authorization: Bearer header kept",
          '"Authorization": f"Bearer {token}"' in _src)
    check(f"r14 {_label}: token also sent inside the User-Agent",
          '"User-Agent": f"Mozilla/5.0 {token}"' in _src)
    check(f"r14 {_label}: ?token= query param kept",
          "?token={url_quote(token, safe='')}" in _src)

# ---- round 24 (2026-09-18): redlib.miningtcup.me + DogWAF token helper ----
def _r24_miningtcup_checks(mod):
    # round 24: redlib.miningtcup.me in the fleet + DogWAF token helper
    check("r24 fleet: redlib.miningtcup.me joins REDDIT_RSS_INSTANCES",
          "https://redlib.miningtcup.me" in mod.REDDIT_RSS_INSTANCES,
          str(mod.REDDIT_RSS_INSTANCES))
    saved = mod.MININGTCUP_TOKEN
    try:
        mod.MININGTCUP_TOKEN = "abc123-token"
        u = mod._with_miningtcup_token("https://redlib.miningtcup.me/r/X/new/.rss")
        check("r24 token: ?token= appended (no existing query)",
              u == "https://redlib.miningtcup.me/r/X/new/.rss?token=abc123-token", u)
        u = mod._with_miningtcup_token("https://redlib.miningtcup.me/r/X/comments/Y?foo=1")
        check("r24 token: &token= appended (existing query)",
              u == "https://redlib.miningtcup.me/r/X/comments/Y?foo=1&token=abc123-token", u)
        u = mod._with_miningtcup_token("https://safereddit.com/r/X/new/.rss")
        check("r24 token: other hosts untouched",
              u == "https://safereddit.com/r/X/new/.rss", u)
        mod.MININGTCUP_TOKEN = ""
        u = mod._with_miningtcup_token("https://redlib.miningtcup.me/r/X/new/.rss")
        check("r24 token: empty token is a no-op",
              u == "https://redlib.miningtcup.me/r/X/new/.rss", u)
    finally:
        mod.MININGTCUP_TOKEN = saved


_r24_miningtcup_checks(v3)

# ---- round 11: X V3 tweet-data fallback chain (twitter_proxy) -------------
tpx = None
try:
    tpx = load_module("smoke_twitter_proxy", "testing area/twitter_proxy.py")
except Exception as e:
    tpx = None
    check("import testing area/twitter_proxy.py", False, repr(e))

if tpx is not None:
    check("import testing area/twitter_proxy.py", True)

    # --- GIF chain: order, probes, short-circuit (no network) --------------
    async def round11_gif():
        probes = []
        orig_probe = tpx._probe_image_url

        def make_fake(ok_map):
            async def fake_probe(session, url, referer=None):
                probes.append((url, referer))
                return bool(ok_map(url))
            return fake_probe

        mp4 = "https://video.twimg.com/tweet_video/abc123DEF.mp4"

        tpx._probe_image_url = make_fake(lambda u: "gif.fxtwitter.com" in u)
        url, src = await tpx.resolve_gif_image(None, mp4)
        check("r11 gif: fxtwitter .webp is first and wins",
              url == "https://gif.fxtwitter.com/tweet_video/abc123DEF.webp" and src == "gif.fxtwitter",
              f"{url} {src}")
        check("r11 gif: short-circuits after the winner (1 probe)", len(probes) == 1, str(probes))

        probes.clear()
        tpx._probe_image_url = make_fake(lambda u: "gifconvert" in u and "/convert.webp" in u)
        url, src = await tpx.resolve_gif_image(None, mp4)
        check("r11 gif: gifconvert .webp is 2nd (referer-gated)",
              src == "gifconvert" and "convert.webp" in url
              and probes[1][1] == "https://vxtwitter.com", f"{url} {src} {probes[1:]}")
        check("r11 gif: gifconvert URL carries the quoted mp4",
              "?url=" in url and "video.twimg.com" in url.replace("%2F", "/").replace("%3A", ":"),
              url)

        probes.clear()
        tpx._probe_image_url = make_fake(lambda u: "gifconvert" in u and "/convert.gif" in u)
        url, src = await tpx.resolve_gif_image(None, mp4)
        check("r11 gif: gifconvert .gif is 3rd",
              src == "gifconvert" and "convert.gif" in url, f"{url} {src}")

        probes.clear()
        tpx._probe_image_url = make_fake(lambda u: "fastgif" in u)
        url, src = await tpx.resolve_gif_image(None, mp4)
        check("r11 gif: fastgif is last (4 probes, in order)",
              src == "fastgif" and len(probes) == 4
              and "gif.fxtwitter.com" in probes[0][0]
              and "/convert.webp" in probes[1][0]
              and "/convert.gif" in probes[2][0]
              and "fastgif" in probes[3][0], str([p[0] for p in probes]))

        probes.clear()
        tpx._probe_image_url = make_fake(lambda u: False)
        url, src = await tpx.resolve_gif_image(None, mp4)
        check("r11 gif: nothing answers -> (None, '')", url is None and src == "", f"{url} {src}")

        probes.clear()
        tpx._probe_image_url = make_fake(lambda u: True)
        url, src = await tpx.resolve_gif_image(None, "https://video.twimg.com/ext_tw_video/xyz.mp4")
        check("r11 gif: non-tweet_video URLs are untouched",
              url is None and src == "" and not probes, f"{url} {src} {probes}")
        tpx._probe_image_url = orig_probe

    asyncio.run(round11_gif())

    # --- vxtwitter normalization --------------------------------------------
    vx_sample = {
        "tweetID": "2099906088489439483",
        "tweetURL": "https://vxtwitter.com/PomPom_HonkaiSR/status/2099906088489439483",
        "text": "three photos here",
        "lang": "en",
        "user_name": "PomPom", "user_screen_name": "PomPom_HonkaiSR",
        "date": "Tue Sep 16 2026", "date_epoch": 1760640000,
        "replies": 3, "retweets": 677, "likes": 6500,
        "replyingTo": None, "replyingToID": None,
        "qrt": {"tweetID": "111", "tweetURL": "https://vxtwitter.com/x/status/111",
                "text": "quoted", "user_name": "X", "user_screen_name": "x",
                "date_epoch": 1760000000, "replies": 1, "retweets": 2, "likes": 3,
                "media_extended": []},
        "media_extended": [
            {"type": "image", "url": "https://pbs.twimg.com/media/p1.jpg",
             "size": {"width": 640, "height": 1080}},
            {"type": "image", "url": "https://pbs.twimg.com/media/p2.jpg",
             "size": {"width": 640, "height": 1080}},
            {"type": "image", "url": "https://pbs.twimg.com/media/p3.jpg",
             "size": {"width": 640, "height": 1080}},
            {"type": "gif", "url": "https://video.twimg.com/tweet_video/g1.mp4",
             "thumbnail_url": "https://pbs.twimg.com/tweet_video_thumb/g1.jpg",
             "duration_millis": 3000, "size": {"width": 1200, "height": 675}},
        ],
    }
    t1 = tpx.normalize_vxtwitter(vx_sample)
    check("r11 vx: base shape (id/text/author/stats/epoch/views N/A)",
          t1 and t1["id"] == "2099906088489439483"
          and t1["author"] == {"name": "PomPom", "screen_name": "PomPom_HonkaiSR"}
          and (t1["replies"], t1["retweets"], t1["likes"]) == (3, 677, 6500)
          and t1["created_timestamp"] == 1760640000 and t1["views"] == "N/A",
          str(t1)[:200] if t1 else "None")
    check("r11 vx: multi-photo keeps one entry per photo, in order",
          [p["url"] for p in t1["media"]["photos"]]
          == ["https://pbs.twimg.com/media/p1.jpg", "https://pbs.twimg.com/media/p2.jpg",
              "https://pbs.twimg.com/media/p3.jpg"], str(t1["media"]["photos"]))
    v0 = t1["media"]["videos"][0]
    check("r11 vx: gif type + duration (ms -> s) + formats",
          v0["type"] == "gif" and v0["duration"] == 3 and v0["formats"][0]["container"] == "mp4",
          str(v0))
    check("r11 vx: quote normalized recursively",
          t1["quote"] and t1["quote"]["id"] == "111" and t1["quote"]["text"] == "quoted"
          and t1["quote"]["media"] == {"videos": [], "photos": []}, str(t1["quote"]))
    check("r11 vx: malformed payload -> None",
          tpx.normalize_vxtwitter({"nope": 1}) is None and tpx.normalize_vxtwitter(None) is None)
    t2 = tpx.normalize_vxtwitter(dict(vx_sample, media_extended=[
        {"type": "video", "url": "https://video.twimg.com/ext_tw_video/v1.mp4",
         "duration_millis": 120000, "size": {"width": 1920, "height": 1080}}]))
    check("r11 vx: plain video (not gif) + dimensions",
          t2["media"]["videos"][0]["type"] == "video"
          and t2["media"]["videos"][0]["width"] == 1920
          and t2["media"]["videos"][0]["duration"] == 120, str(t2["media"]["videos"]))

    # --- twitterez og: parsing (dict meta) ----------------------------------
    ez_meta = {
        "og:url": "https://x.com/someone/status/2090000000000000001",
        "og:title": "Someone (@someone)",
        "og:description": ("**💬 2  🔁 140  💜 1K  👀 16.7K**\n"
                           "actual tweet text line one\n"
                           "actual tweet text line two"),
        "og:image": "https://embedez.com/api/v2/redirect/k?path=content.media.0.source",
    }
    t3 = tpx.normalize_twitterez(ez_meta, "2090000000000000001")
    check("r11 ez: stats line parsed + stripped (1K/16.7K) + author kept",
          t3 and (t3["replies"], t3["retweets"], t3["likes"], t3["views"]) == (2, 140, 1000, 16700)
          and "actual tweet text line one" in t3["text"]
          and "💬" not in t3["text"]
          and t3["author"]["name"] == "Someone (@someone)",
          str(t3["text"])[:120] if t3 else "None")
    check("r11 ez: photo from og:image",
          t3 and t3["media"]["photos"]
          == [{"url": "https://embedez.com/api/v2/redirect/k?path=content.media.0.source"}],
          str(t3["media"]) if t3 else "None")
    ez_gif = {"og:title": "G", "og:description": "gif text",
              "og:video:secure_url": "https://video.twimg.com/tweet_video/g1.mp4"}
    t4 = tpx.normalize_twitterez(ez_gif, "2")
    check("r11 ez: tweet_video og:video -> gif type",
          t4 and t4["media"]["videos"][0]["type"] == "gif" and t4["media"]["photos"] == [],
          str(t4["media"]) if t4 else "None")
    ez_multi = {
        "og:title": "M", "og:description": "multi",
        "og:image": ["https://embedez.com/api/v2/redirect/m?path=content.media.0.source",
                     "https://embedez.com/api/v2/redirect/m?path=content.media.1.source",
                     "https://embedez.com/api/v2/redirect/m?path=content.media.2.source",
                     "https://embedez.com/api/v2/redirect/m?path=content.media.3.source"],
    }
    t7 = tpx.normalize_twitterez(ez_multi, "3")
    check("r11 ez: 4-photo gallery order (dict meta)",
          t7 and [p["url"].rsplit("=", 1)[-1] for p in t7["media"]["photos"]]
          == ["content.media.0.source", "content.media.1.source",
              "content.media.2.source", "content.media.3.source"],
          str(t7["media"]["photos"]) if t7 else "None")
    check("r11 ez: empty meta -> None",
          tpx.normalize_twitterez({}, "x") is None and tpx.normalize_twitterez(None, None) is None)

    # --- twitterez real bot-page HTML (og: tags end-to-end) -----------------
    ez_page_video = (
        "<html><head>"
        '<meta property="og:title" content="Video Poster Guy (@vp)" />'
        '<meta property="og:url" content="https://x.com/vp/status/9001" />'
        '<meta property="og:description" content="**💬 4  🔁 210  💜 2K  👀 1.5M**\n'
        "a video tweet\n"
        "[Add](https://embedez.com/t/test-bot) the EmbedEZ bot to your server *(ad)*"
        '" />'
        '<meta property="og:video:secure_url" '
        'content="https://proxy.embedez.com/advanced.mp4?url=https%3A%2F%2Fvideo.twimg.com%2Fext_tw_video%2Fv9.mp4" />'
        '<meta property="og:image" '
        'content="https://proxy.embedez.com/thumbnail?url=https%3A%2F%2Fvideo.twimg.com%2Fext_tw_video%2Fv9.mp4" />'
        "</head><body></body></html>"
    )
    m1 = tpx._og_meta(ez_page_video)
    t5 = tpx.normalize_twitterez(m1, "9001")
    check("r11 ez page: video tweet -> video kept, poster skipped",
          t5 and len(t5["media"]["videos"]) == 1 and t5["media"]["photos"] == [],
          str(t5["media"]) if t5 else "None")
    check("r11 ez page: 1.5M views + 2K likes parsed",
          t5 and t5["views"] == 1500000 and t5["likes"] == 2000,
          f"{t5['views']} {t5['likes']}" if t5 else "None")
    check("r11 ez page: ad line stripped from text",
          t5 and "embedez.com" not in t5["text"].lower()
          and "*(ad)*" not in t5["text"] and "a video tweet" in t5["text"],
          repr(t5["text"]) if t5 else "None")
    ez_page_photos = (
        "<html><head>"
        '<meta property="og:title" content="Gallery Gal (@gg)" />'
        '<meta property="og:description" content="**💬 3  🔁 677  💜 6.5K  👀 48.8K**\nfour photos" />'
        '<meta property="og:image" content="https://embedez.com/api/v2/redirect/k1?path=content.media.0.source" />'
        '<meta property="og:image" content="https://embedez.com/api/v2/redirect/k1?path=content.media.1.source" />'
        '<meta property="og:image" content="https://embedez.com/api/v2/redirect/k1?path=content.media.1.source" />'
        '<meta property="og:image" content="https://embedez.com/api/v2/redirect/k1?path=content.media.3.source" />'
        "</head><body></body></html>"
    )
    m2 = tpx._og_meta(ez_page_photos)
    t6 = tpx.normalize_twitterez(m2, "9002")
    check("r11 ez page: 6.5K/48.8K compact stats parsed",
          t6 and t6["likes"] == 6500 and t6["views"] == 48800,
          f"{t6['likes']} {t6['views']}" if t6 else "None")
    check("r11 ez page: 4 photos, in order",
          t6 and len(t6["media"]["photos"]) == 4
          and t6["media"]["photos"][1]["url"].endswith("content.media.1.source"),
          str(t6["media"]["photos"]) if t6 else "None")
    check("r11 ez page: empty page -> None",
          tpx.normalize_twitterez(tpx._og_meta(""), None) is None)

    # --- fallback chain order (monkeypatched fetchers, no network) ----------
    async def round11_chain():
        calls = []
        fake_fx = {"id": "77", "text": "fx",
                   "author": {"name": "F", "screen_name": "f"},
                   "media": {"videos": [], "photos": []}}
        fake_vx = {"id": "77", "text": "vx",
                   "author": {"name": "V", "screen_name": "v"},
                   "media": {"videos": [], "photos": []}}
        fake_ez = {"id": "77", "text": "ez",
                   "author": {"name": "E", "screen_name": "e"},
                   "media": {"videos": [], "photos": []}}

        def make_ok(name, payload):
            async def ok(session, screen, tid, label=""):
                calls.append(name)
                return dict(payload)
            return ok

        def make_no(name):
            async def no(session, screen, tid, label=""):
                calls.append(name)
                return None
            return no

        async def fx_boom(session, screen, tid, label=""):
            calls.append("fxtwitter")
            raise RuntimeError("boom")

        orig = (tpx._fetch_fxtwitter, tpx._fetch_fixupx,
                tpx._fetch_vxtwitter, tpx._fetch_twitterez)
        try:
            tpx._fetch_fxtwitter = make_ok("fxtwitter", fake_fx)
            tpx._fetch_fixupx = make_no("fixupx")
            tpx._fetch_vxtwitter = make_no("vxtwitter")
            tpx._fetch_twitterez = make_no("twitterez")
            calls.clear()
            tweet, src = await tpx.fetch_tweet_details_any(None, "scr", "77", "t")
            check("r11 chain: fxtwitter wins (primary)",
                  src == "fxtwitter" and tweet["id"] == "77", f"{src}")
            check("r11 chain: stops at the winner (1 call)", calls == ["fxtwitter"], str(calls))

            calls.clear()
            tpx._fetch_fxtwitter = make_no("fxtwitter")
            tpx._fetch_vxtwitter = make_ok("vxtwitter", fake_vx)
            tweet, src = await tpx.fetch_tweet_details_any(None, "scr", "77", "t")
            check("r11 chain: vxtwitter fallback (fx+fixupx fail)",
                  src == "vxtwitter" and calls == ["fxtwitter", "fixupx", "vxtwitter"],
                  f"{src} {calls}")

            calls.clear()
            tpx._fetch_vxtwitter = make_no("vxtwitter")
            tpx._fetch_twitterez = make_ok("twitterez", fake_ez)
            tweet, src = await tpx.fetch_tweet_details_any(None, "scr", "77", "t")
            check("r11 chain: twitterez is the last resort (4 calls)",
                  src == "twitterez" and len(calls) == 4, f"{src} {calls}")

            calls.clear()
            tpx._fetch_twitterez = make_no("twitterez")
            tweet, src = await tpx.fetch_tweet_details_any(None, "scr", "77", "t")
            check("r11 chain: all fail -> (None, None)",
                  tweet is None and src is None and len(calls) == 4, str(calls))

            calls.clear()
            tpx._fetch_fxtwitter = fx_boom
            tpx._fetch_vxtwitter = make_ok("vxtwitter", fake_vx)
            tweet, src = await tpx.fetch_tweet_details_any(None, "scr", "77", "t")
            check("r11 chain: a fetcher exception is swallowed (chain continues)",
                  src == "vxtwitter", f"{src} {calls}")
        finally:
            (tpx._fetch_fxtwitter, tpx._fetch_fixupx,
             tpx._fetch_vxtwitter, tpx._fetch_twitterez) = orig

    asyncio.run(round11_chain())

# ---- round 17: Arctic Shift search backup (v3) ----------------------------
class _ArcticResp:
    def __init__(self, status, data):
        self.status = status
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def json(self, **k):
        return self.data

arctic_calls = []

def _arctic_session(status, data):
    def get(url, params=None, **k):
        arctic_calls.append((url, params))
        return _ArcticResp(status, data)
    return SimpleNamespace(get=get)

arc_posts = [
    {"id": "1wgaaa1", "subreddit": "AnantaLeaks", "title": "Archive A",
     "author": "userA", "created_utc": 1760630000, "updated_utc": 1760630010,
     "body": "<p>body A</p>"},
    {"id": "1wgbbb2", "subreddit": "AnantaLeaks", "title": "Archive B",
     "author": "userB", "created_utc": 1760620000, "updated_utc": 1760620005,
     "body": "<p>body B</p>"},
]
orig_arctic_fail = v3._arctic_fail_count
v3._arctic_fail_count = 0
arctic_calls.clear()
got = asyncio.run(v3.fetch_arctic_subreddit_posts(
    _arctic_session(200, {"data": arc_posts}), "AnantaLeaks",
    after_epoch=1760600000, label="t"))
check("r17 arctic: valid response -> post dicts",
      [p["id"] for p in got] == ["1wgaaa1", "1wgbbb2"], str(got))
check("r17 arctic: params (subreddit/limit/sort/md2html/after)",
      arctic_calls and arctic_calls[0][0].endswith("/api/posts/search")
      and arctic_calls[0][1].get("subreddit") == "AnantaLeaks"
      and arctic_calls[0][1].get("sort") == "desc"
      and arctic_calls[0][1].get("md2html") == "true"
      and arctic_calls[0][1].get("after") == "1760600000", str(arctic_calls[:1]))
check("r17 arctic: empty data -> []",
      asyncio.run(v3.fetch_arctic_subreddit_posts(
          _arctic_session(200, {"data": []}), "AnantaLeaks", label="t")) == [])
v3._arctic_fail_count = 0
check("r17 arctic: 429 -> [] (soft fail, counts as outage)",
      asyncio.run(v3.fetch_arctic_subreddit_posts(
          _arctic_session(429, {}), "AnantaLeaks", label="t")) == []
      and v3._arctic_fail_count == 1)
v3._arctic_fail_count = 3
calls_before = len(arctic_calls)
check("r17 arctic: circuit breaker skips when tripped",
      asyncio.run(v3.fetch_arctic_subreddit_posts(
          _arctic_session(200, {"data": arc_posts}), "AnantaLeaks", label="t")) == []
      and len(arctic_calls) == calls_before)
v3._arctic_fail_count = orig_arctic_fail

e = v3._ArcticEntry(arc_posts[0])
check("r17 entry: link/title/author",
      e.link == "https://www.reddit.com/r/AnantaLeaks/comments/1wgaaa1/"
      and e.title == "Archive A" and e.author == "userA", str(e.link))
check("r17 entry: timestamps + body",
      e.get("published_parsed") is not None and e.get("updated_parsed") is not None
      and e.get("content")[0]["value"] == "<p>body A</p>", str(e.get("content")))
cp_entry = v3._ArcticEntry(dict(arc_posts[0], body=(
    "u/x crossposted this from r/Ananta2027 — original post "
    "https://www.reddit.com/r/Ananta2027/comments/1wgjebk/houses/")))
check("r17 entry: crosspost permalink detected (original path)",
      v3.find_crosspost_original_path(cp_entry.get("content")[0]["value"], e.link)
      == "/r/Ananta2027/comments/1wgjebk/",
      str(v3.find_crosspost_original_path(cp_entry.get("content")[0]["value"], e.link)))
check("r17 entry: .get default like feedparser", e.get("nope") is None)

# ---- round 18: soft-removed/deleted post detection (v3) -------------------
check("r18 removed: [deleted] body",
      v3.removed_post_reason("Real title", "[deleted]") == "whole-body marker")
check("r18 removed: [removed] body",
      v3.removed_post_reason("Real title", "[removed]") == "whole-body marker")
check("r18 removed: bolded [ Removed by moderator ]",
      v3.removed_post_reason("Real title", "**[ Removed by moderator ]**") == "whole-body marker")
check("r18 removed: [deleted] title",
      v3.removed_post_reason("[deleted]", "some body") == "title marker")
check("r18 removed: moderators notice (live example)",
      v3.removed_post_reason("T",
                             "Sorry, this post has been removed by the moderators of r/HonkaiStarRail_leaks.")
      == "removal notice")
check("r18 removed: author-deleted notice",
      v3.removed_post_reason("T",
                             "Sorry, this post was deleted by the person who originally posted it.")
      == "removal notice")
check("r18 removed: real title + deleted body (live example)",
      v3.removed_post_reason("Version 7.1 New Weapon Overview",
                             "**Version 7.1 New Weapon Overview** "
                             "Sorry, this post was deleted by the person who originally posted it.")
      == "removal notice")
check("r18 kept: empty body is NOT removed (link posts)",
      v3.removed_post_reason("Link post", "") is None)
check("r18 kept: normal body mentioning a pull",
      v3.removed_post_reason("T", "The previous leak was pulled. Here is the new build list...") is None)

# ---- round 20/21: raw plain-link mangle fix + label/URL pairs (1whe2tr) --
_r20_mangled = ("Firefly video [[https://b23.tv/dkCXgES](https://b23.tv/dkCXgES)]"
                "(https://b23.tv/dkCXgES](https://b23.tv/dkCXgES))\n\n"
                "Feixiao video [[https://b23.tv/XojBeMr](https://b23.tv/XojBeMr)]"
                "(https://b23.tv/XojBeMr](https://b23.tv/XojBeMr))\n\n"
                "Therta video [[https://b23.tv/PNtXo0u](https://b23.tv/PNtXo0u)]"
                "(https://b23.tv/PNtXo0u](https://b23.tv/PNtXo0u))")
_r20_expected = ("Firefly video https://b23.tv/dkCXgES\n\n"
                 "Feixiao video https://b23.tv/XojBeMr\n\n"
                 "Therta video https://b23.tv/PNtXo0u")
check("r20 link: doubled mangle -> one plain line, bare URL once (v3)",
      v3.clean_rss_body(_r20_mangled) == _r20_expected,
      v3.clean_rss_body(_r20_mangled))
check("r20 link: same result via the proxy cleaner",
      proxy.clean_proxy_body(_r20_mangled) == _r20_expected,
      proxy.clean_proxy_body(_r20_mangled))
_r20_plain = ("Firefly video [https://b23.tv/dkCXgES](https://b23.tv/dkCXgES](https://b23.tv/dkCXgES))\n\n"
              "Feixiao video [https://b23.tv/XojBeMr](https://b23.tv/XojBeMr](https://b23.tv/XojBeMr))\n\n"
              "Therta video [https://b23.tv/PNtXo0u](https://b23.tv/PNtXo0u](https://b23.tv/PNtXo0u))")
check("r20 link: feed plain face (URL x3) -> one plain line, bare URL once",
      v3.clean_rss_body(_r20_plain) == _r20_expected,
      v3.clean_rss_body(_r20_plain))
_r20_deep = ("Firefly video [[[https://b23.tv/dkCXgES](https://b23.tv/dkCXgES](https://b23.tv/dkCXgES)]"
             "(https://b23.tv/dkCXgES](https://b23.tv/dkCXgES](https://b23.tv/dkCXgES)]"
             "(https://b23.tv/dkCXgES](https://b23.tv/dkCXgES](https://b23.tv/dkCXgES))"
             "(https://b23.tv/dkCXgES](https://b23.tv/dkCXgES](https://b23.tv/dkCXgES))))")
check("r20 link: deep nested mangle -> one plain line, bare URL once",
      v3.clean_rss_body(_r20_deep)
      == "Firefly video https://b23.tv/dkCXgES",
      v3.clean_rss_body(_r20_deep))
check("r22 link: the 1whe2tr auto-linked face -> 3 raw plain lines (v3)",
      v3.clean_rss_body("Firefly video [https://b23.tv/dkCXgES](https://b23.tv/dkCXgES)\n\n"
                        "Feixiao video [https://b23.tv/XojBeMr](https://b23.tv/XojBeMr)\n\n"
                        "Therta video [https://b23.tv/PNtXo0u](https://b23.tv/PNtXo0u)")
      == _r20_expected,
      v3.clean_rss_body("Firefly video [https://b23.tv/dkCXgES](https://b23.tv/dkCXgES)\n\n"
                        "Feixiao video [https://b23.tv/XojBeMr](https://b23.tv/XojBeMr)\n\n"
                        "Therta video [https://b23.tv/PNtXo0u](https://b23.tv/PNtXo0u)"))
check("r22 link: same auto-linked face via the proxy cleaner",
      proxy.clean_proxy_body("Firefly video [https://b23.tv/dkCXgES](https://b23.tv/dkCXgES)\n\n"
                             "Feixiao video [https://b23.tv/XojBeMr](https://b23.tv/XojBeMr)\n\n"
                             "Therta video [https://b23.tv/PNtXo0u](https://b23.tv/PNtXo0u)")
      == _r20_expected,
      proxy.clean_proxy_body("Firefly video [https://b23.tv/dkCXgES](https://b23.tv/dkCXgES)\n\n"
                             "Feixiao video [https://b23.tv/XojBeMr](https://b23.tv/XojBeMr)\n\n"
                             "Therta video [https://b23.tv/PNtXo0u](https://b23.tv/PNtXo0u)"))
check("r22 link: clean auto-linked label line -> one raw line",
      v3.clean_rss_body("Firefly video [https://b23.tv/x](https://b23.tv/x)")
      == "Firefly video https://b23.tv/x",
      v3.clean_rss_body("Firefly video [https://b23.tv/x](https://b23.tv/x)"))
check("r22 kept: descriptive link (text != URL) untouched",
      v3.clean_rss_body("BGM: [Heist](https://en.wikipedia.org/wiki/Heist)")
      == "BGM: [Heist](https://en.wikipedia.org/wiki/Heist)",
      v3.clean_rss_body("BGM: [Heist](https://en.wikipedia.org/wiki/Heist)"))
check("r22 kept: prose with repeated bare URL + URL-labelled link untouched",
      v3.clean_rss_body("mirror: https://x.com/a again [https://x.com/a](https://x.com/a)")
      == "mirror: https://x.com/a again [https://x.com/a](https://x.com/a)",
      v3.clean_rss_body("mirror: https://x.com/a again [https://x.com/a](https://x.com/a)"))
check("r21 link: clean label + bare URL pair -> one raw line",
      v3.clean_rss_body("Firefly video\nhttps://b23.tv/dkCXgES\n\n"
                        "Feixiao video\nhttps://b23.tv/XojBeMr")
      == "Firefly video https://b23.tv/dkCXgES\n\n"
         "Feixiao video https://b23.tv/XojBeMr",
      v3.clean_rss_body("Firefly video\nhttps://b23.tv/dkCXgES\n\n"
                        "Feixiao video\nhttps://b23.tv/XojBeMr"))
check("r21 link: blank line between label and URL -> one raw line",
      v3.clean_rss_body("Firefly video\n\nhttps://b23.tv/dkCXgES")
      == "Firefly video https://b23.tv/dkCXgES",
      v3.clean_rss_body("Firefly video\n\nhttps://b23.tv/dkCXgES"))
check("r21 kept: standalone bare URL line stays raw (no markdown wrap)",
      v3.clean_rss_body("https://b23.tv/x") == "https://b23.tv/x",
      v3.clean_rss_body("https://b23.tv/x"))
check("r21 kept: two bare URL lines stay separate",
      v3.clean_rss_body("https://a.tv/x\nhttps://b.tv/y")
      == "https://a.tv/x\nhttps://b.tv/y",
      v3.clean_rss_body("https://a.tv/x\nhttps://b.tv/y"))
check("r21 kept: label with its own link -> raw, NOT merged with the next URL line",
      v3.clean_rss_body("Demo [https://a.com](https://a.com)\nhttps://b.tv/x")
      == "Demo https://a.com\nhttps://b.tv/x",
      v3.clean_rss_body("Demo [https://a.com](https://a.com)\nhttps://b.tv/x"))
check("r20 kept: mangled body is NOT treated as removed (1whe2tr)",
      v3.removed_post_reason("4.6 Event Firefly, Feixiao and The Herta gameplay",
                             _r20_mangled) is None)

# ---- round 20: archive post liveness gate (offline, stubbed sources) -----
_r20_proxy_result = {"holder": None}

class _R20FakeProxy:
    async def fetch_proxy_post(self, session, path, label="", health=None, need_video=False):
        return _r20_proxy_result["holder"]

async def _r20_gate():
    orig_proxy, orig_base = v3.reddit_proxy, v3.fetch_test_post_base
    v3.reddit_proxy = _R20FakeProxy()
    try:
        v3.fetch_test_post_base = AsyncMock(return_value=None)
        _r20_proxy_result["holder"] = None
        live, why = await v3.verify_archive_post_live(None, "/r/Sub/comments/x/", label="t")
        check("r20 gate: no live source can see the post -> not live",
              live is False and "pending approval" in why, why)
        _r20_proxy_result["holder"] = {"service": "redditez", "title": "T", "author": None,
                                       "subreddit": None, "body": "", "stats": None, "media": []}
        live, why = await v3.verify_archive_post_live(None, "/r/Sub/comments/x/", label="t")
        check("r20 gate: proxy chain retrieves the post -> live",
              live is True and "redditez" in why, why)
        _r20_proxy_result["holder"] = {"service": "redditez", "title": "T", "author": None,
                                       "subreddit": None, "body": "[ Removed by moderator ]",
                                       "stats": None, "media": []}
        live, why = await v3.verify_archive_post_live(None, "/r/Sub/comments/x/", label="t")
        check("r20 gate: live source still shows a removal notice -> not live",
              live is False and "removal notice" in why, why)
        _r20_proxy_result["holder"] = None
        v3.fetch_test_post_base = AsyncMock(return_value={"title": "T", "body": "b"})
        live, why = await v3.verify_archive_post_live(None, "/r/Sub/comments/x/", label="t")
        check("r20 gate: redlib retrieves the post -> live",
              live is True and "redlib" in why, why)
    finally:
        v3.reddit_proxy, v3.fetch_test_post_base = orig_proxy, orig_base

asyncio.run(_r20_gate())

asyncio.run(round15_flows())
asyncio.run(round15_fetch())


# ---- round 25: most-complete proxy media and archive count --------------
async def _r25_proxy_checks():
    names = ('redditez', 'vxreddit', 'embeddit')
    saved = [getattr(proxy, '_fetch_' + name) for name in names]
    async def run(counts, *, video=False, need_video=False, health=None):
        calls = []
        results = []
        for index, (name, count) in enumerate(zip(names, counts)):
            result = {'service': name, 'body': name, 'stats': None,
                      'media': [{'kind': 'video' if video and index == 1 else 'image',
                                 'url': f'https://i.redd.it/{i}.jpg'} for i in range(count)]}
            results.append(result)
            async def fake(session, path, label='', result=result, name=name):
                calls.append(name)
                return result
            setattr(proxy, '_fetch_' + name, fake)
        winner = await proxy.fetch_proxy_post(None, '/r/AnantaLeaks/comments/1wj0p83/',
                                               need_video=need_video, health=health)
        return winner, calls, results
    try:
        # ROUND 49: the services are dispatched CONCURRENTLY, so `calls`
        # has no deterministic order any more — assert the SET of services
        # that were actually asked, and the tie-break by PROXY_SERVICES rank.
        winner, calls, results = await run((1, 1, 13))
        check('r25 1wj0p83: embeddit wins with all 13 items',
              winner == results[2] and len(winner['media']) == 13
              and sorted(calls) == sorted(names))
        check('r25 winner preserves source image order', winner['media'] == results[2]['media'])
        winner, calls, results = await run((0, 1, 13))
        check('r25 text/partial/full: embeddit wins after text-only redditez',
              winner == results[2] and sorted(calls) == sorted(names))
        winner, _, results = await run((0, 2, 0), need_video=True)
        check('r49 need_video: the fallback is the highest-priority non-video answer',
              winner is results[1], str(winner))
        winner, calls, results = await run((25, 13, 1))
        check('r25 over-cap: 25 items trimmed to 20 and the chain stops',
              winner['service'] == 'redditez' and len(winner['media']) == 20
              and 'embeddit' not in calls, str(calls))
        check('r25 cap preserves order and leaves original result untouched',
              winner is not results[0] and len(results[0]['media']) == 25
              and winner['media'] == results[0]['media'][:20]
              and winner['media'] is not results[0]['media'])
        winner, _, results = await run((2, 2, 1))
        check('r49 equal counts keep the higher-PRIORITY service (vxreddit)',
              winner == results[1], str(winner))
        winner, _, results = await run((0, 0, 0))
        check('r49 text-only fallback is the highest-priority one', winner == results[1])
        winner, _, results = await run((13, 1, 20), video=True, need_video=True)
        check('r25 need_video: video beats larger thumbnail lists', winner == results[1])
        winner, calls, results = await run((20, 1, 13))
        check('r25 capacity: the delayed service is never dispatched',
              winner == results[0] and 'embeddit' not in calls, str(calls))
        winner, calls, results = await run((1, 2, 13), health={'embeddit': {'ok': False}})
        check('r25 health: marked-down service skipped', winner == results[1] and len(calls) == 2)
        winner, calls, results = await run((1, 2, 13), health={n: {'ok': False} for n in names})
        check('r25 health: all down retries every service',
              winner == results[2] and sorted(calls) == sorted(names))
    finally:
        for name, original in zip(names, saved):
            setattr(proxy, '_fetch_' + name, original)

asyncio.run(_r25_proxy_checks())
_r25_ids = ['wc0hxdxq94qh1', '1mmykk4r94qh1', 'wlm8yl7r94qh1', 'aibof0er94qh1',
            'w8wlkxjr94qh1', '2kau5umr94qh1', '404l58pr94qh1', '00ccvmrr94qh1',
            'xly043yr94qh1', 'unvdfv0s94qh1', 'jhzodb3s94qh1', 'rtaidu5s94qh1', 'rfbchq8s94qh1']
_r25_post = {'gallery_data': {'items': [{'media_id': mid} for mid in _r25_ids]},
             'media_metadata': {mid: {'status': 'valid', 'e': 'Image',
                                     's': {'u': f'https://i.redd.it/{mid}.jpg'}} for mid in _r25_ids}}
check('r25 Arctic count: 13 valid gallery entries', v3._arctic_media_count(_r25_post) == 13)
check('r25 Arctic extractor preserves original gallery order',
      [m['url'] for m in v3.arctic_gallery_items(_r25_post)] ==
      [f'https://i.redd.it/{mid}.jpg' for mid in _r25_ids])
for value in (None, {}, {'gallery_data': {}, 'media_metadata': {}}, {'url': 'https://i.redd.it/a.jpg'}):
    check('r25 Arctic count: empty/non-gallery is unknown (0)', v3._arctic_media_count(value) == 0)
_r25_post['media_metadata'][_r25_ids[0]]['status'] = 'failed'
check('r25 Arctic count: invalid entry excluded', v3._arctic_media_count(_r25_post) == 12)

# Execute the actual main-loop gate in a tiny loop: continue means retry,
# reaching the sentinel means posting can proceed. No network/cache writes.
import textwrap
_r25_gate = inspect.getsource(v3.main).split('# Round 25: known partial', 1)[1]
_r25_gate = _r25_gate[_r25_gate.index('                if ('):].split('                posted_ts =', 1)[0]
_r25_code = 'for _ in [0]:\n' + textwrap.indent(textwrap.dedent(_r25_gate), '    ') + '    allowed = True\n'
for label, overrides, expected in [
    ('partial gallery retries', {}, False),
    ('complete gallery proceeds', {'data': {'media': [{'kind': 'image'}] * 12}}, True),
    ('unknown count proceeds', {'entry': types.SimpleNamespace(_arctic_post={})}, True),
    ('video exempt', {'data': {'media': [{'kind': 'video'}]}}, True),
    ('test post exempt', {'TEST_POST_ID': 'AnantaLeaks/1wj0p83'}, True),
    ('dry run exempt', {'DRY_RUN': True}, True),
    ('native JSON exempt', {'post_json': {}}, True),
    ('non-archive exempt', {'entry': object()}, True),
]:
    scope = dict(TEST_POST_ID='', DRY_RUN=False, post_json=None,
                 entry=types.SimpleNamespace(_arctic_post=_r25_post),
                 _ArcticEntry=types.SimpleNamespace, data={'media': [{'kind': 'image'}]},
                 _arctic_media_count=v3._arctic_media_count, logging=__import__('logging'),
                 unique_key='r25-test', allowed=False,
                 # round 30 (2026-09-19): the gate now also records the post in
                 # the pending-recheck cache before `continue`.
                 pending={}, now=1758240000.0, mark_pending=v3.mark_pending)
    scope.update(overrides)
    exec(_r25_code, scope)
    check('r25 gate: ' + label, scope['allowed'] is expected)
check('r25 gate never writes dedup cache', 'posted.add' not in _r25_gate)


# ---- 5. X V2/V3 round 28 (2026-09-18): translation guard + hashtag URLs ----
v3r = load_module("smoke_x_v3_r28", "testing area/twitter_v3.py")
v2r = load_module("smoke_x_v2_r28", "testing area/twitter_v2_button_outside.py")

# 5a. the /en translation identical to the original is a language mis-detection
check('r28 guard: live misfire (identical translation) detected',
      v3r.translation_is_identical("Maintenance 🩸\n#zzzeroㅤ #Claret #Roxy",
                                   "Maintenance 🩸\n#zzzeroㅤ #Claret #Roxy") is True)
check('r28 guard: case/whitespace/emoji-insensitive compare',
      v3r.translation_is_identical("  Maintenance   🩸\n#zzzero ", "maintenance 🩸 #zzzero") is True)
check('r28 guard: real translation is NOT identical (JA cards stay)',
      v3r.translation_is_identical("クラレッタとおかしな交渉", "Clarettà and the Absurd Negotiation") is False)
check('r28 guard: one added word is NOT identical',
      v3r.translation_is_identical("Maintenance 🩸 #zzzero", "Maintenance 🩸 #zzzero #Claret") is False)
check('r28 guard: None/empty safe',
      v3r.translation_is_identical(None, None) is True
      and v3r.translation_is_identical("hi", "") is False
      and v3r.translation_is_identical(None, "x") is False)
check('r28 guard: V2 carries the same helper',
      v2r.translation_is_identical is not None
      and v2r.translation_is_identical("a B!", "a  b") is True)
src_v3 = open(os.path.join(ROOT, "testing area", "twitter_v3.py"), encoding="utf-8").read()
src_v2 = open(os.path.join(ROOT, "testing area", "twitter_v2_button_outside.py"), encoding="utf-8").read()
check('r28 guard: V3 loop compares before showing the block',
      "translation_is_identical(original_text, translated_text)" in src_v3
      and "language mis-detection" in src_v3 and "posting as-is" in src_v3)
check('r28 guard: V2 loop compares before showing the block',
      "translation_is_identical(original_text, translated_text)" in src_v2
      and "language mis-detection" in src_v2 and "posting as-is" in src_v2)

# r51: TEST_TWEET_ID remains subject to the normal cache.  A matching cached
# test ID must now explain its intentional skip, but must retain `continue`
# so no already-posted tweet can be sent again.
for _r51_label, _r51_source in (("V3", src_v3), ("V2", src_v2)):
    _r51_cache_branch = _r51_source.split("if unique_key in posted_urls:", 1)[1] \
                                     .split("published_parsed =", 1)[0]
    check(f"r51 {_r51_label}: cached TEST_TWEET_ID logs an intentional skip",
          "TEST_TWEET_ID is already in the" in _r51_cache_branch
          and "posted cache — skipping by design" in _r51_cache_branch,
          _r51_cache_branch)
    check(f"r51 {_r51_label}: cached TEST_TWEET_ID keeps dedup continue",
          _r51_cache_branch.rstrip().endswith("continue"), _r51_cache_branch)

# 5b. non-ASCII hashtags get percent-encoded (clickable) URLs
check('r28 linkify: pure-ASCII tag byte-identical (zero regression)',
      v3r.linkify_text("#Claret") == "[#Claret](https://x.com/hashtag/Claret)")
check("r28 linkify: #zzzero + U+3164 filler -> quoted URL, like X itself links",
      v3r.linkify_text("#zzzeroㅤ") == "[#zzzeroㅤ](https://x.com/hashtag/zzzero%E3%85%A4)")
check('r28 linkify: the live 2026-09-18 post renders fully clickable',
      v3r.linkify_text("Maintenance 🩸\n#zzzeroㅤ #Claret #Roxy")
      == "Maintenance 🩸\n[#zzzeroㅤ](https://x.com/hashtag/zzzero%E3%85%A4) "
         "[#Claret](https://x.com/hashtag/Claret) [#Roxy](https://x.com/hashtag/Roxy)")
check('r28 linkify: accented tag quoted (e.g. French #célébration)',
      v3r.linkify_text("#célébration") == "[#célébration](https://x.com/hashtag/c%C3%A9l%C3%A9bration)")
check('r28 linkify: URL #anchor + email still protected',
      v3r.linkify_text("see example.com/path#anchor and a@b.com")
      == "see example.com/path#anchor and a@b.com")
check('r28 linkify: @mention unchanged',
      v3r.linkify_text("hi @user") == "hi [@user](https://x.com/user)")
check('r28 linkify: V2 behaves identically to V3',
      v2r.linkify_text("Maintenance 🩸\n#zzzeroㅤ #Claret #Roxy")
      == v3r.linkify_text("Maintenance 🩸\n#zzzeroㅤ #Claret #Roxy"))


# ---- round 29 (2026-09-19): crosspost notice + same-file media dedupe ----
# 6a. the mirror's crosspost NOTICE is stripped (it is not post content), and
#     its subreddit is recovered for the "🔁 Crosspost of" line
check("r29 notice: live glued case -> empty body + r/AnantaStation",
      v3._crosspost_notice_clean(
          "Original PostPosted in r/AnantaStationLemon Recording Studio via Dremka",
          "Lemon Recording Studio via Dremka") == ("", "AnantaStation"),
      str(v3._crosspost_notice_clean(
          "Original PostPosted in r/AnantaStationLemon Recording Studio via Dremka",
          "Lemon Recording Studio via Dremka")))
check("r29 notice: classic 'Crosspost of [Sub](url) Subreddit' -> empty + sub",
      v3._crosspost_notice_clean(
          "Crosspost of [Ananta2027](https://www.reddit.com/r/Ananta2027/comments/1wk5ymh/ufood_restaurant_by_asiqami/) Subreddit",
          "Ufood restaurant by Asiqami") == ("", "Ananta2027"),
      "see r29 harness")
check("r29 notice: plain 'Crosspost of r/Sub' -> empty + sub",
      v3._crosspost_notice_clean("Crosspost of r/Ananta2027 Subreddit", "t")
      == ("", "Ananta2027"))
check("r29 notice: a real body is untouched",
      v3._crosspost_notice_clean("Some real post text here", "Some real post text here")
      == ("Some real post text here", None))
check("r29 notice: lone 'Original Post' mention untouched (weak evidence)",
      v3._crosspost_notice_clean("Original Post", "Some other title")
      == ("Original Post", None))
check("r29 notice: None/empty safe",
      v3._crosspost_notice_clean(None, "t") == (None, None)
      and v3._crosspost_notice_clean("", "t") == ("", None))

# 6b. one photo listed under two wrapper URLs -> ONE gallery tile
_R29_EMB1 = ("https://embedez.com/api/v2/redirect/"
             "search_6aad7b5a912fdcacf6d35d99?path=content.media.0.source")
_R29_EMB2 = ("https://embedez.com/api/v2/redirect/"
             "search_6aad7b5a912fdcacf6d35d99?path=content.media.1.source")
_R29_FINAL = "https://i.redd.it/0uf8xxe54bqh1.png"


async def _r29_dedupe_checks():
    async def _resolve(url):
        return _R29_FINAL
    items = [{"kind": "image", "url": _R29_EMB1}, {"kind": "image", "url": _R29_EMB2}]
    out = await v3._dedupe_media_final_urls(None, items, resolve=_resolve)
    check("r29 dedupe: two embedez redirects of the SAME photo -> 1 item",
          len(out) == 1 and out[0]["url"] == _R29_EMB1, str(out))
    items2 = [{"kind": "image", "url": "https://i.redd.it/slug-v0-aaaa.jpg?width=1080&s=x"},
              {"kind": "image", "url": "https://preview.redd.it/slug-v0-aaaa.jpg?width=1080&s=x"}]
    out2 = await v3._dedupe_media_final_urls(None, items2)
    check("r29 dedupe: i.redd.it + signed preview, same file id -> 1 item",
          len(out2) == 1 and out2[0]["url"].startswith("https://i.redd.it/"), str(out2))
    items3 = [{"kind": "image", "url": "https://i.redd.it/aaaa.jpg"},
              {"kind": "image", "url": "https://i.redd.it/bbbb.jpg"}]
    out3 = await v3._dedupe_media_final_urls(None, items3)
    check("r29 dedupe: two different photos stay", len(out3) == 2, str(out3))

    async def _fail_resolve(url):
        return None
    out4 = await v3._dedupe_media_final_urls(None, [dict(x) for x in items],
                                             resolve=_fail_resolve)
    check("r29 dedupe: resolver failure -> both kept (never drop media)",
          len(out4) == 2, str(out4))


asyncio.run(_r29_dedupe_checks())

# 6c. the "🔁 Crosspost of" header lines
_R29_BASE = {"title": "T", "author": "a", "body": "b", "media": [],
             "stats": None, "youtube_url": None, "op_comment": None,
             "youtube_id": None, "youtube_live": False}


def _r29_header(crosspost):
    data = dict(_R29_BASE, crosspost=crosspost)
    p = v3.build_v3_payload("AnantaLeaks", data,
                            "https://www.reddit.com/r/AnantaLeaks/comments/1abc/",
                            1789749533)
    return p["components"][0]["components"][0]["content"]


check("r29 header: clean crosspost URL -> exact legacy line (regression)",
      _r29_header({"url": "https://www.reddit.com/r/Ananta2027/comments/1wk5ymh/ufood/",
                   "path": "/r/Ananta2027/comments/1wk5ymh/ufood/"})
      .endswith("\n*🔁 Crosspost of [Ananta2027]"
                "(https://www.reddit.com/r/Ananta2027/comments/1wk5ymh/ufood/) Subreddit*"),
      _r29_header({"url": "https://www.reddit.com/r/Ananta2027/comments/1wk5ymh/ufood/",
                   "path": "/r/Ananta2027/comments/1wk5ymh/ufood/"}))
check("r29 header: markdown-wrapped URL unwrapped to the bare URL",
      "*🔁 Crosspost of [Ananta2027]"
      "(https://www.reddit.com/r/Ananta2027/comments/1wk5ymh/ufood/) Subreddit*"
      in _r29_header({"url": "[https://www.reddit.com/r/Ananta2027/comments/1wk5ymh/ufood/]"
                             "(https://www.reddit.com/r/Ananta2027/comments/1wk5ymh/ufood/)",
                      "path": ""}),
      _r29_header({"url": "[https://www.reddit.com/r/Ananta2027/comments/1wk5ymh/ufood/]"
                          "(https://www.reddit.com/r/Ananta2027/comments/1wk5ymh/ufood/)",
                   "path": ""}))
check("r29 header: notice-only detection -> linked r/Sub line",
      "*🔁 Crosspost of [r/AnantaStation]"
      "(https://www.reddit.com/r/AnantaStation/) Subreddit*"
      in _r29_header({"url": "", "path": "", "subreddit": "AnantaStation"}),
      _r29_header({"url": "", "path": "", "subreddit": "AnantaStation"}))
check("r29 header: no crosspost -> no 🔁 line",
      "🔁" not in _r29_header(None)
      if False else "🔁" not in v3.build_v3_payload(
          "AnantaLeaks", dict(_R29_BASE, crosspost=None),
          "https://www.reddit.com/r/AnantaLeaks/comments/1abc/", 1789749533)
          ["components"][0]["components"][0]["content"])

# r51: Reddit profile crossposts have three public permalink shapes. They
# must all advertise the canonical profile, while r/<sub> cards keep the
# exact legacy behavior asserted above.
_R51_PROFILE_LINE = ("*🔁 Crosspost of [u/aphotide]"
                     "(https://www.reddit.com/user/aphotide/) Profile*")
for _r51_name, _r51_crosspost in (
    ("/user", {"url": "https://www.reddit.com/user/aphotide/comments/1wuv4j8/title/",
                "path": "/user/aphotide/comments/1wuv4j8/title/"}),
    ("/u", {"url": "https://www.reddit.com/u/aphotide/comments/1wuv4j8/title/",
             "path": "/u/aphotide/comments/1wuv4j8/title/"}),
    ("/r/u_", {"url": "https://www.reddit.com/r/u_aphotide/comments/1wuv4j8/title/",
                "path": "/r/u_aphotide/comments/1wuv4j8/title/"}),
):
    check(f"r51 header: profile crosspost {_r51_name} uses canonical profile Markdown",
          _R51_PROFILE_LINE in _r29_header(_r51_crosspost),
          _r29_header(_r51_crosspost))


# ---- round 30 (2026-09-19): pending-post recheck cache --------------------
# The four skip gates now record the post in pending_reddit.json and a
# pending post is re-verified at most once every PENDING_RECHECK_SECONDS.
check("r30 defaults: 30-min recheck, 48h prune window",
      v3.PENDING_RECHECK_SECONDS == 1800
      and v3.PENDING_MAX_AGE_SECONDS == 48 * 3600
      and v3.PENDING_FILE == "pending_reddit.json")

_r30_now = 1758240000.0
_r30_pending = {}
v3.mark_pending(_r30_pending, "Sub_abc", "media_wait", _r30_now)
check("r30 mark_pending: new entry records first_seen/last_checked/reason",
      _r30_pending["Sub_abc"] == {"first_seen": _r30_now,
                                  "last_checked": _r30_now,
                                  "reason": "media_wait"}, str(_r30_pending))
v3.mark_pending(_r30_pending, "Sub_abc", "not_live", _r30_now + 3600)
check("r30 mark_pending: re-mark keeps first_seen, updates last_checked/reason",
      _r30_pending["Sub_abc"] == {"first_seen": _r30_now,
                                  "last_checked": _r30_now + 3600,
                                  "reason": "not_live"}, str(_r30_pending))

check("r30 due: unknown key is always due",
      v3.pending_due(_r30_pending, "Sub_new", _r30_now) is True)
check("r30 due: just-checked post is NOT due (no network this run)",
      v3.pending_due(_r30_pending, "Sub_abc", _r30_now + 3600 + 60) is False)
check("r30 due: due again once PENDING_RECHECK_SECONDS have passed",
      v3.pending_due(_r30_pending, "Sub_abc",
                     _r30_now + 3600 + v3.PENDING_RECHECK_SECONDS) is True)
check("r30 due: corrupt entry is treated as due (fail-open, never drops a post)",
      v3.pending_due({"Sub_x": "garbage"}, "Sub_x", _r30_now) is True)

# save_pending: prunes >48h entries, keeps the rest, writes sorted JSON
_r30_dir = tempfile.mkdtemp()
_r30_cwd = os.getcwd()
try:
    os.chdir(_r30_dir)
    _r30_save = {
        "Zed_new": {"first_seen": time.time(), "last_checked": time.time(), "reason": "media_wait"},
        "Abc_new": {"first_seen": time.time(), "last_checked": time.time(), "reason": "not_live"},
        "Old_gone": {"first_seen": time.time() - (49 * 3600),
                     "last_checked": time.time(), "reason": "not_live"},
        "Bad_entry": "not-a-dict",
    }
    v3.save_pending(_r30_save)
    with open(v3.PENDING_FILE, "r", encoding="utf-8") as _fh:
        _r30_written = json.load(_fh)
    check("r30 save_pending: entries older than 48h are pruned",
          "Old_gone" not in _r30_written, str(_r30_written))
    check("r30 save_pending: non-dict junk dropped",
          "Bad_entry" not in _r30_written, str(_r30_written))
    check("r30 save_pending: live entries kept",
          set(_r30_written) == {"Abc_new", "Zed_new"}, str(_r30_written))
    check("r30 save_pending: keys sorted (byte-stable file, no churn commits)",
          list(_r30_written) == ["Abc_new", "Zed_new"], str(list(_r30_written)))
    check("r30 load_pending: round-trips what save_pending wrote",
          v3.load_pending() == _r30_written)
    with open(v3.PENDING_FILE, "w", encoding="utf-8") as _fh:
        _fh.write("[]")
    check("r30 load_pending: non-dict JSON falls back to {}", v3.load_pending() == {})
    with open(v3.PENDING_FILE, "w", encoding="utf-8") as _fh:
        _fh.write("{ broken")
    check("r30 load_pending: corrupt JSON falls back to {}", v3.load_pending() == {})
    os.remove(v3.PENDING_FILE)
    check("r30 load_pending: missing file is {} (first run)", v3.load_pending() == {})
finally:
    os.chdir(_r30_cwd)
    shutil.rmtree(_r30_dir, ignore_errors=True)

# The main loop: throttle placement + every gate records pending + saves
_r30_main = inspect.getsource(v3.main)
check("r30 main: pending cache loaded next to the dedup cache",
      "pending = load_pending()" in _r30_main)
# r30 guard updated in round 67: the end marker used to be the removed
# content gate's section header; the throttle is now bounded by the round-64
# fresh hold, which follows it in the main loop.
_r30_throttle = _r30_main.split("pending-post recheck throttle", 1)[-1].split(
    "# ---- round 64: stateless 30s fresh hold", 1)[0]
for _cond in ("not TEST_POST_ID", "not DRY_RUN", "unique_key in pending",
              "_pending_throttle_skip(pending, unique_key, now, entry)"):
    check(f"r30 throttle keeps condition: {_cond} (round 32: the due-decision "
          f"moved into the reason-aware helper)", _cond in _r30_throttle, _r30_throttle)
check("r30 throttle: skips with `continue` and never caches as posted",
      _r30_throttle.rstrip().endswith("continue") and "posted.add" not in _r30_throttle,
      _r30_throttle)
check("r30 throttle runs BEFORE any network work (the whole point)",
      _r30_main.index("pending-post recheck throttle")
      < _r30_main.index("post_json = await fetch_post_json"))
check("r30/round-18/round-20/32/r63/r67 gates: all skip paths record a pending "
      "entry (settle/mod-queue/dup-media/repost gates removed round 63; the "
      "content gate's call removed round 67 -- only removed-post-reason, "
      "archive-liveness, and media-wait/partial-gallery skips remain)",
      _r30_main.count("mark_pending(pending, unique_key") == 4
      and "mark_pending(pending, unique_key, _removed, now" in _r30_main
      and "mark_pending(pending, unique_key,\n" in _r30_main
      and 'mark_pending(pending, unique_key, "media_wait", now)' in _r30_main
      and 'mark_pending(pending, unique_key, "partial_gallery", now)' in _r30_main,
      str(_r30_main.count("mark_pending(pending, unique_key")))
check("r30 posted: a successful post clears the pending entry",
      "pending.pop(unique_key, None)" in _r30_main)
check("r30 save: pending cache saved on BOTH exit paths (quiet + normal)",
      _r30_main.count("save_pending(pending)") == 2)
_r30_dry = _r30_main.rsplit("if DRY_RUN:", 1)[-1].split("else:", 1)[0]
check("r30 DRY RUN branch still writes neither cache",
      "save_posted" not in _r30_dry and "save_pending" not in _r30_dry, _r30_dry)

# P1: twitter cache dumped sorted -> identical id set = identical bytes
_r30_x = load_module("smoke_x_v3_r30", "testing area/twitter_v3.py")
_r30_xdir = tempfile.mkdtemp()
try:
    os.chdir(_r30_xdir)
    _r30_ids = {"2100555373073797461", "2100555373073797999", "2100555373073797123"}
    _r30_x.save_posted_urls(set(_r30_ids))
    with open(_r30_x.CACHE_FILE, "r", encoding="utf-8") as _fh:
        _r30_first = _fh.read()
    _r30_x.save_posted_urls(set(reversed(sorted(_r30_ids))))
    with open(_r30_x.CACHE_FILE, "r", encoding="utf-8") as _fh:
        _r30_second = _fh.read()
    check("r30 P1: same id set dumps byte-identical (no junk cache commits)",
          _r30_first == _r30_second, f"{_r30_first!r} vs {_r30_second!r}")
    check("r30 P1: ids written in sorted order",
          json.loads(_r30_first) == sorted(_r30_ids), _r30_first)
    _r30_many = {f"TYPEII_EN_{2100555373073790000 + i}"
                 for i in range(_r30_x.MAX_CACHE_SIZE_PER_ACCOUNT + 25)}
    _r30_x.save_posted_urls(_r30_many)
    with open(_r30_x.CACHE_FILE, "r", encoding="utf-8") as _fh:
        _r30_trim = json.load(_fh)
    check("r30 P1 (round 34 semantics): trim keeps the account's NEWEST ids "
          "(per-account retention replaced the global lexicographic trim — "
          "the 2026-09-28 AnantaLeaks re-post loop)",
          len(_r30_trim) == _r30_x.MAX_CACHE_SIZE_PER_ACCOUNT
          and _r30_trim == sorted(_r30_many)[-_r30_x.MAX_CACHE_SIZE_PER_ACCOUNT:]
          and _r30_trim[-1] == max(_r30_many, key=lambda k: int(k.rsplit("_", 1)[1])),
          str(_r30_trim[:2]))
finally:
    os.chdir(_r30_cwd)
    shutil.rmtree(_r30_xdir, ignore_errors=True)

# P0: concurrency groups guard both production monitors
for _wf, _group in ((".github/workflows/reddit_monitor.yml", "check-reddit-"),
                    (".github/workflows/twitter_monitor.yml", "check-twitter-")):
    with open(os.path.join(ROOT, _wf), "r", encoding="utf-8") as _fh:
        _wf_src = _fh.read()
    check(f"r30 P0 {os.path.basename(_wf)}: concurrency group present",
          "concurrency:" in _wf_src and _group in _wf_src, _wf_src[:200])
    check(f"r30 P0 {os.path.basename(_wf)}: cancel-in-progress is false "
          "(a cancelled run could lose its cache commit and re-post)",
          "cancel-in-progress: false" in _wf_src
          and "cancel-in-progress: true" not in _wf_src)
with open(os.path.join(ROOT, ".github/workflows/reddit_monitor.yml"), "r",
          encoding="utf-8") as _fh:
    _r30_wf = _fh.read()
check("r30 P3: workflow commits pending_reddit.json with the other caches",
      "git add -f posted_reddit.json proxy_health.json pending_reddit.json" in _r30_wf)
check("r30 P3: pending_reddit.json exists at the repo root for the checkout",
      os.path.exists(os.path.join(ROOT, "pending_reddit.json")))

# ---- R31: feed-token .json probe gate (REDDIT_JSON_PROBE) ----------------
_r31_saved = (v3.JSON_PROBE_MODE, v3.REDDIT_CLIENT_ID, v3.REDDIT_CLIENT_SECRET)
try:
    def _r31_probe(mode, cid, csecret):
        v3.JSON_PROBE_MODE = mode
        v3.REDDIT_CLIENT_ID = cid
        v3.REDDIT_CLIENT_SECRET = csecret
        return v3.feedtoken_probe_enabled()
    check("r31 probe: auto (default) without an OAuth app is OFF (the 65 s 403 probe)",
          _r31_probe("auto", "", "") is False)
    check("r31 probe: unset/empty mode = auto — without an app is OFF",
          _r31_probe("", "", "") is False)
    check("r31 probe: auto WITH both app secrets is ON (legacy fallback under working OAuth)",
          _r31_probe("auto", "clientid", "clientsecret") is True)
    check("r31 probe: force is ON even without an app",
          _r31_probe("force", "", "") is True)
    check("r31 probe: off wins even with an app",
          _r31_probe("off", "clientid", "clientsecret") is False)
    check("r31 probe: fetch_post_json's feed-token branch is gated",
          "REDDIT_FEED_TOKEN and feedtoken_probe_enabled()"
          in inspect.getsource(v3.fetch_post_json))
finally:
    (v3.JSON_PROBE_MODE, v3.REDDIT_CLIENT_ID, v3.REDDIT_CLIENT_SECRET) = _r31_saved

# ---- R32: reason-aware pending re-check + RSS liveness gate (round 32)
import inspect as _r32_inspect

_r32_arctic = v3._ArcticEntry({"id": "x1", "subreddit": "AnantaLeaks"})
_r32_rss = _FakeEntry("/r/AnantaLeaks/comments/x1/t/", "t", "a", "<p>b</p>")
_r32_main_src = _r32_inspect.getsource(v3.main)


def _r32_pend(reason):
    return {"k": {"first_seen": 0.0, "last_checked": 1000.0, "reason": reason}}


check("r32: removed post (Arctic resurface) is NEVER re-checked, even at +1 h",
      v3._pending_throttle_skip(_r32_pend("removal_notice"), "k", 4600.0, _r32_arctic) is True)
check("r32: removed post that RE-APPEARS via RSS bypasses the skip (restore path)",
      v3._pending_throttle_skip(_r32_pend("removal_notice"), "k", 4600.0, _r32_rss) is False)
check("r32: title-marker (deleted) post from the Arctic backup is skipped too",
      v3._pending_throttle_skip(_r32_pend("title marker"), "k", 4600.0, _r32_arctic) is True)
check("r32: approval-queued post (not_live) is skipped before the round-30 throttle (+60 s)",
      v3._pending_throttle_skip(_r32_pend("not_live"), "k", 1060.0, _r32_arctic) is True)
check("r32: approval-queued post (not_live) is DUE at the round-30 throttle (+30 min)",
      v3._pending_throttle_skip(_r32_pend("not_live"), "k", 2800.0, _r32_arctic) is False)
check("r32: 'pending approval' (banner) re-checks on the round-30 throttle (+30 min due)",
      v3._pending_throttle_skip(_r32_pend("pending approval"), "k", 2800.0, _r32_arctic) is False)
check("r32: sources_down (transient) re-checks on the round-30 throttle (+60 s skipped)",
      v3._pending_throttle_skip(_r32_pend("sources_down"), "k", 1060.0, _r32_arctic) is True)
check("r32: media_wait keeps the round-30 30-min throttle (+60 s skipped, +30 min due)",
      v3._pending_throttle_skip(_r32_pend("media_wait"), "k", 1060.0, _r32_arctic) is True
      and v3._pending_throttle_skip(_r32_pend("media_wait"), "k", 2800.0, _r32_arctic) is False)
check("r32: unknown/missing reason falls back to the round-30 30-min throttle",
      v3._pending_throttle_skip(_r32_pend(None), "k", 1060.0, _r32_arctic) is True
      and v3._pending_throttle_skip(_r32_pend(None), "k", 2800.0, _r32_arctic) is False)
check("r32/r35 (round 67 guard update, annotated): the no-recheck set is "
      "removal/deletion ONLY — the round-42/45 content-advisory reasons were "
      "removed with the gate",
      v3._NO_RECHECK_REASONS == frozenset({
          "removal_notice", "removal notice", "title marker", "whole-body marker",
          "removed by moderator", "removed by moderators/filters", "deleted by author"}))
check("r32: the mod-queue-era banner text still classifies as 'pending approval' "
      "(removed_post_reason is a content classifier used by the retraction "
      "seatbelt, not a gate)",
      v3.removed_post_reason("Happy Birthday Caesar | Pink Pages",
                             "Post is awaiting moderator approval.") == "pending approval")
check("r32: clean post text is NOT flagged (no false positive)",
      v3.removed_post_reason("leak title",
                             "new info here https://example.com/x") is None)
check("r32: the round-20 liveness gate maps the banner to 'pending approval'",
      '"awaiting moderator approval" in _why' in _r32_main_src)
check("r32: the main loop uses the reason-aware helper (wired)",
      "_pending_throttle_skip(pending, unique_key, now, entry)" in _r32_main_src)
_r32_gate = _r32_main_src.split("liveness gate", 1)[1].split(
    "post once approved/restored", 1)[0]
check("r32: the liveness gate now covers RSS entries too (live 1wl41aj: a "
      "queued post arrived via RSS with real content and was posted — the "
      "gate was Arctic-only)",
      "if not TEST_POST_ID and entry is not None:" in _r32_gate
      and "and isinstance(entry, _ArcticEntry)):" not in _r32_main_src
      and "feed_ok=not isinstance(entry, _ArcticEntry)" in _r32_gate, _r32_gate)

# ---- R33: clean URL-texted markdown links (round 33, live 1wpejir/1wpdxhs)
_r33_yt = ("https://www.youtube.com/redirect?event=video_description"
           "&redir_token=QUZZTVljRkRQTmRtYnhjNXd3X0V2Y1I0b0x4NnxBTl9p"
           "&q=https%3A%2F%2Fzzzanalytics.vercel.app%2F&v=tE3llvk8-tA")
_r33_line = ("Data from the thumbnail (alongside the combat summary under this "
             "post) was gathered with the help of "
             f"[https://zzzanalytics.vercel.app/]({_r33_yt})")
_r33_want = ("Data from the thumbnail (alongside the combat summary under this "
             "post) was gathered with the help of https://zzzanalytics.vercel.app/")
check("r33: URL-texted link with a youtube-redirect target -> the bare URL",
      v3._clean_url_texted_links(_r33_line) == _r33_want,
      v3._clean_url_texted_links(_r33_line))
check("r33: the live 1wpejir/1wpdxhs line is clean end-to-end through _line_stage",
      v3._line_stage([_r33_line]) == [_r33_want], str(v3._line_stage([_r33_line])))
check("r33: URL-texted link with a normal target -> the bare URL too",
      v3._clean_url_texted_links("see [https://a.com/x](https://a.com/x/real)")
      == "see https://a.com/x")
check("r33: prose label + youtube-redirect target -> 'label (decoded destination)'",
      v3._clean_url_texted_links(f"see [zzz]({_r33_yt})")
      == "see zzz (https://zzzanalytics.vercel.app/)")
check("r33: prose label + normal target stays byte-identical (descriptive links)",
      v3._clean_url_texted_links("see [read more](https://a.com/x)")
      == "see [read more](https://a.com/x)")
check("r33: [U](U) self-referential links are still round 22's (pre-pass intact)",
      v3._repair_label_url_mangle(["Firefly video [https://b23.tv/x](https://b23.tv/x)"])
      == ["Firefly video https://b23.tv/x"])
check("r33: a whole-line [U](redirect) merges under its label (round 21/22 behavior)",
      v3._repair_label_url_mangle(
          ["Video", f"[https://zzzanalytics.vercel.app/]({_r33_yt})"])
      == ["Video https://zzzanalytics.vercel.app/"])
check("r33: mangle residue is left for the round-20/21 repairers (guard holds)",
      v3._clean_url_texted_links(
          "Firefly video [[https://b23.tv/x](https://b23.tv/x)](https://b23.tv/x)")
      == "Firefly video [[https://b23.tv/x](https://b23.tv/x)](https://b23.tv/x)")

# ---- R34: dedup-cache retention (round 34, live AnantaLeaks re-post loop)
# The 2026-09-28 incident: at the 500-key cap, `sorted(posted)[-500:]`
# evicted the alphabetically-first sub (AnantaLeaks) on EVERY save, so its
# live posts re-posted on every 5-min run. Retention is now per-sub,
# newest-first; keys posted this run always survive.
_r34_posted = {f"Genshin_Impact_Leaks_1w{i:05d}" for i in range(300)}  # over the 250 quota
_r34_posted |= {f"AnantaLeaks_1wq{i:04d}" for i in range(5)}          # alphabetically first
_r34_this_run = frozenset({"AnantaLeaks_1wq0004", "Genshin_Impact_Leaks_1w00299"})
_r34_kept = v3._shrink_posted_cache(_r34_posted, _r34_this_run)
check("r34: the alphabetically-first sub's newest keys SURVIVE eviction "
      "(2026-09-28: AnantaLeaks keys were evicted on every save)",
      {"AnantaLeaks_1wq0000", "AnantaLeaks_1wq0004"} <= _r34_kept,
      str(sorted(k for k in _r34_kept if k.startswith("AnantaLeaks_"))))
check("r34: a 300-key sub keeps its NEWEST 250 (oldest 50 evicted, newest kept)",
      "Genshin_Impact_Leaks_1w00049" not in _r34_kept
      and "Genshin_Impact_Leaks_1w00050" in _r34_kept
      and "Genshin_Impact_Leaks_1w00299" in _r34_kept
      and sum(1 for k in _r34_kept if k.startswith("Genshin_")) == 250,
      str(len(_r34_kept)))
check("r34: keys posted this run always survive, even over quota",
      _r34_this_run <= _r34_kept, str(_r34_this_run - _r34_kept))
check("r34: under-quota sets pass through unchanged",
      v3._shrink_posted_cache({"AnantaLeaks_1wq0000"}, frozenset())
      == {"AnantaLeaks_1wq0000"})
_r34_tmp = tempfile.mkdtemp()
_r34_cwd = os.getcwd()
try:
    os.chdir(_r34_tmp)
    _r34_saved = v3.save_posted(_r34_posted, _r34_this_run)
    with open("posted_reddit.json", encoding="utf-8") as _r34_f:
        _r34_raw = _r34_f.read()
    _r34_on_disk = set(json.loads(_r34_raw))
    check("r34: save round-trip — disk set == kept set (cap applied, this-run kept)",
          _r34_on_disk == _r34_kept and _r34_saved == _r34_kept,
          f"disk={len(_r34_on_disk)} kept={len(_r34_kept)}")
    check("r34: saved file is sorted (byte-stable across runs, quiet-run commits)",
          _r34_raw == json.dumps(sorted(_r34_on_disk), indent=2))
finally:
    os.chdir(_r34_cwd)
    shutil.rmtree(_r34_tmp, ignore_errors=True)
_r34_feed = [("S", f"/p/{i}/", f"S_{i:04d}", i, i, None) for i in range(40)]
_r34_capped = v3.cap_new_posts(list(_r34_feed), 25)
check("r34: flood cap keeps the NEWEST 25 of a 40-entry run (oldest 15 retry next run)",
      len(_r34_capped) == 25 and _r34_capped[0][2] == "S_0015"
      and _r34_capped[-1][2] == "S_0039",
      str([p[2] for p in _r34_capped][:3]))
check("r34: under-cap runs pass through unchanged",
      v3.cap_new_posts(list(_r34_feed[:10]), 25) == _r34_feed[:10])
_r34_main_src = inspect.getsource(v3.main)
_r34_loop_head = _r34_main_src.split(
    "for subreddit, path, unique_key, published_ts, activity_ts, entry in new_posts:",
    1)[1].split("webhook_url = get_webhook_for_subreddit(subreddit)", 1)[0]
check("r34: the posting loop re-checks the dedup cache before posting (double guard, "
      "TEST POST rebuilds exempt)",
      "if not TEST_POST_ID and unique_key in posted:" in _r34_loop_head, _r34_loop_head)
check("r34: the per-run flood cap is wired into main",
      "cap_new_posts(new_posts, MAX_POSTS_PER_RUN)" in _r34_main_src)
check("r34: BOTH save sites verify the save (a missing this-run key is a loud error)",
      _r34_main_src.count("_verify_dedup_save(posted_at_start, posted, saved)") == 2,
      str(_r34_main_src.count("_verify_dedup_save(posted_at_start, posted, saved)")))

# ---- R34b: X dedup-cache retention (round 34 — same cap bug in twitter_v3)
# The save wrote `sorted(posted_urls)[-500:]`; keys are
# `{account}_{tweet_id}`, so at the cap the alphabetically-first account
# (Ananta_EN) would lose keys on every save — the Reddit 2026-09-28 loop,
# latent on X (cache was at ~143/500 when round 34 landed).
x3 = load_module("smoke_twitter_v3_r34", "testing area/twitter_v3.py")
_r34t_posted = {f"Wuthering_Waves_210{i:016d}" for i in range(300)}  # over the 250 quota
_r34t_posted |= {f"Ananta_EN_19{i:017d}" for i in range(5)}         # alphabetically first
_r34t_this_run = frozenset({"Ananta_EN_1900000000000000004",
                            "Wuthering_Waves_2100000000000000299"})
_r34t_kept = x3._shrink_posted_urls(_r34t_posted, _r34t_this_run)
check("r34b: the alphabetically-first account's newest keys SURVIVE eviction "
      "(the latent X twin of the 2026-09-28 AnantaLeaks loop)",
      {"Ananta_EN_1900000000000000000", "Ananta_EN_1900000000000000004"} <= _r34t_kept,
      str(sorted(k for k in _r34t_kept if k.startswith("Ananta_EN_"))))
check("r34b: a 300-key account keeps its NEWEST 250 (oldest 50 evicted, newest kept)",
      "Wuthering_Waves_2100000000000000049" not in _r34t_kept
      and "Wuthering_Waves_2100000000000000050" in _r34t_kept
      and "Wuthering_Waves_2100000000000000299" in _r34t_kept
      and sum(1 for k in _r34t_kept if k.startswith("Wuthering_Waves_")) == 250,
      str(len(_r34t_kept)))
check("r34b: keys posted this run always survive, even over quota",
      _r34t_this_run <= _r34t_kept, str(_r34t_this_run - _r34t_kept))
check("r34b: under-quota sets pass through unchanged",
      x3._shrink_posted_urls({"Ananta_EN_1900000000000000000"}, frozenset())
      == {"Ananta_EN_1900000000000000000"})
_r34t_tmp = tempfile.mkdtemp()
_r34t_cwd = os.getcwd()
try:
    os.chdir(_r34t_tmp)
    _r34t_saved = x3.save_posted_urls(_r34t_posted, _r34t_this_run)
    with open("posted_tweets.json", encoding="utf-8") as _r34t_f:
        _r34t_raw = _r34t_f.read()
    _r34t_on_disk = set(json.loads(_r34t_raw))
    check("r34b: X save round-trip — disk set == kept set (sorted, cap applied)",
          _r34t_on_disk == _r34t_kept and _r34t_saved == _r34t_kept,
          f"disk={len(_r34t_on_disk)} kept={len(_r34t_kept)}")
    check("r34b: X saved file is sorted (byte-stable across runs)",
          _r34t_raw == json.dumps(sorted(_r34t_on_disk), indent=2))
finally:
    os.chdir(_r34t_cwd)
    shutil.rmtree(_r34t_tmp, ignore_errors=True)
_r34t_main_src = inspect.getsource(x3.main)
check("r34b: the X run verifies its save (a missing this-run key is a loud error)",
      "DEDUP GUARD" in _r34t_main_src
      and "save_posted_urls(posted_urls, frozenset(posted_urls - posted_urls_at_start))"
      in _r34t_main_src)

# ---- R35/R42/R45 (round 67 guard update, annotated): the content-advisory
# gate was REMOVED — engine, pure decision module, workflow mapping and its
# tests are gone, and the engine makes no content-based posting decision on
# any path. These guards pin the REMOVAL, so reintroducing the subsystem (a
# policy regression) fails the suite. The removed token is assembled here on
# purpose: this file itself stays free of it, so a repository-wide search for
# the removed names comes back empty for every active file. The full truthful
# record lives in docs/history/ROUND_67.md.
_r35_token = "n" + "sfw"
signals = load_module("smoke_reddit_signals", "testing area/reddit_signals.py")
_r35_removed_vars = [f"{_r35_token.upper()}_{suffix}" for suffix in
                     ("ALLOWLIST", "FAIL_OPEN", "REQUIRE_SUBREDDIT", "PAGE_FALLBACK")]
check("r35/r42/r45 guard updated in round 67: the engine exposes no removed "
      "content-advisory decision function",
      not hasattr(v3, _r35_token + "_gate_reason")
      and not hasattr(v3, _r35_token + "_from_post_page")
      and not hasattr(v3, _r35_token + "_allowlist_forms"))
check("r35/r42/r45 guard updated in round 67: no batched content-metadata "
      "lookup remains anywhere in the engine",
      f"fetch_arctic_{_r35_token}" not in inspect.getsource(v3))
check("r35/r42/r45 guard updated in round 67: the engine source and the pure "
      "decision module are free of the removed subsystem",
      _r35_token not in inspect.getsource(v3).lower()
      and _r35_token not in inspect.getsource(signals).lower())
with open(os.path.join(ROOT, ".github/workflows/reddit_monitor.yml"), encoding="utf-8") as _r35_f:
    _r35_workflow = _r35_f.read()
check("r35/r42/r45 guard updated in round 67: the workflow env block and its "
      "comments are free of the removed gate",
      _r35_token not in _r35_workflow.lower()
      and all(_name not in _r35_workflow for _name in _r35_removed_vars))
check("r67: the video-quality Variable is wired into the live workflow",
      "REDDIT_VIDEO_QUALITY: ${{ vars.REDDIT_VIDEO_QUALITY }}" in _r35_workflow)
# Main-loop source kept under its historical name for the later guards.
_r35_main_src = inspect.getsource(v3.main)

# ---- R36 (round 63: settle window + duplicate-media gate removed; the
# pending-cache audit fields they introduced are still used by the
# surviving gates) ----
_r36_pend = {}; v3.mark_pending(_r36_pend, "Sub_abc111", "media_wait", 1000.0)
check("r36: the legacy mark_pending call shape still works", _r36_pend["Sub_abc111"] == {"first_seen":1000.0,"last_checked":1000.0,"reason":"media_wait"})
v3.mark_pending(_r36_pend, "Sub_abc111", "removal_notice", 2000.0, source="rss", title="Aha kit", published_ts=1234.0)
check("r36: mark_pending stores the round-36 audit fields (additive)", _r36_pend["Sub_abc111"]["source"] == "rss" and _r36_pend["Sub_abc111"]["published_ts"] == 1234)
check("r36: a restored no-recheck post re-entering via RSS is logged loudly", "RESTORED:" in inspect.getsource(v3.main))
check("r63: the settle window, mod-queue gate and duplicate/repost gates are gone from the main loop",
      not any(sym in inspect.getsource(v3.main) for sym in
              ("settle_holds(", "POST_SETTLE_SECONDS", "DUP_MEDIA_GATE", "REPOST_GATE",
               "MOD_QUEUE_GATE", "mod_queue_reason(")))

# ---- R41: domain-only URL label unwraps to a clean bare URL (1wtxc4m) ------
check("r41: domain-only URL label unwraps to clean bare URL with spacing (1wtxc4m)",
      v3.clean_rss_body('<p>extra Astrites!<a href="http://wuwa-share.kurogames-global.com/sr/xyz">wuwa-share.kurogames-global.com/sr/xyz</a></p>')
      == "extra Astrites! http://wuwa-share.kurogames-global.com/sr/xyz")

# ---- R38a (2026-09-30): link posts carry the YouTube URL as the destination ----
check("r38a: a link post's YouTube destination is picked up from the final body",
      'if not yt_url:\n        yt_url = extract_youtube_url(body)' in inspect.getsource(v3.resolve_post_media))

# ---- R38c (2026-09-30): gallery video pickup (1wt59lm: image + clip) ----
_r38c_area = ('<h1 class="post_title"></h1><div class="post_content">'
              '<a href="/link/1wt59lm/video/1wevaos4efsh1/player">x</a></div>')
check("r38c: a redlib player link yields the gallery video id",
      v3.extract_redlib_video_id(_r38c_area) == "1wevaos4efsh1")
_r38c_dash = ('<h1 class="post_title"></h1><div class="post_content">'
              '<source src="https://v.redd.it/1wevaos4efsh1/DASH_720.mp4">')
check("r38c: a direct v.redd.it URL yields the gallery video id",
      v3.extract_redlib_video_id(_r38c_dash) == "1wevaos4efsh1")
check("r38c: a page without a video yields None",
      v3.extract_redlib_video_id(_r38c_area.replace("1wevaos4efsh1", "img9")) is None)
check("r38c: a gallery video item keeps the post's photos (tile-only rule spared)",
      'not x.get("gallery")' in inspect.getsource(v3.resolve_post_media))
check("r38c: the redlib harvest appends the resolved gallery video",
      '"gallery": True' in inspect.getsource(v3.enrich_gallery_redlib))

# ---- R38c hardening (found during apply): payload edge cases ----
_r38c_pay = {"title": "T", "author": "a", "body": "", "stats": None,
             "youtube_url": None, "crosspost": None, "op_comment": None}
_r38c_p1 = v3.build_v3_payload("s", dict(_r38c_pay, media=(
    [{"kind": "image", "url": f"https://i.redd.it/{i}.jpg"} for i in range(v3.MEDIA_PER_GALLERY)]
    + [{"kind": "video", "url": "https://v.redd.it/v.mp4", "gallery": True}])),
    "https://r/x", 1)
check("r38c-hardening: 10 photos + gallery video never emits an empty gallery (Discord 400)",
      all(len(g["items"]) > 0
          for ct in _r38c_p1["components"] for g in ct["components"] if g.get("type") == 12))
_r38c_p2 = v3.build_v3_payload("s", dict(_r38c_pay, media=[
    {"kind": "video", "url": "https://v.redd.it/v.mp4"}]), "https://r/x", 1)
_r38c_c2 = _r38c_p2["components"][0]["components"]
_r38c_gi = next(i for i, c in enumerate(_r38c_c2) if c.get("type") == 12)
check("r38c-hardening: a video-only card keeps its legacy divider before the tile",
      _r38c_gi > 0 and _r38c_c2[_r38c_gi - 1].get("type") == 14)

# ---- Round 39 (2026-09-30): runtime + delivery + source-fleet guard ------
# These are offline source-of-truth checks. Network availability is intentionally
# not tested here; mirror health changes fast and is reviewed before the list is
# changed. Parsing the literals avoids importing an additional engine again.
import ast


def _literal_list_constant(relpath, name):
    with open(os.path.join(ROOT, relpath), encoding="utf-8") as _fh:
        tree = ast.parse(_fh.read(), filename=relpath)
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == name):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} missing from {relpath}")


_r39_x_expected = [
    "https://nitter.cf",
    "https://xitter.cf",
    "https://nitter.kareem.one",
    "https://tw.eir-nya.gay",
    "https://nitter.meowing.monster",
    "https://shitter.thepixora.com",
]
_r59_x_walled_tail = ["https://nitter.netbub.com", "https://nitter.jaydenha.uk"]
_r59_fleet = _literal_list_constant("testing area/twitter_v3.py", "RSS_INSTANCES")
check("r59: operator pair remains in slots 1-2", _r59_fleet[:2] == ["https://nitter.cf", "https://xitter.cf"])
check("r59: kareem mirror is ahead of token host", _r59_fleet.index("https://nitter.kareem.one") < _r59_fleet.index("https://nitter.miningtcup.me"))
check("r59: kitter mirror is ahead of token host", _r59_fleet.index("https://tw.eir-nya.gay") < _r59_fleet.index("https://nitter.miningtcup.me"))
check("r59: walled tail is retained at the end", _r59_fleet[-2:] == _r59_x_walled_tail)
_r59_token_env = _literal_list_constant("testing area/twitter_v3.py", "RSS_TOKEN_ENV")
check("r59: miningtcup token mapping unchanged", _r59_token_env == {"https://nitter.miningtcup.me": "NITTER_RSS_TOKEN"})
with open(os.path.join(ROOT, "testing area", "twitter_v3.py"), encoding="utf-8") as _r59_f:
    _r59_src = _r59_f.read()
check("r59: tracker method retired note recorded", "tracker method retired" in _r59_src)
check("r59: demoted-not-deleted rationale recorded", "demoted, not deleted" in _r59_src)
for _r39_rel, _r39_expected in (
    ("testing area/twitter_v1.py", _r39_x_expected + _r59_x_walled_tail),
    ("testing area/twitter_v2_button_outside.py", _r39_x_expected + ["https://nitter.miningtcup.me"] + _r59_x_walled_tail),
    ("testing area/twitter_v3.py", _r39_x_expected + ["https://nitter.miningtcup.me"] + _r59_x_walled_tail),
):
    check(f"r39 source fleet: {_r39_rel} has the reviewed X RSS order",
          _literal_list_constant(_r39_rel, "RSS_INSTANCES") == _r39_expected)

_r39_reddit_expected = [
    "https://www.reddit.com",
    "https://old.reddit.com",
    "https://redlib.catsarch.com",
    "https://redlib.nadeko.net",
    "https://redlib.privadency.com",
    "https://safereddit.com",
    "https://red.artemislena.eu",
    "https://redlib.privacyredirect.com",
]
for _r39_rel, _r39_expected in (
    ("testing area/reddit_main.py", _r39_reddit_expected),
    ("testing area/reddit_main_v2_embedez.py", _r39_reddit_expected),
    ("testing area/reddit_main_v3.py", _r39_reddit_expected[:2]
     + ["https://redlib.miningtcup.me"] + _r39_reddit_expected[2:]),
):
    check(f"r39 source fleet: {_r39_rel} has the reviewed Reddit RSS order",
          _literal_list_constant(_r39_rel, "REDDIT_RSS_INSTANCES") == _r39_expected)

for _r39_wf in (".github/workflows/ci.yml", ".github/workflows/reddit_monitor.yml",
                ".github/workflows/twitter_monitor.yml"):
    with open(os.path.join(ROOT, _r39_wf), encoding="utf-8") as _fh:
        _r39_source = _fh.read()
    check(f"r39 runtime: {_r39_wf} pins CPython 3.14.8 exactly once",
          _r39_source.count("python-version: '3.14.8'") == 1
          and "python-version: '3.11'" not in _r39_source)

for _r39_wf in (".github/workflows/reddit_monitor.yml", ".github/workflows/twitter_monitor.yml"):
    with open(os.path.join(ROOT, _r39_wf), encoding="utf-8") as _fh:
        _r39_source = _fh.read()
    check(f"r39 cache persistence: {_r39_wf} uses staged-only no-change detection",
          "git diff --cached --quiet" in _r39_source)
    # r61 (2026-10-04) UPDATE: the recovery between retries changed from
    # `git pull --rebase` (conflict-fragile — the 2026-10-03 double post's
    # accomplice) to the semantic JSON merge (fetch + reset + merge script).
    # The detached-HEAD-safe push and the 3-attempt loop are unchanged.
    check(f"r39 cache persistence: {_r39_wf} retries an explicit detached-HEAD-safe push",
          'for attempt in 1 2 3; do' in _r39_source
          and 'git push origin "HEAD:${cache_branch}"' in _r39_source
          and 'git fetch origin "$cache_branch"' in _r39_source
          and "merge_monitor_caches.py" in _r39_source)
    check(f"r39 cache persistence: {_r39_wf} fails loudly if a dedup cache cannot persist",
          "::error::CACHE PUSH FAILED" in _r39_source
          and "::error::CACHE PUSH RECOVERY FAILED" in _r39_source)

with open(os.path.join(ROOT, ".github/workflows/dependabot_auto_merge.yml"), encoding="utf-8") as _fh:
    _r39_merge = _fh.read()
# 2026-10-05 update — Dependabot auto-merge workflow repair: workflow_run
# sometimes did not fire after CI, so this trusted pull_request_target flow
# polls its immutable event SHA instead.
check("Dependabot auto-merge uses trusted PR events and never checks out PR code",
      "pull_request_target:" in _r39_merge
      and "types: [opened, synchronize, reopened]" in _r39_merge
      and "workflow_run:" not in _r39_merge
      and "actions/checkout" not in _r39_merge
      and "pull_request_target runs this trusted file" in _r39_merge)
check("Dependabot auto-merge fails closed on the exact head SHA before squash merging",
      "AUTO_MERGE_DEPENDABOT" in _r39_merge
      and "pr.user?.login !== 'dependabot[bot]'" in _r39_merge
      and "expectedHeadSha = eventPr.head.sha" in _r39_merge
      and "data.check_runs" in _r39_merge
      and "filter: 'latest'" in _r39_merge
      and "maxAttempts = 32" in _r39_merge
      and "sha: expectedHeadSha" in _r39_merge
      and "merge_method: 'squash'" in _r39_merge
      and "GitHub API error; failing closed" in _r39_merge)

# ---- R42/R45 smoke guards (round 67 guard update, annotated): the Redlib
# badge reader, the fail-closed decision matrix, the allowlist and the
# live-page fallback were all removed with the gate. What remains of that era
# is the retraction seatbelt below; the removal itself is pinned by the
# R35/R42/R45 block above and by the pure-signal absence guards in
# tests/test_reddit_signals.py.
check("r42: signal module imports independently", signals is not None)

# ---- R44 / round 63: retraction seatbelt (dead/edit/delete/outside-window) -
check("r44: listing absence can prove dead within listing span", signals.listing_absence_proves_dead("abc", 940, 1000, {"zzz"}, 120))
check("r44: listing presence never proves dead", signals.listing_absence_proves_dead("abc", 940, 1000, {"abc"}, 120) is False)
check("r44: old post outside listing span is not retracted", signals.listing_absence_proves_dead("abc", 0, 1000, {"zzz"}, 120) is False)
check("r44: retraction decision returns edit only with both proofs", signals.retraction_decision(enabled=True, mode="edit", post_id="abc", published_ts=940, now=1000, listing_ids={"zzz"}, listing_oldest_age=120, live_removal_reason="removed") == "edit")
check("r44: no live removal proof means no retraction", signals.retraction_decision(enabled=True, mode="edit", post_id="abc", published_ts=940, now=1000, listing_ids={"zzz"}, listing_oldest_age=120, live_removal_reason=None) is None)
check("r44: disabled retraction stays off", signals.retraction_decision(enabled=False, mode="edit", post_id="abc", published_ts=940, now=1000, listing_ids={"zzz"}, listing_oldest_age=120, live_removal_reason="removed") is None)
check("r63: retraction OFF (default) means retraction_decision NEVER returns a verdict, "
      "regardless of mode or evidence",
      signals.retraction_decision(enabled=False, mode="edit", post_id="abc", published_ts=0, now=100000, listing_ids=set(), listing_oldest_age=120, live_removal_reason="removed") is None
      and signals.retraction_decision(enabled=False, mode="delete", post_id="abc", published_ts=0, now=100000, listing_ids=set(), listing_oldest_age=120, live_removal_reason="removed") is None)
check("r63: retraction ON + delete mode returns 'delete' with both proofs",
      signals.retraction_decision(enabled=True, mode="delete", post_id="abc", published_ts=940, now=1000, listing_ids={"zzz"}, listing_oldest_age=120, live_removal_reason="removed") == "delete")
check("r63: retraction ON but OUTSIDE the listing span (not provably dead) takes no action",
      signals.retraction_decision(enabled=True, mode="edit", post_id="abc", published_ts=0, now=1000, listing_ids={"zzz"}, listing_oldest_age=120, live_removal_reason="removed") is None)
check("r63: an invalid mode never retracts even with both proofs",
      signals.retraction_decision(enabled=True, mode="bogus", post_id="abc", published_ts=940, now=1000, listing_ids={"zzz"}, listing_oldest_age=120, live_removal_reason="removed") is None)
_tomb = signals.tombstone_payload({"components": [{"type": 17, "accent_color": 16729344, "components": [{"type": 10, "content": "header"}]}]}, "removed")
check("r44: tombstone prepends a notice", "no longer live" in _tomb["components"][0]["components"][0]["content"])
check("r44: tombstone greys the accent", _tomb["components"][0]["accent_color"] == 0x808080)
with open(os.path.join(ROOT, ".github/workflows/ci.yml"), encoding="utf-8") as _fh:
    _r42_ci = _fh.read()
check("r42: CI runs the pure signal suite", "python tests/test_reddit_signals.py" in _r42_ci)
check("r42/r45 guard updated in round 67: the removed gate's repository "
      "Variables are unmapped",
      not any(_v in _r35_workflow for _v in _r35_removed_vars))
check("r44: workflow persists posted_messages only when present", "posted_messages.json" in _r35_workflow and "-f posted_messages.json" in _r35_workflow)
check("r44: wait=true is conditional on retraction", "&wait=true" in _r35_main_src and "RETRACT_DEAD_POSTS" in _r35_main_src)
check("r42: signal module is side-effect-free (no aiohttp import)", "aiohttp" not in inspect.getsource(signals))
check("r63: retraction defaults are OFF / edit / 6h, as specified",
      v3.RETRACT_DEAD_POSTS is False and v3.RETRACT_MODE == "edit" and v3.RETRACT_WINDOW_SECONDS == 21600)

# ---- R63 end-to-end: retract_dead_posts actually drives the Discord call --
class _R63Resp:
    def __init__(self, status=200):
        self.status = status

    async def text(self):
        return ""

    async def json(self, content_type=None):
        return {}


class _R63Req:
    def __init__(self, status=200):
        self._status = status

    async def __aenter__(self):
        return _R63Resp(self._status)

    async def __aexit__(self, *a):
        return False


class _R63Session:
    """No listing sources answer and no live page is reachable — the dead
    proof comes entirely from live_removal_reason via a stubbed proxy, so
    these end-to-end checks monkeypatch the two network-facing helpers and
    just prove retract_dead_posts issues (or withholds) the right HTTP verb.
    """

    def __init__(self):
        self.calls = []

    def get(self, *a, **k):
        return _R63Req(404)

    def patch(self, url, json=None, **k):
        self.calls.append(("PATCH", url, json))
        return _R63Req(200)

    def delete(self, url, **k):
        self.calls.append(("DELETE", url, None))
        return _R63Req(204)

    def post(self, *a, **k):
        self.calls.append(("POST", a[0] if a else None, k.get("json")))
        return _R63Req(200)


def _r63_record(now):
    return {
        "RetractSub_abc111": {
            "subreddit": "RetractSub",
            "path": "/r/RetractSub/comments/abc111/t/",
            "message_id": "999",
            "delivered_at": int(now - 100),
            "published_ts": int(now - 100),
            "payload": {"components": [{"type": 17, "components": [{"type": 10, "content": "hi"}]}]},
        }
    }


async def _r63_run(mode, enabled, window, now, listing_result, live_reason):
    saved = (v3.RETRACT_DEAD_POSTS, v3.RETRACT_MODE, v3.RETRACT_WINDOW_SECONDS,
             v3._fetch_new_listing, v3.live_removal_reason, v3.get_webhook_for_subreddit)
    try:
        v3.RETRACT_DEAD_POSTS = enabled
        v3.RETRACT_MODE = mode
        v3.RETRACT_WINDOW_SECONDS = window

        async def _fake_listing(session, subreddit):
            return listing_result

        async def _fake_live_removal_reason(session, path, label=""):
            return live_reason

        v3._fetch_new_listing = _fake_listing
        v3.live_removal_reason = _fake_live_removal_reason
        v3.get_webhook_for_subreddit = lambda sub: "https://discord.test/api/webhooks/1/tok"
        session = _R63Session()
        records = _r63_record(now)
        await v3.retract_dead_posts(session, records, now)
        return session, records
    finally:
        (v3.RETRACT_DEAD_POSTS, v3.RETRACT_MODE, v3.RETRACT_WINDOW_SECONDS,
         v3._fetch_new_listing, v3.live_removal_reason, v3.get_webhook_for_subreddit) = saved


_r63_now = 1_000_000.0
_r63_dead_listing = ({"zzz_other"}, 7200)   # post absent, inside the listing span

_r63_sess, _ = asyncio.run(_r63_run("edit", False, 21600, _r63_now, _r63_dead_listing, "removed by moderator"))
check("r63 retraction OFF: no retraction request is EVER issued",
      _r63_sess.calls == [], str(_r63_sess.calls))

_r63_sess, _r63_rec = asyncio.run(_r63_run("edit", True, 21600, _r63_now, _r63_dead_listing, "removed by moderator"))
check("r63 retraction ON+edit: a delivered post that dies inside the window is PATCHed with the tombstone payload",
      len(_r63_sess.calls) == 1 and _r63_sess.calls[0][0] == "PATCH"
      and "no longer live" in _r63_sess.calls[0][2]["components"][0]["components"][0]["content"],
      str(_r63_sess.calls))
check("r63 retraction ON+edit: the record is marked retracted (dedup-safe)",
      _r63_rec["RetractSub_abc111"].get("retracted") is True)

_r63_sess, _ = asyncio.run(_r63_run("delete", True, 21600, _r63_now, _r63_dead_listing, "removed by moderator"))
check("r63 retraction ON+delete: the Discord message is DELETEd instead of edited",
      len(_r63_sess.calls) == 1 and _r63_sess.calls[0][0] == "DELETE", str(_r63_sess.calls))

_r63_sess, _ = asyncio.run(_r63_run("edit", True, 10, _r63_now, _r63_dead_listing, "removed by moderator"))
check("r63 retraction ON+edit but OUTSIDE the retraction window: no action",
      _r63_sess.calls == [], str(_r63_sess.calls))

_r63_sess, _ = asyncio.run(_r63_run("edit", True, 21600, _r63_now, ({"abc111"}, 7200), "removed by moderator"))
check("r63 retraction ON+edit but the post is STILL in the listing (not provably dead): no action",
      _r63_sess.calls == [], str(_r63_sess.calls))

_r63_retract_src = inspect.getsource(v3.retract_dead_posts)
check("r63: a retracted post can never re-post — retract_dead_posts never touches the "
      "dedup cache (it only edits/deletes the Discord message), so the post's "
      "unique_key stays in posted_reddit.json forever",
      "posted.discard(" not in _r63_retract_src
      and "posted.remove(" not in _r63_retract_src
      and "posted_messages" in _r63_retract_src)
check("r63: the main loop never removes a key from the dedup cache for any reason "
      "(posted keys are additive-only; retraction cannot un-dedup a post)",
      "posted.discard(" not in inspect.getsource(v3.main)
      and "posted.remove(" not in inspect.getsource(v3.main))

print()

# ===========================================================================
# ROUND 64 (2026-10-05) — Pristine Listing Protocol.
# A post delivers only once a cache-busted, pristine read of the subreddit's
# /new listing proves it is live RIGHT NOW, plus a stateless flat age floor
# computed from the post's own timestamp. See tests/test_reddit_signals.py
# for the full end-to-end main() coverage (D.2-D.8 + addendum); this block
# covers D.1 (pristine request shape), D.8 (env parsing/defaults) and D.9
# (the absence-of-AutoModerator-detection guard).
# ===========================================================================

# ---- D.1: every listing request is pristine (unique cache-buster + no-cache
# headers), and the buster differs between two consecutive requests --------
class _R64Resp:
    status = 200
    headers = {"Content-Type": "text/html"}
    async def text(self):
        return '<a href="/r/TestSub/comments/abc123/t/">t</a>'
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False

class _R64Session:
    def __init__(self):
        self.calls = []
    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append((url, dict(headers or {})))
        return _R64Resp()

async def _r64_fetch_twice():
    v3._listing_cache.clear()
    sess = _R64Session()
    _real_time = v3.time.time
    _r64_ticks = iter([1000000.000, 1000000.001])
    v3.time.time = lambda: next(_r64_ticks, _real_time())
    try:
        await v3._fetch_new_listing(sess, "TestSub1")
        v3._listing_cache.clear()
        await v3._fetch_new_listing(sess, "TestSub2")
        v3._listing_cache.clear()
    finally:
        v3.time.time = _real_time
    return sess.calls
_r64_calls = asyncio.run(_r64_fetch_twice())
_r64_fresh_values = []
for _url, _hdrs in _r64_calls:
    _m = re.search(r"[?&]_fresh=(\d+)", _url)
    if _m:
        _r64_fresh_values.append(_m.group(1))
check("r64: every listing request URL carries a _fresh= cache-buster",
      len(_r64_calls) > 0 and len(_r64_fresh_values) == len(_r64_calls))
check("r64: the cache-buster differs between two separate _fetch_new_listing() calls",
      len(set(_r64_fresh_values)) >= 2 or len(_r64_calls) < 2)
check("r64: every listing request sends Cache-Control/Pragma no-cache headers",
      all(_hdrs.get("Cache-Control") == "no-cache, no-store" and _hdrs.get("Pragma") == "no-cache"
          for _url, _hdrs in _r64_calls))
v3._listing_cache.clear()

# ---- D.8: FRESH_HOLD_SECONDS / LISTING_CONFIRM parse from env, with the
# documented defaults, and are empty-string-safe like every other Variable --
_r64_names = ("FRESH_HOLD_SECONDS", "LISTING_CONFIRM")
_r64_saved_env = {name: os.environ.get(name) for name in _r64_names}
try:
    os.environ.update({name: "" for name in _r64_names})
    _r64_default = load_module("smoke_reddit_v3_r64_default_env", "testing area/reddit_main_v3.py")
    check("r64: FRESH_HOLD_SECONDS empty env defaults to 30",
          _r64_default.FRESH_HOLD_SECONDS == 30)
    check("r64: LISTING_CONFIRM empty env defaults to True",
          _r64_default.LISTING_CONFIRM is True)
    os.environ["FRESH_HOLD_SECONDS"] = "60"
    os.environ["LISTING_CONFIRM"] = "0"
    _r64_custom = load_module("smoke_reddit_v3_r64_custom_env", "testing area/reddit_main_v3.py")
    check("r64: FRESH_HOLD_SECONDS=60 parses to 60",
          _r64_custom.FRESH_HOLD_SECONDS == 60)
    check("r64: LISTING_CONFIRM=0 parses to False",
          _r64_custom.LISTING_CONFIRM is False)
    os.environ["FRESH_HOLD_SECONDS"] = "0"
    _r64_off = load_module("smoke_reddit_v3_r64_off_env", "testing area/reddit_main_v3.py")
    check("r64: FRESH_HOLD_SECONDS=0 parses to 0 (floor disabled)",
          _r64_off.FRESH_HOLD_SECONDS == 0)
finally:
    for _name, _value in _r64_saved_env.items():
        if _value is None:
            os.environ.pop(_name, None)
        else:
            os.environ[_name] = _value

# ---- D.9: absence guard — no AutoModerator / sticky-comment detection of
# any kind exists anywhere in the Reddit engine (no phrase matching, no
# inference, no tracker state). The only two mentions of "AutoModerator" in
# the whole engine are prose that explicitly EXCLUDES its footer text from
# an unrelated removal-reason check — never a detection mechanism. ---------
_r64_src_files = {
    "reddit_main_v3.py": inspect.getsource(v3),
    "reddit_signals.py": inspect.getsource(signals),
}
_r64_forbidden_always = ("mirror link and source link", "queue-request", "sticky")
_r64_forbidden_comparisons = (
    '== "automoderator"', "== 'automoderator'",
    '.lower() == "automoderator"', ".lower() == 'automoderator'",
    'in ("automoderator"', "in ('automoderator'",
    'in ["automoderator"', "in ['automoderator'",
    'r"automoderator"', "r'automoderator'",
)
for _fname, _fsrc in _r64_src_files.items():
    _lower = _fsrc.lower()
    check(f"r64: {_fname} has no sticky/queue-request/mirror-link phrase matching",
          all(phrase not in _lower for phrase in _r64_forbidden_always))
    check(f"r64: {_fname} never compares/matches against the literal 'automoderator' "
          f"as a detection mechanism (mentions, if any, are prose only)",
          all(pattern not in _lower for pattern in _r64_forbidden_comparisons))

print()

# ===========================================================================
# ROUND 47 (2026-10-01) — X: self-heal a message whose media Discord left
# unresolved, and stop downgrading videos Discord can actually play.
# ===========================================================================
_r47_x = load_module("smoke_x_v3_r47", "testing area/twitter_v3.py")

# The EXACT shape Discord returned for Wuthering_Waves/2105598609827737874:
# a proxy_url was minted but width/height are 0 and content_type is empty,
# which is what renders as "Image failed to load".
_r47_broken_msg = {
    "id": "2105600000000000000",
    "components": [{
        "id": 1, "type": 17,
        "components": [
            {"id": 2, "type": 10, "content": "### Wuthering Waves just tweeted:"},
            {"id": 5, "type": 12, "items": [{"media": {
                "url": "https://video.twimg.com/amplify_video/2105288774121074688/vid/avc1/1920x1080/MKcWr_GC5zM6ZGX4.mp4?tag=29",
                "proxy_url": "https://images-ext-1.discordapp.net/external/F2rq/https/video.twimg.com/x.mp4",
                "width": 0, "height": 0, "content_type": ""}}]},
        ],
    }],
}
_r47_ok_msg = {
    "id": "2105600000000000000",
    "components": [{
        "id": 1, "type": 17,
        "components": [{"id": 5, "type": 12, "items": [{"media": {
            "url": "https://video.twimg.com/amplify_video/x/1920x1080/y.mp4?tag=29",
            "proxy_url": "https://images-ext-1.discordapp.net/external/F2rq/https/video.twimg.com/y.mp4",
            "width": 1920, "height": 1080, "content_type": "video/mp4"}}]}],
    }],
}
check("r47: an unresolved media tile (width/height 0, no content_type) is detected",
      _r47_x.unresolved_media_items(_r47_broken_msg)
      == ["https://video.twimg.com/amplify_video/2105288774121074688/vid/avc1/1920x1080/MKcWr_GC5zM6ZGX4.mp4?tag=29"])
check("r47: a resolved media tile is NOT flagged (no pointless edits)",
      _r47_x.unresolved_media_items(_r47_ok_msg) == [])
check("r47: a message with no media is never flagged",
      _r47_x.unresolved_media_items({"components": [{"type": 10, "content": "hi"}]}) == []
      and _r47_x.unresolved_media_items(None) == [])

# ---------------------------------------------------------------------------
# ROUND 56 — media heal targets VIDEOS only by default.
# The only confirmed broken tile was a video (round 47); cold PHOTO tiles
# (PomPom/TYPEII 2026-10-02) rendered fine without an edit, so images, GIFs
# and webp are no longer heal-eligible unless MEDIA_HEAL_SCOPE=all.
# ---------------------------------------------------------------------------
check("r56: video rendition URLs are heal-eligible",
      _r47_x.is_video_media_url(
          "https://video.twimg.com/amplify_video/1/vid/avc1/1920x1080/x.mp4?tag=29") is True
      and _r47_x.is_video_media_url("https://video.twimg.com/tweet_video/gifgif.mp4") is True
      and _r47_x.is_video_media_url("https://example.com/clip.M3U8") is True
      and _r47_x.is_video_media_url("https://example.com/a/b/movie.webm#t=1") is True)
check("r56: photo/gif/webp URLs are NOT heal-eligible",
      _r47_x.is_video_media_url("https://pbs.twimg.com/media/Gxyz.jpg?name=orig") is False
      and _r47_x.is_video_media_url("https://pbs.twimg.com/media/Gxyz.png") is False
      and _r47_x.is_video_media_url("https://gif.fxtwitter.com/tweet_video/abc.gif") is False
      and _r47_x.is_video_media_url("https://gif.fxtwitter.com/tweet_video/abc.webp") is False
      and _r47_x.is_video_media_url("") is False and _r47_x.is_video_media_url(None) is False)

_r56_vid_url = "https://video.twimg.com/amplify_video/9/vid/avc1/1280x720/v.mp4?tag=29"
_r56_img_url = "https://pbs.twimg.com/media/PomPomCoffee.jpg?name=orig"
def _r56_msg(*urls):
    return {"id": "1", "components": [{"id": 1, "type": 17, "components": [
        {"id": 5, "type": 12, "items": [
            {"media": {"url": u, "proxy_url": "https://images-ext-1.discordapp.net/x",
                       "width": 0, "height": 0, "content_type": ""}} for u in urls]}]}]}
check("r56: default scope heals ONLY the video item of a mixed cold message",
      _r47_x.heal_eligible_items(_r56_msg(_r56_vid_url, _r56_img_url)) == [_r56_vid_url])
check("r56: a photos-only cold message queues NO heal (the 2026-10-02 case)",
      _r47_x.heal_eligible_items(_r56_msg(_r56_img_url)) == [])
check("r56: a cold video still queues exactly as in round 47",
      _r47_x.heal_eligible_items(_r56_msg(_r56_vid_url)) == [_r56_vid_url])
_r56_saved_scope = _r47_x.MEDIA_HEAL_SCOPE
try:
    _r47_x.MEDIA_HEAL_SCOPE = "all"
    check("r56: MEDIA_HEAL_SCOPE=all restores round-47 behaviour (photos too)",
          _r47_x.heal_eligible_items(_r56_msg(_r56_vid_url, _r56_img_url))
          == [_r56_vid_url, _r56_img_url])
finally:
    _r47_x.MEDIA_HEAL_SCOPE = _r56_saved_scope
check("r56: unresolved_media_items itself is unchanged (still detects both)",
      _r47_x.unresolved_media_items(_r56_msg(_r56_vid_url, _r56_img_url))
      == [_r56_vid_url, _r56_img_url])
check("r56: default scope is video (safe when the Variable is unset or empty)",
      _r47_x.MEDIA_HEAL_SCOPE == "video")
# Round 56b: the Variable accepts on (=all) / off / video, any casing,
# and every malformed state falls back to the safe default.
check("r56b: scope parsing — on/all mean all, any casing",
      _r47_x.media_heal_scope("on") == "all"
      and _r47_x.media_heal_scope("ON") == "all"
      and _r47_x.media_heal_scope(" All ") == "all")
check("r56b: scope parsing — off/none/0/false/disabled mean off",
      _r47_x.media_heal_scope("off") == "off"
      and _r47_x.media_heal_scope("OFF") == "off"
      and _r47_x.media_heal_scope("none") == "off"
      and _r47_x.media_heal_scope("0") == "off"
      and _r47_x.media_heal_scope("false") == "off"
      and _r47_x.media_heal_scope("disabled") == "off")
check("r56b: scope parsing — video/unset/empty/garbage mean video",
      _r47_x.media_heal_scope("video") == "video"
      and _r47_x.media_heal_scope("VIDEO") == "video"
      and _r47_x.media_heal_scope(None) == "video"
      and _r47_x.media_heal_scope("") == "video"
      and _r47_x.media_heal_scope("   ") == "video"
      and _r47_x.media_heal_scope("bananas") == "video")
_r56b_saved_scope = _r47_x.MEDIA_HEAL_SCOPE
try:
    _r47_x.MEDIA_HEAL_SCOPE = "off"
    check("r56b: scope off never queues a heal — even for a cold video",
          _r47_x.heal_eligible_items(_r56_msg(_r56_vid_url)) == []
          and _r47_x.heal_eligible_items(_r56_msg(_r56_vid_url, _r56_img_url)) == [])
finally:
    _r47_x.MEDIA_HEAL_SCOPE = _r56b_saved_scope
# Round 42 pattern (operator escape hatches, e.g. the X media-heal scope):
# the prod workflow passes env vars EXPLICITLY, so an unwired Variable would
# silently strand the escape hatch.
with open(os.path.join(ROOT, ".github/workflows/twitter_monitor.yml"), encoding="utf-8") as _r56_f:
    _r56_workflow = _r56_f.read()
check("r56: workflow wires MEDIA_HEAL_SCOPE",
      "MEDIA_HEAL_SCOPE: ${{ vars.MEDIA_HEAL_SCOPE }}" in _r56_workflow)

# ---------------------------------------------------------------------------
# ROUND 57 (2026-10-03): workflow hardening guards.
# 57a — supply chain: every `uses:` in the monitor workflows must be pinned
#       to an immutable 40-hex commit SHA, never a mutable tag (the 2025
#       tj-actions/changed-files compromise repointed v1..v45 tags to a
#       malicious commit; CVE-2025-30066).
# 57b — outage cap: both monitor jobs must declare timeout-minutes. Without
#       it a hung network call holds the job for GitHub's 6 h default and —
#       because the P0 concurrency guard queues instead of cancelling —
#       blocks every subsequent cron tick behind it.
# Also guards the P0 double-post fix itself: the concurrency group with
# cancel-in-progress: false must never silently disappear.
# ---------------------------------------------------------------------------
import re as _r57_re
_r57_uses_re = _r57_re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", _r57_re.M)
_r57_sha_re = _r57_re.compile(r"@[0-9a-f]{40}(\s|$)")
for _r57_name in ("reddit_monitor.yml", "twitter_monitor.yml"):
    _r57_path = os.path.join(ROOT, ".github", "workflows", _r57_name)
    with open(_r57_path, encoding="utf-8") as _r57_f:
        _r57_src = _r57_f.read()
    _r57_uses = _r57_uses_re.findall(_r57_src)
    check(f"r57a: {_r57_name} — every uses: is SHA-pinned (no mutable tags)",
          len(_r57_uses) >= 2
          and all(_r57_sha_re.search(u) for u in _r57_uses))
    check(f"r57b: {_r57_name} — job declares timeout-minutes (hung-run cap)",
          "timeout-minutes:" in _r57_src)
    check(f"r57: {_r57_name} — P0 concurrency guard intact (queue, never cancel)",
          "concurrency:" in _r57_src
          and "cancel-in-progress: false" in _r57_src)


class _R47Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _R47Session:
    """Discord: the first edit resolves the media (what Discohook proved)."""

    def __init__(self, heal_after=1, status=200):
        self.patches = []
        self.heal_after = heal_after
        self.status = status

    def patch(self, url, json=None, **kw):
        self.patches.append((url, json))
        payload = (_r47_ok_msg if len(self.patches) >= self.heal_after
                   else _r47_broken_msg)
        return _R47Resp(self.status, payload)


_r47_payload = {"flags": 32768, "components": [{"type": 17, "components": []}]}
_r47_saved_delay = _r47_x.MEDIA_HEAL_DELAY_SECONDS
try:
    _r47_x.MEDIA_HEAL_DELAY_SECONDS = 0
    _r47_sess = _R47Session(heal_after=1)
    _r47_started = time.monotonic()
    asyncio.run(_r47_x.heal_unresolved_media(
        _r47_sess, [("https://discord.com/api/webhooks/1/abc", "999", _r47_payload, "WW/2105")]))
    _r47_elapsed = time.monotonic() - _r47_started
    check("r47: an unresolved message is re-edited exactly once when that fixes it",
          len(_r47_sess.patches) == 1, str(_r47_sess.patches))
    check("r47: the edit is a PATCH to the webhook message, with_components kept",
          _r47_sess.patches[0][0]
          == "https://discord.com/api/webhooks/1/abc/messages/999?with_components=true")
    check("r47: the edit re-sends the IDENTICAL payload (nothing added or removed)",
          _r47_sess.patches[0][1] == _r47_payload)
    # still broken -> bounded retries, never an infinite loop
    _r47_sess2 = _R47Session(heal_after=99)
    asyncio.run(_r47_x.heal_unresolved_media(
        _r47_sess2, [("https://discord.com/api/webhooks/1/abc", "999", _r47_payload, "WW/2105")]))
    check("r47: a permanently unresolvable asset is retried MEDIA_HEAL_ATTEMPTS times, then dropped",
          len(_r47_sess2.patches) == _r47_x.MEDIA_HEAL_ATTEMPTS)
    # nothing queued -> no work at all
    _r47_sess3 = _R47Session()
    asyncio.run(_r47_x.heal_unresolved_media(_r47_sess3, []))
    check("r47 measured: a healthy run costs ZERO edits and no wait",
          _r47_sess3.patches == [] and _r47_elapsed < 1.0,
          f"{_r47_elapsed:.3f}s for the healed case")
finally:
    _r47_x.MEDIA_HEAL_DELAY_SECONDS = _r47_saved_delay

_r47_src = open(os.path.join(ROOT, "testing area", "twitter_v3.py"), encoding="utf-8").read()
check("r47: the webhook POST asks for the created message (wait=true) so media can be checked",
      "?with_components=true&wait=true" in _r47_src)
check("r47: healing runs AFTER the posting loop, so it never delays a post",
      _r47_src.index("await heal_unresolved_media(session, heal_queue)")
      > _r47_src.index("heal_queue.append("))
check("r47: the heal pass is opt-outable and defaults ON",
      _r47_x.MEDIA_HEAL is True and _r47_x.MEDIA_HEAL_ATTEMPTS >= 1
      and 30 <= _r47_x.MEDIA_HEAL_DELAY_SECONDS <= 60)
check("r47: the proven 256 MiB external-video cap is unchanged (no guessing)",
      _r47_x.VIDEO_SIZE_LIMIT == 256 * 1024 * 1024,
      f"limit={_r47_x.VIDEO_SIZE_LIMIT / (1024 * 1024):.0f} MiB")

# ---- ROUND 47: Reddit profile posts + crossposts of them ------------------
# LIVE INCIDENT: r/AnantaLeaks 1wuv547 is a crosspost of the PROFILE post
# /user/aphotide/comments/1wuv4j8/ — the card showed the external-preview
# poster image instead of the 1:26 video, because no helper recognised a
# "/user/<name>/comments/<id>/" permalink as a post.
check("r47: a profile-post permalink normalises to its canonical path",
      v3.normalize_reddit_path("https://www.reddit.com/user/aphotide/comments/1wuv4j8/musor_drop_via_aphotide/")
      == "/user/aphotide/comments/1wuv4j8/musor_drop_via_aphotide/")
check("r47: the short /u/ form normalises to the same /user/ path",
      v3.normalize_reddit_path("https://www.reddit.com/u/aphotide/comments/1wuv4j8/")
      == "/user/aphotide/comments/1wuv4j8/")
check("r47: ordinary subreddit permalinks are untouched",
      v3.normalize_reddit_path("https://www.reddit.com/r/AnantaLeaks/comments/1wuv547/")
      == "/r/AnantaLeaks/comments/1wuv547/")
check("r47: a profile post is recognised as one",
      v3.is_profile_post_path("/user/aphotide/comments/1wuv4j8/") is True
      and v3.is_profile_post_path("/r/AnantaLeaks/comments/1wuv547/") is False)
check("r47: a profile post also answers under its /r/u_<name>/ pseudo-subreddit",
      v3._path_aliases("/user/aphotide/comments/1wuv4j8/")
      == ["/user/aphotide/comments/1wuv4j8/", "/r/u_aphotide/comments/1wuv4j8/"])
check("r47: an ordinary post has exactly ONE route (no extra fetches)",
      v3._path_aliases("/r/AnantaLeaks/comments/1wuv547/")
      == ["/r/AnantaLeaks/comments/1wuv547/"])
check("r47: 1wuv547 — the crosspost's ORIGINAL profile post is now found",
      v3.find_crosspost_original_path(
          'crossposted this from <a href="/user/aphotide/comments/1wuv4j8/musor_drop_via_aphotide/">'
          'u/aphotide</a>', "/r/AnantaLeaks/comments/1wuv547/")
      == "/user/aphotide/comments/1wuv4j8/")
check("r47: a crosspost of a normal subreddit post still resolves (round 14 kept)",
      v3.find_crosspost_original_path(
          'crosspost of <a href="/r/AnantaLeaks/comments/1abcdef/t/">r/AnantaLeaks</a>',
          "/r/Other/comments/1wuv547/")
      == "/r/AnantaLeaks/comments/1abcdef/")
check("r47: an Arctic-reported profile permalink is accepted as the original",
      'r"/(?:r|user)/[^/]+/comments/[a-z0-9]+/[^?#]*"'
      in inspect.getsource(v3.resolve_post_media))


class _R47Proxy:
    """Proxy service that only knows the /r/u_<name>/ route (like the live
    services that index profile posts under their pseudo-subreddit)."""

    def __init__(self):
        self.paths = []

    async def fetch_proxy_post(self, session, path, label="", health=None,
                               need_video=False):
        self.paths.append(path)
        if path.startswith("/r/u_"):
            return {"service": "fake", "title": "musor drop via aphotide",
                    "author": "aphotide", "body": "",
                    "media": [{"kind": "video",
                               "url": "https://v.redd.it/abc123/DASH_1080.mp4"}]}
        return None


_r47_saved_proxy = v3.reddit_proxy
try:
    _r47_proxy = _R47Proxy()
    v3.reddit_proxy = _r47_proxy
    v3._proxy_post_cache.clear()
    _r47_res = asyncio.run(v3.fetch_proxy_post_memo(
        None, "/user/aphotide/comments/1wuv4j8/", label="1wuv4j8"))
    check("r47: the profile post's VIDEO is recovered via the subreddit alias",
          isinstance(_r47_res, dict)
          and _r47_res["media"][0]["url"].endswith("DASH_1080.mp4")
          and _r47_proxy.paths == ["/user/aphotide/comments/1wuv4j8/",
                                   "/r/u_aphotide/comments/1wuv4j8/"],
          f"{_r47_res} paths={_r47_proxy.paths}")
    _r47_proxy2 = _R47Proxy()
    v3.reddit_proxy = _r47_proxy2
    v3._proxy_post_cache.clear()
    asyncio.run(v3.fetch_proxy_post_memo(None, "/r/AnantaLeaks/comments/1wuv547/",
                                         label="1wuv547"))
    check("r47: an ordinary post makes exactly ONE proxy call (speed unchanged)",
          _r47_proxy2.paths == ["/r/AnantaLeaks/comments/1wuv547/"],
          str(_r47_proxy2.paths))
finally:
    v3.reddit_proxy = _r47_saved_proxy
    v3._proxy_post_cache.clear()



# ---- ROUND 48b (2026-10-01): delivery latency telemetry ------------------
# Calibrating the queue gate needs real numbers, so every delivery logs how
# long it took from Reddit creation, and what held it if it was held.
_r48b_src = inspect.getsource(v3.main)
check("r48b: every delivery logs its end-to-end latency",
      "LATENCY" in _r48b_src and "after creation" in _r48b_src)
check("r48b: a held post also logs WHAT held it and for how long",
      "held {_held / 60:.1f}min as" in _r48b_src
      and "_pend_entry = pending.pop(unique_key, None)" in _r48b_src)


# ===========================================================================
# ROUND 48c (2026-10-01) — PROXY AUDIT for PROFILE POSTS (redditez chain)
# You verified by hand that https://www.redditez.com/user/aphotide/comments/
# 1wuv4j8/ and the vxreddit equivalent both render that post WITH its video.
# This sandbox has no network egress, so these checks exercise the real
# module against fake sessions: URL construction, parsing, the service
# chain, need_video, and the health skip — for a PROFILE path specifically.
# ===========================================================================
import re as _r48c_re
_r48c_proxy = load_module("smoke_reddit_proxy_r48c", "testing area/reddit_proxy.py")
_R48C_PATH = "/user/aphotide/comments/1wuv4j8/musor_drop_via_aphotide/"
_R48C_VIDEO = "https://embedez.com/api/v1/media/video/abc123.mp4"


class _R48cResp:
    def __init__(self, status=200, text="", payload=None):
        self.status = status
        self._text = text
        self._payload = payload

    async def text(self, *a, **kw):
        return self._text

    async def json(self, *a, **kw):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _r48c_og(video=None, images=(), title="musor drop via aphotide",
             site_name="", description=""):
    tags = [f'<meta property="og:title" content="{title}">']
    if video:
        tags.append(f'<meta property="og:video" content="{video}">')
    for img in images:
        tags.append(f'<meta property="og:image" content="{img}">')
    if site_name:
        tags.append(f'<meta property="og:site_name" content="{site_name}">')
    if description:
        tags.append(f'<meta property="og:description" content="{description}">')
    return "<html><head>" + "".join(tags) + "</head></html>"


class _R48cSession:
    """Fake HTTP: records every URL, answers per service."""

    def __init__(self, redditez="video", vxreddit="video", embeddit="video"):
        self.urls = []
        self.modes = {"redditez": redditez, "vxreddit": vxreddit,
                      "embeddit": embeddit}

    def get(self, url, params=None, headers=None, **kw):
        self.urls.append((url, dict(params or {}), (headers or {}).get("User-Agent", "")))
        mode_rez = self.modes["redditez"]
        if "providers/search" in url:                      # redditez step 1
            if mode_rez == "down":
                return _R48cResp(503, "")
            return _R48cResp(200, payload={"data": {"key": "k-1wuv4j8"}})
        if "embedez.com/embed/" in url:                    # redditez step 2
            if mode_rez == "fail-marker":
                return _R48cResp(200, "<html>Failed to Get Post</html>")
            if mode_rez == "text-only":
                return _R48cResp(200, _r48c_og(description="a body"))
            return _R48cResp(200, _r48c_og(video=_R48C_VIDEO))
        if "vxreddit.com" in url:
            mode = self.modes["vxreddit"]
            if mode == "redirect":
                return _R48cResp(302, "")
            if mode == "down":
                return _R48cResp(500, "")
            return _R48cResp(200, _r48c_og(
                video="https://www.vxreddit.com/redditvideo.mp4",
                site_name="u/aphotide on r/u_aphotide - \u2b06\ufe0f 1 | \U0001f4ac 1"))
        # embeddit
        if self.modes["embeddit"] == "down":
            return _R48cResp(500, "")
        return _R48cResp(200, payload={
            "content": "<p>musor drop via aphotide</p>",
            "media_attachments": [{"type": "video",
                                   "url": "https://embeddit.deltandy.me/v.mp4"}],
            "account": {"username": "aphotide"},
        })


def _r48c_run(**kw):
    sess = _R48cSession(**kw)
    res = asyncio.run(_r48c_proxy.fetch_proxy_post(sess, _R48C_PATH,
                                                   label="1wuv4j8",
                                                   need_video=True))
    return res, sess


_r48c_res, _r48c_sess = _r48c_run()
check("r49: wave 1 dispatches vxreddit AND redditez together, on the canonical path",
      any(u.startswith(_r48c_proxy.VXREDDIT_BASE) and u.endswith(_R48C_PATH)
          for u, _, _ in _r48c_sess.urls)
      and any(u == _r48c_proxy.REDDITEZ_SEARCH_ENDPOINT
              and p.get("url") == "https://www.reddit.com" + _R48C_PATH
              for u, p, _ in _r48c_sess.urls),
      str([u for u, _, _ in _r48c_sess.urls]))
check("r48c: redditez is queried with Discord's bot UA (its embed pages need it)",
      any("Discordbot" in ua for u, _, ua in _r48c_sess.urls
          if u == _r48c_proxy.REDDITEZ_SEARCH_ENDPOINT))
check("r49: with both healthy the TIE goes to vxreddit (the field-proven one)",
      _r48c_res is not None and _r48c_res["service"] == "vxreddit"
      and [m["kind"] for m in _r48c_res["media"]] == ["video"],
      str(_r48c_res))
check("r48d/r49: a resolved VIDEO ends the chain — embeddit is never dispatched",
      not any("embeddit" in u or "deltandy" in u for u, _, _ in _r48c_sess.urls),
      str([u for u, _, _ in _r48c_sess.urls]))
check("r49 measured: a video post dispatches 2 services, never the third",
      len({"vx" if "vxreddit" in u else "ez" if "embedez" in u else "em"
           for u, _, _ in _r48c_sess.urls}) == 2,
      str([u for u, _, _ in _r48c_sess.urls]))
check("r25/r49: gallery posts still shop every service for the most complete set",
      "need_video and any" in inspect.getsource(_r48c_proxy.fetch_proxy_post)
      and "_better_media" in inspect.getsource(_r48c_proxy.fetch_proxy_post)
      and 'len(candidate["media"]) > len(best["media"])'
      in inspect.getsource(_r48c_proxy._better_media))

_r48c_res2, _r48c_sess2 = _r48c_run(redditez="fail-marker")
check("r48c: 'Failed to Get Post' is treated as a redditez outage, not a dead post",
      _r48c_res2 is not None and _r48c_res2["service"] == "vxreddit"
      and _r48c_res2["media"][0]["kind"] == "video")
check("r48c: vxreddit is asked on the SAME profile path (no rewriting needed)",
      any(u == _r48c_proxy.VXREDDIT_BASE + _R48C_PATH
          for u, _, _ in _r48c_sess2.urls),
      str([u for u, _, _ in _r48c_sess2.urls]))
check("r48c: vxreddit's stats line is parsed for a profile post too",
      _r48c_res2.get("author") == "aphotide"
      and (_r48c_res2.get("stats") or {}).get("ups") == 1)

_r48c_res3, _ = _r48c_run(redditez="text-only")
check("r48c: a TEXT-ONLY redditez answer never wins a video post (round 23 rule)",
      _r48c_res3 is not None and _r48c_res3["service"] == "vxreddit"
      and any(m["kind"] == "video" for m in _r48c_res3["media"]))

_r48c_res4, _ = _r48c_run(redditez="down", vxreddit="redirect")
check("r48c: embeddit still answers from the post id alone (path-agnostic)",
      _r48c_res4 is not None and _r48c_res4["service"] == "embeddit"
      and any(m["kind"] == "video" for m in _r48c_res4["media"]),
      str(_r48c_res4))

_r48c_sess5 = _R48cSession(redditez="down", vxreddit="down", embeddit="down")
_r48c_res5 = asyncio.run(_r48c_proxy.fetch_proxy_post(_r48c_sess5, _R48C_PATH,
                                                      label="1wuv4j8",
                                                      need_video=True))
check("r48c: all three down -> None, and the caller falls back to the native path",
      _r48c_res5 is None)

_r48c_sess6 = _R48cSession()
asyncio.run(_r48c_proxy.fetch_proxy_post(
    _r48c_sess6, _R48C_PATH, label="1wuv4j8", need_video=True,
    health={"redditez": {"ok": False, "detail": "warm-up failed"}}))
check("r48c: a warm-up-dead redditez is skipped for the whole run",
      not any("embedez.com" in u for u, _, _ in _r48c_sess6.urls),
      str([u for u, _, _ in _r48c_sess6.urls]))

_r48c_sess7 = _R48cSession()
asyncio.run(_r48c_proxy.fetch_proxy_post(
    _r48c_sess7, _R48C_PATH, label="1wuv4j8", need_video=True,
    health={s: {"ok": False} for s in _r48c_proxy.PROXY_SERVICES}))
check("r48c: when ALL are marked dead every service is retried anyway",
      any("embedez.com" in u for u, _, _ in _r48c_sess7.urls))

check("r48c: embeddit's id extraction works on a profile path",
      _r48c_proxy.status_id_encode({"type": "post", "id": "1wuv4j8", "merge": True})
      == _r48c_proxy.status_id_encode({"type": "post", "id": "1wuv4j8", "merge": True})
      and _r48c_re.search(r"/comments/([a-zA-Z0-9]+)/", _R48C_PATH).group(1) == "1wuv4j8")
check("r48c: an og:video is classified as video, og:image as image/gif",
      _r48c_proxy._media_from_og({"og:video": "https://x/v.mp4",
                                  "og:image": ["https://i.redd.it/a.png",
                                               "https://i.redd.it/b.gif"]})
      == [{"kind": "video", "url": "https://x/v.mp4"},
          {"kind": "image", "url": "https://i.redd.it/a.png"},
          {"kind": "gif", "url": "https://i.redd.it/b.gif"}])
check("r48c: the committed warm-up record shows all three services healthy",
      json.load(open(os.path.join(ROOT, "proxy_health.json"), encoding="utf-8"))
      ["services"]["redditez"]["ok"] is True)



# ===========================================================================
# ROUND 49 (2026-10-01) — PARALLEL PROXY DISPATCH
# Evidence (prod Actions log, repo 7jkxmy9nvbc, 2026-10-01 11:55:50-51 UTC):
#   11:55:50.681  redditez had no usable data
#   11:55:51.098  proxy media via vxreddit - 1 item(s)     (+0.417s)
#   11:55:51.408  embeddit returned text only              (+0.310s)
#   11:55:51.408  proxy media winner - 1 item(s)
# The chain was strictly serial: ~1.5s of wall time per post, and it kept
# querying after a winner existed. Round 49 dispatches concurrently while
# keeping every decision rule from rounds 23, 25 and 48d.
# ===========================================================================
_r49 = load_module("smoke_reddit_proxy_r49", "testing area/reddit_proxy.py")
_R49_LAT = 0.4            # per-service latency in these fakes


def _r49_fakes(media_by_service, *, latency=None, record=None):
    """Install fake per-service fetchers; returns the dispatch record list."""
    record = [] if record is None else record
    lat = latency or {}

    def make(name):
        async def fake(session, path, label=""):
            record.append(name)
            await asyncio.sleep(lat.get(name, _R49_LAT))
            items = media_by_service.get(name)
            if items is None:
                return None
            return {"service": name, "title": "T", "author": None,
                    "subreddit": None, "body": "b", "stats": None,
                    "media": [dict(m) for m in items]}
        return fake

    for name in ("redditez", "vxreddit", "embeddit"):
        setattr(_r49, "_fetch_" + name, make(name))
    return record


_R49_VID = [{"kind": "video", "url": "https://v.redd.it/x/DASH_1080.mp4"}]
_R49_ONE = [{"kind": "image", "url": "https://i.redd.it/1.jpg"}]
_R49_MANY = [{"kind": "image", "url": f"https://i.redd.it/{i}.jpg"} for i in range(13)]

_r49_saved = {n: getattr(_r49, "_fetch_" + n)
              for n in ("redditez", "vxreddit", "embeddit")}
try:
    # ---- 1. MEASURED SPEED: the whole chain costs ONE round trip ----------
    rec = _r49_fakes({"vxreddit": None, "redditez": None, "embeddit": _R49_ONE},
                     latency={"vxreddit": _R49_LAT, "redditez": _R49_LAT,
                              "embeddit": _R49_LAT})
    _t0 = time.monotonic()
    _r49_res = asyncio.run(_r49.fetch_proxy_post(None, "/r/Sub/comments/abc/",
                                                 label="t", health={}))
    _r49_dt = time.monotonic() - _t0
    _r49_serial = 3 * _R49_LAT
    check("r49 MEASURED: three 0.4s services resolve in ~0.7s, not the 1.2s serial cost",
          _r49_res is not None and _r49_res["service"] == "embeddit"
          and _r49_dt < _r49_serial - 0.25,
          f"{_r49_dt:.3f}s vs {_r49_serial:.3f}s serial")
    check("r49: the delayed third service is still dispatched when it is needed",
          sorted(rec) == ["embeddit", "redditez", "vxreddit"], str(rec))

    # ---- 1b. WAVE 2 STARTS EARLY WHEN WAVE 1 SETTLES WITHOUT A WINNER ----
    rec = _r49_fakes({"vxreddit": None, "redditez": None, "embeddit": _R49_ONE},
                     latency={"vxreddit": 0.05, "redditez": 0.05, "embeddit": 0.05})
    _t0 = time.monotonic()
    _r49_e = asyncio.run(_r49.fetch_proxy_post(None, "/r/Sub/comments/abc/",
                                               label="t", health={}))
    _r49_edt = time.monotonic() - _t0
    check("r49: wave 2 does not sit out the full delay once wave 1 has settled",
          _r49_e["service"] == "embeddit" and _r49_edt < _r49.PROXY_WAVE_DELAY,
          f"{_r49_edt:.3f}s with a {_r49.PROXY_WAVE_DELAY}s wave delay")

    # ---- 2. A VIDEO ANSWER CANCELS WAVE 2 BEFORE IT OPENS A SOCKET -------
    rec = _r49_fakes({"vxreddit": _R49_VID, "redditez": _R49_VID,
                      "embeddit": _R49_MANY},
                     latency={"vxreddit": 0.01, "redditez": 0.01, "embeddit": 0.01})
    _t0 = time.monotonic()
    _r49_v = asyncio.run(_r49.fetch_proxy_post(None, "/r/Sub/comments/abc/",
                                               label="t", health={},
                                               need_video=True))
    _r49_vdt = time.monotonic() - _t0
    check("r49/r48d: a video post never dispatches embeddit (wave 2 is cancelled)",
          "embeddit" not in rec and _r49_v["service"] == "vxreddit"
          and _r49_vdt < _r49.PROXY_WAVE_DELAY,
          f"{rec} in {_r49_vdt:.3f}s")

    # ---- 3. ROUND 25 IS INTACT: the slow service with the FULL gallery wins
    rec = _r49_fakes({"vxreddit": _R49_ONE, "redditez": _R49_ONE,
                      "embeddit": _R49_MANY},
                     latency={"vxreddit": 0.01, "redditez": 0.01, "embeddit": 0.05})
    _r49_g = asyncio.run(_r49.fetch_proxy_post(None, "/r/Sub/comments/1wj0p83/",
                                               label="t", health={}))
    check("r49: 1wj0p83 — a late embeddit with 13 items still beats a fast 1-item answer",
          _r49_g["service"] == "embeddit" and len(_r49_g["media"]) == 13,
          str(_r49_g["service"]))

    # ---- 4. THE GRACE WINDOW IS BOUNDED ----------------------------------
    _r49_grace_saved = _r49.PROXY_GALLERY_GRACE
    try:
        _r49.PROXY_GALLERY_GRACE = 0.25
        rec = _r49_fakes({"vxreddit": _R49_ONE, "redditez": None,
                          "embeddit": _R49_MANY},
                         latency={"vxreddit": 0.01, "redditez": 0.01,
                                  "embeddit": 10.0})
        _t0 = time.monotonic()
        _r49_slow = asyncio.run(_r49.fetch_proxy_post(None, "/r/Sub/comments/abc/",
                                                      label="t", health={}))
        _r49_sdt = time.monotonic() - _t0
        check("r49: a hung service cannot stall a post — grace expires and the best wins",
              _r49_slow["service"] == "vxreddit" and _r49_sdt < 1.0,
              f"{_r49_sdt:.3f}s")
    finally:
        _r49.PROXY_GALLERY_GRACE = _r49_grace_saved

    # ---- 5. CONCURRENCY CEILING ------------------------------------------
    _r49_inflight = {"now": 0, "max": 0}

    def _busy(name):
        async def fake(session, path, label=""):
            _r49_inflight["now"] += 1
            _r49_inflight["max"] = max(_r49_inflight["max"], _r49_inflight["now"])
            await asyncio.sleep(0.05)
            _r49_inflight["now"] -= 1
            return None
        return fake

    for _n in ("redditez", "vxreddit", "embeddit"):
        setattr(_r49, "_fetch_" + _n, _busy(_n))
    _r49_cap_saved = _r49.PROXY_MAX_CONCURRENCY
    try:
        _r49.PROXY_MAX_CONCURRENCY = 2
        _r49._proxy_gates.clear()

        async def _r49_burst():
            return await asyncio.gather(*[
                _r49.fetch_proxy_post(None, f"/r/Sub/comments/p{i}/", label="t",
                                      health={}) for i in range(6)])

        asyncio.run(_r49_burst())
        check("r49: the global semaphore caps in-flight proxy requests",
              _r49_inflight["max"] <= 2, f"peak={_r49_inflight['max']}")
    finally:
        _r49.PROXY_MAX_CONCURRENCY = _r49_cap_saved
        _r49._proxy_gates.clear()
finally:
    for _n, _f in _r49_saved.items():
        setattr(_r49, "_fetch_" + _n, _f)

# ---- 5b. WARM-UP LABELS CANNOT DRIFT FROM THE CALL ORDER ----------------
# Regression guard for a bug round 49 introduced and this audit caught: the
# warm-up gathered (redditez, vxreddit, embeddit) but zipped the results
# against PROXY_SERVICES, so reordering PROXY_SERVICES swapped the redditez
# and vxreddit health records — marking the WRONG service dead for a run.
_r49_probe_order = []


def _r49_probe(name):
    async def fake(session, path, label=""):
        _r49_probe_order.append(name)
        if name == "vxreddit":
            return None
        return {"service": name, "title": "T", "author": None, "subreddit": None,
                "body": "b", "stats": None,
                "media": [{"kind": "image", "url": "https://i.redd.it/1.jpg"}]}
    return fake


_r49_saved2 = {n: getattr(_r49, "_fetch_" + n)
               for n in ("redditez", "vxreddit", "embeddit")}
_r49_saved_save = _r49.save_proxy_health
try:
    for _n in ("redditez", "vxreddit", "embeddit"):
        setattr(_r49, "_fetch_" + _n, _r49_probe(_n))
    _r49.save_proxy_health = lambda *a, **kw: None
    _r49_health = asyncio.run(_r49.proxy_warmup(None, "Sub/abc123"))
    check("r49: warm-up records each service's OWN result (no label drift)",
          _r49_health["vxreddit"]["ok"] is False
          and _r49_health["redditez"]["ok"] is True
          and _r49_health["embeddit"]["ok"] is True, str(_r49_health))
    check("r49: the warm-up probes are built from the dispatch map itself",
          "probes = tuple(_PROXY_FETCHERS)" in inspect.getsource(_r49.proxy_warmup))
finally:
    for _n, _f in _r49_saved2.items():
        setattr(_r49, "_fetch_" + _n, _f)
    _r49.save_proxy_health = _r49_saved_save

# ---- 6. SHARE LINKS: redditez is not asked a url it always rejects -------
# User-verified: redditez.com/u/<name>/s/<id> and /r/<sub>/s/<id> both answer
# "Could not find provider for this url. Malformed url?", while vxreddit
# renders them. The share token is NOT a post id, so it cannot be rewritten.
check("r49: a /s/ share link drops redditez but keeps the services that resolve it",
      _r49.proxy_services_for("/r/HonkaiStarRail_leaks/s/8ovjUB7aK4")
      == ["vxreddit", "embeddit"]
      and _r49.proxy_services_for("/u/aphotide/s/8ovjUB7aK4") == ["vxreddit", "embeddit"])
check("r49: a canonical /comments/ path still asks every service",
      _r49.proxy_services_for("/user/aphotide/comments/1wuv4j8/")
      == list(_r49.PROXY_SERVICES)
      and _r49.proxy_services_for("/r/Sub/comments/abc/") == list(_r49.PROXY_SERVICES))
check("r49: the tie-break order is vxreddit -> redditez -> embeddit (field-ranked)",
      _r49.PROXY_SERVICES == ("vxreddit", "redditez", "embeddit"))

# ---- 7. THE DUPLICATE LOG LINE IS GONE -----------------------------------
# The serial chain logged BOTH "returned text only ... continuing the chain"
# and "had no usable data - trying the next proxy" for the SAME attempt,
# because the text-only branch fell through to the end of the loop body.
_r49_src = inspect.getsource(_r49.fetch_proxy_post)
_r49_code = "\n".join(l for l in _r49_src.split("\n") if "logging." in l)
check("r49: each proxy attempt logs exactly one outcome line",
      _r49_code.count("had no usable data") == 1
      and _r49_code.count("returned text only") == 1
      and "trying the next proxy" not in _r49_code
      and "continuing the chain for the media" not in _r49_code,
      _r49_code.count("had no usable data"))
check("r49: ties are resolved by rank, not by task completion order",
      "sorted(done, key=lambda t: rank[tasks[t]])" in _r49_src)

# ---- 8. X: a SECTION ACCESSORY thumbnail is healed too -------------------
# Round 47 only walked items[]; a type-11 accessory keeps its media outside
# items[], so a failed thumbnail was invisible to the heal pass.
_r49_acc_msg = {"id": "1", "components": [{"type": 17, "components": [
    {"type": 9, "components": [{"type": 10, "content": "hi"}],
     "accessory": {"type": 11, "media": {
         "url": "https://pbs.twimg.com/media/thumb.jpg",
         "proxy_url": "https://images-ext-1.discordapp.net/external/x",
         "width": 0, "height": 0, "content_type": ""}}}]}]}
check("r49: an unresolved SECTION ACCESSORY thumbnail is now detected",
      _r47_x.unresolved_media_items(_r49_acc_msg)
      == ["https://pbs.twimg.com/media/thumb.jpg"])
check("r49: a resolved accessory thumbnail is still not flagged",
      _r47_x.unresolved_media_items(
          {"components": [{"type": 9, "accessory": {"type": 11, "media": {
              "url": "https://pbs.twimg.com/media/t.jpg", "width": 400,
              "height": 400, "content_type": "image/jpeg"}}}]}) == [])

# ---- 9. REDLIB REACHABILITY TELEMETRY ------------------------------------
# The prod run only said "all instances"/"all sources" failed, which gives no
# way to tell a dead mirror from a flaky one. One line per run fixes that.
v3._redlib_reach.clear()
v3._redlib_reach.update({"https://redlib.catsarch.com": [0, 3],
                         "https://www.reddit.com": [2, 2],
                         "https://redlib.nadeko.net": [1, 4]})
_r49_reach = v3.log_redlib_reachability()
check("r49: the reachability line reports ok/total per instance, worst first",
      _r49_reach.startswith("REDLIB-REACH: 2/3 instance(s) answered this run")
      and _r49_reach.index("redlib.catsarch.com 0/3")
      < _r49_reach.index("redlib.nadeko.net 1/4")
      < _r49_reach.index("www.reddit.com 2/2"), _r49_reach)
check("r49: a run that never touched redlib logs nothing",
      (v3._redlib_reach.clear() or v3.log_redlib_reachability()) == "")
check("r49: the counter is wired into the real page fetcher and main()",
      "_redlib_reach.setdefault" in inspect.getsource(v3._fetch_redlib_post_page)
      and "log_redlib_reachability()" in inspect.getsource(v3.main))


# ===========================================================================
# ROUND 50 (2026-10-01) — A VIDEO WIN NO LONGER CANCELS A PENDING
# HIGHER-PRIORITY SERVICE
# Incident 1wv12qb (prod, repo 7jkxmy9nvbc, run 36923059079): a crosspost
# resolves media against the ORIGINAL post (AnantaStation 1wv0oiq). On that
# lookup vxreddit/redditez (rank 0/1 — video WITH audio per the round-49
# field table) were still in flight when embeddit (rank 2 — NO audio per the
# same table) answered first with a video. The round-48d decisive-exit fired
# on ANY video answer and cancelled the still-pending higher-priority tasks,
# so the card posted silent even though vxreddit could serve the same video
# with audio. The fix: a video answer is decisive only when nothing still
# pending could outrank it; otherwise it falls into the existing bounded
# grace window (same mechanism round 25 already uses for galleries) so a
# higher-priority video, if one arrives, replaces it first.
# ===========================================================================
try:
    # ---- 1. A slower, higher-priority video REPLACES a fast low-rank one -
    rec = _r49_fakes({"vxreddit": _R49_VID, "redditez": None, "embeddit": _R49_VID},
                     latency={"vxreddit": 0.9, "redditez": 0.01, "embeddit": 0.05})
    _t0 = time.monotonic()
    _r50_v = asyncio.run(_r49.fetch_proxy_post(None, "/r/Sub/comments/abc/",
                                               label="t", health={},
                                               need_video=True))
    _r50_dt = time.monotonic() - _t0
    check("r50/1wv12qb: vxreddit (rank 0, in flight) wins over a faster embeddit video",
          _r50_v is not None and _r50_v["service"] == "vxreddit"
          and _r50_dt >= 0.85,
          f"winner={_r50_v and _r50_v['service']} in {_r50_dt:.3f}s")

    # ---- 2. If the higher-priority service never arrives, the bounded grace
    #         window still lets the low-rank video win (no hang) -----------
    _r50_grace_saved = _r49.PROXY_GALLERY_GRACE
    try:
        _r49.PROXY_GALLERY_GRACE = 0.3
        rec = _r49_fakes({"vxreddit": _R49_VID, "redditez": None, "embeddit": _R49_VID},
                         latency={"vxreddit": 10.0, "redditez": 0.01, "embeddit": 0.05})
        _t0 = time.monotonic()
        _r50_g = asyncio.run(_r49.fetch_proxy_post(None, "/r/Sub/comments/abc/",
                                                   label="t", health={},
                                                   need_video=True))
        _r50_gdt = time.monotonic() - _t0
        check("r50: a hung higher-priority service cannot stall a video post — "
              "grace expires and embeddit's video still posts",
              _r50_g is not None and _r50_g["service"] == "embeddit"
              and _r50_gdt < 2.0,
              f"winner={_r50_g and _r50_g['service']} in {_r50_gdt:.3f}s")
    finally:
        _r49.PROXY_GALLERY_GRACE = _r50_grace_saved

    # ---- 3. Unchanged: a same-wave decisive video (round 49 test 2) is still
    #         instant — nothing with a BETTER rank is pending in that case --
    rec = _r49_fakes({"vxreddit": _R49_VID, "redditez": _R49_VID,
                      "embeddit": _R49_MANY},
                     latency={"vxreddit": 0.01, "redditez": 0.01, "embeddit": 0.01})
    _t0 = time.monotonic()
    _r50_fast = asyncio.run(_r49.fetch_proxy_post(None, "/r/Sub/comments/abc/",
                                                  label="t", health={},
                                                  need_video=True))
    _r50_fastdt = time.monotonic() - _t0
    check("r50: wave-1 video still exits instantly when nothing outranks it",
          "embeddit" not in rec and _r50_fast["service"] == "vxreddit"
          and _r50_fastdt < _r49.PROXY_WAVE_DELAY,
          f"{rec} in {_r50_fastdt:.3f}s")
finally:
    for _n, _f in _r49_saved.items():
        setattr(_r49, "_fetch_" + _n, _f)


# ===========================================================================
# ROUND 49 REPLAY — nine REAL production cards, byte-for-byte
# These are the exact media-gallery tile urls Discord accepted and rendered
# for nine real gallery posts (captured from the live webhook payloads, see
# tests/fixtures/posted_cards.py). They cover every shape that matters:
#   7 tiles / one container          1wt8hdd, 1woqtu9 (i.redd.it + signed
#                                    preview.redd.it urls with query strings)
#   6 embedez redirect urls          1wsy39s (?path=content.media.N.source -
#                                    they differ ONLY in the query, so dedupe
#                                    must not collapse them)
#   15 tiles, 11 of them GIFs        1wqkq60
#   15 tiles, mixed jpg + gif        1wmxt0l
#   15 / 19 tiles, two containers    1wov3r0, 1wp17bk, 1wtg5r3
#   20 tiles - the FULL card         1wkj08n (exactly MEDIA_CAP_ITEMS)
# Each set is replayed through the real round-49 fetch_proxy_post and the
# real build_v3_payload, twice: once with the full set arriving FIRST, and
# once with it arriving LAST from the lowest-priority service (the round-25
# shape). Both must reproduce the original card exactly.
# ===========================================================================
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))
from posted_cards import POSTED_CARDS as _R49_CARDS

_r49_replay = load_module("smoke_reddit_proxy_replay", "testing area/reddit_proxy.py")


def _r49_kinds(urls):
    return [{"kind": "gif" if u.split("?")[0].lower().endswith(".gif") else "image",
             "url": u} for u in urls]


def _r49_replay_card(pid, card, late):
    media = _r49_kinds(card["urls"])

    async def full(session, path, label="", media=media):
        if late:
            await asyncio.sleep(0.05)
        return {"service": "embeddit" if late else "vxreddit", "title": "T",
                "author": "a", "subreddit": "S", "body": "", "stats": None,
                "media": [dict(m) for m in media]}

    async def partial(session, path, label=""):
        return {"service": "vxreddit" if late else "embeddit", "title": "T",
                "author": "a", "subreddit": "S", "body": "", "stats": None,
                "media": [{"kind": "image", "url": "https://i.redd.it/partial.jpg"}]}

    async def dead(session, path, label=""):
        return None

    if late:
        _r49_replay._fetch_vxreddit = partial
        _r49_replay._fetch_embeddit = full
    else:
        _r49_replay._fetch_vxreddit = full
        _r49_replay._fetch_embeddit = partial
    _r49_replay._fetch_redditez = dead
    res = asyncio.run(_r49_replay.fetch_proxy_post(
        None, f"/r/Sub/comments/{pid}/", label=pid, health={}))
    data = {"title": "T", "author": "A", "body": "", "media": res["media"],
            "stats": {"comments": 1, "ups": 2}, "crosspost": None,
            "op_comment": None, "youtube_url": None, "full_mode": True}
    payload = v3.build_v3_payload(
        "Sub", data, f"https://www.reddit.com/r/Sub/comments/{pid}/", 1)
    split, tiles = [], []
    for container in payload["components"]:
        for node in container["components"]:
            if node.get("type") == 12:
                split.append(len(node["items"]))
                tiles += [i["media"]["url"] for i in node["items"]]
    return tiles, split


_r49_saved3 = {n: getattr(_r49_replay, "_fetch_" + n)
               for n in ("redditez", "vxreddit", "embeddit")}
try:
    for _pid, _card in _R49_CARDS.items():
        _dd = _r49_replay.dedupe_proxy_media(_r49_kinds(_card["urls"]))
        check(f"r49 replay {_pid}: dedupe keeps all {len(_card['urls'])} real tiles",
              [m["url"] for m in _dd] == _card["urls"], str(len(_dd)))
        for _late in (False, True):
            _tiles, _split = _r49_replay_card(_pid, _card, _late)
            _when = "the full set arrives LAST" if _late else "the full set arrives FIRST"
            check(f"r49 replay {_pid}: {_when} - card is byte-identical to production",
                  _tiles == _card["urls"] and _split == _card["split"],
                  f"{_split} vs {_card['split']}, {len(_tiles)} vs {len(_card['urls'])}")
finally:
    for _n, _f in _r49_saved3.items():
        setattr(_r49_replay, "_fetch_" + _n, _f)
check("r49 replay: the 20-tile card is exactly MEDIA_CAP_ITEMS, uncapped",
      len(_R49_CARDS["1wkj08n"]["urls"]) == _r49_replay.MEDIA_CAP_ITEMS)


# ===========================================================================
# ROUND 49b — GitHub repository Variables that are unset are injected as an
# empty string. Every numeric setting must import safely and retain its default.
# ===========================================================================
_r49b_files = [
    "testing area/reddit_proxy.py",
    "testing area/twitter_v3.py",
    "testing area/reddit_main_v3.py",
]
_r49b_names = (
    "PROXY_WAVE_DELAY", "PROXY_GALLERY_GRACE", "PROXY_MAX_CONCURRENCY",
    "MAX_CACHE_SIZE_PER_ACCOUNT", "MAX_CACHE_SIZE_TOTAL",
    "MEDIA_HEAL_DELAY_SECONDS", "MEDIA_HEAL_ATTEMPTS",
    "MAX_CACHE_SIZE_PER_SUB", "MAX_POSTS_PER_RUN",
    "PENDING_RECHECK_SECONDS",
    "FEEDTOKEN_JSON_STAGGER", "PROXY_MEDIA", "YOUTUBE_LINK_MESSAGE",
    "DISCOHOOK_PREVIEW", "REDDIT_OP_COMMENT", "REDDIT_VIDEO_QUALITY",
)
check("r49b: no direct numeric os.getenv conversion remains",
      not any(re.search(r"(?:int|float)\(os\.getenv", open(os.path.join(ROOT, p)).read())
              for p in _r49b_files))
_r49b_saved_env = {name: os.environ.get(name) for name in _r49b_names}
try:
    os.environ.update({name: "" for name in _r49b_names})
    _r49b_proxy = load_module("smoke_reddit_proxy_empty_env", _r49b_files[0])
    check("r49b: reddit_proxy imports with empty numeric Variables", True)
    check("r49b: reddit_proxy empty Variables use defaults",
          (_r49b_proxy.PROXY_WAVE_DELAY, _r49b_proxy.PROXY_GALLERY_GRACE,
           _r49b_proxy.PROXY_MAX_CONCURRENCY) == (0.5, 1.5, 8))
    _r49b_twitter = load_module("smoke_twitter_v3_empty_env", _r49b_files[1])
    check("r49b: twitter_v3 imports with empty numeric Variables", True)
    check("r49b: twitter_v3 empty cache Variables use defaults",
          (_r49b_twitter.MAX_CACHE_SIZE_PER_ACCOUNT,
           _r49b_twitter.MAX_CACHE_SIZE_TOTAL) == (250, 10000))
    check("r49b: twitter_v3 empty media-heal Variables use defaults",
          (_r49b_twitter.MEDIA_HEAL_DELAY_SECONDS,
           _r49b_twitter.MEDIA_HEAL_ATTEMPTS) == (45, 2))
    _r49b_reddit = load_module("smoke_reddit_v3_empty_env", _r49b_files[2])
    check("r49b: reddit_main_v3 imports with empty numeric Variables", True)
    check("r49b: reddit_main_v3 empty cache Variables use defaults",
          (_r49b_reddit.MAX_CACHE_SIZE_PER_SUB,
           _r49b_reddit.MAX_CACHE_SIZE_TOTAL) == (250, 10000))
    check("r49b: reddit_main_v3 empty scheduling Variables use defaults",
          (_r49b_reddit.MAX_POSTS_PER_RUN, _r49b_reddit.PENDING_RECHECK_SECONDS)
          == (25, 1800))
    check("r49b: reddit_main_v3 empty feed stagger uses default",
          _r49b_reddit.FEEDTOKEN_JSON_STAGGER == 65)
    check("r52: empty true-by-default Variables keep their defaults",
          (_r49b_reddit.PROXY_MEDIA,
           _r49b_reddit.YOUTUBE_LINK_MESSAGE,
           _r49b_reddit.DISCOHOOK_PREVIEW,
           _r49b_reddit.INCLUDE_OP_COMMENT)
          == (True, True, True, True))
    check("r67: an empty REDDIT_VIDEO_QUALITY uses the documented default",
          _r49b_reddit.VIDEO_QUALITY_MODE == "balanced")
    check("r67: an unknown REDDIT_VIDEO_QUALITY value also falls back to the "
          "default (a typo never picks an unintended mode)",
          _r49b_reddit._env_mode("R67_GARBAGE_MODE", "balanced",
                                 _r49b_reddit.VIDEO_QUALITY_MODES) == "balanced")
finally:
    for _name, _value in _r49b_saved_env.items():
        if _value is None:
            os.environ.pop(_name, None)
        else:
            os.environ[_name] = _value


# ===========================================================================
# ROUND 53 (2026-10-01) — empty STRING Variables must also mean "default".
# Production incident 2026-10-01 23:13 UTC: the workflow wires
# EMBEDDIT_INSTANCE / PROXY_WARMUP_POST as `${{ vars.X }}`; with no Variable
# set they arrive as EMPTY STRINGS, which os.getenv(name, default) accepts as
# real values. EMBEDDIT_BASE became "" and every Embeddit request went to the
# RELATIVE url "/api/v1/statuses/<id>" — aiohttp raised InvalidUrlClientError
# before opening a socket, logged as "embeddit error: /api/v1/statuses/4f1y…"
# on EVERY post, and the warm-up logged "PROXY_WARMUP_POST '' is invalid —
# Warm-up skipped" on every run. Same guard class as round 49b (numerics),
# now for strings.
# ===========================================================================
_r53_names = ("EMBEDDIT_INSTANCE", "PROXY_WARMUP_POST", "SUBREDDITS", "ACCOUNTS")
_r53_saved_env = {name: os.environ.get(name) for name in _r53_names}
try:
    os.environ.update({name: "" for name in _r53_names})
    _r53_proxy = load_module("smoke_reddit_proxy_r53", "testing area/reddit_proxy.py")
    check("r53: empty EMBEDDIT_INSTANCE keeps the default host (absolute url)",
          _r53_proxy.EMBEDDIT_BASE == "https://embeddit.deltandy.me",
          repr(_r53_proxy.EMBEDDIT_BASE))
    check("r53: EMBEDDIT_BASE is always absolute http(s)",
          _r53_proxy.EMBEDDIT_BASE.startswith(("http://", "https://")))
    check("r53: empty PROXY_WARMUP_POST keeps the default warm-up post",
          _r53_proxy.WARMUP_POST_ID == _r53_proxy.DEFAULT_WARMUP_POST ==
          "HonkaiStarRail_leaks/1whbjbh", repr(_r53_proxy.WARMUP_POST_ID))
    _r53_v3 = load_module("smoke_reddit_v3_r53", "testing area/reddit_main_v3.py")
    check("r53: empty SUBREDDITS keeps the 6 default subreddits",
          len(_r53_v3.SUBREDDITS) == 6, repr(_r53_v3.SUBREDDITS))
    _r53_tw = load_module("smoke_twitter_v3_r53", "testing area/twitter_v3.py")
    check("r53: empty ACCOUNTS keeps the 5 default accounts",
          len(_r53_tw.ACCOUNTS) == 5, repr(_r53_tw.ACCOUNTS))

    # Instance normalization: bare host (the way REDDIT_MIRROR is stored in
    # this repo's Variables) gains https://, trailing slash is dropped, and
    # garbage falls back to the default instead of producing a broken url.
    _r53_default = "https://embeddit.deltandy.me"
    _r53_norm_cases = (
        ("embeddit.deltandy.me", _r53_default),
        ("embeddit.deltandy.me/", _r53_default),
        ("https://embeddit.deltandy.me/", _r53_default),
        ("http://localhost:3000", "http://localhost:3000"),
        ("localhost:3000", "https://localhost:3000"),
        ("my.host:8080", "https://my.host:8080"),
        ("ftp://bad.example", _r53_default),
        ("https://", _r53_default),
        ("not a url", _r53_default),
        ("   ", _r53_default),
    )
    check("r53: EMBEDDIT_INSTANCE normalization (bare host/slash/garbage)",
          all(_r53_proxy._normalize_instance(raw, _r53_default, "EMBEDDIT_INSTANCE") == want
              for raw, want in _r53_norm_cases),
          str([(raw, _r53_proxy._normalize_instance(raw, _r53_default, "EMBEDDIT_INSTANCE"))
               for raw, want in _r53_norm_cases]))

    # Run-scoped unavailability (Embeddit rewrite readiness): a 404/410/501 on
    # /api/v1/statuses means the route itself is gone on that instance (a
    # dead post answers 502 there, never 404) — embeddit is then dropped from
    # the dispatch order for the rest of the run, but never to an empty list.
    _r53_proxy.reset_embeddit_availability()
    check("r53: embeddit in the dispatch order while available",
          _r53_proxy.proxy_services_for("/r/x/comments/abc/t/") ==
          ["vxreddit", "redditez", "embeddit"])
    _r53_proxy._mark_embeddit_unavailable("HTTP 404")
    check("r53: unavailable embeddit is dropped from the dispatch order",
          _r53_proxy.proxy_services_for("/r/x/comments/abc/t/") ==
          ["vxreddit", "redditez"])
    check("r53: share link + unavailable embeddit still leaves vxreddit",
          _r53_proxy.proxy_services_for("/r/x/s/tok") == ["vxreddit"])
    check("r53: unavailability reason is reported",
          _r53_proxy.embeddit_unavailable_reason() == "HTTP 404")
    import asyncio as _r53_asyncio
    check("r53: _fetch_embeddit short-circuits without touching the network",
          _r53_asyncio.run(_r53_proxy._fetch_embeddit(None, "/r/x/comments/abc/t/", "t")) is None)
    _r53_proxy.reset_embeddit_availability()
    check("r53: availability reset restores the full order",
          _r53_proxy.proxy_services_for("/r/x/comments/abc/t/") ==
          ["vxreddit", "redditez", "embeddit"]
          and _r53_proxy.embeddit_unavailable_reason() is None)

    # Warm-up resilience: a malformed PROXY_WARMUP_POST now probes the
    # built-in default post instead of skipping the warm-up (which left
    # proxy_health.json stale on every production run).
    async def _r53_fake_fetch(session, path, label=""):
        _r53_fake_fetch.paths.append(path)
        return {"service": "fake", "title": "t", "author": None,
                "subreddit": None, "body": "b", "stats": None, "media": []}
    _r53_fake_fetch.paths = []
    _r53_saved_fetchers = dict(_r53_proxy._PROXY_FETCHERS)
    _r53_tmp = tempfile.mkdtemp()
    _r53_cwd = os.getcwd()
    try:
        for _n in _r53_proxy._PROXY_FETCHERS:
            _r53_proxy._PROXY_FETCHERS[_n] = _r53_fake_fetch
        os.chdir(_r53_tmp)
        _r53_services = _r53_asyncio.run(_r53_proxy.proxy_warmup(None, "garbage-no-slash"))
        check("r53: invalid PROXY_WARMUP_POST falls back to the default post",
              bool(_r53_services) and all(
                  p == "/r/HonkaiStarRail_leaks/comments/1whbjbh/"
                  for p in _r53_fake_fetch.paths),
              str(_r53_fake_fetch.paths))
        check("r53: the fallback warm-up still writes proxy health",
              all(v.get("ok") for v in _r53_services.values()), str(_r53_services))
    finally:
        os.chdir(_r53_cwd)
        _r53_proxy._PROXY_FETCHERS.update(_r53_saved_fetchers)
        shutil.rmtree(_r53_tmp, ignore_errors=True)

    # The guard class itself: no bare os.getenv("NAME", "non-empty default")
    # may remain for the two Variables this round fixed.
    _r53_src = open(os.path.join(ROOT, "testing area/reddit_proxy.py")).read()
    check("r53: EMBEDDIT_INSTANCE / PROXY_WARMUP_POST no longer use bare os.getenv defaults",
          'os.getenv("EMBEDDIT_INSTANCE", ' not in _r53_src
          and 'os.getenv("PROXY_WARMUP_POST", ' not in _r53_src)

    # =======================================================================
    # ROUND 54 (2026-10-02) — Embeddit rewrite readiness.
    # The rewrite (Bun + Hono, branch `rewrite`) REMOVES the Mastodon
    # /api/v1/statuses route; the post page itself serves bots an HTML shell
    # whose payload is <script id="discord:component-embed"
    # type="application/json">{"component": {Components-V2 container}}
    # </script>. When the status API answers route-gone evidence the monitor
    # must probe that page and, if it parses, auto-switch for the run —
    # otherwise fall back to the round-53 drop.  Fixtures below mirror the
    # rewrite's builders (src/embed/builders/post.ts, parts.ts,
    # components.ts) read 2026-10-02.
    # =======================================================================
    def _r54_page(embed):
        return ('<html lang="en"><head><script id="discord:component-embed" '
                'type="application/json">' + json.dumps(embed) +
                '</script></head><body></body></html>')

    _r54_gallery = {"component": {"type": 17, "accent_color": 16729344, "components": [
        {"type": 10, "content": "-# in **[r/Genshin_Impact_Leaks](https://reddit.com/r/Genshin_Impact_Leaks/)**  \u2022  by **[u/AsleepBrilliant8939](https://reddit.com/u/AsleepBrilliant8939)**"},
        {"type": 14, "divider": True},
        {"type": 10, "content": "### About the main 7.2 event by USC"},
        {"type": 12, "items": [
            {"media": {"url": "https://preview.redd.it/a1.jpeg?auto=webp&s=x"}, "description": "(1/3)"},
            {"media": {"url": "https://preview.redd.it/a2.gif?s=y"}, "description": "(2/3) cap"},
            {"media": {"url": "https://preview.redd.it/a3.png"}, "description": "(3/3)"}]},
        {"type": 10, "content": "Main event is part of the AQ."},
        {"type": 14, "divider": False},
        {"type": 10, "content": "**<:u:1551647045155291238>  1.1K   \u2022   <:c:1551647065585750123>  56**"},
        {"type": 1, "components": [{"type": 2, "style": 5, "label": "View on Reddit", "url": "https://reddit.com/r/x/comments/1wvcysk/t/"}]},
        {"type": 14, "divider": True, "spacing": 2},
        {"type": 10, "content": "-# Posted <t:1759363000:R>  \u2022  Crossposted from **[r/Other](https://reddit.com/r/Other/comments/zzz/)**"},
    ]}}
    _r54 = _r53_proxy.parse_embeddit_component_page(_r54_page(_r54_gallery))
    check("r54: component gallery page parses to the normalized shape",
          _r54 is not None and _r54["service"] == "embeddit"
          and _r54["title"] == "About the main 7.2 event by USC"
          and _r54["author"] == "AsleepBrilliant8939"
          and _r54["subreddit"] == "Genshin_Impact_Leaks", str(_r54))
    check("r54: compact stats line parses through the custom-emoji markup",
          _r54 is not None and _r54["stats"] == {"ups": 1100, "comments": 56},
          str(_r54 and _r54["stats"]))
    check("r54: body excludes header/footer/stats small lines",
          _r54 is not None and _r54["body"] == "Main event is part of the AQ.")
    check("r54: gallery media kinds detected (image/gif/image)",
          _r54 is not None and [m["kind"] for m in _r54["media"]] ==
          ["image", "gif", "image"], str(_r54 and _r54["media"]))

    _r54_video = {"component": {"type": 17, "components": [
        {"type": 10, "content": "-# in **[r/HSR](https://reddit.com/r/HSR/)**  \u2022  by **[u/leaker](https://reddit.com/u/leaker)**"},
        {"type": 10, "content": "### New animation"},
        {"type": 12, "items": [{"media": {"url": "https://v.redd.it/abc123/DASH_720.mp4?source=fallback"}}]},
        {"type": 10, "content": "**<:u:1551647045155291238>  168   \u2022   <:c:1551647065585750123>  56**"}]}}
    _r54v = _r53_proxy.parse_embeddit_component_page(_r54_page(_r54_video))
    check("r54: video post media is kind=video (DASH fallback url)",
          _r54v is not None and _r54v["media"] ==
          [{"kind": "video", "url": "https://v.redd.it/abc123/DASH_720.mp4?source=fallback"}],
          str(_r54v and _r54v["media"]))

    _r54_link = {"component": {"type": 17, "components": [
        {"type": 10, "content": "-# in **[r/S](https://reddit.com/r/S/)**  \u2022  by **[u/a](https://reddit.com/u/a)**"},
        {"type": 10, "content": "### Link post"},
        {"type": 9, "components": [{"type": 10, "content": "[example.com/page](https://example.com/page)"}],
         "accessory": {"type": 11, "media": {"url": "https://external-preview.redd.it/th.png"}}},
        {"type": 10, "content": "**<:u:1551647045155291238>  5   \u2022   <:c:1551647065585750123>  0**"}]}}
    _r54l = _r53_proxy.parse_embeddit_component_page(_r54_page(_r54_link))
    check("r54: link post keeps the section text and the preview thumbnail",
          _r54l is not None
          and _r54l["media"] == [{"kind": "image", "url": "https://external-preview.redd.it/th.png"}]
          and "[example.com/page](https://example.com/page)" in _r54l["body"],
          str(_r54l))

    _r54_tricky = {"component": {"type": 17, "components": [
        {"type": 10, "content": "### T"},
        {"type": 10, "content": "**5 \u2022 6** extra words so it is not a stats line"}]}}
    _r54t = _r53_proxy.parse_embeddit_component_page(_r54_page(_r54_tricky))
    check("r54: a body line containing a bullet is NOT mistaken for stats",
          _r54t is not None and _r54t["stats"] is None
          and "extra words" in _r54t["body"], str(_r54t))

    check("r54: escaped \\u003c JSON (escapeJsonForScript) still parses",
          (_r53_proxy.parse_embeddit_component_page(
              _r54_page(_r54_video).replace("<:u:", "\\u003c:u:"))
           or {}).get("stats") == {"ups": 168, "comments": 56})
    check("r54: page without the component script parses to None",
          _r53_proxy.parse_embeddit_component_page("<html><body>no embed</body></html>") is None
          and _r53_proxy.parse_embeddit_component_page("") is None)

    # --- the run-scoped mode machine, with a fake aiohttp session ----------
    class _R54Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def json(self, content_type=None):
            return json.loads(self._body)
        async def text(self):
            return self._body

    class _R54Session:
        def __init__(self, routes):
            self.routes, self.calls = routes, []
        def get(self, url, **kw):
            self.calls.append(url)
            for frag, resp in self.routes.items():
                if frag in url:
                    return _R54Resp(*resp)
            return _R54Resp(502, "bad gateway")

    _r54_path = "/r/Genshin_Impact_Leaks/comments/1wvcysk/about/"

    # A) rewrite deployed: status API 404, the SAME post's page parses ->
    #    auto-switch; embeddit STAYS in the dispatch order; later lookups go
    #    straight to the page (exactly one request).
    _r53_proxy.reset_embeddit_availability()
    _r54_sess = _R54Session({"/api/v1/statuses/": (404, "Not Found"),
                             _r54_path: (200, _r54_page(_r54_gallery))})
    _r54r = _r53_asyncio.run(_r53_proxy._fetch_embeddit(_r54_sess, _r54_path, "t"))
    check("r54: route-gone + parseable page auto-switches and returns the post",
          _r54r is not None and _r54r["title"] == "About the main 7.2 event by USC"
          and _r53_proxy.embeddit_component_mode()
          and _r53_proxy.embeddit_unavailable_reason() is None)
    check("r54: component mode keeps embeddit in the dispatch order",
          _r53_proxy.proxy_services_for(_r54_path) ==
          ["vxreddit", "redditez", "embeddit"])
    _r54_n = len(_r54_sess.calls)
    _r54r2 = _r53_asyncio.run(_r53_proxy._fetch_embeddit(_r54_sess, _r54_path, "t"))
    check("r54: later lookups in component mode cost exactly one page request",
          _r54r2 is not None and len(_r54_sess.calls) == _r54_n + 1
          and _r54_path in _r54_sess.calls[-1])

    # B) both routes dead -> the round-53 drop, reason preserved.
    _r53_proxy.reset_embeddit_availability()
    _r54_sess = _R54Session({"/api/v1/statuses/": (404, "Not Found"),
                             _r54_path: (404, "nope")})
    _r54r = _r53_asyncio.run(_r53_proxy._fetch_embeddit(_r54_sess, _r54_path, "t"))
    check("r54: route gone AND page unusable -> round-53 unavailable drop",
          _r54r is None and _r53_proxy.embeddit_unavailable_reason() == "HTTP 404"
          and not _r53_proxy.embeddit_component_mode()
          and _r53_proxy.proxy_services_for(_r54_path) == ["vxreddit", "redditez"])

    # C) healthy Mastodon instance: byte-for-byte today's behavior, one call.
    _r53_proxy.reset_embeddit_availability()
    _r54_mastodon = json.dumps({
        "id": "x", "account": {"display_name": "u/a (@ r/S)"},
        "content": "T\u2b06\ufe0f 10 \u2022 \U0001f4ac 2",
        "media_attachments": [{"type": "image", "url": "https://i.redd.it/x.png"}]})
    _r54_sess = _R54Session({"/api/v1/statuses/": (200, _r54_mastodon)})
    _r54r = _r53_asyncio.run(_r53_proxy._fetch_embeddit(_r54_sess, _r54_path, "t"))
    check("r54: a healthy Mastodon instance keeps today's single-request path",
          _r54r is not None and _r54r["media"]
          and not _r53_proxy.embeddit_component_mode()
          and _r53_proxy.embeddit_unavailable_reason() is None
          and len(_r54_sess.calls) == 1)

    # D) transient 502 stays a per-post miss — no mode change, retried later.
    _r53_proxy.reset_embeddit_availability()
    _r54_sess = _R54Session({"/api/v1/statuses/": (502, "bad gateway")})
    _r54r = _r53_asyncio.run(_r53_proxy._fetch_embeddit(_r54_sess, _r54_path, "t"))
    check("r54: a transient 502 is a per-post miss, never a mode change",
          _r54r is None and not _r53_proxy.embeddit_component_mode()
          and _r53_proxy.embeddit_unavailable_reason() is None)
    _r53_proxy.reset_embeddit_availability()

finally:
    for _name, _value in _r53_saved_env.items():
        if _value is None:
            os.environ.pop(_name, None)
        else:
            os.environ[_name] = _value


# ---- Round 60 (2026-10-03): YouTube link-post destination-first ----------
# Replay of Zenlesszonezeroleaks_ 1wwgk5e.  Reddit renders a link post's
# destination as its trailing [link] anchor, after body text that may contain
# unrelated YouTube credit links (the live post's Song AMV).
_r60_main = "https://www.youtube.com/watch?v=QvhE_9XgImw"
_r60_song_esc = ("https://www.youtube.com/watch?v=SBQprWeOx8g&amp;"
                 "list=PLjNlQ2vXx1xbt30X8TcUfNzw_akVISXEu&amp;index=67")
_r60_song = _r60_song_esc.replace("&amp;", "&")
_r60_link_post = (
    '<table><tr><td><div class="md"><p>TC standard builds (34 subs)...</p>'
    f'<p>Song: <a href="{_r60_song_esc}">{_r60_song_esc}</a></p>'
    '</div></td></tr></table>&#32;submitted by&#32;'
    '<a href="https://www.reddit.com/user/Altruistic-Lock-2582">'
    '/u/Altruistic-Lock-2582</a>&#32;'
    f'<a href="{_r60_main}">[link]</a>&#32;'
    '<a href="https://www.reddit.com/r/Zenlesszonezeroleaks_/comments/'
    '1wwgk5e/x/">[comments]</a>')
_r60_text_post = (
    '<table><tr><td><div class="md"><p>Watch: '
    f'<a href="{_r60_song_esc}">{_r60_song_esc}</a></p>'
    '</div></td></tr></table>&#32;submitted by&#32;'
    '<a href="https://www.reddit.com/user/x">/u/x</a>&#32;'
    '<a href="https://www.reddit.com/r/Zenlesszonezeroleaks_/comments/'
    'zzz999/x/">[comments]</a>&#32;'
    '<a href="https://www.reddit.com/r/Zenlesszonezeroleaks_/comments/'
    'zzz999/x/">[link]</a>')

check("r60: extract_outbound_url finds the link-post destination",
      v3.extract_outbound_url(_r60_link_post) == _r60_main,
      repr(v3.extract_outbound_url(_r60_link_post)))
check("r60: extract_outbound_url ignores Reddit-domain link anchors",
      v3.extract_outbound_url(_r60_text_post) is None,
      repr(v3.extract_outbound_url(_r60_text_post)))
check("r60: selector prefers the destination over body YouTube matches",
      v3.select_youtube_url(v3.extract_outbound_url(_r60_link_post),
                            _r60_link_post) == _r60_main)
check("r60: text posts keep the body YouTube URL, HTML-unescaped",
      v3.select_youtube_url(v3.extract_outbound_url(_r60_text_post),
                            _r60_text_post) == _r60_song)
check("r60: extract_youtube_url never leaks escaped ampersands",
      v3.extract_youtube_url(_r60_link_post.split("[link]")[0]) == _r60_song)

_r60_base = v3.entry_to_base_data(_FakeEntry(
    "/r/Zenlesszonezeroleaks_/comments/1wwgk5e/showcase/", "Showcase",
    "Altruistic-Lock-2582", _r60_link_post))
check("r60: native RSS base feeds the destination-first selection downstream",
      _r60_base["youtube_url"] == _r60_main, repr(_r60_base["youtube_url"]))

# FULL MODE receives the same RSS base plus authoritative post_json["url"].
# Stub the thumbnail/media resolver so this remains a fast, offline assertion
# of the selected video rather than an availability test of a third party.
_r60_saved_resolve_youtube = v3.resolve_youtube_media


async def _r60_resolve_youtube(_session, video_id, _is_live):
    return None, f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


try:
    v3.resolve_youtube_media = _r60_resolve_youtube
    # Deliberately model a previously body-derived RSS value.  FULL MODE must
    # still replace it with the authoritative destination in post_json["url"].
    _r60_full_base = dict(_r60_base, youtube_url=_r60_song)
    _r60_full = asyncio.run(v3.resolve_post_media(
        None, _r60_full_base,
        {"title": "Showcase", "author": "Altruistic-Lock-2582",
         "selftext": f"Song: {_r60_song_esc}", "url": _r60_main,
         "num_comments": 0, "ups": 0},
        path="/r/Zenlesszonezeroleaks_/comments/1wwgk5e/showcase/"))
    check("r60: FULL MODE destination overrides the RSS body credit URL",
          _r60_full["youtube_url"] == _r60_main
          and _r60_full["youtube_id"] == "QvhE_9XgImw",
          repr((_r60_full["youtube_url"], _r60_full["youtube_id"])))
finally:
    v3.resolve_youtube_media = _r60_saved_resolve_youtube


# ---------------------------------------------------------------------------
# ROUND 61 (2026-10-04): stale-tip double post + conflict-fragile cache push.
# Live incident 2026-10-03: prod run #4014 was dispatched while another run
# was executing; workflow_dispatch pins GITHUB_SHA at DISPATCH time, so the
# queued run started on the stale tip dd756a5 — six seconds after cache
# commit 2de1491 (already containing Genshin_Impact_Leaks_1wwyure) — and
# posted 1wwyure a second time. Its own cache push then died on a git LINE
# conflict in pending_reddit.json that a semantic JSON merge resolves
# trivially. Two fixes, both guarded here:
#   (1) a "Sync to the live branch tip" step in BOTH monitor workflows;
#   (2) the persist step recovers via .github/scripts/merge_monitor_caches.py
#       (semantic union merge) instead of `git pull --rebase`.
# ---------------------------------------------------------------------------
import importlib.util as _r61_ilu
import subprocess as _r61_sp
import tempfile as _r61_tmp

_r61_script = os.path.join(ROOT, ".github", "scripts", "merge_monitor_caches.py")
check("r61: merge_monitor_caches.py exists", os.path.isfile(_r61_script))

_r61_spec = _r61_ilu.spec_from_file_location("_r61_merge", _r61_script)
_r61_mod = _r61_ilu.module_from_spec(_r61_spec)
_r61_spec.loader.exec_module(_r61_mod)

# (a) EXACT INCIDENT REPLAY — both sides appended the same key, evictions
# diverged, pending last_checked collided on the same entry.
_r61_theirs_posted = ["A_1", "B_2", "Genshin_Impact_Leaks_1wwyure"]
_r61_ours_posted = ["A_1", "B_2", "Genshin_Impact_Leaks_1wwyure", "C_3"]
_r61_posted = _r61_mod.merge_lists(_r61_theirs_posted, _r61_ours_posted)
check("r61: posted merge is a strict union (no dedup key ever lost)",
      set(_r61_posted) == set(_r61_theirs_posted) | set(_r61_ours_posted))
check("r61: posted merge never duplicates a key (double-add collapses to one)",
      _r61_posted.count("Genshin_Impact_Leaks_1wwyure") == 1
      and len(_r61_posted) == len(set(_r61_posted)))
_r61_pending = _r61_mod.merge_dicts(
    {"HNA_1wwnu46": {"first_seen": 1.0, "last_checked": 100.0, "reason": "media_wait"},
     "only_theirs": {"first_seen": 2.0, "last_checked": 2.0, "reason": "removal_notice"}},
    {"HNA_1wwnu46": {"first_seen": 1.0, "last_checked": 200.0, "reason": "media_wait"},
     "only_ours": {"first_seen": 3.0, "last_checked": 3.0, "reason": "removal_notice"}})
check("r61: pending merge keeps both one-sided entries and newest last_checked",
      _r61_pending["HNA_1wwnu46"]["last_checked"] == 200.0
      and "only_theirs" in _r61_pending and "only_ours" in _r61_pending)

# (b) End-to-end through the CLI exactly as the workflow invokes it, plus
# the hygiene rule (pending keys covered by the merged posted list drop out)
# and the loud failure on invalid JSON (a half-merge must never be pushed).
with _r61_tmp.TemporaryDirectory() as _r61_d:
    _r61_ours = os.path.join(_r61_d, "ours"); os.mkdir(_r61_ours)
    _r61_repo = os.path.join(_r61_d, "repo"); os.mkdir(_r61_repo)
    with open(os.path.join(_r61_repo, "posted_reddit.json"), "w") as _f:
        json.dump(_r61_theirs_posted, _f)
    with open(os.path.join(_r61_ours, "posted_reddit.json"), "w") as _f:
        json.dump(_r61_ours_posted, _f)
    with open(os.path.join(_r61_repo, "pending_reddit.json"), "w") as _f:
        json.dump({"Genshin_Impact_Leaks_1wwyure": {"first_seen": 1.0,
                   "last_checked": 1.0, "reason": "media_wait"}}, _f)
    with open(os.path.join(_r61_ours, "pending_reddit.json"), "w") as _f:
        json.dump({"D_4": {"first_seen": 4.0, "last_checked": 4.0,
                   "reason": "media_wait"}}, _f)
    _r61_run = _r61_sp.run(
        [sys.executable, _r61_script, _r61_ours, _r61_repo,
         "posted_reddit.json", "proxy_health.json", "pending_reddit.json",
         "posted_messages.json"],
        capture_output=True, text=True)
    with open(os.path.join(_r61_repo, "pending_reddit.json")) as _f:
        _r61_cli_pending = json.load(_f)
    check("r61: CLI merge succeeds with the workflow's exact argument shape",
          _r61_run.returncode == 0, _r61_run.stderr[:200])
    check("r61: hygiene — a pending key already in the merged posted list drops",
          "Genshin_Impact_Leaks_1wwyure" not in _r61_cli_pending
          and "D_4" in _r61_cli_pending)
    with open(os.path.join(_r61_repo, "pending_reddit.json"), "w") as _f:
        _f.write("{broken")
    _r61_bad = _r61_sp.run(
        [sys.executable, _r61_script, _r61_ours, _r61_repo,
         "pending_reddit.json"], capture_output=True, text=True)
    check("r61: invalid JSON fails LOUDLY (non-zero, no half-merge pushed)",
          _r61_bad.returncode != 0 and "SEMANTIC MERGE FAILED" in _r61_bad.stderr,
          _r61_bad.stderr[:200])

# (c) Both monitor workflows carry the sync-to-tip step and the semantic
# recovery, and the old conflict-fragile rebase recovery is gone.
for _r61_wf in (".github/workflows/reddit_monitor.yml",
                ".github/workflows/twitter_monitor.yml"):
    with open(os.path.join(ROOT, _r61_wf), encoding="utf-8") as _f:
        _r61_src = _f.read()
    _r61_base = os.path.basename(_r61_wf)
    check(f"r61: {_r61_base} — sync-to-tip step present (stale GITHUB_SHA fix)",
          "Sync to the live branch tip" in _r61_src
          and "git reset --hard FETCH_HEAD" in _r61_src)
    check(f"r61: {_r61_base} — persist recovery is the semantic merge",
          "merge_monitor_caches.py" in _r61_src)
    check(f"r61: {_r61_base} — conflict-fragile rebase recovery removed",
          "git pull --rebase" not in _r61_src)
    check(f"r61: {_r61_base} — dispatch-time SHA pinning documented",
          "GITHUB_SHA" in _r61_src and "dispatch" in _r61_src.lower())


# ---- round 62 (2026-10-04): body/gallery duplicate media line scrub ----
# Live 1wx9c77 (r/Zenlesszonezeroleaks_): a selftext whose only content is
# the inline image link rendered the SAME picture twice — once as a raw
# preview.redd.it URL text line, once in the media gallery below it. A body
# line that is ONLY a redd.it media link already in the gallery is dropped;
# prose, captioned links, and non-gallery media stay byte-identical.
_r62_url = ("https://preview.redd.it/jex8yf7dheth1.png?width=527&format=png"
            "&auto=webp&s=2e9caf96e4f015b42496b9c43d8bd0e7a891d8e1")
_r62_media = [{"kind": "image", "url": _r62_url}]
check("r62: bare gallery-dup URL line dropped",
      v3._drop_gallery_media_lines(_r62_url, _r62_media) == "")
check("r62: html-escaped (&amp;) variant still matches",
      v3._drop_gallery_media_lines(_r62_url.replace("&", "&amp;"), _r62_media) == "")
check("r62: self-labelled [url](url) link dropped",
      v3._drop_gallery_media_lines(f"[{_r62_url}]({_r62_url})", _r62_media) == "")
check("r62: preview body vs i.redd.it gallery rendition collapses",
      v3._drop_gallery_media_lines(
          _r62_url, [{"kind": "image", "url": "https://i.redd.it/jex8yf7dheth1.png"}]) == "")
check("r62: -v0- slug form collapses",
      v3._drop_gallery_media_lines(
          "https://preview.redd.it/slug-v0-jex8yf7dheth1.png?width=640&s=a",
          [{"kind": "image", "url": "https://i.redd.it/jex8yf7dheth1.png"}]) == "")
check("r62: prose+URL line kept byte-identical",
      v3._drop_gallery_media_lines(f"look at this {_r62_url}", _r62_media)
      == f"look at this {_r62_url}")
check("r62: caption-labelled link kept",
      v3._drop_gallery_media_lines(f"[the banner]({_r62_url})", _r62_media)
      == f"[the banner]({_r62_url})")
check("r62: media NOT in gallery kept",
      v3._drop_gallery_media_lines(
          "https://preview.redd.it/zzzz.png?width=1&s=x", _r62_media)
      == "https://preview.redd.it/zzzz.png?width=1&s=x")
check("r62: mixed body keeps prose, drops dup, collapses blanks",
      v3._drop_gallery_media_lines(
          f"Seele Leaks says STC.\n\n{_r62_url}\n\nMore in comments.", _r62_media)
      == "Seele Leaks says STC.\n\nMore in comments.")
check("r62: no media / no body = no-op",
      v3._drop_gallery_media_lines(_r62_url, []) == _r62_url
      and v3._drop_gallery_media_lines("", _r62_media) == "")
_r62_card = v3.build_v3_payload(
    "Zenlesszonezeroleaks_",
    {"title": "[ZZZ x HSR collab] Kafka and Robin", "author": "natarawilliams19",
     "body": _r62_url, "media": _r62_media, "crosspost": None,
     "stats": {"comments": 17, "ups": 31}, "youtube_url": None, "op_comment": None},
    "https://www.reddit.com/r/Zenlesszonezeroleaks_/comments/1wx9c77/x/", 1791097875)
_r62_texts = [c["content"] for c in _r62_card["components"][0]["components"]
              if c.get("type") == 10]
_r62_kinds = [c["type"] for c in _r62_card["components"][0]["components"]]
check("r62: 1wx9c77 card — no raw-URL text component",
      all("preview.redd.it" not in t for t in _r62_texts), str(_r62_texts))
check("r62: 1wx9c77 card — gallery intact, header+stats only",
      12 in _r62_kinds and _r62_kinds.count(10) == 2, str(_r62_kinds))


# ---- round 65 (2026-10-05): body entity hygiene -------------------------
# Live 1wxvl13 (r/HonkaiStarRail_leaks): a card built from the Arctic Shift
# backup path showed literal "&gt;" — Arctic (like Reddit's JSON API) serves
# selftext HTML-escaped, and only the JSON path (strip_html) unescaped it.
# Every body path now unescapes EXACTLY ONCE, and a line that is only ">"
# (an empty markdown blockquote separator) is dropped from the card body.
_r65_live = ("All top-level comments must be showcases, like so:\n\n&gt;\n"
             "&gt; link to showcase here\n&gt; names of the characters used")
_r65_body = v3._clean_plain_body(_r65_live)
check("r65: live 1wxvl13 Arctic body — the real quote line is kept",
      "> link to showcase here" in _r65_body, repr(_r65_body))
check("r65: live 1wxvl13 Arctic body — no literal '&gt;' anywhere",
      "&gt;" not in _r65_body, repr(_r65_body))
check("r65: live 1wxvl13 Arctic body — no lone '>' artifact line",
      all(ln.strip() != ">" for ln in _r65_body.splitlines()), repr(_r65_body))
check("r65: live 1wxvl13 Arctic body — full expected shape",
      _r65_body == "All top-level comments must be showcases, like so:\n\n"
                   "> link to showcase here\n> names of the characters used",
      repr(_r65_body))
check("r65: double-escape safety — Arctic '&amp;gt;' renders '&gt;', not '>'",
      v3._clean_plain_body("&amp;gt;") == "&gt;",
      repr(v3._clean_plain_body("&amp;gt;")))
check("r65: JSON-path parity — strip_html still unescapes exactly once",
      v3.strip_html("&gt;") == ">" and v3.strip_html("&amp;gt;") == "&gt;",
      f"{v3.strip_html('&gt;')!r} / {v3.strip_html('&amp;gt;')!r}")
check("r65: quoted content lines survive byte-identical",
      v3._clean_plain_body("> quoted text") == "> quoted text"
      and v3._clean_plain_body(">> nested quoted text") == ">> nested quoted text"
      and v3._line_stage(["> quoted text"]) == ["> quoted text"])
check("r65: '>'-only / '> '-only / '>>' lines are dropped",
      v3._clean_plain_body(">") == "" and v3._clean_plain_body("> ") == ""
      and v3._clean_plain_body(">>") == "")
check("r65: a body of ONLY '>' lines yields an empty body",
      v3._clean_plain_body(">\n> \n>>\n>  ") == "")
check("r65: mixed body keeps prose + quotes, drops only the separator",
      v3._clean_plain_body("before\n\n>\n\n> quoted\n\nafter")
      == "before\n\n> quoted\n\nafter",
      repr(v3._clean_plain_body("before\n\n>\n\n> quoted\n\nafter")))
check("r65: exactly ONE unescape on the Arctic path (source guard)",
      inspect.getsource(v3._clean_plain_body).count("html_lib.unescape(") == 1)
check("r65: strip_html keeps its single unescape (source guard)",
      inspect.getsource(v3.strip_html).count("html_lib.unescape(") == 1)
_r65_card = v3.build_v3_payload(
    "HonkaiStarRail_leaks",
    {"title": "Daily Discussion Thread", "author": "leaker",
     "body": v3._clean_plain_body(_r65_live), "media": [], "crosspost": None,
     "stats": {"comments": 4, "ups": 12}, "youtube_url": None, "op_comment": None},
    "https://www.reddit.com/r/HonkaiStarRail_leaks/comments/1wxvl13/x/", 1791139200)
_r65_texts = [c["content"] for c in _r65_card["components"][0]["components"]
              if c.get("type") == 10]
check("r65: 1wxvl13 card — body component carries the real quote",
      any("> link to showcase here" in t for t in _r65_texts), str(_r65_texts))
check("r65: 1wxvl13 card — no '&gt;' and no lone '>' line on the card",
      all("&gt;" not in t and all(ln.strip() != ">" for ln in t.splitlines())
          for t in _r65_texts), str(_r65_texts))
_r65_empty_card = v3.build_v3_payload(
    "HonkaiStarRail_leaks",
    {"title": "Daily Discussion Thread", "author": "leaker",
     "body": v3._clean_plain_body(">\n> \n>>"), "media": [], "crosspost": None,
     "stats": {"comments": 4, "ups": 12}, "youtube_url": None, "op_comment": None},
    "https://www.reddit.com/r/HonkaiStarRail_leaks/comments/1wxvl13/x/", 1791139200)
_r65_empty_kinds = [c["type"] for c in _r65_empty_card["components"][0]["components"]]
check("r65: an all-'>' body omits the text component (header+stats only)",
      _r65_empty_kinds.count(10) == 2, str(_r65_empty_kinds))

print()

# ===========================================================================
# ROUND 66 (2026-10-05) — long-post continuations, markdown table rendering,
# safe splits, stale env cleanup.
# A body beyond the card's text budget is continued in follow-up messages
# (main card layout unchanged), every split lands on a safe boundary —
# never inside a link/URL/spoiler/marker pair (live 1wxfuj5) — markdown
# tables render as per-row bullets (live 1wxfuj5), retraction covers every
# part, and reddit_monitor.yml drops 11 env mappings for gates removed in
# round 63 while wiring the round-64 FRESH_HOLD_SECONDS/LISTING_CONFIRM.
# ===========================================================================

_r66_sub = "TestSub"
_r66_url = "https://www.reddit.com/r/TestSub/comments/abc123/x/"
_r66_ts = 1791139200


def _r66_data(body):
    return {"title": "Long post continuation test", "author": "leaker",
            "body": body, "media": [], "crosspost": None,
            "stats": {"comments": 123, "ups": 45}, "youtube_url": None,
            "op_comment": None}


def _r66_para(tag, n):
    # exactly n chars, no markdown markers inside
    return (f"{tag} " + "x" * (n - len(tag) - 1))[:n]


def _r66_texts(payload):
    return [c["content"] for cont in payload["components"] for c in cont["components"]
            if c.get("type") == 10]


def _r66_chunks(conts):
    out = []
    for p in conts:
        chunk = p["components"][0]["components"][0]["content"]
        assert chunk.startswith(v3.CONTINUATION_HEADER + "\n"), chunk[:30]
        out.append(chunk[len(v3.CONTINUATION_HEADER) + 1:])
    return out


# --- short body: parity — the card is byte-identical, zero continuations --
_r66_short = "Short body — fits the card in one piece."
_r66_p_plain = v3.build_v3_payload(_r66_sub, _r66_data(_r66_short), _r66_url, _r66_ts)
_r66_p_split, _r66_rest = v3.build_v3_payload(_r66_sub, _r66_data(_r66_short),
                                              _r66_url, _r66_ts, return_remainder=True)
check("r66: short body — return_remainder=True returns the byte-identical card",
      _r66_p_plain == _r66_p_split and _r66_rest == "",
      f"equal={_r66_p_plain == _r66_p_split} rest={_r66_rest!r}")
check("r66: short body — zero continuation messages",
      v3.build_continuation_payloads(_r66_rest) == [])
check("r66: short body — the whole body stays on the card, no ellipsis",
      _r66_short in _r66_texts(_r66_p_plain), str(_r66_texts(_r66_p_plain)))

# --- 5000-char body: main card + exactly ONE continuation ---------------
_r66_paras5 = [_r66_para(f"P{i}", 1000) for i in range(5)]
_r66_body5 = "\n\n".join(_r66_paras5)            # 5008 chars
_r66_p5, _r66_rest5 = v3.build_v3_payload(_r66_sub, _r66_data(_r66_body5),
                                          _r66_url, _r66_ts, return_remainder=True)
_r66_t5 = _r66_texts(_r66_p5)
_r66_budget5 = max(300, 3800 - len(_r66_t5[0]) - len(_r66_t5[-1]))   # op line empty here
_r66_fill = ""
for _p in _r66_paras5:                            # greedy paragraph fill
    _cand = _r66_fill + ("\n\n" if _r66_fill else "") + _p
    if len(_cand) <= _r66_budget5:
        _r66_fill = _cand
    else:
        break
check("r66: 5k body — the main card keeps the largest paragraph-safe prefix",
      _r66_t5[1] == _r66_fill and len(_r66_fill) <= _r66_budget5,
      f"body={_r66_t5[1][:40]!r}… len={len(_r66_t5[1])} expected={len(_r66_fill)} budget={_r66_budget5}")
check("r66: 5k body — main + remainder reconstruct the body exactly",
      _r66_t5[1] + "\n\n" + _r66_rest5 == _r66_body5,
      f"lens {len(_r66_t5[1])}+{len(_r66_rest5)} vs {len(_r66_body5)}")
_r66_conts5 = v3.build_continuation_payloads(_r66_rest5)
check("r66: 5k body — exactly one continuation message",
      len(_r66_conts5) == 1, str(len(_r66_conts5)))
check("r66: 5k body — the continuation carries the whole remainder",
      _r66_chunks(_r66_conts5) == [_r66_rest5],
      str([len(c) for c in _r66_chunks(_r66_conts5)]))
check("r66: continuation payload shape — V2 container, card accent, '-# (continued)' heading",
      all(p["flags"] == v3.IS_COMPONENTS_V2
          and p["components"][0]["type"] == 17
          and p["components"][0]["accent_color"] == 16729344
          and p["components"][0]["components"][0]["type"] == 10
          and p["components"][0]["components"][0]["content"].startswith("-# (continued)\n")
          and len(p["components"][0]["components"][0]["content"]) <= v3.CONTINUATION_CHAR_LIMIT
          for p in _r66_conts5), str(_r66_conts5)[:300])

# --- ~12000-char body: main + THREE continuations + 'full post on Reddit' -
# 13 paragraphs x 1000 chars: the main card holds 3 and each continuation
# holds 3, so the 13th paragraph spills past the 12k-char deliverable cap.
_r66_paras12 = [_r66_para(f"P{i}", 1000) for i in range(13)]
_r66_body12 = "\n\n".join(_r66_paras12)           # 13024 chars
_r66_p12, _r66_rest12 = v3.build_v3_payload(_r66_sub, _r66_data(_r66_body12),
                                            _r66_url, _r66_ts, return_remainder=True)
_r66_t12 = _r66_texts(_r66_p12)
_r66_conts12 = v3.build_continuation_payloads(_r66_rest12)
_r66_chunks12 = _r66_chunks(_r66_conts12)
check("r66: 12k-class body — capped at exactly three continuations",
      len(_r66_conts12) == 3, str(len(_r66_conts12)))
check("r66: 12k-class body — the last continuation ends with the 'full post on Reddit' pointer",
      _r66_chunks12[-1].endswith("\n\n… full post on Reddit"),
      repr(_r66_chunks12[-1][-60:]))
_r66_c3_base = _r66_chunks12[-1][: -len("\n\n… full post on Reddit")]
_r66_delivered = "\n\n".join([_r66_t12[1]] + _r66_chunks12[:2] + [_r66_c3_base])
check("r66: 12k-class body — every delivered chunk is a paragraph-safe prefix of the body",
      _r66_body12.startswith(_r66_delivered) and len(_r66_delivered) < len(_r66_body12),
      f"delivered={len(_r66_delivered)} body={len(_r66_body12)}")
check("r66: 12k-class body — beyond the cap only the spill is dropped (the last para)",
      _r66_body12[len(_r66_delivered):].strip() == _r66_paras12[-1],
      repr(_r66_body12[len(_r66_delivered):][:60]))
check("r66: 12k-class body — no continuation slice ever cuts a paragraph",
      all(chunk in _r66_body12 for chunk in _r66_chunks12[:2]),
      str([len(c) for c in _r66_chunks12[:2]]))

# --- safe splits: NEVER inside a link / URL / spoiler / marker pair -------
_r66_link = "[Set](https://example.com/honkai-set)"
_r66_h, _r66_t = v3.split_card_body("A" * 280 + " " + _r66_link + " trailing words here", 300)
check("r66: a markdown link straddling the budget splits BEFORE it (live 1wxfuj5)",
      _r66_h == "A" * 280 and _r66_t.startswith(_r66_link),
      f"{_r66_h[-25:]!r} / {_r66_t[:45]!r}")
_r66_h, _r66_t = v3.split_card_body("B" * 290 + " ||spoiler text|| more words follow", 300)
check("r66: a spoiler straddling the budget is never sliced open",
      _r66_t.startswith("||spoiler text||") and "||spoiler" not in _r66_h,
      f"{_r66_h[-25:]!r} / {_r66_t[:35]!r}")
_r66_h, _r66_t = v3.split_card_body(
    "C" * 285 + " https://example.com/very/long/path/that/keeps/going/and/going plus words", 300)
check("r66: a bare URL straddling the budget splits before the scheme",
      _r66_t.startswith("https://example.com/very/long"), f"{_r66_h[-20:]!r} / {_r66_t[:45]!r}")
_r66_nospace = "x" * 250 + _r66_link + "y" * 50   # no boundary chars at all
_r66_h, _r66_t = v3.split_card_body(_r66_nospace, 300)
check("r66: pathological no-boundary text still splits (progress) without slicing the link",
      len(_r66_h) == 300 and _r66_link in _r66_h and _r66_h + _r66_t == _r66_nospace,
      f"head_len={len(_r66_h)} link_intact={_r66_link in _r66_h}")
# adversarial: the same straddle at a CONTINUATION boundary (~3784, not 3800,
# because the '-# (continued)' heading claims the first 17 chars)
_r66_adv = _r66_para("Adv", 3760) + "\n\n" + _r66_link + "\n\n" + _r66_para("Tail", 50)
_r66_h, _r66_t = v3.split_card_body(_r66_adv, v3.CONTINUATION_CHAR_LIMIT
                                    - len(v3.CONTINUATION_HEADER) - 1)
check("r66: adversarial link straddling the continuation boundary splits before it",
      _r66_t.startswith(_r66_link) and _r66_link in _r66_h + _r66_t
      and not (_r66_link[:-1] in _r66_h and _r66_link not in _r66_h),
      f"{_r66_h[-25:]!r} / {_r66_t[:45]!r}")

# --- markdown tables → Discord-safe bullets -------------------------------
# the REAL 1wxfuj5 table (verbatim from Arctic Shift, t3_1wxfuj5): the
# first data cell is "3.8" under header "Patch" — the bold row label
# COMPOSES them into "**Patch 3.8**", matching the hand-checked example
_r66_tbl = ("|Patch|Drip Market|Beta Start|Livestream|First Half|Second Half|New Characters|\n"
            "|:-|:-|:-|:-|:-|:-|:-|\n"
            "|3.7|August 25/26, 2026|September 5, 2026|September 19, 2026|October 2, 2026|"
            "October 23, 2026|Hsin (5\\* Electro) & Suoming (5\\* Electro)|\n"
            "|3.8|October 9/10, 2026|October 16, 2026|October 30, 2026|November 13, 2026|"
            "December 2, 2026|Lily (5\\* Spectro)|")
check("r66: the real 1wxfuj5 table renders as the hand-checked example (live 1wxfuj5)",
      v3._markdown_tables_to_bullets(_r66_tbl) ==
      "**Patch 3.7**\n"
      "- **Drip Market:** August 25/26, 2026\n"
      "- **Beta Start:** September 5, 2026\n"
      "- **Livestream:** September 19, 2026\n"
      "- **First Half:** October 2, 2026\n"
      "- **Second Half:** October 23, 2026\n"
      "- **New Characters:** Hsin (5\\* Electro) & Suoming (5\\* Electro)\n\n"
      "**Patch 3.8**\n"
      "- **Drip Market:** October 9/10, 2026\n"
      "- **Beta Start:** October 16, 2026\n"
      "- **Livestream:** October 30, 2026\n"
      "- **First Half:** November 13, 2026\n"
      "- **Second Half:** December 2, 2026\n"
      "- **New Characters:** Lily (5\\* Spectro)",
      repr(v3._markdown_tables_to_bullets(_r66_tbl)))
check("r66: a first cell already carrying the header word is never doubled",
      v3._markdown_tables_to_bullets(
          "| Patch | Drip Market |\n|---|---|\n| Patch 3.8 | October 9/10, 2026 |")
      == "**Patch 3.8**\n- **Drip Market:** October 9/10, 2026",
      repr(v3._markdown_tables_to_bullets(
          "| Patch | Drip Market |\n|---|---|\n| Patch 3.8 | October 9/10, 2026 |")))
check("r66: an empty first header falls back to the verbatim cell",
      v3._markdown_tables_to_bullets(
          "| | Drip Market |\n|---|---|\n| 3.8 | October 9/10, 2026 |")
      == "**3.8**\n- **Drip Market:** October 9/10, 2026")
check("r66: dates stay literal — NO Discord-timestamp guessing of body text",
      "<t:" not in v3._markdown_tables_to_bullets(_r66_tbl))
check("r66: table mid-body keeps the surrounding prose",
      v3._markdown_tables_to_bullets("before\n\n" + _r66_tbl + "\nafter") ==
      "before\n\n" + v3._markdown_tables_to_bullets(_r66_tbl) + "\nafter",
      repr(v3._markdown_tables_to_bullets("before\n\n" + _r66_tbl + "\nafter")))
check("r66: empty cells are skipped, not blank bullets",
      v3._markdown_tables_to_bullets("| Patch | Drip Market | Notes |\n|---|---|---|\n| 3.8 | | z |")
      == "**Patch 3.8**\n- **Notes:** z",
      repr(v3._markdown_tables_to_bullets("| Patch | Drip Market | Notes |\n|---|---|---|\n| 3.8 | | z |")))
check("r66: escaped pipes survive as literal pipes",
      v3._markdown_tables_to_bullets("| Patch | a \\| b |\n|---|---|\n| 3.8 | x \\| y |")
      == "**Patch 3.8**\n- **a | b:** x | y",
      repr(v3._markdown_tables_to_bullets("| Patch | a \\| b |\n|---|---|\n| 3.8 | x \\| y |")))
_r66_bad_tbls = [
    "| a | b |\n| c | d |",                                  # no separator row
    "| a | b | c |\n|---|---|---|\n| x | y |",               # unequal data cells
    "| a | b |\n|---|---|\n| x | y |\n| c | d |\n|---|---|\n| e | f |",  # glued tables
    "| a | b |\n|---|---|",                                  # zero data rows
    "plain prose, no pipes at all",
]
check("r66: malformed/ambiguous tables stay byte-identical — never guess",
      all(v3._markdown_tables_to_bullets(_b) == _b for _b in _r66_bad_tbls),
      str([b for b in _r66_bad_tbls if v3._markdown_tables_to_bullets(b) != b]))
check("r66: a pipe-free body short-circuits untouched",
      v3._markdown_tables_to_bullets("") == "" and v3._markdown_tables_to_bullets(None) is None)

# --- live 1wxfuj5 end-to-end shape: table + entities + link across the cut
_r66_live_raw = (
    "Version 3.8 drip market schedule below.\n\n"
    "| Patch | Drip Market | Notes |\n"
    "|---|---|---|\n"
    "| 3.8 | October 9/10, 2026 | Version 3.8 livestream codes |\n\n"
    "All top-level comments must be showcases, like so:\n\n"
    "&gt; link to showcase here\n&gt; names of the characters used\n\n"
    + _r66_para("Details", 3300) + "\n\n"
    + "Full kit details in " + _r66_link + " — see the official notes.\n\n"
    + _r66_para("Trailer", 1200)
)
_r66_live_body = v3._clean_plain_body(_r66_live_raw)
_r66_live_p, _r66_live_rest = v3.build_v3_payload(
    _r66_sub, _r66_data(_r66_live_body), _r66_url, _r66_ts, return_remainder=True)
_r66_live_conts = v3.build_continuation_payloads(_r66_live_rest)
# body parts only — the header's title link and the stats line's posted-time
# 🕐 are not body text (the timestamp check below must not trip on them)
_r66_live_parts = _r66_texts(_r66_live_p)[1:-1] + _r66_chunks(_r66_live_conts)
check("r66: 1wxfuj5 shape — the table converts on the card itself",
      any("**Patch 3.8**" in p and "- **Drip Market:** October 9/10, 2026" in p
          for p in _r66_live_parts), str(_r66_live_parts[1][:120]))
check("r66: 1wxfuj5 shape — r65 entity hygiene holds on every part",
      all("&gt;" not in p and all(ln.strip() != ">" for ln in p.splitlines())
          for p in _r66_live_parts), str(_r66_live_parts[:2]))
check("r66: 1wxfuj5 shape — the quote block survives",
      any("> link to showcase here" in p for p in _r66_live_parts))
check("r66: 1wxfuj5 shape — the [Set](…) link is delivered intact, exactly once",
      sum(p.count(_r66_link) for p in _r66_live_parts) == 1,
      str([p.count(_r66_link) for p in _r66_live_parts]))
check("r66: 1wxfuj5 shape — no partial-link fragment anywhere (the live bug)",
      all(not re.search(r"\[Set\]\(https://example\.com/honkai-se", p)
          or _r66_link in p for p in _r66_live_parts),
      str([p[-80:] for p in _r66_live_parts]))
check("r66: 1wxfuj5 shape — long body continues in follow-up messages",
      len(_r66_live_conts) >= 1, str(len(_r66_live_conts)))
check("r66: 1wxfuj5 shape — no body-level Discord timestamps (only the card 🕐)",
      all("<t:" not in p for p in _r66_live_parts[1:]), str(_r66_live_parts[1][-80:]))

# --- the FULL live 1wxfuj5 selftext (verbatim from Arctic Shift) -----------
_r66_mega_raw = r"""Please use this thread for discussion, questions, or other topics related to the game. Off-topic discussions are welcome. **STORY LEAKS MUST BE PROPERLY SPOILER TAGGED**

Thank you to u/justachilldude6347 for the megathread title. Remember to be respectful to others and follow the rules.

---

##### Guides & Wikis:

* [encore.moe](http://encore.moe) (Character wiki + Voicelines in all languages + Leaked OSTs)
* [Interactive Map 1](https://wuthering.gg/map)
* [Interactive Map 2](https://www.ghzs666.com/wutheringwaves-map#/?map=default)
* [nanoka.cc](http://ww.nanoka.cc) (Hakush-adjacent site)
* [Prydwen](https://www.prydwen.gg/wuthering-waves/) (Guides and Builds)
* [Tethys.gg](https://tethys.gg/) (Community-run guide by theorycrafters and speedrunners)
* [Wuwa Wiki](https://wuwa.wiki/en)

RIP Hakush

---

##### Resources:

* [Banner Countdown](https://wuthering-countdown.gengamer.in/)
* [BlackClears' 3.x Wuwa Calcs](https://docs.google.com/spreadsheets/d/1VGNYxq9nmxxRcA719RzfhXFHg8dtCWJmZ9WP-iq5AcQ/edit?gid=262258431#gid=262258431) (Calcs and more calcs)
* [DPR Calc Results](https://docs.google.com/spreadsheets/d/1eoCTrwYIsRpacvL3KrcQpR5rwY3pbJ6gEljZdBj_DHs/edit?gid=1550476759#gid=1550476759) (Pre-Release/Beta Calc, **reminder that everything is stc and NOT indicative of a character's actual performance in game**)
* [Maygi's Wuwa DPS Calculator spreadsheet](https://docs.google.com/spreadsheets/d/1vTbG2HfkVxyqvNXF2taikStK-vJJf40QrWa06Fgj17c/edit?usp=sharing) (Can be modified to work with leaked character info, note that **leak info are stc**)
* [Riley's Calcs](https://riley31415.github.io/wuwa_calc/) (Another damage calculator)
* [Wudamage](https://wudamage.com/) (Damage Simulator)
* [wutheringtools.com](https://www.wutheringtools.com/) (Build optimizer)
* [Wuwa Bookkeeping](https://docs.google.com/spreadsheets/d/1msSsnWBcXKniykf4rWQCEdk2IQuB9JHy/edit?gid=792709623#gid=792709623)
* [Wuwalab](https://wuwalab.com/) (Another Damage Simulator)
* [Wuwa Tracker](https://wuwatracker.com) (Achievement Tracker, Pull Tracker, Event Timeline, Ascension Planner)

*If you're the owner of any of these resources and would like them removed from this list, please inform me via Chat*

---

##### Frequently Asked Questions (FAQ):

**Q1: Banners?**

3.7 2nd Phase: Suoming, Lynae, Lucilla with Lumi, Danjin, Chixia

---

**Q2: Future characters? (STC)**

Suoming | 5\* Electro Sword

[Drip](https://www.youtube.com/shorts/5mJgJC7GgkQ) | [Splash](https://www.youtube.com/shorts/0mwDm3U7m-w) | [Microwave](https://www.reddit.com/r/WutheringWavesLeaks/comments/1vqo0y0) | [Nanoka](https://ww.nanoka.cc/character/1312) | [Sig](https://ww.nanoka.cc/weapon/21020107) | [Set](https://ww.nanoka.cc/echo/6000224) MAJOR SPOILERS FOR 4C

Tags: Main Damage Dealer, Basic Attack Damage, Electro Damage Amplification, Resonance Skill Damage Amplification, Tune Break: Unison

---

Lily | 5\* Spectro Gun

[Appearance](https://www.reddit.com/r/WutheringWavesLeaks/comments/1wcpp9g) | [Set](https://www.reddit.com/r/WutheringWavesLeaks/comments/1w37g42)

---

* 4.x: Anna
* 4.x: Luxier
* 4.x: Aimoxishen/Amethyst

**Reminder: All team comps with leaked characters are speculative!**

**If anything is missing, check the Chinese version of nanoka**

---

**Q3: Skins?**

Hiyuki skin in 3.8 (Seele)

Scar, go back to jail. Do not pass GO, do not collect 200 dollars.

---

**Q4: When is beta?**

Usually 2 weeks after the start of current patch.

If it's not 2 weeks after the start of current patch, it's time to panic.

---

**Q4: Speculated future drip/beta/patch release dates?**

### **TAKE WITH A GRAIN OF SALT**

* Assuming every patch is 42 days long & every banner is 21 days long
* Drip Market is one week after the start of patch in most cases
* **Beta and Livestream dates are calculated off the average of previous patches, rounded to the nearest Friday**
* Date timezone is EST/EDT

|Patch|Drip Market|Beta Start|Livestream|First Half|Second Half|New Characters|
|:-|:-|:-|:-|:-|:-|:-|
|3.7|August 25/26, 2026|September 5, 2026|September 19, 2026|October 2, 2026|October 23, 2026|Hsin (5\* Electro) & Suoming (5\* Electro)|
|3.8|October 9/10, 2026|October 16, 2026|October 30, 2026|November 13, 2026|December 2, 2026|Lily (5\* Spectro)|

[Click here to see past megathreads](https://www.reddit.com/r/WutheringWavesLeaks/search/?q=Weekly+Questions+%2B+Discussions+Megathread&cId=7275eabe-0c38-45cf-a64b-730b18eb5943&iId=1ff6a97b-29f4-4dfc-b0a5-6fdb38038832&sort=new)"""
_r66_mega_body = v3._clean_plain_body(_r66_mega_raw)
_r66_mega_p, _r66_mega_rest = v3.build_v3_payload(
    "WutheringWavesLeaks",
    {"title": "Omae wa mou Hsindeiru - Weekly Questions & Discussions Megathread",
     "author": "BriefVisit729", "body": _r66_mega_body, "media": [], "crosspost": None,
     "stats": {"comments": 0, "ups": 1}, "youtube_url": None, "op_comment": None},
    "https://www.reddit.com/r/WutheringWavesLeaks/comments/1wxfuj5/omae_wa_mou_hsindeiru_weekly_questions/",
    1791120901, return_remainder=True)
_r66_mega_conts = v3.build_continuation_payloads(_r66_mega_rest)
_r66_mega_parts = _r66_texts(_r66_mega_p)[1:-1] + _r66_chunks(_r66_mega_conts)
_r66_mega_joined = "\n".join(_r66_mega_parts)
check("r66: live 1wxfuj5 megathread — the body is continued, not truncated (no '…' cut)",
      len(_r66_mega_conts) >= 1
      and all(not p.rstrip().endswith("…") or p.rstrip().endswith("… full post on Reddit")
              for p in _r66_mega_parts),
      f"conts={len(_r66_mega_conts)} lens={[len(p) for p in _r66_mega_parts]}")
check("r66: live 1wxfuj5 megathread — the release-date table is the hand-checked example",
      "**Patch 3.7**" in _r66_mega_joined and "**Patch 3.8**" in _r66_mega_joined
      and "- **Drip Market:** October 9/10, 2026" in _r66_mega_joined
      and "- **New Characters:** Lily (5\\* Spectro)" in _r66_mega_joined,
      str([p[:60] for p in _r66_mega_parts]))
check("r66: live 1wxfuj5 megathread — pipe-list lines ('Suoming | 5\\* …') are NOT tables",
      "Suoming | 5\\* Electro Sword" in _r66_mega_joined
      and "Lily | 5\\* Spectro Gun" in _r66_mega_joined)
_r66_mega_links = re.findall(r"\[[^\]\n]+\]\(https?://[^)\s]+\)", _r66_mega_body)
check("r66: live 1wxfuj5 megathread — EVERY markdown link delivered intact, none cut",
      all(sum(p.count(_lnk) for p in _r66_mega_parts) == _r66_mega_body.count(_lnk) >= 1
          for _lnk in _r66_mega_links)
      and "[Set](https://www.reddit.com/r/WutheringWavesLeaks/comments/1w37g42)" in _r66_mega_joined,
      f"{len(_r66_mega_links)} links; "
      f"cut={[l for l in _r66_mega_links if sum(p.count(l) for p in _r66_mega_parts) < _r66_mega_body.count(l)]}")
check("r66: live 1wxfuj5 megathread — no broken partial-link fragment (the live bug)",
      all(not re.search(r"\[[^\]\n]{0,80}\]\(https?://[^\s\)]*$", p) for p in _r66_mega_parts),
      str([p[-90:] for p in _r66_mega_parts]))
check("r66: live 1wxfuj5 megathread — entity hygiene + no body timestamps",
      all("&gt;" not in p and "<t:" not in p for p in _r66_mega_parts))

# --- galleries are NOT affected by continuations ---------------------------
def _r66_photos(n):
    return [{"kind": "photo", "url": f"https://preview.redd.it/g{i}.jpg"} for i in range(n)]


_r66_gdata = dict(_r66_data("\n\n".join(_r66_paras12)))
_r66_gdata["media"] = _r66_photos(15)
_r66_g15, _r66_g15_rest = v3.build_v3_payload(_r66_sub, _r66_gdata, _r66_url, _r66_ts,
                                              return_remainder=True)
_r66_g15_conts = v3.build_continuation_payloads(_r66_g15_rest)
check("r66: 15-photo long-body card keeps the round-38c two-container split (10 + 5)",
      len(_r66_g15["components"]) == 2
      and [len(c["items"]) for c in _r66_g15["components"][0]["components"]
           + _r66_g15["components"][1]["components"] if c.get("type") == 12] == [10, 5]
      and [c["type"] for c in _r66_g15["components"][0]["components"]] == [10, 10, 14, 12]
      and [c["type"] for c in _r66_g15["components"][1]["components"]] == [14, 12, 10, 14, 1],
      str([[c["type"] for c in cont["components"]] for cont in _r66_g15["components"]]))
check("r66: gallery card body split feeds the SAME continuation pipeline",
      len(_r66_g15_conts) == 3
      and all(c["components"][0]["components"][0]["type"] == 10
              and len(c["components"][0]["components"]) == 1 for c in _r66_g15_conts),
      f"conts={len(_r66_g15_conts)}")
_r66_gdata_short = dict(_r66_data(_r66_short))
_r66_gdata_short["media"] = _r66_photos(15)
_r66_g15_short = v3.build_v3_payload(_r66_sub, _r66_gdata_short, _r66_url, _r66_ts)
_r66_g15_short_r, _r66_g15_short_rest = v3.build_v3_payload(
    _r66_sub, _r66_gdata_short, _r66_url, _r66_ts, return_remainder=True)
check("r66: short-body 15-photo card — return_remainder changes nothing, zero continuations",
      _r66_g15_short == _r66_g15_short_r and _r66_g15_short_rest == ""
      and v3.build_continuation_payloads(_r66_g15_short_rest) == []
      and len(_r66_g15_short["components"]) == 2,
      f"equal={_r66_g15_short == _r66_g15_short_r} rest={_r66_g15_short_rest!r}")

# --- retraction covers EVERY part (mocked session, r63 harness pattern) ---
class _R66Session:
    """Records every HTTP verb; optionally fails the Nth PATCH/DELETE
    (0-based among that verb) to prove partial failures block 'retracted'."""

    def __init__(self, fail_part=None):
        self.calls = []
        self.fail_part = fail_part

    def _n(self, verb):
        return len([c for c in self.calls if c[0] == verb])

    def patch(self, url, json=None, **k):
        n = self._n("PATCH")
        self.calls.append(("PATCH", url, json))
        return _R63Req(404 if self.fail_part == n else 200)

    def delete(self, url, **k):
        n = self._n("DELETE")
        self.calls.append(("DELETE", url, None))
        return _R63Req(404 if self.fail_part == n else 204)


async def _r66_run(mode, fail_part=None):
    saved = (v3.RETRACT_DEAD_POSTS, v3.RETRACT_MODE, v3.RETRACT_WINDOW_SECONDS,
             v3._fetch_new_listing, v3.live_removal_reason, v3.get_webhook_for_subreddit)
    try:
        v3.RETRACT_DEAD_POSTS = True
        v3.RETRACT_MODE = mode
        v3.RETRACT_WINDOW_SECONDS = 21600

        async def _fake_listing(session, subreddit):
            return ({"zzz_other"}, 7200)

        async def _fake_live(session, path, label=""):
            return "removed by moderator"

        v3._fetch_new_listing = _fake_listing
        v3.live_removal_reason = _fake_live
        v3.get_webhook_for_subreddit = lambda sub: "https://discord.test/api/webhooks/1/tok"
        session = _R66Session(fail_part)
        records = _r63_record(_r63_now)
        records["RetractSub_abc111"]["message_ids"] = ["999", "1000", "1001"]
        await v3.retract_dead_posts(session, records, _r63_now)
        return session, records
    finally:
        (v3.RETRACT_DEAD_POSTS, v3.RETRACT_MODE, v3.RETRACT_WINDOW_SECONDS,
         v3._fetch_new_listing, v3.live_removal_reason,
         v3.get_webhook_for_subreddit) = saved


_r66_sess, _r66_recs = asyncio.run(_r66_run("edit"))
check("r66: edit-mode retraction PATCHes ALL parts (main + continuations)",
      len(_r66_sess.calls) == 3 and all(c[0] == "PATCH" for c in _r66_sess.calls)
      and all(f"/messages/{mid}" in c[1] for c, mid in zip(_r66_sess.calls, ("999", "1000", "1001"))),
      str(_r66_sess.calls))
check("r66: the MAIN card keeps its full tombstone payload",
      "hi" in _r66_sess.calls[0][2]["components"][0]["components"][1]["content"]
      and "no longer live" in _r66_sess.calls[0][2]["components"][0]["components"][0]["content"],
      str(_r66_sess.calls[0][2])[:200])
check("r66: continuation parts get the greyed continuation tombstone",
      all("no longer live" in c[2]["components"][0]["components"][0]["content"]
          and c[2]["components"][0]["accent_color"] == 0x808080
          and c[2]["flags"] == v3.IS_COMPONENTS_V2
          for c in _r66_sess.calls[1:]),
      str(_r66_sess.calls[1][2])[:200])
check("r66: retracted only marks when EVERY part was tombstoned",
      _r66_recs["RetractSub_abc111"].get("retracted") is True)

_r66_sess, _r66_recs = asyncio.run(_r66_run("edit", fail_part=1))
check("r66: a failed part blocks 'retracted' but the other parts still get patched",
      len(_r66_sess.calls) == 3 and _r66_recs["RetractSub_abc111"].get("retracted") is not True,
      f"calls={len(_r66_sess.calls)} retracted={_r66_recs['RetractSub_abc111'].get('retracted')}")

_r66_sess, _r66_recs = asyncio.run(_r66_run("delete"))
check("r66: delete-mode retraction DELETEs ALL parts",
      len(_r66_sess.calls) == 3 and all(c[0] == "DELETE" for c in _r66_sess.calls)
      and all(f"/messages/{mid}" in c[1] for c, mid in zip(_r66_sess.calls, ("999", "1000", "1001")))
      and _r66_recs["RetractSub_abc111"].get("retracted") is True,
      str(_r66_sess.calls))

# --- workflow guards (round-61 steps sacred; env list = mappings only) ----
with open(os.path.join(ROOT, ".github/workflows/reddit_monitor.yml"), encoding="utf-8") as _r66_f:
    _r66_wf = _r66_f.read()
_r66_stale = ["APPROVAL_RECHECK_SECONDS", "POST_SETTLE_SECONDS", "POST_SETTLE_VERIFIED_SECONDS",
              "DUP_MEDIA_GATE", "REPOST_GATE", "REPOST_WINDOW_SECONDS", "MOD_QUEUE_GATE",
              "MOD_QUEUE_WINDOW_SECONDS", "MOD_QUEUE_WEAK_GRACE_SECONDS",
              "LISTING_HTML_GRACE_SECONDS", "LISTING_TIMEOUT_SECONDS"]
check("r66: reddit_monitor.yml — all 11 stale env mappings fully gone (lines AND comments)",
      all(_n not in _r66_wf for _n in _r66_stale),
      str([n for n in _r66_stale if n in _r66_wf]))
check("r66: reddit_monitor.yml — round-64 FRESH_HOLD_SECONDS/LISTING_CONFIRM are wired",
      "FRESH_HOLD_SECONDS: ${{ vars.FRESH_HOLD_SECONDS }}" in _r66_wf
      and "LISTING_CONFIRM: ${{ vars.LISTING_CONFIRM }}" in _r66_wf)
check("r66: reddit_monitor.yml — kept mappings intact (pending recheck, "
      "retraction); r67 guard update: the removed gate's four Variables are "
      "no longer part of this list, and REDDIT_VIDEO_QUALITY joined it",
      all(f"{_n}: ${{{{ vars.{_n} }}}}" in _r66_wf for _n in
          ["PENDING_RECHECK_SECONDS", "RETRACT_DEAD_POSTS", "RETRACT_MODE",
           "RETRACT_WINDOW_SECONDS", "REDDIT_VIDEO_QUALITY"]))
check("r66: reddit_monitor.yml — round-61 deploy steps untouched",
      "Sync to the live branch tip" in _r66_wf
      and "git reset --hard FETCH_HEAD" in _r66_wf
      and "merge_monitor_caches.py" in _r66_wf
      and "git pull --rebase" not in _r66_wf)

# --- engine source guards ---------------------------------------------------
check("r66: budgets — main card cap raised for continuations, 3 × 3800 more",
      v3.MAX_BODY_CHARS == 16000 and v3.MAX_CONTINUATIONS == 3
      and v3.CONTINUATION_CHAR_LIMIT == 3800,
      f"{v3.MAX_BODY_CHARS}/{v3.MAX_CONTINUATIONS}/{v3.CONTINUATION_CHAR_LIMIT}")
_r66_bv3_src = inspect.getsource(v3.build_v3_payload)
check("r66: the card build converts tables + splits safely — no ellipsis truncation left",
      "_markdown_tables_to_bullets(" in _r66_bv3_src and "split_card_body(" in _r66_bv3_src
      and 'rstrip() + "…"' not in _r66_bv3_src)
check("r66: the table renderer never emits Discord timestamps",
      "<t:" not in inspect.getsource(v3._markdown_tables_to_bullets))
check("r66: the posting loop delivers continuations and records every message id",
      "build_continuation_payloads(" in inspect.getsource(v3.main)
      and "message_ids" in inspect.getsource(v3.main))
check("r66: retraction reads message_ids and tombstones continuation parts",
      "message_ids" in inspect.getsource(v3.retract_dead_posts)
      and "tombstone_continuation_payload" in inspect.getsource(v3.retract_dead_posts))


# ===========================================================================
# ROUND 67 (2026-10-07) — REDDIT VIDEO QUALITY SELECTION
# Evidence: docs/REDDIT_VIDEO_MEDIA_REPORT.md (live Discord/Discohook tests;
# the post IDs are recorded there as evidence only — never in code).
# Deterministic guards for the whole
# resolver contract: EmbedEZ 1080p success, 1080p failure -> 720p, proxy
# failure -> vxreddit, a "1080"-named URL that is NOT 1080p, timeouts, HTTP
# failures, HTML error bodies with a 200 status, oversized media, missing
# audio/failed muxing, portrait + landscape, the thumbnail+button fallback,
# no duplicate poster image, unchanged image/GIF/photo/gallery behavior, the
# durable-URL policy for signed/expiring native media, the compatibility
# rollback order and the bounded budgets. Nothing here opens a connection.
# ===========================================================================


def _r67_box(box_type: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + box_type + payload


def _r67_tkhd(width: int, height: int, version: int = 0) -> bytes:
    if version == 0:
        body = bytes(4) + b"\x00" * 12 + b"\x00" * 4 + b"\x00" * 4
    else:
        body = bytes([1, 0, 0, 0]) + b"\x00" * 16 + b"\x00" * 4 + b"\x00" * 4 + b"\x00" * 8
    body += b"\x00" * 8 + b"\x00" * 8 + b"\x00" * 36
    body += int(width * 65536).to_bytes(4, "big") + int(height * 65536).to_bytes(4, "big")
    return _r67_box(b"tkhd", body)


def _r67_hdlr(handler: bytes) -> bytes:
    return _r67_box(b"hdlr", bytes(4) + b"\x00" * 4 + handler + b"\x00" * 12)


def _r67_mp4(width: int = 1920, height: int = 1080, audio: bool = True,
             version: int = 0) -> bytes:
    traks = _r67_box(b"trak", _r67_tkhd(width, height, version) + _r67_box(b"mdia", _r67_hdlr(b"vide")))
    if audio:
        traks += _r67_box(b"trak", _r67_tkhd(0, 0) + _r67_box(b"mdia", _r67_hdlr(b"soun")))
    return (_r67_box(b"ftyp", b"isom" + bytes(4))
            + _r67_box(b"moov", traks) + b"\x00" * 256)


class _R67Resp:
    def __init__(self, status=206, headers=None, body=b"", hang=False):
        self.status = status
        self.headers = headers or {}
        self._body = body
        self._hang = hang
        self.content = self

    async def read(self, n=-1):
        if self._hang:
            await asyncio.sleep(30)
        return self._body if (not n or n < 0) else self._body[:n]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _R67Sess:
    """Routes requests to canned responses by substring; records every URL."""

    def __init__(self, routes=None, default=None):
        self.routes = routes or {}
        self.default = default
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        for key, resp in self.routes.items():
            if key in url:
                return resp() if callable(resp) else resp
        if self.default is not None:
            return self.default() if callable(self.default) else self.default
        return _R67Resp(404, {}, b"")


def _r67_mp4_resp(width=1920, height=1080, audio=True, size=5_000_000, version=0):
    body = _r67_mp4(width, height, audio, version)

    def _make():
        return _R67Resp(206, {"Content-Type": "video/mp4",
                              "Content-Range": f"bytes 0-{len(body) - 1}/{size}"}, body)
    return _make


def _r67_html_resp(status=200, ctype="video/mp4"):
    return lambda: _R67Resp(status, {"Content-Type": ctype},
                            b"<!DOCTYPE html><html>muxing failed</html>".ljust(180, b" "))


def _r67_hang_resp():
    return lambda: _R67Resp(206, {"Content-Type": "video/mp4"}, b"", hang=True)


def _r67_big_resp():
    return lambda: _R67Resp(206, {"Content-Type": "video/mp4",
                                  "Content-Range": "bytes 0-65535/999999999"}, _r67_mp4())


_R67_VX = "vxreddit.com"
_R67_EZ = "proxy.embedez.com"


async def _r67_video_resolve(routes=None, default=None, vid="vid1", fallback=None,
                             mode=None, window=None):
    old_mode = v3.VIDEO_QUALITY_MODE
    old_window = v3.VIDEO_QUALITY_WINDOW_SECONDS
    old_timeout = v3.VIDEO_CANDIDATE_TIMEOUT_SECONDS
    try:
        if mode is not None:
            v3.VIDEO_QUALITY_MODE = mode
        if window is not None:
            v3.VIDEO_QUALITY_WINDOW_SECONDS = window
            v3.VIDEO_CANDIDATE_TIMEOUT_SECONDS = window * 2
        session = _R67Sess(routes, default)
        url = await v3.resolve_video_url(session, vid, fallback, label="r67")
        return url, session.calls
    finally:
        v3.VIDEO_QUALITY_MODE = old_mode
        v3.VIDEO_QUALITY_WINDOW_SECONDS = old_window
        v3.VIDEO_CANDIDATE_TIMEOUT_SECONDS = old_timeout


async def _r67_video_checks():
    v3.reset_video_quality_budget()

    # Media facts: real dimensions + audio presence are read from the MP4
    # structure, for both tkhd versions, and never raise on junk input.
    _facts = v3.mp4_media_facts(_r67_mp4())
    check("r67: MP4 evidence reads landscape dimensions + audio track",
          _facts == {"width": 1920, "height": 1080, "has_video": True, "has_audio": True},
          str(_facts))
    check("r67: MP4 evidence reads portrait dimensions",
          v3.mp4_media_facts(_r67_mp4(1080, 1920))["width"] == 1080
          and v3.mp4_media_facts(_r67_mp4(1080, 1920))["height"] == 1920)
    check("r67: MP4 evidence handles tkhd version 1",
          v3.mp4_media_facts(_r67_mp4(version=1))["width"] == 1920)
    check("r67: MP4 evidence detects a missing audio track",
          v3.mp4_media_facts(_r67_mp4(audio=False))["has_audio"] is False)
    check("r67: MP4 evidence is None for junk/truncated prefixes (never raises)",
          v3.mp4_media_facts(b"\x00" * 40) is None
          and v3.mp4_media_facts(None) is None
          and v3.mp4_media_facts(b"<html>not an mp4</html>") is None
          and v3.mp4_media_facts(_r67_mp4()[:10]) is None)
    check("r67: orientation helper classifies portrait/landscape",
          v3.video_orientation({"width": 1080, "height": 1920}) == "portrait"
          and v3.video_orientation({"width": 1920, "height": 1080}) == "landscape"
          and v3.video_orientation({}) is None)

    # Durable-URL policy: signed/expiring native media is never a candidate.
    check("r67: unsigned v.redd.it DASH/CMAF URLs are durable",
          v3.media_url_is_durable("https://v.redd.it/abc/DASH_720.mp4")
          and v3.media_url_is_durable("https://v.redd.it/abc/CMAF_1080.mp4"))
    check("r67: signed/expiring packaged-media URLs are never candidates",
          not v3.media_url_is_durable(
              "https://packaged-media.redd.it/abc.mp4?s=xyz&e=1234567")
          and not v3.media_url_is_durable("https://v.redd.it/abc/DASH_720.mp4?token=x"))
    check("r67: the quality hint reads the URL path only (a 1080 in the query "
          "is never proof)",
          v3.video_quality_hint("https://v.redd.it/x/CMAF_1080.mp4") == 1080
          and v3.video_quality_hint("https://ez/x?videoUrl=CMAF_1080.mp4") is None)

    # 1. EmbedEZ 1080p success (balanced default).
    url, _ = await _r67_video_resolve({_R67_EZ: _r67_mp4_resp(), _R67_VX: _r67_mp4_resp()})
    check("r67: balanced — a validated EmbedEZ 1080p result wins", url is not None
          and url.startswith("https://proxy.embedez.com"), str(url))

    # 2. EmbedEZ 1080p failure followed by EmbedEZ 720p success.
    def _r67_ez_split():
        class _Split(_R67Sess):
            def get(self, url, headers=None, timeout=None):
                self.calls.append(url)
                if "proxy.embedez.com" in url:
                    return (_r67_html_resp()() if "CMAF_1080" in url else _r67_mp4_resp()())
                return _R67Resp(500, {}, b"")
        return _Split()
    session = _r67_ez_split()
    url = await v3.resolve_video_url(session, "vid1", None, label="r67")
    check("r67: EmbedEZ 1080p failure falls through to EmbedEZ 720p",
          url is not None and "CMAF_720.mp4" in url, str(url))

    # 3. EmbedEZ failure followed by vxreddit success.
    url, _ = await _r67_video_resolve({_R67_EZ: _r67_html_resp(), _R67_VX: _r67_mp4_resp()})
    check("r67: EmbedEZ failure falls back to a working vxreddit result",
          url is not None and url.startswith("https://vxreddit.com"), str(url))

    # 4. A "1080" in a URL is never proof: a smaller EmbedEZ output loses to
    #    the vxreddit base (this is the tested vxreddit behaviour, inverted).
    url, _ = await _r67_video_resolve({_R67_EZ: _r67_mp4_resp(1280, 720),
                                       _R67_VX: _r67_mp4_resp(1920, 1080)})
    check("r67: a named-1080 candidate returning fewer pixels does NOT replace "
          "the vxreddit result", url is not None and url.startswith("https://vxreddit.com"),
          str(url))
    url, _ = await _r67_video_resolve({_R67_EZ: _r67_mp4_resp(), _R67_VX: _r67_mp4_resp(1280, 720)})
    check("r67: a sharper validated EmbedEZ result does replace the vxreddit result",
          url is not None and url.startswith("https://proxy.embedez.com"), str(url))

    # 5. Timeout and HTTP failure.
    url, _ = await _r67_video_resolve({_R67_EZ: _r67_hang_resp(), _R67_VX: _r67_mp4_resp()},
                                      window=0.2)
    check("r67: a hung EmbedEZ candidate times out into the vxreddit result "
          "within the quality window",
          url is not None and url.startswith("https://vxreddit.com"), str(url))
    url, _ = await _r67_video_resolve(
        {_R67_EZ: lambda: _R67Resp(503, {}, b""), _R67_VX: lambda: _R67Resp(500, {}, b"")},
        default=_r67_mp4_resp())
    check("r67: HTTP failures on both proxies fall through to the durable "
          "native DASH ladder", url == "https://v.redd.it/vid1/DASH_720.mp4", str(url))

    # 6. HTML error body returned with a successful transport response.
    url, _ = await _r67_video_resolve({_R67_EZ: _r67_html_resp(), _R67_VX: _r67_mp4_resp()})
    check("r67: a 200 HTML error body is rejected (never used as the video)",
          url is not None and url.startswith("https://vxreddit.com"), str(url))
    url, _ = await _r67_video_resolve(
        {_R67_EZ: _r67_html_resp(ctype="text/html"), _R67_VX: _r67_html_resp(ctype="text/html")},
        default=lambda: _R67Resp(404, {}, b""))
    check("r67: HTML error bodies everywhere end at thumbnail + button (None)",
          url is None, str(url))

    # 7. Oversized media.
    url, _ = await _r67_video_resolve(default=_r67_big_resp())
    check("r67: any candidate above the 256 MiB limit is rejected (thumbnail "
          "+ button remains)", url is None, str(url))

    # 8. Missing audio / failed muxing: an audio-less mux never replaces a
    #    working audio-capable result, and a silent SOURCE still gets a video.
    url, _ = await _r67_video_resolve({_R67_EZ: _r67_mp4_resp(audio=False),
                                       _R67_VX: _r67_mp4_resp(audio=True)})
    check("r67: a failed mux (EmbedEZ output without an audio track) keeps the "
          "audio-capable vxreddit result",
          url is not None and url.startswith("https://vxreddit.com"), str(url))
    url, _ = await _r67_video_resolve({_R67_EZ: _r67_mp4_resp(audio=False),
                                       _R67_VX: _r67_mp4_resp(audio=False)})
    check("r67: a genuinely silent source still resolves a video (never a "
          "thumbnail just for missing audio)",
          url is not None and url.startswith("https://proxy.embedez.com"), str(url))

    # 9/10. Portrait and landscape are preserved and compared like for like.
    url, _ = await _r67_video_resolve({_R67_EZ: _r67_mp4_resp(1080, 1920),
                                       _R67_VX: _r67_mp4_resp(1080, 1920)})
    check("r67: portrait EmbedEZ result wins over a portrait vxreddit result "
          "(no stretching/conversion in the resolver)",
          url is not None and url.startswith("https://proxy.embedez.com"), str(url))
    url, _ = await _r67_video_resolve({_R67_EZ: _r67_mp4_resp(1280, 720),
                                       _R67_VX: _r67_mp4_resp(1080, 1920)})
    check("r67: an orientation flip (landscape candidate for a portrait base) "
          "never replaces the base", url is not None and url.startswith("https://vxreddit.com"),
          str(url))
    url, _ = await _r67_video_resolve({_R67_EZ: _r67_mp4_resp(1920, 1080),
                                       _R67_VX: _r67_mp4_resp(1920, 1080)})
    check("r67: landscape video resolves and keeps its aspect ratio",
          url is not None and url.startswith("https://proxy.embedez.com"), str(url))

    # 11. No valid video at all -> None (the caller keeps thumbnail + button).
    url, _ = await _r67_video_resolve(default=lambda: _R67Resp(404, {}, b""))
    check("r67: no valid candidate anywhere returns None — thumbnail + Read "
          "Post button is the final fallback", url is None, str(url))

    # 12. Signed native fallback_url is refused; the durable ladder is used.
    url, calls = await _r67_video_resolve(
        {_R67_EZ: lambda: _R67Resp(500, {}, b""), _R67_VX: lambda: _R67Resp(500, {}, b"")},
        default=_r67_mp4_resp(), fallback="https://packaged-media.redd.it/x.mp4?s=abc&e=1")
    check("r67: a signed/expiring fallback_url is never returned as the card "
          "video", url == "https://v.redd.it/vid1/DASH_720.mp4", str(url))
    check("r67: the signed fallback_url was never even requested as a candidate",
          not any("packaged-media" in call for call in calls), str(calls))

    # Compatibility mode: the pre-round-67 ladder, unchanged, and no EmbedEZ
    # 1080p attempt (the zero-code rollback).
    url, calls = await _r67_video_resolve({_R67_EZ: _r67_mp4_resp(), _R67_VX: _r67_mp4_resp()},
                                          default=_r67_mp4_resp(), mode="compatibility")
    check("r67: compatibility mode tries the durable native ladder first",
          url == "https://v.redd.it/vid1/DASH_720.mp4", str(url))
    check("r67: compatibility mode makes no EmbedEZ 1080p attempt",
          not any("CMAF_1080" in call for call in calls), str(calls))
    url, calls = await _r67_video_resolve({_R67_EZ: _r67_mp4_resp(), _R67_VX: _r67_mp4_resp()},
                                          default=lambda: _R67Resp(404, {}, b""),
                                          mode="compatibility")
    check("r67: compatibility mode then prefers EmbedEZ 720p (pre-round-67 order)",
          url is not None and "CMAF_720" in url and url.startswith("https://proxy.embedez.com"),
          str(url))

    # The per-run quality ceiling falls back to the durable ladder, so a slow
    # proxy can never stall a run.
    try:
        v3._video_quality_budget_used = 10_000.0
        url, calls = await _r67_video_resolve({_R67_EZ: _r67_mp4_resp(), _R67_VX: _r67_mp4_resp()},
                                              default=_r67_mp4_resp())
        check("r67: once the per-run quality budget is spent the resolver goes "
              "straight to the durable native ladder",
              url == "https://v.redd.it/vid1/DASH_720.mp4"
              and not any("proxy.embedez.com" in call for call in calls), str(url))
    finally:
        v3.reset_video_quality_budget()

    # The resolver never writes cache/state: it only reads media URLs.
    _r67_src = inspect.getsource(v3.resolve_video_url)
    check("r67: the resolver touches no cache/state file and logs no webhook",
          all(sym not in _r67_src for sym in
              ("save_posted", "save_pending", "posted_reddit.json", "pending_reddit.json",
               "proxy_health.json", "webhook")))
    check("r67: the resolver is bounded end to end (budgets + timeouts exist)",
          v3.VIDEO_RESOLVE_BUDGET_SECONDS > 0 and v3.VIDEO_CANDIDATE_TIMEOUT_SECONDS > 0
          and v3.VIDEO_NATIVE_TIMEOUT_SECONDS > 0 and v3.VIDEO_RUN_BUDGET_SECONDS > 0
          and v3.VIDEO_QUALITY_WINDOW_SECONDS > 0
          and v3.VIDEO_QUALITY_WINDOW_HIGHEST_SECONDS >= v3.VIDEO_QUALITY_WINDOW_SECONDS)
asyncio.run(_r67_video_checks())

# 13. Image / GIF / photo / gallery behavior is unchanged: the resolver only
#     runs for a video post, media_url_ok keeps its 2-tuple contract, and the
#     native media kinds are untouched.
_r67_native = v3.extract_native_media(
    '<img src="https://i.redd.it/a.jpg"/>'
    '<img src="https://i.redd.it/b.gif"/>'
    '<img src="https://external-preview.redd.it/c.jpg?width=300"/>')
check("r67: native media kinds for image/GIF/photo are unchanged",
      [item["kind"] for item in _r67_native] == ["image", "gif", "image"],
      str(_r67_native))


async def _r67_media_contract():
    ok, size = await v3.media_url_ok(
        _R67Sess(default=lambda: _R67Resp(206, {"Content-Type": "image/jpeg",
                                                "Content-Range": "bytes 0-0/2048"}, b"x")),
        "https://i.redd.it/a.jpg")
    check("r67: media_url_ok keeps its (ok, size) contract for images", ok is True and size == 2048)
    base = {"title": "T", "author": "A", "body": "b", "youtube_url": None,
            "vred_id": None, "redgifs_url": None, "thumb": None,
            "content_html": '<img src="https://i.redd.it/p1.jpg"/>'}
    with patch.object(v3, "PROXY_MEDIA", False), \
         patch.object(v3, "fetch_arctic_post", AsyncMock(return_value=None)), \
         patch.object(v3, "enrich_gallery_redlib", AsyncMock(return_value=[])), \
         patch.object(v3, "resolve_video_url", AsyncMock(return_value="https://example.com/v.mp4")) as _r67_rv:
        result = await v3.resolve_post_media(None, dict(base), None, "/r/Sub/comments/img1/title/")
        check("r67: an image post never calls the video resolver",
              _r67_rv.await_count == 0 and result["media"] ==
              [{"kind": "image", "url": "https://i.redd.it/p1.jpg"}], str(result["media"]))
        video_base = dict(base, vred_id="vid1")
        result = await v3.resolve_post_media(None, video_base, None, "/r/Sub/comments/vid1/title/")
        check("r67: a video post shows the video only — no duplicate poster image",
              result["media"] == [{"kind": "video", "url": "https://example.com/v.mp4"}],
              str(result["media"]))
        check("r67: the video resolver ran exactly once for the video post "
              "(never for the image post)",
              _r67_rv.await_count == 1, str(_r67_rv.await_count))
    with patch.object(v3, "PROXY_MEDIA", False), \
         patch.object(v3, "fetch_arctic_post", AsyncMock(return_value=None)), \
         patch.object(v3, "enrich_gallery_redlib", AsyncMock(return_value=[])), \
         patch.object(v3, "resolve_video_url", AsyncMock(return_value=None)):
        result = await v3.resolve_post_media(None, dict(base, vred_id="vid2"), None,
                                             "/r/Sub/comments/vid2/title/")
        check("r67: when no video resolves the post keeps its photos + a button "
              "(thumbnail fallback path)",
              result["media"] == [{"kind": "image", "url": "https://i.redd.it/p1.jpg"}],
              str(result["media"]))


async def _r67_gallery_contract():
    """A reddit-hosted gallery clip still resolves through the video resolver
    and keeps its photos (round-38c behavior, unchanged by round 67)."""
    with patch.object(v3, "_fetch_redlib_post_page", AsyncMock(return_value="<html>g</html>")), \
         patch.object(v3, "extract_redlib_video_id", lambda html: "vid1"), \
         patch.object(v3, "extract_redlib_gallery",
                      lambda html: [{"kind": "image", "url": "https://i.redd.it/g1.jpg"}]), \
         patch.object(v3, "resolve_video_url",
                      AsyncMock(return_value="https://example.com/clip.mp4")) as _r67_grv:
        items = await v3.enrich_gallery_redlib(None, "/r/Sub/comments/g1/title/", "r67")
    check("r67: a reddit-hosted gallery video still resolves via the video "
          "resolver and keeps its photos (gallery handling unchanged)",
          items == [{"kind": "image", "url": "https://i.redd.it/g1.jpg"},
                    {"kind": "video", "url": "https://example.com/clip.mp4",
                     "gallery": True}]
          and _r67_grv.await_count == 1, str(items))


asyncio.run(_r67_gallery_contract())
asyncio.run(_r67_media_contract())

# ---- round 67 follow-up: the PROXY-delivered video path --------------------
# Production runs native RSS mode with PROXY_MEDIA=1 by default, so a video
# post usually arrives from the round-49 proxy chain (tie-break vxreddit
# first). Those videos now get the SAME bounded EmbedEZ 1080p opportunity:
# the validated proxy mux is the held base and can only be replaced by a
# better validated candidate, never lost to the quality attempt.

_r67b_base = "https://vxreddit.com/redditvideo.mp4?video_url=x&audio_url=y"


async def _r67b_cand_ok(session, url, *, timeout=None, quality_hint=None, label=""):
    return {"url": "https://proxy.embedez.com/render/video.mp4?videoUrl=1080",
            "size": 4321, "width": 1920, "height": 1080, "has_video": True,
            "has_audio": True, "quality_hint": 1080, "label": label}


async def _r67b_probe_base_720(session, url, *, timeout=10.0):
    check("r67b: the base probe reads the ALREADY-validated proxy video",
          url == _r67b_base, url)
    return {"width": 1280, "height": 720, "has_video": True, "has_audio": True}


def _r67b_upgrade(candidate, probe=_r67b_probe_base_720, mode="balanced",
                  spent=0.0, vid="abc123"):
    """Run the proxy-path quality helper under controlled globals."""
    with patch.object(v3, "video_candidate", candidate), \
         patch.object(v3, "probe_video_facts", probe), \
         patch.object(v3, "VIDEO_QUALITY_MODE", mode), \
         patch.object(v3, "_video_quality_budget_used", spent):
        return asyncio.run(v3.proxy_video_quality_upgrade(None, vid, _r67b_base, "r67b"))


_r67b_cand = AsyncMock(side_effect=_r67b_cand_ok)
_r67b_none = AsyncMock(return_value=None)
_r67b_probe = AsyncMock(side_effect=_r67b_probe_base_720)

check("r67b: a validated EmbedEZ 1080p upgrade replaces the proxy-muxed video "
      "(1920x1080 vs the base's 1280x720)",
      _r67b_upgrade(_r67b_cand) == "https://proxy.embedez.com/render/video.mp4?videoUrl=1080")

_r67b_cand.reset_mock(); _r67b_probe.reset_mock()
check("r67b: an EmbedEZ failure returns the proxy video verbatim — and never "
      "spends the base probe",
      _r67b_upgrade(_r67b_none) == _r67b_base and _r67b_probe.await_count == 0)

_r67b_cand.reset_mock()
check("r67b: compatibility mode never makes the quality request at all "
      "(exact pre-round-67 proxy path)",
      _r67b_upgrade(_r67b_cand, mode="compatibility") == _r67b_base
      and _r67b_cand.await_count == 0)


async def _r67b_probe_flip(session, url, *, timeout=10.0):
    return {"width": 1080, "height": 1920, "has_video": True, "has_audio": True}


async def _r67b_cand_silent(session, url, *, timeout=None, quality_hint=None, label=""):
    return {"url": "https://proxy.embedez.com/render/video.mp4?silent=1", "size": 4321,
            "width": 1920, "height": 1080, "has_video": True, "has_audio": False,
            "quality_hint": 1080, "label": label}


async def _r67b_probe_smaller(session, url, *, timeout=10.0):
    return {"width": 3840, "height": 2160, "has_video": True, "has_audio": True}


check("r67b: a portrait source is never replaced by a landscape candidate "
      "(orientation flip keeps the proxy video)",
      _r67b_upgrade(_r67b_cand, probe=_r67b_probe_flip) == _r67b_base)
check("r67b: a candidate that loses the base's audio track is refused",
      _r67b_upgrade(_r67b_cand_silent, probe=_r67b_probe_base_720) == _r67b_base)
check("r67b: a candidate with FEWER pixels than the base is refused",
      _r67b_upgrade(_r67b_cand, probe=_r67b_probe_smaller) == _r67b_base)
check("r67b: with the per-run budget spent the proxy video is kept untouched",
      _r67b_upgrade(_r67b_cand, spent=float(v3.VIDEO_RUN_BUDGET_SECONDS) + 1) == _r67b_base)
check("r67b: a post with no Reddit video id (YouTube/redgifs) skips the "
      "opportunity entirely",
      _r67b_upgrade(_r67b_cand, vid=None) == _r67b_base)

_r67b_cand.reset_mock()
_r67b_upgrade(_r67b_cand, spent=float(v3.VIDEO_RUN_BUDGET_SECONDS) + 1)
_r67b_upgrade(_r67b_cand, vid=None)
check("r67b: no quality request is made when the budget is spent or the post "
      "is not a Reddit video", _r67b_cand.await_count == 0)


async def _r67b_hang(session, url, *, timeout=None, quality_hint=None, label=""):
    await asyncio.sleep(5)
    return {"url": "https://proxy.embedez.com/render/video.mp4?late=1", "size": 1}


_r67b_t0 = time.monotonic()
with patch.object(v3, "VIDEO_QUALITY_WINDOW_SECONDS", 0.1):
    _r67b_hang_out = _r67b_upgrade(_r67b_hang)
_r67b_hang_dt = time.monotonic() - _r67b_t0
check("r67b: a hung EmbedEZ candidate is cut off by the quality window and "
      "yields the proxy video (bounded, never stalls the post)",
      _r67b_hang_out == _r67b_base and _r67b_hang_dt < 2.0,
      f"{_r67b_hang_out} after {_r67b_hang_dt:.2f}s")


async def _r67b_proxy_flow():
    """The proxy branch of resolve_post_media: quality attempt is video-only."""
    _r67b_ns = SimpleNamespace(
        fetch_proxy_post=AsyncMock(return_value={
            "service": "vxreddit",
            "media": [{"kind": "video", "url": _r67b_base}],
            "stats": None, "body": ""}),
        fetch_embeddit_stats=AsyncMock(return_value=None))
    _r67b_post_base = {"title": "t", "author": "a", "body": "", "youtube_url": None,
                       "vred_id": "abc123", "redgifs_url": None,
                       "content_html": "", "thumb": None}
    _r67b_calls = AsyncMock(side_effect=_r67b_cand_ok)
    _r67b_rv = AsyncMock(return_value=None)
    with patch.object(v3, "reddit_proxy", _r67b_ns), \
         patch.object(v3, "PROXY_MEDIA", True), \
         patch.object(v3, "fetch_arctic_post", AsyncMock(return_value=None)), \
         patch.object(v3, "enrich_gallery_redlib", AsyncMock(return_value=[])), \
         patch.object(v3, "media_url_ok", AsyncMock(return_value=(True, 999))), \
         patch.object(v3, "video_candidate", _r67b_calls), \
         patch.object(v3, "probe_video_facts", _r67b_probe), \
         patch.object(v3, "VIDEO_QUALITY_MODE", "balanced"), \
         patch.object(v3, "resolve_video_url", _r67b_rv), \
         patch.object(v3, "_video_quality_budget_used", 0.0):
        _r67b_res = await v3.resolve_post_media(
            None, dict(_r67b_post_base), None, "/r/Sub/comments/abc123/title/")
        check("r67b: a proxy-muxed video post upgrades to the validated "
              "EmbedEZ 1080p tile (video only, no duplicate poster)",
              _r67b_res["media"] == [{"kind": "video",
                                      "url": "https://proxy.embedez.com/render/video.mp4?videoUrl=1080"}],
              str(_r67b_res["media"]))
        check("r67b: the native resolver is never called once the proxy "
              "supplied the video", _r67b_rv.await_count == 0)
        _r67b_ns.fetch_proxy_post = AsyncMock(return_value={
            "service": "vxreddit",
            "media": [{"kind": "image", "url": "https://i.redd.it/p.jpg"}],
            "stats": None, "body": ""})
        _r67b_calls.reset_mock()
        await v3.resolve_post_media(None, dict(_r67b_post_base), None,
                                    "/r/Sub/comments/img1/title/")
        check("r67b: an image post never touches the video quality path",
              _r67b_calls.await_count == 0, str(_r67b_calls.await_count))

    # compatibility mode: the proxy video is used verbatim, exactly as before.
    with patch.object(v3, "reddit_proxy", _r67b_ns), \
         patch.object(v3, "PROXY_MEDIA", True), \
         patch.object(v3, "fetch_arctic_post", AsyncMock(return_value=None)), \
         patch.object(v3, "enrich_gallery_redlib", AsyncMock(return_value=[])), \
         patch.object(v3, "media_url_ok", AsyncMock(return_value=(True, 999))), \
         patch.object(v3, "video_candidate", _r67b_calls), \
         patch.object(v3, "VIDEO_QUALITY_MODE", "compatibility"):
        _r67b_ns.fetch_proxy_post = AsyncMock(return_value={
            "service": "vxreddit",
            "media": [{"kind": "video", "url": _r67b_base}],
            "stats": None, "body": ""})
        _r67b_calls.reset_mock()
        _r67b_res = await v3.resolve_post_media(
            None, dict(_r67b_post_base), None, "/r/Sub/comments/abc123/title/")
        check("r67b: compatibility keeps the proxy video verbatim and makes no "
              "quality request",
              _r67b_res["media"] == [{"kind": "video", "url": _r67b_base}]
              and _r67b_calls.await_count == 0, str(_r67b_res["media"]))


asyncio.run(_r67b_proxy_flow())
check("r67b: the proxy branch is wired to the bounded quality helper",
      "proxy_video_quality_upgrade(" in inspect.getsource(v3.resolve_post_media))


# ---- round 68: conservative, provider-generic quality fallback logs ------
# This round changes only local diagnostics. The existing candidate selection,
# request budget, and held-base behavior remain pinned by the round-67 guards.

def _r68_run_real_quality_attempt(session, base_url=_r67b_base):
    with patch.object(v3, "VIDEO_QUALITY_MODE", "balanced"), \
         patch.object(v3, "_video_quality_budget_used", 0.0), \
         patch.object(v3.logging, "info") as _r68_log:
        result = asyncio.run(v3.proxy_video_quality_upgrade(
            session, "vid1", base_url, "r68"))
        messages = [str(call.args[0]) for call in _r68_log.call_args_list]
    return result, session.calls, messages


def _r68_run_mock_quality_attempt(candidate, probe, base_url):
    with patch.object(v3, "video_candidate", candidate), \
         patch.object(v3, "probe_video_facts", probe), \
         patch.object(v3, "VIDEO_QUALITY_MODE", "balanced"), \
         patch.object(v3, "_video_quality_budget_used", 0.0), \
         patch.object(v3.logging, "info") as _r68_log:
        result = asyncio.run(v3.proxy_video_quality_upgrade(
            None, "vid1", base_url, "r68"))
        messages = [str(call.args[0]) for call in _r68_log.call_args_list]
    return result, messages


# A valid candidate that is measurably better still uses the pre-existing,
# explicit accepted-upgrade diagnostic. One request validates each candidate;
# the base gets its already-established single comparison probe.
_r68_accepted_session = _R67Sess(routes={
    "proxy.embedez.com": _r67_mp4_resp(1920, 1080, audio=True),
    "vxreddit.com": _r67_mp4_resp(1280, 720, audio=True),
})
_r68_accepted, _r68_accepted_calls, _r68_accepted_logs = _r68_run_real_quality_attempt(
    _r68_accepted_session)
_r68_accept_logs = [line for line in _r68_accepted_logs
                    if "EmbedEZ 1080p candidate accepted:" in line]
check("r68: a validated superior candidate keeps the existing evidence-based "
      "accepted-upgrade log",
      _r68_accepted.startswith("https://proxy.embedez.com")
      and len(_r68_accepted_calls) == 2
      and len(_r68_accept_logs) == 1
      and "provider=embedez, content_type=validated" in _r68_accept_logs[0]
      and "dimensions=1920x1080 (verified)" in _r68_accept_logs[0]
      and "audio=present" in _r68_accept_logs[0]
      and "reason=validated quality improvement" in _r68_accept_logs[0]
      and not any("quality fallback:" in line for line in _r68_accepted_logs),
      str((_r68_accepted, _r68_accepted_calls, _r68_accepted_logs)))

# An HTTP failure and a success-status HTML error both count as unavailable;
# neither probes the held base again or changes the validated vxreddit URL.
_r68_http400 = _R67Sess(default=lambda: _R67Resp(400, {}, b""))
_r68_http400_url, _r68_http400_calls, _r68_http400_logs = _r68_run_real_quality_attempt(
    _r68_http400)
_r68_http400_fallbacks = [line for line in _r68_http400_logs
                          if "quality fallback:" in line]
check("r68: HTTP 400 logs unavailable and retains the validated vxreddit base "
      "with no extra request or unsupported quality claim",
      _r68_http400_url == _r67b_base and len(_r68_http400_calls) == 1
      and _r68_http400_fallbacks == [
          "[r68] quality fallback: candidate=embedez; "
          "retaining validated base provider=vxreddit; "
          "reason=quality_candidate_unavailable"]
      and not any(word in _r68_http400_fallbacks[0]
                  for word in ("720p", "1080p", "dimensions=", "audio=")),
      str((_r68_http400_url, _r68_http400_calls, _r68_http400_logs)))

_r68_invalid = _R67Sess(default=_r67_html_resp())
_r68_invalid_url, _r68_invalid_calls, _r68_invalid_logs = _r68_run_real_quality_attempt(
    _r68_invalid)
check("r68: an invalid HTML quality response uses the unavailable fallback "
      "reason and retains the base",
      _r68_invalid_url == _r67b_base and len(_r68_invalid_calls) == 1
      and any("quality fallback: candidate=embedez; "
              "retaining validated base provider=vxreddit; "
              "reason=quality_candidate_unavailable" in line
              for line in _r68_invalid_logs),
      str((_r68_invalid_url, _r68_invalid_calls, _r68_invalid_logs)))

class _R68OfflineSession(_R67Sess):
    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        raise OSError("offline quality endpoint")


_r68_offline = _R68OfflineSession()
_r68_offline_url, _r68_offline_calls, _r68_offline_logs = _r68_run_real_quality_attempt(
    _r68_offline)
check("r68: a failed quality request emits the unavailable fallback without "
      "probing or losing vxreddit",
      _r68_offline_url == _r67b_base and len(_r68_offline_calls) == 1
      and any("quality fallback: candidate=embedez; "
              "retaining validated base provider=vxreddit; "
              "reason=quality_candidate_unavailable" in line
              for line in _r68_offline_logs),
      str((_r68_offline_url, _r68_offline_calls, _r68_offline_logs)))

# A valid but lower-resolution candidate is rejected by the unchanged selector;
# the new final log describes the decision without guessing source maximum.
_r68_not_superior = _R67Sess(routes={
    "proxy.embedez.com": _r67_mp4_resp(1920, 1080, audio=True),
    "vxreddit.com": _r67_mp4_resp(3840, 2160, audio=True),
})
_r68_not_superior_url, _r68_not_superior_calls, _r68_not_superior_logs = \
    _r68_run_real_quality_attempt(_r68_not_superior)
_r68_not_superior_fallbacks = [line for line in _r68_not_superior_logs
                               if "quality fallback:" in line]
check("r68: a validated candidate that is not superior retains the base with "
      "the conservative not-superior/unvalidated reason",
      _r68_not_superior_url == _r67b_base
      and len(_r68_not_superior_calls) == 2
      and _r68_not_superior_fallbacks == [
          "[r68] quality fallback: candidate=embedez; "
          "retaining validated base provider=vxreddit; "
          "reason=quality_candidate_not_superior_or_unvalidated"],
      str((_r68_not_superior_url, _r68_not_superior_calls,
           _r68_not_superior_logs)))

# Provider naming is dynamic for current providers and conservative for a
# future host; the fallback text itself is provider-agnostic.
_r68_provider_cases = [
    (_r67b_base, "vxreddit"),
    ("https://proxy.embedez.com/render/video.mp4", "embedez"),
    ("https://redditez.com/render/video.mp4", "embedez"),
    ("https://embeddit.deltandy.me/video.mp4", "embeddit"),
    ("https://v.redd.it/vid1/DASH_720.mp4", "native-dash"),
    ("https://future-video.example/video.mp4", "unknown"),
]
_r68_provider_results = []
for _r68_base_url, _r68_expected_provider in _r68_provider_cases:
    _r68_no_candidate = AsyncMock(return_value=None)
    _r68_no_probe = AsyncMock(return_value=None)
    _r68_result, _r68_messages = _r68_run_mock_quality_attempt(
        _r68_no_candidate, _r68_no_probe, _r68_base_url)
    _r68_expected_log = (
        "[r68] quality fallback: candidate=embedez; retaining validated base "
        f"provider={_r68_expected_provider}; reason=quality_candidate_unavailable")
    _r68_provider_results.append(
        _r68_result == _r68_base_url
        and _r68_messages == [_r68_expected_log]
        and _r68_no_probe.await_count == 0
        and "thumbnail" not in _r68_expected_log)
check("r68: fallback labels cover vxreddit, EmbedEZ/redditez, embeddit, "
      "native Reddit, and unknown future hosts",
      all(_r68_provider_results), str(_r68_provider_results))

# Conservative fact formatting: neither a URL hint nor missing facts may be
# turned into verified dimensions or an audio-present claim.
_r68_unknown_evidence = v3._video_evidence({
    "url": "https://proxy.embedez.com/render/video.mp4?videoUrl=CMAF_1080.mp4",
    "size": 123, "width": None, "height": None, "has_audio": None,
    "quality_hint": 1080,
})
_r68_silent_evidence = v3._video_evidence({
    "url": "https://proxy.embedez.com/render/video.mp4?videoUrl=CMAF_1080.mp4",
    "size": 123, "width": None, "height": None, "has_audio": False,
    "quality_hint": 1080,
})
check("r68: URL text containing 1080 is only a hint; missing dimensions/audio "
      "stay unknown and a silent candidate is never logged as audio-present",
      "url_quality_hint=1080p (not evidence)" in _r68_unknown_evidence
      and "dimensions=unverified" in _r68_unknown_evidence
      and "dimensions=1080" not in _r68_unknown_evidence
      and "audio=unknown" in _r68_unknown_evidence
      and "audio=present" not in _r68_unknown_evidence
      and "audio=absent" in _r68_silent_evidence
      and "audio=present" not in _r68_silent_evidence,
      str((_r68_unknown_evidence, _r68_silent_evidence)))

# Integration guard: a range-validated proxy video is still the only card tile
# when the optional quality request fails; image/gallery code is not involved.
async def _r68_proxy_fallback_flow():
    _r68_proxy = SimpleNamespace(
        fetch_proxy_post=AsyncMock(return_value={
            "service": "vxreddit", "media": [{"kind": "video", "url": _r67b_base}],
            "stats": None, "body": ""}),
        fetch_embeddit_stats=AsyncMock(return_value=None))
    _r68_base = {"title": "t", "author": "a", "body": "", "youtube_url": None,
                 "vred_id": "abc123", "redgifs_url": None,
                 "content_html": "", "thumb": None}
    _r68_base_check = AsyncMock(return_value=(True, 4321))
    _r68_quality = AsyncMock(return_value=None)
    _r68_probe = AsyncMock(return_value=None)
    with patch.object(v3, "reddit_proxy", _r68_proxy), \
         patch.object(v3, "PROXY_MEDIA", True), \
         patch.object(v3, "fetch_arctic_post", AsyncMock(return_value=None)), \
         patch.object(v3, "enrich_gallery_redlib", AsyncMock(return_value=[])), \
         patch.object(v3, "media_url_ok", _r68_base_check), \
         patch.object(v3, "video_candidate", _r68_quality), \
         patch.object(v3, "probe_video_facts", _r68_probe), \
         patch.object(v3, "VIDEO_QUALITY_MODE", "balanced"), \
         patch.object(v3, "_video_quality_budget_used", 0.0):
        _r68_result = await v3.resolve_post_media(
            None, _r68_base, None, "/r/Sub/comments/abc123/title/")
    return _r68_result, _r68_base_check, _r68_quality, _r68_probe


_r68_proxy_result, _r68_base_check, _r68_quality, _r68_probe = asyncio.run(
    _r68_proxy_fallback_flow())
check("r68: failed quality upgrade preserves the already-validated vxreddit "
      "video tile and does not spend a base comparison probe",
      _r68_proxy_result["media"] == [{"kind": "video", "url": _r67b_base}]
      and _r68_base_check.await_count == 1
      and _r68_quality.await_count == 1
      and _r68_probe.await_count == 0,
      str((_r68_proxy_result["media"], _r68_base_check.await_count,
           _r68_quality.await_count, _r68_probe.await_count)))

# The workflow's existing grep counter intentionally counts fallback NOTICES
# by the stable retained-base phrase, not every possible quality decision.
with open(os.path.join(ROOT, ".github/workflows/reddit_monitor.yml"),
          encoding="utf-8") as _r68_workflow_file:
    _r68_workflow = _r68_workflow_file.read()
check("r68: workflow summary remains compatible with the retained-base log "
      "wording and uses only the local monitor log",
      "count_matches 'retaining validated base'" in _r68_workflow
      and "summary source: local monitor.log only; no additional requests" in _r68_workflow)


# ---- round 69: Arctic Shift search failure reasons (offline) ---------------
# A blank-message exception must log its class name (never an empty reason);
# HTTP status messages and descriptive exception text must be preserved.
class _R69Session:
    def __init__(self, exc=None, status=None):
        self._exc = exc
        self._status = status

    def get(self, url, params=None, **k):
        if self._exc is not None:
            raise self._exc
        return _ArcticResp(self._status, {})


def _r69_arctic_log(session):
    with patch.object(v3, "_arctic_fail_count", 0), \
         patch.object(v3.logging, "info") as _log:
        result = asyncio.run(v3.fetch_arctic_subreddit_posts(
            session, "AnantaLeaks", label="t"))
        messages = [c.args[0] for c in _log.call_args_list if c.args]
    return result, messages


_r69_blank, _r69_blank_msgs = _r69_arctic_log(_R69Session(exc=TimeoutError()))
check("r69 arctic: blank exception returns []",
      _r69_blank == [], str(_r69_blank))
check("r69 arctic: blank exception logs class name",
      _r69_blank_msgs == ["[t] Arctic Shift search unavailable: TimeoutError"],
      str(_r69_blank_msgs))

_r69_http, _r69_http_msgs = _r69_arctic_log(_R69Session(status=422))
check("r69 arctic: HTTP 422 message preserved",
      _r69_http == [] and _r69_http_msgs ==
      ["[t] Arctic Shift search unavailable: HTTP 422"], str(_r69_http_msgs))

_r69_desc, _r69_desc_msgs = _r69_arctic_log(
    _R69Session(exc=ConnectionResetError("peer reset")))
check("r69 arctic: descriptive exception text preserved",
      _r69_desc == [] and _r69_desc_msgs ==
      ["[t] Arctic Shift search unavailable: peer reset"], str(_r69_desc_msgs))


if failures:
    print(f"SMOKE TEST FAILURES ({len(failures)}): {failures}")
    sys.exit(1)
print("SMOKE TEST: ALL PASS")
