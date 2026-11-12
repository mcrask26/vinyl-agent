"""Last.fm API: similar artists, top albums and tags. Free, needs LASTFM_API_KEY.

Docs: https://www.last.fm/api
Rate limit: Last.fm asks for no more than 5 calls a second on average; we stay at 4.
"""

import time

import requests

from core.config import get_lastfm_key
from tools.ratelimit import RateLimiter

API = "https://ws.audioscrobbler.com/2.0/"
TIMEOUT_S = 15
RETRY_WAITS_S = (2, 8)
HEADERS = {"User-Agent": "VinylAgent/0.4 (personal record-collection app)"}

_limiter = RateLimiter(calls=4, period=1)


class LastfmError(RuntimeError):
    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


class LastfmNotFound(LastfmError):
    pass


def _get(method: str, **params) -> dict:
    query = {"method": method, "api_key": get_lastfm_key(), "format": "json", "autocorrect": 1, **params}
    for attempt in range(len(RETRY_WAITS_S) + 1):
        _limiter.wait()
        try:
            resp = requests.get(API, params=query, headers=HEADERS, timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            raise LastfmError(f"Couldn't reach Last.fm (are you offline, or is Last.fm down?). Details: {exc}",
                              fatal=True) from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        code = body.get("error")
        if code in (10, 26):
            raise LastfmError("Last.fm rejected your API key. Check LASTFM_API_KEY in .env, then restart the app.",
                              fatal=True)
        if code == 6:
            raise LastfmNotFound(body.get("message") or "Not found on Last.fm.")
        if code in (11, 16, 29) or resp.status_code in (429, 500, 502, 503, 504):
            if attempt < len(RETRY_WAITS_S):
                time.sleep(RETRY_WAITS_S[attempt])
                continue
            raise LastfmError("Last.fm is busy or down right now. Try again in a few minutes.", fatal=True)
        if code or not resp.ok:
            raise LastfmError(f"Last.fm error: {body.get('message') or resp.status_code}")
        return body
    raise LastfmError("Last.fm didn't answer.")  # pragma: no cover


def _list(value):
    """Last.fm returns a dict instead of a list when there's only one item."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def similar_artists(artist: str, limit: int = 20) -> list:
    """[{"name", "match" (0-1), "url"}], most similar first."""
    body = _get("artist.getsimilar", artist=artist, limit=limit)
    out = []
    for a in _list((body.get("similarartists") or {}).get("artist")):
        try:
            match = float(a.get("match") or 0)
        except ValueError:
            match = 0.0
        out.append({"name": a.get("name", ""), "match": match, "url": a.get("url", "")})
    return out


def _image(images) -> str:
    by_size = {i.get("size"): i.get("#text") for i in _list(images)}
    return by_size.get("extralarge") or by_size.get("large") or by_size.get("medium") or ""


def top_albums(artist: str, limit: int = 8) -> list:
    """[{"name", "playcount", "image"}], most listened first."""
    body = _get("artist.gettopalbums", artist=artist, limit=limit)
    out = []
    for a in _list((body.get("topalbums") or {}).get("album")):
        name = a.get("name") or ""
        if not name or name == "(null)":
            continue
        out.append({"name": name, "playcount": int(a.get("playcount") or 0), "image": _image(a.get("image"))})
    return out


def top_tags(artist: str, limit: int = 8) -> list:
    body = _get("artist.gettoptags", artist=artist)
    return [t.get("name", "").lower() for t in _list((body.get("toptags") or {}).get("tag"))[:limit]]
