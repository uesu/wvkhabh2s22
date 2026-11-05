"""Focused X/Twitter V3 media tests — round 73 (2026-10-13).

Run:  python tests/test_twitter_media.py
      expect: TWITTER MEDIA TEST: ALL PASS

No network, no secrets, no Discord — every request is served by an in-process
double, so this suite proves the media decisions alone:

  1. CANONICAL MEDIA ORDER — the gallery follows the tweet's own media order
     (the API's media.all), so a mixed post keeps its source order (the
     observed live shape: JPG -> GIF 1 -> GIF 2 -> GIF 3) and photo-only /
     video-only posts with valid media URLs preserve their previous order and
     output (entries whose URL is missing or empty are safely omitted).
     Providers that do not return media.all (vxtwitter, twitterez) keep the
     legacy videos-then-photos order.
  2. GIF FALLBACK QUALITY — a converter answer only wins when its ACTUAL
     response content type is an animated image/webp | image/gif (a URL
     extension is never trusted). An AVIF answer is remembered but never
     preferred: the original mp4 always outranks it, and it is used only as a
     last resort for a GIF whose mp4 cannot play in the gallery at all.
  3. HEALTHY-MEDIA COST — healthy media costs no extra probe, no delay and no
     heal edit. Only Discord's explicitly returned unresolved evidence queues
     the bounded (round 47/56/70) same-payload heal, and provider fallback
     alone never triggers one.
"""
import os
import sys
import json
import time
import types
import asyncio
import inspect
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Match the smoke/signal suites: these tests never open a connection, so
# lightweight stubs keep the suite runnable in offline/minimal sandboxes.
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


# twitter_v3.py lives beside twitter_proxy.py — import it the same way the
# workflow does, so the suite exercises the production module graph.
sys.path.insert(0, os.path.join(ROOT, "testing area"))
x3 = load_module("twitter_media_v3", "testing area/twitter_v3.py")


# ---------------------------------------------------------------------------
# Offline doubles — an aiohttp session whose answer comes from a route table.
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, status=200, headers=None, payload=None):
        self.status = status
        self.headers = headers or {}
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload) if self._payload is not None else ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    """Offline stand-in for aiohttp.ClientSession.

    `routes` maps a URL substring to (status, {header: value}); the FIRST
    matching route wins and anything unmatched answers 404 (the "converter is
    down / asset is gone" case). Every HEAD/GET is recorded in `requests`, so
    a test can prove exactly how many round-trips a path costs.
    """

    def __init__(self, patch_replies=None, patch_default=None, patch_status=200, message_reply=None, message_status=503):
        self.message_reply = message_reply
        self.message_status = message_status
        self.routes = {}
        self.patch_replies = list(patch_replies or [])
        self.patch_default = patch_default if patch_default is not None else {"components": []}
        self.patch_status = patch_status
        self.requests = []   # ("HEAD"|"GET", url, headers)
        self.patches = []    # (url, payload)

    # --- route helpers ---
    def image(self, match, content_type="image/webp"):
        self.routes[match] = (200, {"Content-Type": content_type})
        return self

    def body(self, match, nbytes):
        self.routes[match] = (200, {"Content-Length": str(nbytes),
                                    "Content-Type": "video/mp4"})
        return self

    def gone(self, match):
        self.routes[match] = (404, {})
        return self

    # --- session surface ---
    def _answer(self, url):
        for match, (status, headers) in self.routes.items():
            if match in url:
                return status, dict(headers)
        return 404, {}

    def _send(self, method, url, headers):
        self.requests.append((method, url, dict(headers or {})))
        status, resp_headers = self._answer(url)
        return _Resp(status, resp_headers)

    def head(self, url, headers=None, **kw):
        return self._send("HEAD", url, headers)

    def get(self, url, headers=None, **kw):
        if "/messages/" in url:
            self.requests.append(("GET", url, dict(headers or {})))
            return _Resp(self.message_status, {}, self.message_reply)
        return self._send("GET", url, headers)

    def patch(self, url, json=None, **kw):
        self.patches.append((url, json))
        payload = self.patch_replies.pop(0) if self.patch_replies else self.patch_default
        return _Resp(self.patch_status, {}, payload)

    def urls(self, method=None):
        return [u for m, u, _ in self.requests if method is None or m == method]

    def heads(self):
        return self.urls("HEAD")


def _tiles(gallery):
    return [item["media"]["url"] for item in gallery]


def _quoted(url):
    """The urlencoded form the gifconvert routes carry (url_quote(url, safe=""))."""
    return url.replace(":", "%3A").replace("/", "%2F")


def _probe_counts(session):
    counts = {}
    for _, url, _ in session.requests:
        counts[url] = counts.get(url, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Fixtures — the live FxEmbed/FxTwitter media shape.
# ---------------------------------------------------------------------------
_JPG = "https://pbs.twimg.com/media/GsourceJpgFormat?format=jpg&name=orig"
_GIF1 = "https://video.twimg.com/tweet_video/GifOneAAA.mp4"
_GIF2 = "https://video.twimg.com/tweet_video/GifTwoBBB.mp4"
_GIF3 = "https://video.twimg.com/tweet_video/GifThreeCCC.mp4"
_CLIP = ("https://video.twimg.com/amplify_video/1234567890123456789/vid/avc1/1920x1080/"
         "clip.mp4?tag=29")
_MB = 1024 * 1024


def _photo(url=_JPG):
    return {"type": "photo", "url": url, "width": 1920, "height": 1080}


def _gif(url=_GIF1):
    return {
        "type": "gif", "url": url,
        "thumbnail_url": "https://pbs.twimg.com/media/poster.jpg",
        "duration": 3, "width": 480, "height": 270,
        "formats": [{"url": url, "container": "mp4", "bitrate": 0}],
    }


def _video(url=_CLIP):
    return {
        "type": "video", "url": url,
        "thumbnail_url": "https://pbs.twimg.com/media/poster.jpg",
        "duration": 30, "width": 1920, "height": 1080,
        "formats": [{"url": url, "container": "mp4", "bitrate": 2000000}],
    }


def _fxtwitter_media(entries):
    """What api.fxtwitter.com returns: media.all in tweet order + per-type
    subsets built from the SAME objects (verified against FxEmbed's processor:
    mediaList.forEach pushes into all, photos or videos)."""
    photos = [e for e in entries if not x3.media_entry_is_video(e)]
    videos = [e for e in entries if x3.media_entry_is_video(e)]
    return {"media": {"all": list(entries), "photos": photos, "videos": videos}}


def _legacy_media(entries):
    """A provider without media.all (vxtwitter / twitterez normalizers)."""
    photos = [e for e in entries if not x3.media_entry_is_video(e)]
    videos = [e for e in entries if x3.media_entry_is_video(e)]
    return {"media": {"photos": photos, "videos": videos}}


def _collect(tweet_like, session):
    return asyncio.run(x3.collect_media(session, tweet_like))


# ===========================================================================
# 1. CANONICAL MEDIA ORDER
# ===========================================================================
print("\n--- 1. canonical media order (round 73) ---")


def _entries_for_mixed():
    jpg = _photo()
    gifs = [_gif(_GIF1), _gif(_GIF2), _gif(_GIF3)]
    return jpg, gifs


_jpg, (_g1, _g2, _g3) = _entries_for_mixed()
_mixed_tweet = _fxtwitter_media([_jpg, _g1, _g2, _g3])

check("r73 order: media.all is used verbatim (tweet order, not per-type order)",
      x3.canonical_media_entries(_mixed_tweet["media"]) == [_jpg, _g1, _g2, _g3],
      str(x3.canonical_media_entries(_mixed_tweet["media"])))
check("r73 order: the observed live source order is JPG -> GIF 1 -> GIF 2 -> GIF 3",
      x3.canonical_media_entries(_mixed_tweet["media"])[0]["url"] == _JPG
      and [e["url"] for e in x3.canonical_media_entries(_mixed_tweet["media"])[1:]]
      == [_GIF1, _GIF2, _GIF3])
_photos_only = _fxtwitter_media([_photo(), _photo("https://pbs.twimg.com/media/b.jpg")])
check("r73 order: a photo-only post is unchanged (photos stay photos, same order)",
      x3.canonical_media_entries(_photos_only["media"]) == _photos_only["media"]["photos"])
check("r73 order: a video-only post keeps the API order",
      x3.canonical_media_entries(_fxtwitter_media([_g1, _g2, _g3])["media"]) == [_g1, _g2, _g3])
check("r73 order: no media.all -> legacy videos-then-photos (fallback providers unchanged)",
      x3.canonical_media_entries(_legacy_media([_jpg, _g1, _g2, _g3])["media"])
      == [_g1, _g2, _g3, _jpg])
check("r73 order: entries without a URL are dropped, never emitted as empty tiles",
      x3.canonical_media_entries({"all": [{"type": "photo", "url": ""}, _g1, {}, _jpg]})
      == [_g1, _jpg])
check("r73 order: empty/None media is safe",
      x3.canonical_media_entries({}) == [] and x3.canonical_media_entries(None) == []
      and x3.canonical_media_entries({"media": None}) == [])
_snapshot = json.dumps(_mixed_tweet, sort_keys=True)
x3.canonical_media_entries(_mixed_tweet["media"])
check("r73 order: the tweet payload is never mutated (ordering is in-memory only)",
      json.dumps(_mixed_tweet, sort_keys=True) == _snapshot)
check("r73 order: a video entry without `type` is still routed as a video",
      x3.media_entry_is_video({"url": _GIF1, "formats": [{"url": _GIF1, "container": "mp4"}]}) is True
      and x3.media_entry_is_video({"url": _JPG, "width": 1, "height": 1}) is False
      and x3.media_entry_is_video(_g1) is True and x3.media_entry_is_video(_jpg) is False
      and x3.media_entry_is_video(None) is False)

# --- end-to-end through collect_media (gif.fxtwitter answers animated webp) ---
_s = _Session().image("gif.fxtwitter.com/tweet_video/", "image/webp")
_items, _notes = _collect(_mixed_tweet, _s)
check("r73 order: the mixed card renders JPG -> GIF 1 -> GIF 2 -> GIF 3 in SOURCE order",
      _tiles(_items) == [
          _JPG,
          "https://gif.fxtwitter.com/tweet_video/GifOneAAA.webp",
          "https://gif.fxtwitter.com/tweet_video/GifTwoBBB.webp",
          "https://gif.fxtwitter.com/tweet_video/GifThreeCCC.webp",
      ], str(_tiles(_items)))
check("r73 order: no note is added by the ordering change", _notes == [], str(_notes))

_s2 = _Session().image("gif.fxtwitter.com/tweet_video/", "image/webp")
_items2, _ = _collect(_legacy_media([_jpg, _g1, _g2, _g3]), _s2)
check("r73 order: a provider without media.all keeps videos-then-photos (unchanged)",
      _tiles(_items2) == [
          "https://gif.fxtwitter.com/tweet_video/GifOneAAA.webp",
          "https://gif.fxtwitter.com/tweet_video/GifTwoBBB.webp",
          "https://gif.fxtwitter.com/tweet_video/GifThreeCCC.webp",
          _JPG,
      ], str(_tiles(_items2)))

_s3 = _Session().image("gif.fxtwitter.com/tweet_video/", "image/webp")
_items3, _ = _collect(_fxtwitter_media([_g1, _g2, _g3]), _s3)
check("r73 order: GIFs stay in chronological (source) order relative to each other",
      _tiles(_items3) == [
          "https://gif.fxtwitter.com/tweet_video/GifOneAAA.webp",
          "https://gif.fxtwitter.com/tweet_video/GifTwoBBB.webp",
          "https://gif.fxtwitter.com/tweet_video/GifThreeCCC.webp",
      ], str(_tiles(_items3)))

_photos = [_photo(), _photo("https://pbs.twimg.com/media/second.jpg")]
_s4 = _Session()
_items4, _ = _collect(_fxtwitter_media(_photos), _s4)
check("r73 order: photo-only post — identical URLs, identical order, zero probes",
      _tiles(_items4) == [_JPG, "https://pbs.twimg.com/media/second.jpg"]
      and not _s4.requests, str(_tiles(_items4)) + str(_s4.requests))

_quote_like = _fxtwitter_media([_g1, _photo()])
_s5 = _Session().image("gif.fxtwitter.com/tweet_video/", "image/webp")
_items5, _ = _collect(_quote_like, _s5)
check("r73 order: quoted tweets use the same ordered pass (no separate code path)",
      _tiles(_items5)[0] == x3.GIF_WEBP_BASE + "GifOneAAA.webp"
      and _tiles(_items5)[1] == _JPG, str(_tiles(_items5)))


# ===========================================================================
# 2. GIF FALLBACK QUALITY — content type beats the URL extension
# ===========================================================================
print("\n--- 2. GIF fallback selection (round 73) ---")


def _gif_chain(session, url=_GIF1, **kw):
    return asyncio.run(x3.resolve_gif_image(session, url, **kw))


check("r73 gif: image/webp and image/gif are the animated tier",
      x3.image_format_tier("image/webp") == "animated"
      and x3.image_format_tier("image/gif") == "animated"
      and x3.image_format_tier("IMAGE/WEBP; charset=binary") == "animated")
check("r73 gif: the AVIF family is its own (last-resort) tier",
      x3.image_format_tier("image/avif") == "avif"
      and x3.image_format_tier("image/heic") == "avif"
      and x3.image_format_tier("image/heif") == "avif")
check("r73 gif: static pictures and non-images are never 'animated'",
      x3.image_format_tier("image/png") == "other"
      and x3.image_format_tier("image/jpeg") == "other"
      and x3.image_format_tier("video/mp4") == ""
      and x3.image_format_tier("") == "" and x3.image_format_tier(None) == "")

_s = _Session().image("gif.fxtwitter.com/tweet_video/", "image/webp")
_url, _src = _gif_chain(_s)
check("r73 gif: FxTwitter's .webp is preferred and short-circuits (1 probe, no extra work)",
      _url == "https://gif.fxtwitter.com/tweet_video/GifOneAAA.webp"
      and _src == "gif.fxtwitter" and len(_s.requests) == 1, f"{_url} {_src} {_s.requests}")

_s = _Session().image("gifconvert", "image/webp")
_url, _src = _gif_chain(_s)
check("r73 gif: gifconvert .webp is second, still Referer-gated",
      _src == "gifconvert" and "/convert.webp" in _url
      and _s.requests[1][2].get("Referer") == "https://vxtwitter.com"
      and "gif.fxtwitter.com" in _s.requests[0][1], f"{_url} {_src} {_s.requests}")

_s = _Session().image("/convert.gif", "image/gif")
_url, _src = _gif_chain(_s)
check("r73 gif: gifconvert .gif is third", _src == "gifconvert" and "/convert.gif" in _url)

_s = _Session().image("fastgif", "image/gif")
_url, _src = _gif_chain(_s)
check("r73 gif: the probe order is unchanged — fxtwitter .webp -> convert.webp -> "
      "convert.gif -> fastgif",
      [u for _, u, _ in _s.requests] == [
          "https://gif.fxtwitter.com/tweet_video/GifOneAAA.webp",
          x3.GIFCONVERT_BASE + "webp?url=" + _quoted(_GIF1),
          x3.GIFCONVERT_BASE + "gif?url=" + _quoted(_GIF1),
          "https://fastgif-production.up.railway.app/tweet_video/GifOneAAA.gif",
      ], str([u for _, u, _ in _s.requests]))

_s = _Session()
_url, _src = _gif_chain(_s)
check("r73 gif: nothing answers -> (None, '') and NO .avif probe is made",
      _url is None and _src == ""
      and not any(".avif" in u or "avif?" in u for u in _s.urls()), str(_s.urls()))

# an AVIF answer must NOT win the animated pass
_s = _Session().image("gif.fxtwitter.com/tweet_video/", "image/avif") \
               .image("gifconvert", "image/webp")
_url, _src = _gif_chain(_s)
check("r73 gif: an image/avif answer is skipped in favour of a real animated answer",
      _url == x3.GIFCONVERT_BASE + "webp?url=" + _quoted(_GIF1)
      and _src == "gifconvert", f"{_url} {_src}")

# …and is not returned at all unless the caller opts in
_s = _Session().image("gif.fxtwitter.com/tweet_video/", "image/avif")
_url, _src = _gif_chain(_s)
check("r73 gif: default call never hands back an AVIF (mp4 preferred instead)",
      _url is None and _src == "", f"{_url} {_src}")

_s = _Session().image("gif.fxtwitter.com/tweet_video/", "image/avif")
_url, _src = _gif_chain(_s, allow_avif=True)
check("r73 gif: allow_avif=True returns the URL that ANSWERED, not a guessed extension",
      _url == "https://gif.fxtwitter.com/tweet_video/GifOneAAA.webp"
      and _src == "gif.fxtwitter-avif", f"{_url} {_src}")

_s = _Session().image(x3.GIFCONVERT_BASE + "avif?url=", "image/avif")
_url, _src = _gif_chain(_s, allow_avif=True)
check("r73 gif: the documented AVIF route is probed last when nothing else answers",
      _src == "gifconvert-avif" and "/convert.avif" in _url
      and _s.heads()[-1].endswith("avif?url=" + _quoted(_GIF1)),
      f"{_url} {_src} {_s.heads()}")
# gifconvert is header-gated (500 to plain clients) — its AVIF probe must
# carry the same browser Referer as its .webp/.gif probes, or a route that is
# actually serving would look dead and the tile would lose the AVIF fallback.
_avif_probe = [h for _, u, h in _s.requests if u.endswith("avif?url=" + _quoted(_GIF1))]
check("r73 gif: the gifconvert AVIF probe carries the browser Referer",
      len(_avif_probe) == 1
      and _avif_probe[0].get("Referer") == "https://vxtwitter.com",
      str(_s.requests))

_s = _Session().image(x3.GIF_WEBP_BASE + "GifOneAAA.avif", "image/avif")
_url, _src = _gif_chain(_s, allow_avif=True)
check("r73 gif: the gif.fxtwitter AVIF route is probed without a Referer (like its .webp)",
      _src == "gif.fxtwitter-avif"
      and "Referer" not in _s.requests[-1][2],
      f"{_url} {_src} {_s.requests[-1]}")

_s = _Session().image("gif.fxtwitter.com/tweet_video/", "image/webp")
_url, _src = _gif_chain(_s, allow_avif=True)
check("r73 gif: an animated answer still wins when the caller allows AVIF",
      _url == "https://gif.fxtwitter.com/tweet_video/GifOneAAA.webp"
      and _src == "gif.fxtwitter" and len(_s.requests) == 1, f"{_url} {_src}")

_s = _Session()
_url, _src = _gif_chain(_s, allow_avif=True)
check("r73 gif: allow_avif=True with no AVIF reachable still returns (None, '')",
      _url is None and _src == "" and any(".avif" in u for u in _s.urls()))

_s = _Session().image("/convert.gif", "video/mp4")
_url, _src = _gif_chain(_s)
check("r73 gif: a .gif URL that actually answers video/mp4 is REJECTED (extension not trusted)",
      _url is None and _src == "", f"{_url} {_src}")

# --- collect_media: mp4 preferred over AVIF, AVIF only when nothing plays ---
_s = _Session().image("gif.fxtwitter.com/tweet_video/", "image/avif").body(_GIF1, 4 * _MB)
_items, _notes = _collect(_fxtwitter_media([_g1]), _s)
check("r73 gif: a playable mp4 is preferred over an AVIF fallback",
      _tiles(_items) == [_GIF1] and _notes == [], str(_tiles(_items)) + str(_notes))
check("r73 gif: …and the AVIF routes are never probed on that path",
      not any(".avif" in u for u in _s.urls()), str(_s.urls()))
check("r73 cost: the playable-mp4 card probed the chain once and the mp4 size once "
      "(5 URLs, no repeats)",
      len(_probe_counts(_s)) == 5 and set(_probe_counts(_s).values()) == {1},
      str(_probe_counts(_s)))

_s = (_Session().image(x3.GIFCONVERT_BASE + "avif?url=", "image/avif")
      .body(_GIF1, 400 * _MB))
_items, _notes = _collect(_fxtwitter_media([_g1]), _s)
check("r73 gif: an unplayable mp4 uses the AVIF last resort instead of a static thumbnail",
      _tiles(_items) == [x3.GIFCONVERT_BASE + "avif?url="
                         + _quoted(_GIF1)]
      and _notes == [], str(_tiles(_items)) + str(_notes))
check("r73 gif: the last resort is reached only AFTER the animated candidates fail",
      _s.heads()[0].endswith("GifOneAAA.webp"), str(_s.heads()))

# THE performance contract of the last-resort path: the four animated
# candidates are probed ONCE, never twice — the AVIF routes are the only
# added requests.
_counts = _probe_counts(_s)
_chain_urls = [u for u, _, _ in x3.gif_converter_candidates(_GIF1)]
_avif_urls = [u for u, _, _ in x3.gif_avif_candidates(_GIF1)]
check("r73 cost: the last-resort path re-probes NO converter candidate (single pass)",
      all(_counts.get(u, 0) == 1 for u in _chain_urls) and len(_chain_urls) == 4,
      str(_counts))
check("r73 cost: at most 4 animated-candidate HEADs and at most 2 AVIF-route HEADs",
      sum(_counts.get(u, 0) for u in _chain_urls) <= 4
      and sum(_counts.get(u, 0) for u in _avif_urls) <= 2
      and not any(u not in _chain_urls + _avif_urls and "?url=" in u for u in _counts),
      str(_counts))

# an AVIF learned during the animated pass is REUSED — no AVIF route is asked
_s = _Session().image("gif.fxtwitter.com/tweet_video/GifOneAAA.webp", "image/avif") \
               .body(_GIF1, 400 * _MB)
_items, _notes = _collect(_fxtwitter_media([_g1]), _s)
check("r73 cost: an AVIF remembered in the first pass is reused, not re-discovered",
      _tiles(_items) == ["https://gif.fxtwitter.com/tweet_video/GifOneAAA.webp"]
      and _probe_counts(_s)["https://gif.fxtwitter.com/tweet_video/GifOneAAA.webp"] == 1,
      str(_tiles(_items)) + " " + str(_probe_counts(_s)))
check("r73 cost: …and the AVIF ROUTES are never asked in that case",
      not any("/convert.avif" in u or u.endswith("GifOneAAA.avif") for u in _s.urls()),
      str(_s.urls()))

# the standalone resolver keeps the same single-pass guarantee
_s_avif = _Session().image(x3.GIFCONVERT_BASE + "avif?url=", "image/avif")
_url, _src = _gif_chain(_s_avif, allow_avif=True)
check("r73 cost: resolve_gif_image(allow_avif=True) also probes the chain exactly once",
      _src == "gifconvert-avif" and all(n == 1 for n in _probe_counts(_s_avif).values()),
      str(_probe_counts(_s_avif)))

# …and the helper API exposes the two passes separately, so a caller can
# reuse a remembered AVIF instead of paying for a second chain
_url, _src, _avif, _avif_src = asyncio.run(x3.probe_gif_candidates(_Session(), _GIF1))
check("r73 api: probe_gif_candidates returns (animated, avif) from ONE pass",
      (_url, _src, _avif, _avif_src) == ("", "", "", ""))
check("r73 api: the candidate/route lists are derived from the mp4 URL",
      [u for u, _, _ in x3.gif_converter_candidates(_GIF1)] == [
          "https://gif.fxtwitter.com/tweet_video/GifOneAAA.webp",
          x3.GIFCONVERT_BASE + "webp?url=" + _quoted(_GIF1),
          x3.GIFCONVERT_BASE + "gif?url=" + _quoted(_GIF1),
          "https://fastgif-production.up.railway.app/tweet_video/GifOneAAA.gif",
      ] and x3.gif_avif_candidates(_GIF1) == (
          ("https://gif.fxtwitter.com/tweet_video/GifOneAAA.avif", "gif.fxtwitter-avif", None),
          (x3.GIFCONVERT_BASE + "avif?url=" + _quoted(_GIF1), "gifconvert-avif",
           "https://vxtwitter.com"),
      ))
check("r73 api: non-tweet_video URLs get no candidates at all",
      x3.gif_converter_candidates("https://video.twimg.com/ext_tw_video/x.mp4") == ()
      and x3.gif_avif_candidates("https://video.twimg.com/ext_tw_video/x.mp4") == ()
      and x3.gif_converter_candidates(None) == () and x3.gif_avif_candidates("") == ())

_s = _Session().body(_GIF1, 400 * _MB)
_items, _notes = _collect(_fxtwitter_media([_g1]), _s)
check("r73 gif: with no AVIF either, the round-11 behaviour stands (thumbnail + note)",
      _tiles(_items) == ["https://pbs.twimg.com/media/poster.jpg"]
      and any("Watch it on X" in n for n in _notes), str(_tiles(_items)) + str(_notes))


# ===========================================================================
# 3. DIRECT MP4 FALLBACK — no converter => the video player, unchanged
# ===========================================================================
print("\n--- 3. direct mp4 fallback ---")
_s = _Session().body(_GIF1, 4 * _MB)
_items, _notes = _collect(_fxtwitter_media([_g1]), _s)
check("r73 mp4: a GIF with no animated converter keeps the original mp4 URL",
      _tiles(_items) == [_GIF1] and _notes == [], str(_tiles(_items)) + str(_notes))
check("r73 mp4: the mp4 is used as-is (no proxy rewrite, no variant swap)",
      _tiles(_items)[0] == _GIF1)
check("r73 mp4: an unprobeable size does NOT force a change (safe default)",
      _tiles(_collect(_fxtwitter_media([_g1]), _Session())[0]) == [_GIF1])

_s = _Session().body(_CLIP, 12 * _MB)
_items, _notes = _collect(_fxtwitter_media([_video()]), _s)
check("r73 mp4: an ordinary video is untouched (1 size probe, no converter request)",
      _tiles(_items) == [_CLIP] and _notes == [] and len(_s.requests) == 1
      and not any(tag in u for u in _s.urls()
                  for tag in ("fxtwitter", "gifconvert", "fastgif")),
      str(_tiles(_items)) + str(_s.urls()))

_s = _Session().image("gif.fxtwitter.com/tweet_video/", "image/webp")
_items, _ = _collect(_fxtwitter_media([_jpg, _g1]), _s)
check("r73 cost: healthy media costs ONE probe per GIF and none for the photo",
      len(_s.requests) == 1 and "GifOneAAA" in _s.heads()[0], str(_s.urls()))


# ===========================================================================
# 4. HEALTHY MEDIA IS NOT HEALED — only explicit evidence queues an edit
# ===========================================================================
print("\n--- 4. evidence-based media healing (rounds 47/56/70 kept) ---")
_HEAL_URL = f"{_GIF1}"
_COLD = {"id": "2105600000000000000", "components": [{"id": 1, "type": 17, "components": [
    {"id": 5, "type": 12, "items": [{"media": {
        "url": _HEAL_URL, "proxy_url": "https://images-ext-1.discordapp.net/external/x",
        "width": 0, "height": 0, "content_type": ""}}]}]}]}
_HEALTHY = {"id": "2105600000000000000", "components": [{"id": 1, "type": 17, "components": [
    {"id": 5, "type": 12, "items": [{"media": {
        "url": _HEAL_URL, "proxy_url": "https://images-ext-1.discordapp.net/external/x",
        "width": 480, "height": 270, "content_type": "video/mp4"}}]}]}]}
_WEBP_TILE = {"id": "1", "components": [{"id": 1, "type": 17, "components": [
    {"id": 5, "type": 12, "items": [{"media": {
        "url": "https://gif.fxtwitter.com/tweet_video/GifOneAAA.webp",
        "width": 0, "height": 0, "content_type": ""}}]}]}]}

check("r73 heal: healthy media carries no evidence at all -> nothing to heal",
      x3.unresolved_media_items(_HEALTHY) == [] and x3.heal_eligible_items(_HEALTHY) == [])
check("r73 heal: explicit unresolved evidence (0x0, no content type) is the ONLY trigger",
      x3.heal_eligible_items(_COLD) == [_HEAL_URL])
check("r73 heal: a fallback-provider mp4 that Discord resolved never queues a heal",
      x3.heal_eligible_items({"id": "2", "components": [{"type": 17, "components": [
          {"type": 12, "items": [{"media": {"url": _CLIP, "width": 1920,
                                            "height": 1080,
                                            "content_type": "video/mp4"}}]}]}]}) == [])
check("r73 heal: converted .webp/.gif tiles stay out of scope (round 56 kept)",
      x3.heal_eligible_items(_WEBP_TILE) == []
      and x3.unresolved_media_items(_WEBP_TILE) == [x3.GIF_WEBP_BASE + "GifOneAAA.webp"])

_real_delay = x3.MEDIA_HEAL_DELAY_SECONDS
try:
    x3.MEDIA_HEAL_DELAY_SECONDS = 0
    _s = _Session(patch_replies=[_HEALTHY])
    _started = time.monotonic()
    asyncio.run(x3.heal_unresolved_media(_s, []))
    _elapsed = time.monotonic() - _started
    check("r73 heal measured: a healthy run sends ZERO edits and waits for nothing",
          _s.patches == [] and _elapsed < 0.5, f"{_elapsed:.3f}s")

    _payload = {"flags": 32768, "components": [{"type": 17, "components": []}]}
    _s = _Session(patch_replies=[_HEALTHY])
    asyncio.run(x3.heal_unresolved_media(
        _s, [("https://discord.com/api/webhooks/1/abc", "999", _payload, "WW/2105")]))
    check("r73 heal: the queued edit is a single identical same-message PATCH",
          len(_s.patches) == 1
          and _s.patches[0][0] == "https://discord.com/api/webhooks/1/abc/messages/999"
                                  "?with_components=true"
          and _s.patches[0][1] == _payload, str(_s.patches))

    _s = _Session(message_reply=_HEALTHY, message_status=200)
    asyncio.run(x3.heal_unresolved_media(
        _s, [("https://discord.com/api/webhooks/1/abc", "999", _payload, "WW/2105")]))
    check("media revalidation: resolved during wait sends one GET and zero PATCHes",
          len(_s.requests) == 1 and _s.patches == [])

    _s = _Session(message_reply=_COLD, message_status=200, patch_replies=[_HEALTHY])
    asyncio.run(x3.heal_unresolved_media(
        _s, [("https://discord.com/api/webhooks/1/abc", "999", _payload, "WW/2105")]))
    check("media revalidation: still unresolved gets one identical bounded edit",
          len(_s.requests) == 1 and _s.patches == [
              ("https://discord.com/api/webhooks/1/abc/messages/999?with_components=true", _payload)])

    for status in (401, 403, 404):
        _s = _Session(message_status=status)
        asyncio.run(x3.heal_unresolved_media(
            _s, [("https://discord.com/api/webhooks/1/abc", "999", _payload, "WW/2105")]))
        check(f"media revalidation: terminal GET {status} prevents pointless edit", _s.patches == [])

    _s = _Session(patch_replies=[_COLD] * 10)
    asyncio.run(x3.heal_unresolved_media(
        _s, [("https://discord.com/api/webhooks/1/abc", "999", _payload, "WW/2105")]))
    check("r73 heal: a permanently unresolved asset is still bounded (attempts cap)",
          len(_s.patches) == x3.MEDIA_HEAL_ATTEMPTS <= 4, str(len(_s.patches)))
finally:
    x3.MEDIA_HEAL_DELAY_SECONDS = _real_delay

_src_v3 = open(os.path.join(ROOT, "testing area", "twitter_v3.py"), encoding="utf-8").read()
check("r73 heal: the posting loop gates the queue on heal_eligible_items (no blind edits)",
      "unresolved = heal_eligible_items(created)" in _src_v3
      and "MEDIA-HEAL queued for" in _src_v3)
check("r73 heal: healing still runs AFTER the posting loop (never delays a post)",
      _src_v3.index("await heal_unresolved_media(session, heal_queue)")
      > _src_v3.index("heal_queue.append("))
check("r73 heal: the POST still asks for the created message (wait=true)",
      "?with_components=true&wait=true" in _src_v3)


# ===========================================================================
# 4b. ROUND 100 — the heal outlives the run (carry-over, 2026-11-01)
# ===========================================================================
print("\n--- 4b. media-heal carry-over (round 100) ---")
import tempfile  # noqa: E402

# Live #12739: the 235 MB 1080p rendition of an 813 MB clip never resolved
# within the four in-run edits, but the SAME identical edit made by hand
# ~45 minutes later made it play. The carry-over keeps editing across runs.
_U1080 = ("https://video.twimg.com/amplify_video/2116341933144698881/vid/avc1/1920x1080/"
          "KQTNFf7vricoapOP.mp4?tag=29")
_WEBHOOK = "https://discord.com/api/webhooks/1/abc"
_KEY = "Wuthering_Waves_2116847734669873623"
_CARD = {"flags": 32768, "components": [{"type": 17, "components": [
    {"type": 10, "content": "### [Wuthering Waves](https://x.com/Wuthering_Waves) just tweeted:"},
    {"type": 12, "items": [{"media": {"url": _U1080}}]},
    {"type": 10, "content": "🔽 Original video is ~813 MB — showing a smaller version (~235 MB) "
                            "so it plays in Discord."}]}]}
_COLD_1080 = {"id": "999", "components": [{"type": 17, "components": [{"type": 12, "items": [
    {"media": {"url": _U1080, "width": 0, "height": 0, "content_type": ""}}]}]}]}
_WARM_1080 = {"id": "999", "components": [{"type": 17, "components": [{"type": 12, "items": [
    {"media": {"url": _U1080, "proxy_url": "https://images-ext-1.discordapp.net/external/y",
               "width": 1920, "height": 1080, "content_type": "video/mp4"}}]}]}]}
_NOW = 1_800_000_000


def _carry(session, records, leftovers, now=_NOW):
    return asyncio.run(x3.carry_over_media_heal(session, records, leftovers, now=now))


check("r100 config: carry-over defaults to 6 h, the state file is media_heal_carry.json, "
      "at most 20 carried messages per run",
      (x3.MEDIA_HEAL_CARRY_HOURS, x3.MEDIA_HEAL_PENDING_FILE, x3.MEDIA_HEAL_CARRY_MAX_PER_RUN)
      == (6, "media_heal_carry.json", 20))
check("r100 config: the state file name avoids 'pending' — merge_monitor_caches.py drops "
      "pending-dict keys that the posted list covers, and these keys ARE dedup keys",
      "pending" not in x3.MEDIA_HEAL_PENDING_FILE)
check("r100 config: in-run heal defaults are untouched (45 s x 4, video scope)",
      (x3.MEDIA_HEAL_DELAY_SECONDS, x3.MEDIA_HEAL_ATTEMPTS, x3.MEDIA_HEAL_SCOPE) == (45, 4, "video"))

_real_delay, _real_hook = x3.MEDIA_HEAL_DELAY_SECONDS, x3.DEFAULT_WEBHOOK_URL
try:
    x3.MEDIA_HEAL_DELAY_SECONDS = 0
    x3.DEFAULT_WEBHOOK_URL = _WEBHOOK

    # --- heal_unresolved_media now reports what it could not heal ---
    _s = _Session(patch_replies=[_COLD_1080] * 10)
    _left = asyncio.run(x3.heal_unresolved_media(_s, [(_WEBHOOK, "999", _CARD, _KEY)]))
    check("r100 heal: the bounded in-run edits are unchanged (4 identical PATCHes) and the "
          "still-unresolved message is RETURNED instead of forgotten",
          len(_s.patches) == 4 and _left == [(_WEBHOOK, "999", _CARD, _KEY)], str(_left))
    _s = _Session(patch_replies=[_COLD_1080, _WARM_1080])
    check("r100 heal: a message that resolves on an identical edit is not returned",
          asyncio.run(x3.heal_unresolved_media(_s, [(_WEBHOOK, "999", _CARD, _KEY)])) == []
          and len(_s.patches) == 2)
    _s = _Session(message_reply=_WARM_1080, message_status=200)
    check("r100 heal: resolved during the wait -> nothing returned, nothing edited",
          asyncio.run(x3.heal_unresolved_media(_s, [(_WEBHOOK, "999", _CARD, _KEY)])) == []
          and _s.patches == [])
    _s = _Session(message_status=404)
    check("r100 heal: a deleted message (GET 404) is not carried over",
          asyncio.run(x3.heal_unresolved_media(_s, [(_WEBHOOK, "999", _CARD, _KEY)])) == [])
    check("r100 heal: an empty queue returns an empty list",
          asyncio.run(x3.heal_unresolved_media(_Session(), [])) == [])

    # --- recording this run's leftovers ---
    _s = _Session()
    _recs = _carry(_s, {}, [(_WEBHOOK, "999", _CARD, _KEY)])
    check("r100 carry: a leftover becomes a record holding the ACCOUNT, message id, identical "
          "payload and timestamps — and no request is made for it this run",
          _recs == {_KEY: {"account": "Wuthering_Waves", "message_id": "999", "payload": _CARD,
                           "first_seen": _NOW, "last_checked": _NOW, "runs": 0}}
          and _s.requests == [] and _s.patches == [], str(_recs))
    check("r100 carry: the record never contains the webhook URL (the file is committed to the repo)",
          "discord.com/api/webhooks" not in json.dumps(_recs) and _WEBHOOK not in json.dumps(_recs))
    check("r100 carry: the account is the key minus the numeric tweet id (underscored names survive)",
          x3.media_heal_record("1", {}, "Wuthering_Waves_2116847734669873623", _NOW)["account"]
          == "Wuthering_Waves"
          and x3.media_heal_record("1", {}, "Ananta_EN_1", _NOW)["account"] == "Ananta_EN")

    # --- the next run: the carried message gets ONE identical edit ---
    _later = _NOW + 10 * 60
    _s = _Session(message_reply=_COLD_1080, message_status=200, patch_replies=[_WARM_1080])
    _out = _carry(_s, _recs, [], now=_later)
    check("r100 carry: next run = one GET, still cold -> one IDENTICAL edit -> resolved -> record dropped",
          _out == {} and len(_s.urls("GET")) == 1
          and _s.patches == [(f"{_WEBHOOK}/messages/999?with_components=true", _CARD)],
          str((_out, _s.requests, _s.patches)))
    _s = _Session(message_reply=_COLD_1080, message_status=200, patch_replies=[_COLD_1080])
    _out = _carry(_s, _recs, [], now=_later)
    check("r100 carry: still cold after the carried edit -> kept with runs=1 and a fresh last_checked "
          "(first_seen untouched) — exactly one edit this run",
          _out == {_KEY: dict(_recs[_KEY], runs=1, last_checked=_later)} and len(_s.patches) == 1,
          str(_out))
    _s = _Session(message_reply=_WARM_1080, message_status=200)
    _out = _carry(_s, _recs, [], now=_later)
    check("r100 carry: resolved on its own by the next run -> dropped after the GET, no edit",
          _out == {} and len(_s.urls("GET")) == 1 and _s.patches == [])
    for _status in (401, 403, 404):
        _s = _Session(message_status=_status)
        check(f"r100 carry: GET {_status} (deleted message / changed webhook) -> dropped, no edit",
              _carry(_s, _recs, [], now=_later) == {} and _s.patches == [])
    _s = _Session(message_reply=_COLD_1080, message_status=200, patch_replies=[_COLD_1080],
                  patch_status=500)
    _out = _carry(_s, _recs, [], now=_later)
    check("r100 carry: a failed edit (HTTP 500) keeps the record for the next run",
          _out == {_KEY: dict(_recs[_KEY], runs=1, last_checked=_later)}, str(_out))
    _s = _Session(message_reply=_COLD_1080, message_status=200, patch_replies=[_COLD_1080])
    _expired = _NOW + x3.MEDIA_HEAL_CARRY_HOURS * 3600 + 1
    check("r100 carry: past MEDIA_HEAL_CARRY_HOURS the record is dropped without any request",
          _carry(_s, _recs, [], now=_expired) == {} and _s.requests == [] and _s.patches == [])
    _s = _Session(message_reply=_COLD_1080, message_status=200, patch_replies=[_COLD_1080])
    _edge = _NOW + x3.MEDIA_HEAL_CARRY_HOURS * 3600
    check("r100 carry: exactly at the window edge it still gets its edit",
          len(_s.patches) == 0 and _carry(_s, _recs, [], now=_edge) != {} and len(_s.patches) == 1)
    x3.DEFAULT_WEBHOOK_URL = None
    _s = _Session(message_reply=_COLD_1080, message_status=200)
    check("r100 carry: no webhook for the account any more -> dropped, no request",
          _carry(_s, _recs, [], now=_later) == {} and _s.requests == [])
    x3.DEFAULT_WEBHOOK_URL = _WEBHOOK
    check("r100 carry: malformed records are discarded, sane ones kept",
          _carry(_Session(message_status=404), {"bad": {"payload": "x"}, "worse": 5,
                                                 _KEY: _recs[_KEY]}, [], now=_later) == {})
    check("r100 carry: a key already carried is not re-recorded by the same run's leftovers",
          _carry(_Session(message_reply=_COLD_1080, message_status=200, patch_replies=[_COLD_1080]),
                 _recs, [(_WEBHOOK, "999", _CARD, _KEY)], now=_later)[_KEY]["first_seen"] == _NOW)
    _many = {f"Acc_{i}": dict(_recs[_KEY], first_seen=_NOW + i, last_checked=_NOW + i)
             for i in range(25)}
    _s = _Session(message_reply=_COLD_1080, message_status=200, patch_replies=[_COLD_1080] * 25)
    _out = _carry(_s, _many, [], now=_later)
    check("r100 carry: at most 20 carried messages are touched per run (oldest first); the rest "
          "wait untouched",
          len(_s.patches) == 20 and len(_out) == 25
          and all(_out[f"Acc_{i}"]["runs"] == 1 for i in range(20))
          and all(_out[f"Acc_{i}"]["runs"] == 0 for i in range(20, 25)), str(len(_s.patches)))

    # --- the switch ---
    _real_hours = x3.MEDIA_HEAL_CARRY_HOURS
    x3.MEDIA_HEAL_CARRY_HOURS = 0
    _s = _Session(message_reply=_COLD_1080, message_status=200, patch_replies=[_COLD_1080])
    check("r100 carry: MEDIA_HEAL_CARRY_HOURS=0 -> nothing recorded, nothing touched, existing "
          "records returned as they are",
          _carry(_s, {}, [(_WEBHOOK, "999", _CARD, _KEY)]) == {}
          and _carry(_s, _recs, []) == _recs and _s.requests == [] and _s.patches == [])
    x3.MEDIA_HEAL_CARRY_HOURS = _real_hours

    # --- the state file ---
    with tempfile.TemporaryDirectory() as _tmp:
        _path = os.path.join(_tmp, "media_heal_carry.json")
        check("r100 file: a missing file loads as {}", x3.load_media_heal_pending(_path) == {})
        check("r100 file: records round-trip", x3.save_media_heal_pending(_recs, _path)
              and x3.load_media_heal_pending(_path) == _recs)
        _first = open(_path, "rb").read()
        x3.save_media_heal_pending(x3.load_media_heal_pending(_path), _path)
        check("r100 file: re-saving the same state is byte-identical (a quiet run makes no cache commit)",
              open(_path, "rb").read() == _first)
        x3.save_media_heal_pending({}, _path)
        check("r100 file: an empty state is written as '{}' (the workflow's git add always finds it)",
              open(_path, "rb").read() == b"{}\n")
        open(_path, "w").write("not json")
        check("r100 file: an unreadable file loads as {} instead of crashing the run",
              x3.load_media_heal_pending(_path) == {})
        open(_path, "w").write("[1, 2]")
        check("r100 file: a non-dict file loads as {}", x3.load_media_heal_pending(_path) == {})
        # The workflow's recovery merge must keep these records even though
        # their keys are present in posted_tweets.json.
        import subprocess  # noqa: E402
        _ours, _repo = os.path.join(_tmp, "ours"), os.path.join(_tmp, "repo")
        os.makedirs(_ours), os.makedirs(_repo)
        json.dump([_KEY], open(os.path.join(_ours, "posted_tweets.json"), "w"))
        json.dump([_KEY, "Other_1"], open(os.path.join(_repo, "posted_tweets.json"), "w"))
        x3.save_media_heal_pending(_recs, os.path.join(_ours, "media_heal_carry.json"))
        _older = {_KEY: dict(_recs[_KEY], runs=0, last_checked=_NOW - 600, first_seen=_NOW - 600)}
        x3.save_media_heal_pending(_older, os.path.join(_repo, "media_heal_carry.json"))
        _merge = subprocess.run([sys.executable,
                                 os.path.join(ROOT, ".github", "scripts", "merge_monitor_caches.py"),
                                 _ours, _repo, "posted_tweets.json", "media_heal_carry.json"],
                                capture_output=True, text=True)
        _merged = x3.load_media_heal_pending(os.path.join(_repo, "media_heal_carry.json"))
        check("r100 file: merge_monitor_caches.py keeps the carried record (newest last_checked wins) "
              "although its key is in posted_tweets.json",
              _merge.returncode == 0 and _merged == _recs, f"{_merge.stderr} {_merged}")
        check("r100 file: the merged file is byte-identical to what the engine writes (no churn)",
              open(os.path.join(_repo, "media_heal_carry.json"), "rb").read() == _first)
finally:
    x3.MEDIA_HEAL_DELAY_SECONDS, x3.DEFAULT_WEBHOOK_URL = _real_delay, _real_hook

_src_r100 = open(os.path.join(ROOT, "testing area", "twitter_v3.py"), encoding="utf-8").read()
check("r100 wiring: main() heals, then carries over, then saves — after the posting loop",
      _src_r100.index("heal_leftovers = await heal_unresolved_media(session, heal_queue)")
      < _src_r100.index("carried = await carry_over_media_heal(session, carried, heal_leftovers)")
      < _src_r100.index("save_media_heal_pending(carried)")
      and _src_r100.index("heal_queue.append(")
      < _src_r100.index("heal_leftovers = await heal_unresolved_media(session, heal_queue)"))
check("r100 wiring: the state is loaded from the file every run (never only from memory)",
      "carried = load_media_heal_pending()" in _src_r100)
check("r100 config: MEDIA_HEAL_CARRY_HOURS is empty-string safe and never negative",
      'MEDIA_HEAL_CARRY_HOURS = max(0, _env_int("MEDIA_HEAL_CARRY_HOURS", 6))' in _src_r100)
_wf = open(os.path.join(ROOT, ".github", "workflows", "twitter_monitor.yml"), encoding="utf-8").read()
check("r100 workflow: MEDIA_HEAL_CARRY_HOURS is wired as a repository Variable",
      "MEDIA_HEAL_CARRY_HOURS: ${{ vars.MEDIA_HEAL_CARRY_HOURS }}" in _wf)
check("r100 workflow: the carry file is staged (when present) in the persist step AND its recovery "
      "path, copied aside before the reset, and merged semantically",
      _wf.count("if [ -f media_heal_carry.json ]; then git add -f media_heal_carry.json; fi") == 2
      and 'cp media_heal_carry.json "$ours_dir/media_heal_carry.json"' in _wf
      and "merge_monitor_caches.py \"$ours_dir\" . posted_tweets.json media_heal_carry.json" in _wf)
check("r100 workflow: the carry file is git-ignored like the other caches (force-added only)",
      "media_heal_carry.json" in open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read())


# ===========================================================================
# 5. NO NEW CONFIGURATION, NO NEW DEPENDENCY ON A VARIABLE
# ===========================================================================
print("\n--- 5. configuration and CI wiring ---")
check("r73 config: the new media helpers read no environment variable",
      not any("os.getenv" in inspect.getsource(f) for f in (
          x3.canonical_media_entries, x3.media_entry_is_video,
          x3.image_format_tier, x3.resolve_gif_image, x3.collect_media)))
check("r73 config: media-heal defaults are untouched by this round",
      (x3.MEDIA_HEAL_DELAY_SECONDS, x3.MEDIA_HEAL_ATTEMPTS,
       x3.MEDIA_HEAL, x3.MEDIA_HEAL_SCOPE) == (45, 4, True, "video"),
      f"{x3.MEDIA_HEAL_DELAY_SECONDS} {x3.MEDIA_HEAL_ATTEMPTS} {x3.MEDIA_HEAL_SCOPE}")
check("r73 config: the proven 256 MiB gallery cap is unchanged",
      x3.VIDEO_SIZE_LIMIT == 256 * 1024 * 1024)
# Optional 350 MiB setting is explicit and malformed/unset inputs stay at 256 MiB.
x2 = load_module("twitter_media_v2_limit", "testing area/twitter_v2_button_outside.py")
for _module in (x3, x2):
    for _input in (None, "", "0", "false", "off", "350", "garbage"):
        check(f"high video cap defaults to 256 MiB for {_module.__name__} {_input!r}",
              _module._video_size_limit(_input) == 256 * 1024 * 1024)
    for _input in ("1", "true", "YES", " on "):
        check(f"high video cap opts in to 350 MiB for {_module.__name__} {_input!r}",
              _module._video_size_limit(_input) == 350 * 1024 * 1024)

# Exercise actual selection, not just config parsing, at both limits and their edges.
from unittest.mock import AsyncMock, patch
for _module in (x3, x2):
    for _limit, _size, _expected in ((256, 256, True), (256, 257, False),
                                     (350, 350, True), (350, 351, False)):
        with patch.object(_module, "VIDEO_SIZE_LIMIT", _limit * _MB), \
             patch.object(_module, "probe_video_size", new=AsyncMock(return_value=_size * _MB)):
            _items, _notes = asyncio.run(_module.collect_media(None, _legacy_media([_video()])))
        check(f"video limit selection {_module.__name__}: {_size}/{_limit} MiB",
              bool(_items) and (_items[0]["media"]["url"] == _CLIP) is _expected,
              str((_items, _notes)))

check("r73 config: the animated/AVIF content-type tiers are explicit constants",
      x3.ANIMATED_IMAGE_TYPES == ("image/webp", "image/gif")
      and x3.AVIF_IMAGE_TYPES == ("image/avif", "image/heic", "image/heif"))

# ===========================================================================
# 5. ROUND 110 — link / app card artwork as a thumbnail (2026-11-05)
# ===========================================================================
print("\n--- 5. card artwork thumbnail (round 110) ---")
# Live HonkaiNA 2118221775108771936 (photo) / 2118191579307381245 (video):
# FxTwitter folds the App Store card's icon (pbs.twimg.com/card_img/…) into
# the media list, and it rode along as a second gallery tile. With native
# media present it is now the Section thumbnail; alone it stays a tile.
_R110_CARD = "https://pbs.twimg.com/card_img/2117885179226374144/8O4A5wKA?format=png&name=orig"
_R110_PHOTO = "https://pbs.twimg.com/media/HWS61I-aMAcRkez.jpg?name=orig"
_R110_VIDEO = ("https://video.twimg.com/amplify_video/2118020756831612930/vid/avc1/1280x720/"
               "tdt7yOlAFluoT7TW.mp4?tag=14")
_R110_TWEET = {"author": {"name": "Honkai: Nexus Anima", "screen_name": "HonkaiNA"},
               "text": "Honkai: Nexus Anima Now Available for App Store Pre-registration\n\nDear Animaster,",
               "replies": 0, "retweets": 3, "likes": 15, "views": 2291, "color": "#1d9bf0"}


def _r110_card(items, **kw):
    return x3.build_v3_payload("HonkaiNA", _R110_TWEET, "https://fxtwitter.com/HonkaiNA/status/1",
                               gallery_items=[{"media": {"url": u}} for u in items],
                               media_notes=[], posted_ts=1, **kw)


def _r110_shape(payload):
    out = []
    for c in payload["components"][0]["components"]:
        if c["type"] == 9:
            out.append(("section", [t["content"] for t in c["components"]], c["accessory"]))
        elif c["type"] == 12:
            out.append(("gallery", [i["media"]["url"] for i in c["items"]]))
        else:
            out.append((c["type"], c.get("content")))
    return out


check("r110: is_card_image_url matches only pbs.twimg.com/card_img/ (native /media/ and video "
      "renditions are never cards)",
      x3.is_card_image_url(_R110_CARD) and not x3.is_card_image_url(_R110_PHOTO)
      and not x3.is_card_image_url(_R110_VIDEO) and not x3.is_card_image_url(None)
      and not x3.is_card_image_url("https://evil.example/pbs.twimg.com/card_img/x"))
_r110_p = _r110_shape(_r110_card([_R110_PHOTO, _R110_CARD]))
check("r110: photo + card → Section(header + opening text, thumbnail = card icon), gallery keeps the "
      "photo only; divider / stats / divider / buttons unchanged",
      _r110_p[0][0] == "section"
      and _r110_p[0][1] == ["### [Honkai: Nexus Anima](https://x.com/HonkaiNA) just tweeted:",
                            x3.linkify_text(_R110_TWEET["text"])]
      and _r110_p[0][2] == {"type": 11, "media": {"url": _R110_CARD}}
      and _r110_p[1:] == [(14, None), ("gallery", [_R110_PHOTO]),
                          (10, "-# 💬 0 🔁 3 ❤️ 15 👁️ 2291   •   🕐 <t:1:f>"), (14, None), (1, None)],
      _r110_p)
_r110_v = _r110_shape(_r110_card([_R110_VIDEO, _R110_CARD]))
check("r110: video + card → same shape with the video alone in the gallery",
      _r110_v[0][0] == "section" and _r110_v[0][2]["media"]["url"] == _R110_CARD
      and _r110_v[2] == ("gallery", [_R110_VIDEO]), _r110_v)
check("r110: card-only, photo-only and media-less tweets are byte-identical to the legacy card "
      "(no Section)",
      _r110_shape(_r110_card([_R110_CARD]))[:4] == [
          (10, "### [Honkai: Nexus Anima](https://x.com/HonkaiNA) just tweeted:"),
          (10, x3.linkify_text(_R110_TWEET["text"])), (14, None), ("gallery", [_R110_CARD])]
      and _r110_shape(_r110_card([_R110_PHOTO]))[3] == ("gallery", [_R110_PHOTO])
      and all(k[0] != "section" for k in _r110_shape(_r110_card([]))))
_r110_r = _r110_shape(_r110_card([_R110_PHOTO, _R110_CARD], reply_line="-# ↩ reply",
                                 repost_account="Someone"))
check("r110: a repost header (2 lines) + reply line fill the Section's three text slots; the body "
      "text follows below full-width",
      _r110_r[0][0] == "section" and len(_r110_r[0][1]) == 3
      and _r110_r[0][1][0].startswith("### [Someone reposted]") and _r110_r[0][1][2] == "-# ↩ reply"
      and _r110_r[1] == (10, x3.linkify_text(_R110_TWEET["text"])), _r110_r)
check("r110: a second card image is never dropped — it stays in the gallery after the native media",
      x3.split_card_thumbnail([{"media": {"url": _R110_PHOTO}}, {"media": {"url": _R110_CARD}},
                               {"media": {"url": _R110_CARD.replace("8O4A5wKA", "zzzz")}}])
      == ({"media": {"url": _R110_CARD}},
          [{"media": {"url": _R110_PHOTO}}, {"media": {"url": _R110_CARD.replace("8O4A5wKA", "zzzz")}}]))
_r110_a = _r110_shape(_r110_card([_R110_PHOTO, _R110_CARD],
                                 lead_gallery_items=[{"media": {"url": "https://pbs.twimg.com/media/cover.jpg"}}]))
check("r110: an article card already leads with its cover — no Section; the card image returns to the "
      "gallery ahead of the native media",
      all(k[0] != "section" for k in _r110_a)
      and ("gallery", [_R110_CARD, _R110_PHOTO]) in _r110_a, _r110_a)
_r110_q = x3.build_quote_components({"id": "9", "author": {"name": "Q", "screen_name": "q"}, "text": "quoted"},
                                    [{"media": {"url": _R110_PHOTO}}, {"media": {"url": _R110_CARD}}], [])
check("r110: a quoted post with native media + card gets the thumbnail beside its quote head",
      [c["type"] for c in _r110_q] == [14, 9, 12]
      and _r110_q[1]["accessory"] == {"type": 11, "media": {"url": _R110_CARD}}
      and _r110_q[1]["components"][0]["content"].startswith(">>> [**Quote**]")
      and [i["media"]["url"] for i in _r110_q[2]["items"]] == [_R110_PHOTO])
check("r110: the heal walker still sees the thumbnail (round 49) and the Section never carries an "
      "empty text display",
      _R110_CARD in x3.unresolved_media_items(_r110_card([_R110_PHOTO, _R110_CARD]))
      and all(t["content"] for t in x3.thumbnail_section(
          [{"type": 10, "content": "a"}, {"type": 10, "content": ""}, None, {"type": 10, "content": "b"},
           {"type": 10, "content": "c"}], {"media": {"url": _R110_CARD}})["components"])
      and len(x3.thumbnail_section([{"type": 10, "content": str(i)} for i in range(5)],
                                   {"media": {"url": _R110_CARD}})["components"]) == 3)

_ci = open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8").read()
check("ci runs this suite", "python tests/test_twitter_media.py" in _ci)


print()
if failures:
    print(f"TWITTER MEDIA TEST FAILURES ({len(failures)}): {failures}")
    sys.exit(1)
print("TWITTER MEDIA TEST: ALL PASS")
