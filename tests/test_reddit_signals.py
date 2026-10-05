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
    def __init__(self, age_seconds=120):
        super().__init__()
        self.link = "/r/TestSub/comments/abc123/title/"
        self.title = "Clean Title"
        self.author = "Author"
        self["published_parsed"] = time.gmtime(time.time() - age_seconds)
        self["updated_parsed"] = self["published_parsed"]
        self["content"] = [{"value": "body"}]
        self["summary"] = "body"

class _Feed:
    def __init__(self, age_seconds=120):
        self.entries = [_Entry(age_seconds)]

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

async def _run_main_case(markers, page_html=None, age_seconds=120, listing=(("abc123",), 600),
                          fresh_hold=None, listing_confirm=None):
    names = [
        "SUBREDDITS", "TEST_POST_ID", "DRY_RUN", "fetch_combined_feed",
        "fetch_arctic_nsfw_flags", "_fetch_new_listing",
        "verify_archive_post_live", "fetch_post_json", "resolve_post_media",
        "create_discohook_share", "load_posted", "load_pending", "save_posted",
        "save_pending", "save_posted_messages", "load_posted_messages",
        "fetch_first_redlib_post_page", "RETRACT_DEAD_POSTS", "aiohttp",
        "FRESH_HOLD_SECONDS", "LISTING_CONFIRM",
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
        if fresh_hold is not None:
            v3.FRESH_HOLD_SECONDS = fresh_hold
        if listing_confirm is not None:
            v3.LISTING_CONFIRM = listing_confirm
        v3.fetch_combined_feed = lambda session: _async_return(_Feed(age_seconds))
        v3.fetch_arctic_nsfw_flags = lambda session, ids, label="": _async_return({"abc123": markers})
        if listing is None:
            v3._fetch_new_listing = lambda session, sub: _async_return(None)
        else:
            _listing_ids, _listing_oldest = listing
            v3._fetch_new_listing = lambda session, sub: _async_return((set(_listing_ids), _listing_oldest))
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

# ROUND 64 ("Pristine Listing Protocol") end-to-end main() cases ------------
# Precedence under test: dedup -> FRESH_HOLD floor -> pristine listing
# confirmation -> NSFW gate (unchanged, fail-closed) -> deliver. Every case
# below uses age_seconds/listing/fresh_hold/listing_confirm overrides added
# to _run_main_case so a single harness exercises both new, fully stateless
# gates (neither ever touches `pending` -- every "skipped" assertion below
# also confirms no pending/tracker entry was created for the candidate).
async def _r64_checks():
    # D.2: listed + older than the 30s default floor -> delivers.
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=120,
                                          listing=(("abc123",), 600))
    check("r64: listed + older than FRESH_HOLD_SECONDS delivers", sent == 1)

    # D.3: absent from the pristine listing -> skipped, never cached, then
    # delivers the moment a later tick's listing includes it.
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=120,
                                          listing=(("other",), 600))
    check("r64: absent from pristine listing is skipped, not delivered",
          sent == 0 and "TestSub_abc123" not in pending)
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=120,
                                          listing=(("abc123",), 600))
    check("r64: same candidate delivers instantly once a later tick lists it",
          sent == 1)

    # D.4: younger than the 30s default floor -> skipped even when listed,
    # with NO pending/tracker entry (fully stateless); delivers once old
    # enough on a later tick.
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=10,
                                          listing=(("abc123",), 600))
    check("r64: younger than FRESH_HOLD_SECONDS is skipped even when listed, "
          "and creates no pending/tracker entry",
          sent == 0 and "TestSub_abc123" not in pending)
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=45,
                                          listing=(("abc123",), 600))
    check("r64: same candidate delivers next tick once older than the floor",
          sent == 1)

    # D.5: horizon rule -- candidate older than the listing's own oldest
    # entry (scrolled past limit=100) cannot be confirmed OR disconfirmed,
    # so the gate is inapplicable and normal delivery proceeds, even though
    # the (too-short) listing snapshot here doesn't contain its id.
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=10000,
                                          listing=(("other",), 600))
    check("r64: older than the listing's own span falls through to normal "
          "delivery (confirmation inapplicable)", sent == 1)

    # D.6: outage -- no listing source readable this tick is NEVER read as
    # absence: the candidate is skipped (not cached) and recovers next tick.
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=120,
                                          listing=None)
    check("r64: a listing outage skips the candidate without caching it",
          sent == 0 and "TestSub_abc123" not in pending)
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=120,
                                          listing=(("abc123",), 600))
    check("r64: the same candidate recovers on the next tick once the "
          "listing is readable again", sent == 1)

    # D.7: precedence -- an NSFW post that IS listed and past the fresh
    # hold is still blocked by the (unchanged, fail-closed) NSFW gate.
    sent, pending = await _run_main_case({"over_18": True}, age_seconds=120,
                                          listing=(("abc123",), 600))
    check("r64: precedence holds -- listed + old enough is still NSFW-blocked",
          sent == 0 and pending.get("TestSub_abc123", {}).get("reason") == "nsfw_flag")

    # D.8 (+ addendum): FRESH_HOLD_SECONDS=60 is honored independently of the
    # default -- 45s delivers at the 30s default, is skipped at 60, and
    # delivers once past 60s; FRESH_HOLD_SECONDS=0 disables the floor
    # entirely (a brand-new, 0-second-old, listed post posts immediately).
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=45,
                                          listing=(("abc123",), 600))
    check("r64 addendum: age 45s delivers at the 30s default", sent == 1)
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=45,
                                          listing=(("abc123",), 600), fresh_hold=60)
    check("r64 addendum: the same age 45s is skipped once FRESH_HOLD_SECONDS=60, "
          "with no pending/tracker entry",
          sent == 0 and "TestSub_abc123" not in pending)
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=65,
                                          listing=(("abc123",), 600), fresh_hold=60)
    check("r64 addendum: it delivers once older than the 60s floor", sent == 1)
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=0,
                                          listing=(("abc123",), 600), fresh_hold=0)
    check("r64 addendum: FRESH_HOLD_SECONDS=0 disables the floor entirely "
          "(posts at age 0 once listed)", sent == 1)

    # D.8: LISTING_CONFIRM=0 + FRESH_HOLD_SECONDS=0 together fully restore
    # round-63 delivery byte-for-byte -- the two knobs are independent
    # mechanisms, so true parity with pre-round-64 behavior needs BOTH off;
    # proven here with a candidate that would fail EITHER gate alone
    # (0s old, and absent from the listing) yet still delivers.
    sent, pending = await _run_main_case({"over_18": False}, age_seconds=0,
                                          listing=(("other",), 600),
                                          fresh_hold=0, listing_confirm=False)
    check("r64: LISTING_CONFIRM=0 + FRESH_HOLD_SECONDS=0 restores round-63 "
          "behavior byte-for-byte (both new gates fully bypassed)", sent == 1)
asyncio.run(_r64_checks())


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
check("static NSFW fail-open variable", "NSFW_FAIL_OPEN" in _v3_src)
check("static live-page fallback variable", "NSFW_PAGE_FALLBACK" in _v3_src)
check("static retraction disabled variable", "RETRACT_DEAD_POSTS" in _v3_src)
check("static redlib memo cache", "_redlib_post_page_cache" in _v3_src)
check("static proxy memo cache", "_proxy_post_cache" in _v3_src)
check("r63: settle window, mod-queue gate, dup-media gate and repost gate symbols "
      "are fully gone from the live engine",
      "POST_SETTLE_SECONDS" not in _v3_src and "POST_SETTLE_VERIFIED_SECONDS" not in _v3_src
      and "MOD_QUEUE_GATE" not in _v3_src and "mod_queue_reason(" not in _v3_src
      and "DUP_MEDIA_GATE" not in _v3_src and "REPOST_GATE" not in _v3_src
      and "settle_holds(" not in _v3_src)
check("static webhook wait true conditional", "&wait=true" in _main_src and "RETRACT_DEAD_POSTS" in _main_src)
check("workflow wires NSFW_PAGE_FALLBACK", "NSFW_PAGE_FALLBACK" in _wf)
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


print()
if failures:
    print(f"REDDIT SIGNAL TEST FAILURES ({len(failures)}): {failures}")
    sys.exit(1)
print(f"REDDIT SIGNAL TEST: ALL PASS ({passes} pass)")
