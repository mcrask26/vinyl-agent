"""MusicBrainz API: an artist's studio albums, for "Missing Pieces". Free, no key.

Docs: https://musicbrainz.org/doc/MusicBrainz_API
Rate limit: 1 call per second, and a User-Agent naming the app is required.
"""

import time

import requests

from core.config import get_musicbrainz_contact
from tools.ratelimit import RateLimiter

API = "https://musicbrainz.org/ws/2"
TIMEOUT_S = 20
RETRY_WAITS_S = (3, 10)

_limiter = RateLimiter(calls=1, period=1.1)


class MusicBrainzError(RuntimeError):
    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


def _headers():
    contact = get_musicbrainz_contact() or "personal record-collection app"
    return {"User-Agent": f"VinylAgent/0.4 ( {contact} )", "Accept": "application/json"}


def _get(path: str, params: dict) -> dict:
    for attempt in range(len(RETRY_WAITS_S) + 1):
        _limiter.wait()
        try:
            resp = requests.get(f"{API}{path}", params={**params, "fmt": "json"}, headers=_headers(), timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            raise MusicBrainzError(f"Couldn't reach MusicBrainz (are you offline, or is it down?). Details: {exc}",
                                   fatal=True) from exc
        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt < len(RETRY_WAITS_S):
                time.sleep(RETRY_WAITS_S[attempt])
                continue
            raise MusicBrainzError("MusicBrainz is busy or down right now. Try again in a few minutes.", fatal=True)
        if not resp.ok:
            raise MusicBrainzError(f"MusicBrainz error (HTTP {resp.status_code}).")
        return resp.json()
    raise MusicBrainzError("MusicBrainz didn't answer.")  # pragma: no cover


def _unwrap(title: str) -> str:
    t = (title or "").strip()
    return t[1:-1].strip() if t.startswith("[") and t.endswith("]") and t.count("[") == 1 else t


def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def find_artist(name: str):
    """Best-matching artist {"id", "name", "disambiguation"} or None if nothing matches well."""
    body = _get("/artist", {"query": f'artist:"{name}"', "limit": 5})
    for a in body.get("artists") or []:
        names = [a.get("name", "")] + [al.get("name", "") for al in a.get("aliases") or []]
        if int(a.get("score") or 0) >= 90 and any(_norm(n) == _norm(name) for n in names):
            return {"id": a["id"], "name": a.get("name", name), "disambiguation": a.get("disambiguation", "")}
    return None


def parse_release_groups(groups: list) -> list:
    """Keeps studio albums only: type Album with no secondary type (Live, Compilation, Soundtrack…)."""
    out = []
    for g in groups:
        if g.get("primary-type") != "Album" or g.get("secondary-types"):
            continue
        date = g.get("first-release-date") or ""
        out.append({"id": g["id"], "title": _unwrap(g.get("title", "")), "year": int(date[:4]) if date[:4].isdigit() else None,
                    "url": f"https://musicbrainz.org/release-group/{g['id']}"})
    return sorted(out, key=lambda a: (a["year"] is None, a["year"] or 0, a["title"]))


def studio_albums(artist_id: str) -> list:
    """All studio albums by an artist, oldest first: [{"id", "title", "year", "url"}]."""
    groups, offset = [], 0
    while True:
        body = _get("/release-group", {"artist": artist_id, "type": "album", "limit": 100, "offset": offset,
                                       "release-group-status": "website-default"})  # leaves out bootlegs
        page = body.get("release-groups") or []
        groups += page
        offset += len(page)
        if not page or offset >= int(body.get("release-group-count") or 0) or offset >= 300:
            break
    return parse_release_groups(groups)
