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
check("buttons: Read Post, YouTube, statics", labels == ["Read Post", "YouTube", "Citlali News", "Support"], str(labels))
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
check("r23 all text-only: the FIRST text-only result is the body/stats fallback",
      _r23c and _r23c["service"] == "redditez"
      and _r23c["stats"] == {"ups": 1, "comments": 2}, str(_r23c))

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
        winner, calls, results = await run((1, 1, 13))
        check('r25 1wj0p83: embeddit wins with all 13 items',
              winner == results[2] and len(winner['media']) == 13 and len(calls) == 3)
        check('r25 winner preserves source image order', winner['media'] == results[2]['media'])
        winner, calls, results = await run((0, 1, 13))
        check('r25 text/partial/full: embeddit wins after text-only redditez',
              winner == results[2] and calls == list(names))
        winner, _, results = await run((0, 2, 0), need_video=True)
        check('r25 need_video: no video keeps first non-video fallback', winner is results[0])
        winner, calls, results = await run((25, 13, 1))
        check('r25 over-cap: 25 items trimmed to 20 and chain stops',
              winner['service'] == 'redditez' and len(winner['media']) == 20
              and calls == ['redditez'])
        check('r25 cap preserves order and leaves original result untouched',
              winner is not results[0] and len(results[0]['media']) == 25
              and winner['media'] == results[0]['media'][:20]
              and winner['media'] is not results[0]['media'])
        winner, _, results = await run((2, 2, 1))
        check('r25 equal counts preserve priority', winner == results[0])
        winner, _, results = await run((0, 0, 0))
        check('r25 text-only fallback remains first', winner == results[0])
        winner, _, results = await run((13, 1, 20), video=True, need_video=True)
        check('r25 need_video: video beats larger thumbnail lists', winner == results[1])
        winner, calls, results = await run((20, 1, 13))
        check('r25 capacity: later services never called',
              winner == results[0] and calls == ['redditez'])
        winner, calls, results = await run((1, 2, 13), health={'embeddit': {'ok': False}})
        check('r25 health: marked-down service skipped', winner == results[1] and len(calls) == 2)
        winner, calls, results = await run((1, 2, 13), health={n: {'ok': False} for n in names})
        check('r25 health: all down retries every service', winner == results[2] and len(calls) == 3)
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
_r30_throttle = _r30_main.split("pending-post recheck throttle", 1)[-1].split("if entry is not None:", 1)[0]
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
check("r30 gates: all four skip paths record a pending entry",
      _r30_main.count("mark_pending(pending, unique_key") == 4
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
    _r30_many = {str(2100555373073790000 + i) for i in range(_r30_x.MAX_CACHE_SIZE + 25)}
    _r30_x.save_posted_urls(_r30_many)
    with open(_r30_x.CACHE_FILE, "r", encoding="utf-8") as _fh:
        _r30_trim = json.load(_fh)
    check("r30 P1: trim keeps the NEWEST MAX_CACHE_SIZE ids",
          len(_r30_trim) == _r30_x.MAX_CACHE_SIZE
          and _r30_trim == sorted(_r30_many)[-_r30_x.MAX_CACHE_SIZE:]
          and _r30_trim[-1] == max(_r30_many, key=int), str(_r30_trim[:2]))
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
check("r32: approval-queued post (not_live) is skipped before the short interval (+60 s)",
      v3._pending_throttle_skip(_r32_pend("not_live"), "k", 1060.0, _r32_arctic) is True)
check("r32: approval-queued post (not_live) is DUE at the short interval (+300 s)",
      v3._pending_throttle_skip(_r32_pend("not_live"), "k", 1300.0, _r32_arctic) is False)
check("r32: 'pending approval' (banner) re-checks on the short interval (+300 s due)",
      v3._pending_throttle_skip(_r32_pend("pending approval"), "k", 1300.0, _r32_arctic) is False)
check("r32: sources_down (transient) re-checks on the short interval (+60 s skipped)",
      v3._pending_throttle_skip(_r32_pend("sources_down"), "k", 1060.0, _r32_arctic) is True)
check("r32: media_wait keeps the round-30 30-min throttle (+60 s skipped, +30 min due)",
      v3._pending_throttle_skip(_r32_pend("media_wait"), "k", 1060.0, _r32_arctic) is True
      and v3._pending_throttle_skip(_r32_pend("media_wait"), "k", 2800.0, _r32_arctic) is False)
check("r32: unknown/missing reason falls back to the round-30 30-min throttle",
      v3._pending_throttle_skip(_r32_pend(None), "k", 1060.0, _r32_arctic) is True
      and v3._pending_throttle_skip(_r32_pend(None), "k", 2800.0, _r32_arctic) is False)
check("r32: the no-recheck set is exactly the removal/deletion reasons",
      v3._NO_RECHECK_REASONS == frozenset({
          "removal_notice", "removal notice", "title marker", "whole-body marker",
          "removed by moderator", "removed by moderators/filters", "deleted by author"}))
check("r32: the short-recheck set is exactly the approval/transient reasons",
      v3._SHORT_RECHECK_REASONS == frozenset(
          {"not_live", "sources_down", "pending approval"}))
check("r32: the mod-queue banner is recognized as 'pending approval'",
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


print()
if failures:
    print(f"SMOKE TEST FAILURES ({len(failures)}): {failures}")
    sys.exit(1)
print("SMOKE TEST: ALL PASS")
