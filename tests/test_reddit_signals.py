"""Pure Reddit signal tests — no network, no secrets.

Run:  python tests/test_reddit_signals.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Match the smoke test: these tests never open a connection, so lightweight
# stubs keep the suite runnable in offline/minimal sandboxes.
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

failures: list[str] = []
passes = 0


def check(label: str, cond, extra: str = ""):
    global passes
    print(("PASS " if cond else "FAIL ") + label + (f"  {extra}" if extra and not cond else ""))
    if cond:
        passes += 1
    else:
        failures.append(label)


def load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


signals = load_module("reddit_signals_unit", "testing area/reddit_signals.py")
v3 = load_module("reddit_v3_unit", "testing area/reddit_main_v3.py")

check("import: reddit_signals", signals is not None)
check("import: reddit_main_v3", v3 is not None)

# 18 removal cases ---------------------------------------------------------
_removal_cases = [
    ("[removed]", "body", "title marker"),
    ("[deleted]", "body", "title marker"),
    ("**[ Removed by moderator ]**", "body", "title marker"),
    ("T", "[removed]", "whole-body marker"),
    ("T", "[deleted]", "whole-body marker"),
    ("T", "[ Removed by moderator ]", "whole-body marker"),
    ("T", "Sorry, this post has been removed by the moderators of r/x", "removal notice"),
    ("T", "Sorry this post was deleted", "removal notice"),
    ("T", "removed by Reddit's filters", "removed by moderators/filters"),
    ("T", "removed by the moderators", "removed by moderators/filters"),
    ("T", "This was deleted by the person who originally posted it", "deleted by author"),
    ("T", "Post is awaiting moderator approval.", "pending approval"),
    ("Legit [removed] mention", "new info", None),
    ("T", "I am a bot, and this action was performed automatically", None),
    ("T", "", None),
    ("T", "body mentions deleted after 500 chars " + ("x" * 410) + " removed by reddit", None),
    ("T", "please reply to this comment with the source\n\nI am a bot, and this action was performed automatically", None),
    ("T", "normal leak text", None),
]
for i, (title, body, expected) in enumerate(_removal_cases, 1):
    check(f"removal {i:02d}", signals.removed_post_reason(title, body) == expected,
          str(signals.removed_post_reason(title, body)))

# 12 Redlib live-page NSFW badge cases ------------------------------------
_page_cases = [
    ('<div class="post"><small class="nsfw">NSFW</small></div>', True),
    ('<div class="post"><small class="tag nsfw">NSFW</small></div>', True),
    ('<div class="post"><small class="spoiler">Spoiler</small></div>', False),
    ('<div class="post"><a href="/r/x/comments/abc/t/">t</a></div>', False),
    ('<span class="post_title">Title</span>', False),
    ('<span class="created">1 minute ago</span>', False),
    ('Too Many Requests', None),
    ('404 Not Found', None),
    ('captcha bot check', None),
    ('', None),
    (None, None),
    ('<div>plain shell</div>', None),
]
for i, (html, expected) in enumerate(_page_cases, 1):
    check(f"page nsfw {i:02d}", signals.nsfw_from_post_page(html) is expected,
          str(signals.nsfw_from_post_page(html)))

# 34 fail-closed NSFW gate cases ------------------------------------------
_gate_cases = [
    ({"over_18": True}, "nsfw_flag"),
    ({"over_18": False, "thumbnail": "nsfw"}, "nsfw_flag"),
    ({"over_18": False, "thumbnail": "NSFW"}, "nsfw_flag"),
    ({"over_18": False, "thumbnail": "spoiler"}, None),
    ({"over_18": False}, None),
    (None, "nsfw_unknown"),
    ({"archive_status": "missing"}, "nsfw_unknown"),
    ({"archive_status": "missing", "page_nsfw": False}, None),
    ({"archive_status": "missing", "page_nsfw": True}, "nsfw_flag"),
    ({"lookup_error": True}, "nsfw_unknown"),
    ({"malformed": True}, "nsfw_unknown"),
    ({"over_18": "false"}, "nsfw_unknown"),
    ({"over_18": False, "subreddit_over18": True}, "nsfw_subreddit"),
    ({"over_18": False, "source_present": True, "source_over_18": True}, "nsfw_crosspost_source"),
    ({"over_18": False, "source_present": True, "source_over_18": False, "source_thumbnail": "nsfw"}, "nsfw_crosspost_source"),
    ({"over_18": False, "source_present": True, "source_over_18": False, "source_subreddit_over18": True}, "nsfw_crosspost_source"),
    ({"over_18": False, "source_present": True}, "nsfw_unknown"),
    ({"over_18": False, "source_present": True, "source_over_18": False}, None),
    ({"over_18": False, "source_present": False}, None),
    ({"over_18": False, "subreddit_over18": False}, None),
    ({"over_18": False, "source_present": True, "source_over_18": False, "source_subreddit_over18": False}, None),
    ({"over_18": False, "source_present": True, "source_over_18": "no"}, "nsfw_unknown"),
    ({"over_18": False, "source_present": True, "source_over_18": False, "source_subreddit_over18": "no"}, None),
    ({"over_18": False, "subreddit_over18": "no"}, None),
]
for i, (markers, expected) in enumerate(_gate_cases, 1):
    check(f"nsfw gate base {i:02d}", signals.nsfw_gate_reason("pid", "author", markers) == expected,
          str(signals.nsfw_gate_reason("pid", "author", markers)))
check("nsfw gate fail-open missing", signals.nsfw_gate_reason("pid", "author", None, fail_open=True) is None)
check("nsfw gate fail-open lookup_error", signals.nsfw_gate_reason("pid", "author", {"lookup_error": True}, fail_open=True) is None)
check("nsfw gate require subreddit unknown", signals.nsfw_gate_reason("pid", "author", {"over_18": False}, require_subreddit=True) == "nsfw_unknown")
check("nsfw gate require source subreddit unknown", signals.nsfw_gate_reason("pid", "author", {"over_18": False, "source_present": True, "source_over_18": False}, require_subreddit=True) == "nsfw_unknown")
check("nsfw gate allowlisted id bypasses unknown", signals.nsfw_gate_reason("pid", "author", None, allowlist=["t3_pid"]) is None)
check("nsfw gate allowlisted author bypasses flag", signals.nsfw_gate_reason("pid", "/u/author", {"over_18": True}, allowlist=["Author"]) is None)
check("nsfw gate non-allowlisted flag stays blocked", signals.nsfw_gate_reason("pid", "other", {"over_18": True}, allowlist=["someone"]) == "nsfw_flag")
check("nsfw allowlist forms strip t3", "pid" in signals.nsfw_allowlist_forms("t3_pid"))
check("nsfw allowlist forms strip user prefix", "name" in signals.nsfw_allowlist_forms("/u/Name"))
check("nsfw gate clean live fallback ignores spoiler", signals.nsfw_gate_reason("pid", "author", {"archive_status": "missing", "page_nsfw": signals.nsfw_from_post_page('<div class="post"><small class="spoiler">Spoiler</small></div>')}) is None)

# 12 settle cases ----------------------------------------------------------
check("settle verified window default", signals.settle_window_seconds(True) == 60)
check("settle unverified window default", signals.settle_window_seconds(False) == 300)
check("settle verified custom", signals.settle_window_seconds(True, verified_seconds=90, unverified_seconds=300) == 90)
check("settle unverified custom", signals.settle_window_seconds(False, verified_seconds=90, unverified_seconds=300) == 300)
check("settle verified holds before 60", signals.settle_holds(941, 1000, confirmed_in_new=True))
check("settle verified passes at 60", signals.settle_holds(940, 1000, confirmed_in_new=True) is False)
check("settle unverified holds before 300", signals.settle_holds(701, 1000, confirmed_in_new=False))
check("settle unverified passes at 300", signals.settle_holds(700, 1000, confirmed_in_new=False) is False)
check("settle zero verified off", signals.settle_holds(999, 1000, confirmed_in_new=True, verified_seconds=0) is False)
check("settle zero unverified off", signals.settle_holds(999, 1000, confirmed_in_new=False, unverified_seconds=0) is False)
check("settle invalid verified falls back", signals.settle_window_seconds(True, verified_seconds="bad") == 60)
check("settle invalid unverified falls back", signals.settle_window_seconds(False, unverified_seconds="bad") == 300)

# 14 media identity cases --------------------------------------------------
_media_cases = [
    ("https://i.redd.it/a.jpg", "https://i.redd.it/a.jpg"),
    ("https://preview.redd.it/slug-v0-a.jpg?width=1080&s=x", "https://i.redd.it/a.jpg"),
    ("https://preview.redd.it/a.png?width=1080&s=x", "https://i.redd.it/a.png"),
    ("https://preview.redd.it/a.gif?format=mp4&s=x", "https://i.redd.it/a.gif"),
    ("HTTPS://Example.COM/Path/?q=1", "example.com/Path"),
    ("https://example.com/Path/", "example.com/Path"),
    ("https://www.reddit.com/gallery/abc", None),
    ("https://old.reddit.com/r/x/comments/abc/t/", None),
    ("https://v.redd.it/abc/DASH_720.mp4", "v.redd.it/abc/DASH_720.mp4"),
    ("https://packaged-media.redd.it/x", "packaged-media.redd.it/x"),
    ("", None),
    (None, None),
    ("not a url", None),
    ("https://EXAMPLE.com/Case", "example.com/Case"),
]
for i, (url, expected) in enumerate(_media_cases, 1):
    check(f"media identity {i:02d}", signals.media_identity(url) == expected, str(signals.media_identity(url)))

# 20 content fingerprint / repost cases -----------------------------------
_fp_cases = [
    (("Sub", "Author", "Title"), "sub|author|title"),
    (("Sub_Reddit", "Author", "Port: Belovodye!!"), "sub reddit|author|port belovodye"),
    (("Sub", "Author", "A\u200bB"), "sub|author|ab"),
    (("Sub", "/u/Author", "Title"), "sub|u author|title"),
    (("Sub", "Author", "Ｔｉｔｌｅ"), "sub|author|title"),
]
for i, (args, expected) in enumerate(_fp_cases, 1):
    check(f"fingerprint value {i:02d}", signals.content_fingerprint(*args) == expected,
          str(signals.content_fingerprint(*args)))
check("fingerprint deleted author", signals.content_fingerprint("s", "[deleted]", "t") is None)
check("fingerprint unknown author", signals.content_fingerprint("s", "unknown", "t") is None)
check("fingerprint automoderator author", signals.content_fingerprint("s", "AutoModerator", "t") is None)
check("fingerprint empty title", signals.content_fingerprint("s", "a", "") is None)
check("fingerprint empty sub", signals.content_fingerprint("", "a", "t") is None)
check("fingerprint punctuation equality", signals.content_fingerprint("s", "a", "A - B") == signals.content_fingerprint("s", "a", "A B"))
check("fingerprint whitespace equality", signals.content_fingerprint("s", "a", "A   B") == signals.content_fingerprint("s", "a", "A B"))
check("fingerprint author difference", signals.content_fingerprint("s", "a", "T") != signals.content_fingerprint("s", "b", "T"))
check("fingerprint sub difference", signals.content_fingerprint("s1", "a", "T") != signals.content_fingerprint("s2", "a", "T"))
check("fingerprint title difference", signals.content_fingerprint("s", "a", "T1") != signals.content_fingerprint("s", "a", "T2"))
_fp = signals.content_fingerprint("s", "a", "t")
check("duplicate repost hit", signals.duplicate_repost_hit({_fp: "S_old"}, _fp) == "S_old")
check("duplicate repost miss", signals.duplicate_repost_hit({_fp: "S_old"}, signals.content_fingerprint("s", "a", "u")) is None)
check("duplicate repost no map", signals.duplicate_repost_hit(None, _fp) is None)
check("duplicate repost no fingerprint", signals.duplicate_repost_hit({_fp: "S_old"}, None) is None)
check("fingerprint exact normalized title", signals.content_fingerprint("s", "a", "Port Belovodye") == signals.content_fingerprint("s", "a", "port-belovodye"))

# 20 retraction/tombstone cases -------------------------------------------
check("retract absence proof true", signals.listing_absence_proves_dead("abc", 940, 1000, {"zzz"}, 120))
check("retract present false", signals.listing_absence_proves_dead("abc", 940, 1000, {"abc"}, 120) is False)
check("retract unreadable listing false", signals.listing_absence_proves_dead("abc", 940, 1000, None, 120) is False)
check("retract missing listing age false", signals.listing_absence_proves_dead("abc", 940, 1000, {"zzz"}, None) is False)
check("retract old outside span false", signals.listing_absence_proves_dead("abc", 0, 1000, {"zzz"}, 120) is False)
check("retract margin extends span", signals.listing_absence_proves_dead("abc", 800, 1000, {"zzz"}, 120, tail_margin_seconds=90) is True)
check("retract edit decision", signals.retraction_decision(enabled=True, mode="edit", post_id="abc", published_ts=940, now=1000, listing_ids={"zzz"}, listing_oldest_age=120, live_removal_reason="removed") == "edit")
check("retract delete decision", signals.retraction_decision(enabled=True, mode="delete", post_id="abc", published_ts=940, now=1000, listing_ids={"zzz"}, listing_oldest_age=120, live_removal_reason="removed") == "delete")
check("retract off decision", signals.retraction_decision(enabled=False, mode="edit", post_id="abc", published_ts=940, now=1000, listing_ids={"zzz"}, listing_oldest_age=120, live_removal_reason="removed") is None)
check("retract bad mode decision", signals.retraction_decision(enabled=True, mode="off", post_id="abc", published_ts=940, now=1000, listing_ids={"zzz"}, listing_oldest_age=120, live_removal_reason="removed") is None)
check("retract no removal decision", signals.retraction_decision(enabled=True, mode="edit", post_id="abc", published_ts=940, now=1000, listing_ids={"zzz"}, listing_oldest_age=120, live_removal_reason=None) is None)
_payload = {"flags": 1, "components": [{"type": 17, "accent_color": 16729344, "components": [{"type": 10, "content": "header"}, {"type": 1, "components": []}]}]}
_tomb = signals.tombstone_payload(_payload, "removed")
check("tombstone preserves original object", _payload["components"][0]["components"][0]["content"] == "header")
check("tombstone adds notice", "no longer live" in _tomb["components"][0]["components"][0]["content"])
check("tombstone keeps header", any(c.get("content") == "header" for c in _tomb["components"][0]["components"] if isinstance(c, dict)))
check("tombstone keeps buttons", any(c.get("type") == 1 for c in _tomb["components"][0]["components"] if isinstance(c, dict)))
check("tombstone greys accent", _tomb["components"][0]["accent_color"] == 0x808080)
_tomb2 = signals.tombstone_payload(_tomb, "removed")
check("tombstone is idempotent", sum(1 for c in _tomb2["components"][0]["components"] if isinstance(c, dict) and "no longer live" in str(c.get("content"))) == 1)
check("tombstone content fallback", "content" in signals.tombstone_payload({"content": "old"}, "removed"))
check("tombstone empty fallback", "content" in signals.tombstone_payload({}, "removed"))
check("tombstone reason included", "removed" in _tomb["components"][0]["components"][0]["content"])

# 12 V3 archive normalization/fetch cases ---------------------------------
_parent = {"over_18": True, "thumbnail": "default", "subreddit_over18": False}
_markers = v3._arctic_nsfw_markers({"id": "abc", "over_18": False, "thumbnail": "default", "url": "https://example.com", "title": "T", "author": "A", "created_utc": 10, "crosspost_parent_list": [_parent]})
check("arctic markers preserve over_18", _markers["over_18"] is False)
check("arctic markers preserve url", _markers["url"] == "https://example.com")
check("arctic markers preserve title", _markers["title"] == "T")
check("arctic markers preserve author", _markers["author"] == "A")
check("arctic markers preserve created", _markers["created_utc"] == 10)
check("arctic markers detect source", _markers["source_present"] is True)
check("arctic markers source over18", _markers["source_over_18"] is True)
check("arctic markers malformed missing over18", v3._arctic_nsfw_markers({"id": "x"}).get("malformed") is True)

class _Resp:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
    async def json(self, content_type=None):
        return self.payload
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False

class _Sess:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []
    def get(self, url, params=None, **kwargs):
        self.calls.append((url, params))
        if self.error:
            raise self.error
        return _Resp(self.payload)

async def _fetch_checks():
    old = v3._arctic_fail_count
    try:
        v3._arctic_fail_count = 0
        s = _Sess({"data": [{"id": "abc", "over_18": False, "title": "T", "author": "A"}]})
        flags = await v3.fetch_arctic_nsfw_flags(s, ["abc", "missing"])
        check("fetch flags one request", len(s.calls) == 1)
        check("fetch flags found record", flags["abc"]["archive_status"] == "found")
        check("fetch flags missing record", flags["missing"] == {"archive_status": "missing"})
        v3._arctic_fail_count = 0
        down = await v3.fetch_arctic_nsfw_flags(_Sess(error=RuntimeError("down")), ["abc"])
        check("fetch flags failure lookup_error", down == {"abc": {"lookup_error": True}})
    finally:
        v3._arctic_fail_count = old
asyncio.run(_fetch_checks())

# 8 end-to-end-ish main cases with fake session ----------------------------
class _Entry(dict):
    def __init__(self):
        super().__init__()
        self.link = "/r/TestSub/comments/abc123/title/"
        self.title = "Clean Title"
        self.author = "Author"
        self["published_parsed"] = time.gmtime(time.time() - 120)
        self["updated_parsed"] = self["published_parsed"]
        self["content"] = [{"value": "body"}]
        self["summary"] = "body"

class _Feed:
    entries = [_Entry()]

class _FakeResponse:
    status = 204
    async def text(self):
        return ""
    async def json(self, content_type=None):
        return {"id": "msg1"}
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False

class _FakeClientSession:
    last = None
    def __init__(self):
        type(self).last = self
        self.posts = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return _FakeResponse()

async def _run_main_case(markers, page_html=None):
    names = [
        "SUBREDDITS", "TEST_POST_ID", "DRY_RUN", "fetch_combined_feed",
        "fetch_arctic_nsfw_flags", "fetch_arctic_post_metadata", "_fetch_new_listing",
        "verify_archive_post_live", "fetch_post_json", "resolve_post_media",
        "create_discohook_share", "load_posted", "load_pending", "save_posted",
        "save_pending", "save_posted_messages", "load_posted_messages",
        "fetch_first_redlib_post_page", "RETRACT_DEAD_POSTS", "aiohttp",
    ]
    old = {name: getattr(v3, name) for name in names}
    old_env = os.environ.get("DISCORD_WEBHOOK_URL")
    old_sub_env = os.environ.get("WEBHOOK_REDDIT_TESTSUB")
    pending = {}
    try:
        os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.test/webhook"
        os.environ["WEBHOOK_REDDIT_TESTSUB"] = "https://discord.test/webhook"
        v3.SUBREDDITS = ["TestSub"]
        v3.TEST_POST_ID = ""
        v3.DRY_RUN = False
        v3.RETRACT_DEAD_POSTS = False
        v3.fetch_combined_feed = lambda session: _async_return(_Feed())
        v3.fetch_arctic_nsfw_flags = lambda session, ids, label="": _async_return({"abc123": markers})
        v3.fetch_arctic_post_metadata = lambda session, ids, label="": _async_return({})
        v3._fetch_new_listing = lambda session, sub: _async_return(({"abc123"}, 600))
        v3.verify_archive_post_live = lambda session, path, label="", feed_ok=True: _async_return((True, "live"))
        v3.fetch_post_json = lambda session, pid, use_oauth=False: _async_return(None)
        v3.resolve_post_media = lambda session, base, post_json, path="", label="": _async_return({
            "title": base.get("title") or "T", "author": base.get("author") or "A",
            "body": base.get("body") or "body", "media": [], "stats": None,
            "crosspost": None, "op_comment": None, "youtube_url": None,
            "youtube_id": None, "youtube_live": False, "full_mode": False,
        })
        v3.create_discohook_share = lambda session, payload, label: _async_return(None)
        v3.load_posted = lambda: set()
        v3.load_pending = lambda: pending
        v3.save_posted = lambda posted, keep_newest=frozenset(): set(posted)
        v3.save_pending = lambda p: None
        v3.load_posted_messages = lambda: {}
        v3.save_posted_messages = lambda m: None
        v3.fetch_first_redlib_post_page = lambda session, path: _async_return(page_html)
        class _Aio:
            ClientSession = _FakeClientSession
            ClientTimeout = staticmethod(lambda total=None: None)
        v3.aiohttp = _Aio
        await v3.main()
        return len(_FakeClientSession.last.posts), dict(pending)
    finally:
        for name, value in old.items():
            setattr(v3, name, value)
        if old_env is None:
            os.environ.pop("DISCORD_WEBHOOK_URL", None)
        else:
            os.environ["DISCORD_WEBHOOK_URL"] = old_env
        if old_sub_env is None:
            os.environ.pop("WEBHOOK_REDDIT_TESTSUB", None)
        else:
            os.environ["WEBHOOK_REDDIT_TESTSUB"] = old_sub_env


def _async_return(value):
    async def _coro(*args, **kwargs):
        return value
    return _coro()

async def _main_checks():
    sent, pending = await _run_main_case({"over_18": False})
    check("main clean archive sends once", sent == 1)
    sent, pending = await _run_main_case({"over_18": True})
    check("main nsfw post blocks Discord", sent == 0 and pending.get("TestSub_abc123", {}).get("reason") == "nsfw_flag")
    sent, pending = await _run_main_case({"over_18": False, "subreddit_over18": True})
    check("main nsfw subreddit blocks Discord", sent == 0 and pending.get("TestSub_abc123", {}).get("reason") == "nsfw_subreddit")
    sent, pending = await _run_main_case({"over_18": False, "source_present": True, "source_over_18": True})
    check("main nsfw source blocks Discord", sent == 0 and pending.get("TestSub_abc123", {}).get("reason") == "nsfw_crosspost_source")
    sent, pending = await _run_main_case({"archive_status": "missing"}, '<div class="post"><a href="/r/x/comments/abc123/t/">t</a></div>')
    check("main live-page clean fallback sends", sent == 1)
    sent, pending = await _run_main_case({"archive_status": "missing"}, '<div class="post"><small class="nsfw">NSFW</small></div>')
    check("main live-page nsfw fallback blocks", sent == 0 and pending.get("TestSub_abc123", {}).get("reason") == "nsfw_flag")
    sent, pending = await _run_main_case({"archive_status": "missing"}, '<div class="post"><small class="spoiler">Spoiler</small></div>')
    check("main spoiler fallback sends", sent == 1)
    sent, pending = await _run_main_case({"archive_status": "missing"}, None)
    check("main unreadable fallback fails closed", sent == 0 and pending.get("TestSub_abc123", {}).get("reason") == "nsfw_unknown")
asyncio.run(_main_checks())

# 16 static/wiring cases ---------------------------------------------------
_v3_src = inspect.getsource(v3)
_main_src = inspect.getsource(v3.main)
with open(os.path.join(ROOT, ".github/workflows/reddit_monitor.yml"), encoding="utf-8") as fh:
    _wf = fh.read()
with open(os.path.join(ROOT, ".github/workflows/ci.yml"), encoding="utf-8") as fh:
    _ci = fh.read()
with open(os.path.join(ROOT, ".github/workflows/twitter_monitor.yml"), encoding="utf-8") as fh:
    _twitter_wf = fh.read()
check("static signal module has no aiohttp", "aiohttp" not in inspect.getsource(signals))
check("static V3 imports reddit_signals", "reddit_signals" in _v3_src)
check("static verified settle variable", "POST_SETTLE_VERIFIED_SECONDS" in _v3_src)
check("static NSFW fail-open variable", "NSFW_FAIL_OPEN" in _v3_src)
check("static live-page fallback variable", "NSFW_PAGE_FALLBACK" in _v3_src)
check("static repost gate variable", "REPOST_GATE" in _v3_src)
check("static retraction disabled variable", "RETRACT_DEAD_POSTS" in _v3_src)
check("static redlib memo cache", "_redlib_post_page_cache" in _v3_src)
check("static proxy memo cache", "_proxy_post_cache" in _v3_src)
check("static modqueue skipped when confirmed", "skipping mod-queue instance checks" in _main_src)
check("static webhook wait true conditional", "&wait=true" in _main_src and "RETRACT_DEAD_POSTS" in _main_src)
check("workflow wires POST_SETTLE_VERIFIED_SECONDS", "POST_SETTLE_VERIFIED_SECONDS" in _wf)
check("workflow wires NSFW_PAGE_FALLBACK", "NSFW_PAGE_FALLBACK" in _wf)
check("workflow wires REPOST_WINDOW_SECONDS", "REPOST_WINDOW_SECONDS" in _wf)
check("workflow persists posted_messages", "posted_messages.json" in _wf)
for _r52_name in (
    "PROXY_MEDIA", "PROXY_WARMUP_POST", "EMBEDDIT_INSTANCE",
    "YOUTUBE_LINK_MESSAGE", "DISCOHOOK_PREVIEW", "REDDIT_OP_COMMENT",
    "YOUTUBE_MEDIA_EMBED", "FEEDTOKEN_JSON_STAGGER",
    "PENDING_RECHECK_SECONDS", "APPROVAL_RECHECK_SECONDS",
    "MAX_CACHE_SIZE_PER_SUB", "MAX_CACHE_SIZE_TOTAL", "MAX_POSTS_PER_RUN",
):
    check(f"r52 workflow maps {_r52_name} as a Variable",
          f"{_r52_name}: ${{{{ vars.{_r52_name} }}}}" in _wf)
check("r52 X workflow maps MAX_CACHE_SIZE_PER_ACCOUNT as a Variable",
      "MAX_CACHE_SIZE_PER_ACCOUNT: ${{ vars.MAX_CACHE_SIZE_PER_ACCOUNT }}" in _twitter_wf)
check("ci runs this suite", "python tests/test_reddit_signals.py" in _ci)


# ---------------------------------------------------------------------------
# ROUND 46 / 46b (2026-10-01) — listing provenance, the queued-post regression
# 1wuqy6z (zero comments) and 1wurg8f (AutoModerator source-rule comment) were
# mirrored ~8.5 min after creation while still awaiting moderator approval,
# because they appeared in Reddit's /new.rss and that counted as released.
# ---------------------------------------------------------------------------
check("r46 release proof: an RSS listing hit never proves release",
      signals.listing_proves_release(True, signals.LISTING_RSS) is False)
check("r46 release proof: an API-backed listing hit does",
      signals.listing_proves_release(True, signals.LISTING_HTML) is True)
check("r46 release proof: pre-round-46 callers default to API-backed",
      signals.listing_proves_release(True) is True)
check("r46 release proof: absence never proves release",
      signals.listing_proves_release(False, signals.LISTING_HTML) is False
      and signals.listing_proves_release(None, signals.LISTING_HTML) is False)
check("r46 verdict: 1wuqy6z — the banner holds even when RSS shows the post",
      signals.queue_verdict(strong_hold_text=True, in_listing=True,
                            listing_source=signals.LISTING_RSS)
      == signals.PENDING_APPROVAL)
check("r48 verdict: the banner now outranks even an API-backed listing",
      signals.queue_verdict(strong_hold_text=True, in_listing=True,
                            listing_source=signals.LISTING_HTML)
      == signals.PENDING_APPROVAL)
check("r46 verdict: 1wurg8f — the AutoMod comment holds inside the grace",
      signals.queue_verdict(weak_hold_text=True, in_listing=True,
                            listing_source=signals.LISTING_RSS,
                            post_age_seconds=510, weak_grace_seconds=900)
      == signals.PENDING_APPROVAL)
check("r46 verdict: the weak signal releases after the grace window",
      signals.queue_verdict(weak_hold_text=True, in_listing=True,
                            listing_source=signals.LISTING_RSS,
                            post_age_seconds=1200, weak_grace_seconds=900) is None)
check("r46 verdict: an unknown post age makes the weak signal hold (fail closed)",
      signals.queue_verdict(weak_hold_text=True, in_listing=True,
                            listing_source=signals.LISTING_RSS,
                            post_age_seconds=None, weak_grace_seconds=900)
      == signals.PENDING_APPROVAL)
check("r46 verdict: negative space is refused on an RSS listing",
      signals.queue_verdict(in_listing=False, listing_source=signals.LISTING_RSS,
                            listing_oldest_age=12 * 3600, page_age_seconds=3600,
                            tail_margin_seconds=6 * 3600) is None)
check("r46 verdict: negative space still works on an API-backed listing (r38d)",
      signals.queue_verdict(in_listing=False, listing_source=signals.LISTING_HTML,
                            listing_oldest_age=12 * 3600, page_age_seconds=3600,
                            tail_margin_seconds=6 * 3600) == signals.PENDING_APPROVAL)
check("r46 verdict: an unreadable listing still fails OPEN (round 37 kept)",
      signals.queue_verdict(in_listing=None, listing_source=signals.LISTING_HTML,
                            listing_oldest_age=None, page_age_seconds=3600) is None)
check("r46 verdict: a healthy post on an API-backed listing posts (no new wait)",
      signals.queue_verdict(in_listing=True, listing_source=signals.LISTING_HTML,
                            listing_oldest_age=12 * 3600, page_age_seconds=60) is None)

_R46_PAGE_BANNER = ('<h1 class="post_title">Aventurine Waveflair</h1>'
                    '<div>Post is awaiting moderator approval.</div>')
_R46_PAGE_AUTOMOD = ('<h1 class="post_title">4.7 New Weekly Boss</h1>'
                     '<div class="comment">AutoModerator Please respond to this '
                     'comment with a mirror link and source link.</div>')
_R46_PAGE_CLEAN = '<h1 class="post_title">normal</h1><span class="created">2h ago</span>'


def _r46_gate(page, listing, age=510):
    saved_page, saved_listing = v3._fetch_redlib_post_page, v3._fetch_new_listing

    async def _page(*_a, **_kw):
        return page

    async def _listing(*_a, **_kw):
        return listing

    try:
        v3._fetch_redlib_post_page = _page
        v3._fetch_new_listing = _listing
        return asyncio.run(v3.mod_queue_reason(
            None, "HonkaiStarRail_leaks",
            "/r/HonkaiStarRail_leaks/comments/1wuqy6z/x/", post_age_seconds=age))
    finally:
        v3._fetch_redlib_post_page = saved_page
        v3._fetch_new_listing = saved_listing


check("r46 wiring: 1wuqy6z is HELD when only the RSS listing shows it",
      _r46_gate(_R46_PAGE_BANNER,
                ({"1wuqy6z"}, 7200.0, signals.LISTING_RSS)) == "pending approval")
check("r46 wiring: 1wurg8f is HELD on the AutoMod comment alone",
      _r46_gate(_R46_PAGE_AUTOMOD,
                ({"1wuqy6z"}, 7200.0, signals.LISTING_RSS)) == "pending approval")
check("r46 wiring: an API-backed listing releases the same post",
      _r46_gate(_R46_PAGE_AUTOMOD,
                ({"1wuqy6z"}, 7200.0, signals.LISTING_HTML)) is None)
check("r46 wiring: an ordinary post is not held (posting speed unchanged)",
      _r46_gate(_R46_PAGE_CLEAN,
                ({"1wuqy6z"}, 7200.0, signals.LISTING_RSS)) is None)
check("r46b wiring: no renderable page + RSS-only listing HOLDS a young post",
      _r46_gate(None, ({"1wuqy6z"}, 7200.0, signals.LISTING_RSS)) == "pending approval")
check("r46b wiring: no renderable page but an API-backed listing shows it -> post",
      _r46_gate(None, ({"1wuqy6z"}, 7200.0, signals.LISTING_HTML)) is None)
check("r46b wiring: absent from a readable API-backed listing HOLDS (no page)",
      _r46_gate(None, ({"other"}, 7200.0, signals.LISTING_HTML)) == "pending approval")
check("r46b wiring: an older post with no page is not held for ever",
      _r46_gate(None, ({"1wuqy6z"}, 7200.0, signals.LISTING_RSS),
                age=v3.MOD_QUEUE_WEAK_GRACE_SECONDS + 1) is None)
check("workflow wires MOD_QUEUE_WEAK_GRACE_SECONDS",
      "MOD_QUEUE_WEAK_GRACE_SECONDS" in _wf)



# ---------------------------------------------------------------------------
# ROUND 48 (2026-10-01) — PRECEDENCE FIX after a live escape.
# r/HonkaiStarRail_leaks 1wuwjiw: created 19:06:00 (+08), DELIVERED 19:16,
# and at 19:17 the post page still read "Post is awaiting moderator approval"
# with AutoModerator's stickied source-rule comment above the OP's reply.
# Round 46 asked the listing FIRST, so a queued post that is nevertheless
# visible in an API-backed /new listing was released without the page's own
# banner ever being consulted. Direct page evidence now wins.
# ---------------------------------------------------------------------------
check("r48: 1wuwjiw — the banner holds the post whatever the listing says",
      signals.queue_verdict(strong_hold_text=True, in_listing=True,
                            listing_source=signals.LISTING_HTML,
                            post_age_seconds=600) == signals.PENDING_APPROVAL
      and signals.queue_verdict(strong_hold_text=True, in_listing=True,
                                listing_source=signals.LISTING_RSS,
                                post_age_seconds=600) == signals.PENDING_APPROVAL
      and signals.queue_verdict(strong_hold_text=True, in_listing=False,
                                listing_source=signals.LISTING_HTML)
      == signals.PENDING_APPROVAL
      and signals.queue_verdict(strong_hold_text=True, in_listing=None)
      == signals.PENDING_APPROVAL)
check("r48: with NO banner an API-backed listing still releases instantly (speed kept)",
      signals.queue_verdict(strong_hold_text=False, in_listing=True,
                            listing_source=signals.LISTING_HTML,
                            post_age_seconds=5) is None)
check("r48: the weak AutoMod signal still yields to an API-backed listing",
      signals.queue_verdict(weak_hold_text=True, in_listing=True,
                            listing_source=signals.LISTING_HTML,
                            post_age_seconds=10, weak_grace_seconds=900) is None)
check("r48: the banner is checked before anything else in queue_verdict",
      inspect.getsource(signals.queue_verdict).index("if strong_hold_text:")
      < inspect.getsource(signals.queue_verdict).index("if listing_proves_release("))

_R48_PAGE = ('<h1 class="post_title">4.7 Apoc Shadow Pom Pom mechanics information via Cyrleak</h1>'
             '<div>Post is awaiting moderator approval.</div>'
             '<div class="comment">AutoModerator Please respond to this comment with a '
             'mirror link and source link. Failure to do so will result in post removal.</div>')
check("r48 wiring: 1wuwjiw is HELD even when the API-backed listing contains it",
      v3.mod_queue_decision(_R48_PAGE, {"1wuwjiw"}, "1wuwjiw",
                            listing_oldest_age=12 * 3600,
                            listing_source=signals.LISTING_HTML,
                            post_age_seconds=600) == "pending approval")
check("r48 wiring: once the banner is gone the same post posts immediately",
      v3.mod_queue_decision(
          '<h1 class="post_title">4.7 Apoc Shadow Pom Pom</h1>'
          '<span class="created">11m ago</span>',
          {"1wuwjiw"}, "1wuwjiw", listing_oldest_age=12 * 3600,
          listing_source=signals.LISTING_HTML, post_age_seconds=660) is None)


print()
if failures:
    print(f"REDDIT SIGNAL TEST FAILURES ({len(failures)}): {failures}")
    sys.exit(1)
print(f"REDDIT SIGNAL TEST: ALL PASS ({passes} pass)")
