"""Discogs: find the exact pressing of a record, its details, and what it's worth.

Docs: https://www.discogs.com/developers
Free. Needs a personal access token (DISCOGS_TOKEN in .env).
Rate limit: 60 requests per minute with a token. We stay at 55/min, watch the
X-Discogs-Ratelimit-Remaining header, and back off and retry on "429 Too Many Requests".
"""

import re
import time

import requests

from core.config import get_currency, get_discogs_token
from tools.ratelimit import RateLimiter

BASE_URL = "https://api.discogs.com"
WEB_URL = "https://www.discogs.com"
USER_AGENT = "VinylAgent/0.1 +https://github.com (student project)"
TIMEOUT_S = 20
RETRY_WAITS_S = (5, 15, 30)

_limiter = RateLimiter(calls=55, period=60)

SELLER_SETTINGS_URL = "https://www.discogs.com/settings/seller"


class DiscogsError(RuntimeError):
    """Something went wrong talking to Discogs. The message is safe to show in the app.

    fatal=True means every other call would fail the same way (offline, bad token, Discogs
    down, rate limit), so a batch job should stop instead of trying the next record.
    """

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


class DiscogsNotFound(DiscogsError):
    pass


class DiscogsPricesUnavailable(DiscogsError):
    """Your Discogs account isn't allowed price suggestions yet (seller setup incl. PayPal unfinished)."""


def _message(resp) -> str:
    try:
        return resp.json().get("message", "") or resp.text[:200]
    except ValueError:
        return resp.text[:200]


def _get(path: str, params=None) -> dict:
    headers = {"User-Agent": USER_AGENT, "Authorization": f"Discogs token={get_discogs_token()}"}
    resp = None
    for wait_s in (0,) + RETRY_WAITS_S:
        if wait_s:
            time.sleep(wait_s)
        _limiter.wait()
        try:
            resp = requests.get(BASE_URL + path, params=params, headers=headers, timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            raise DiscogsError(
                f"Couldn't reach Discogs (are you offline, or is Discogs down?). Details: {exc}", fatal=True
            ) from exc
        # Nearly out of calls for this minute: pause a little so the next call doesn't get a 429.
        remaining = resp.headers.get("X-Discogs-Ratelimit-Remaining")
        if remaining is not None and remaining.isdigit() and int(remaining) <= 2:
            time.sleep(5)
        if resp.status_code != 429:
            break
    if resp.status_code == 429:
        raise DiscogsError("Discogs's rate limit was reached (60 calls a minute). Wait a minute and try again.", fatal=True)
    if resp.status_code == 401:
        raise DiscogsError("Discogs rejected your token. Check DISCOGS_TOKEN in .env, then restart the app.", fatal=True)
    if resp.status_code == 404:
        raise DiscogsNotFound(_message(resp) or "Discogs doesn't have that item.")
    if resp.status_code >= 500:
        raise DiscogsError(f"Discogs is having problems right now (HTTP {resp.status_code}). Try again in a few minutes.", fatal=True)
    if resp.status_code >= 400:
        raise DiscogsError(f"Discogs said: {_message(resp)} (HTTP {resp.status_code})")
    try:
        return resp.json()
    except ValueError as exc:
        raise DiscogsError("Discogs returned a response that wasn't JSON.") from exc


# ---------- Helpers ----------

def release_url(release_id: int) -> str:
    return f"{WEB_URL}/release/{release_id}"


def normalize_catno(catno: str) -> str:
    """'SD 19129' / 'SD-19129' / 'sd19129' all compare equal."""
    return re.sub(r"[^A-Z0-9]", "", (catno or "").upper())


def parse_release_ref(text: str) -> int:
    """Release ID from a Discogs URL ('.../release/1234-Name'), '[r1234]', 'r1234' or '1234'."""
    text = (text or "").strip()
    if re.search(r"/master/\d+", text):
        raise ValueError(
            "That's a Discogs *master* page (all versions of an album). Open the specific pressing "
            "you own and paste its /release/ link instead."
        )
    m = re.search(r"/release/(\d+)", text) or re.fullmatch(r"\[?r?(\d+)\]?", text, flags=re.I)
    if not m:
        raise ValueError("That doesn't look like a Discogs release link or ID (e.g. https://www.discogs.com/release/1234567).")
    return int(m.group(1))


def _dedupe(items):
    seen, out = set(), []
    for item in items or []:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ---------- Search ----------

def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def parse_search_result(item: dict, wanted_catno: str = "", wanted_artist: str = "") -> dict:
    """One Discogs search hit (a release = one pressing, or a master = the album, any pressing)."""
    title = item.get("title") or ""
    artist, _, album = title.partition(" - ")
    catno = item.get("catno") or ""
    formats = _dedupe(item.get("format"))
    kind = item.get("type") or "release"
    return {
        "kind": kind,
        "release_id": item["id"] if kind == "release" else None,
        "master_id": item["id"] if kind == "master" else (item.get("master_id") or None),
        "artist": _artist_name(artist),
        "album": (album or title).strip(),
        "year": int(item["year"]) if str(item.get("year") or "").isdigit() else None,
        "country": item.get("country") or "",
        "labels": _dedupe(item.get("label")),
        "catno": catno,
        "formats": formats,
        "genres": item.get("genre") or [],
        "styles": item.get("style") or [],
        "thumb": item.get("thumb") or "",
        "cover": item.get("cover_image") or "",
        "url": WEB_URL + item["uri"] if item.get("uri") else release_url(item["id"]),
        "have": (item.get("community") or {}).get("have", 0),
        "want": (item.get("community") or {}).get("want", 0),
        "catno_match": _catno_matches(wanted_catno, catno),
        "artist_match": not wanted_artist or _words(wanted_artist) <= _words(artist),
        "unofficial": any(f in ("Unofficial Release",) for f in formats),
    }


def _catno_matches(wanted: str, catno: str) -> bool:
    if not wanted or not catno:
        return False
    return normalize_catno(wanted) in [normalize_catno(c) for c in catno.split(",")]


def _artist_name(name: str) -> str:
    """Discogs adds ' (2)' etc. to tell same-named artists apart; drop it for display."""
    return re.sub(r"\s\(\d+\)$", "", name or "").strip()


_TITLE_STOPWORDS = {"the", "a", "an", "and", "of", "lp", "album", "vinyl"}


def title_score(found: str, wanted: str) -> int:
    """2 = same title, 1 = has every word of the title you typed, 0 = different (or nothing typed)."""
    from core.names import album_matches  # pure helper, no app state

    if not wanted:
        return 0
    if album_matches(found, wanted):
        return 2
    want = _words(wanted) - _TITLE_STOPWORDS
    return 1 if want and want <= _words(found) else 0


def rank_candidates(candidates: list, wanted_album: str = "") -> list:
    """Best guess first: matching catalog number, then the right artist, then the title you typed
    (so "Caribou" doesn't lose to a more popular album by the same artist), then official
    releases, then the most-owned (the likeliest pressing / the real album, not a bootleg)."""
    return sorted(candidates, key=lambda c: (not c["catno_match"], not c.get("artist_match", True),
                                             -title_score(c.get("album", ""), wanted_album),
                                             c.get("unofficial", False), -(c["have"] or 0)))


def _search(params: dict, kind: str, wanted_catno: str, wanted_artist: str, limit: int) -> list:
    params = dict(params, type=kind, format="Vinyl", per_page=min(limit, 50))
    payload = _get("/database/search", params)
    return [parse_search_result(r, wanted_catno, wanted_artist) for r in payload.get("results") or [] if r.get("id")]


def _merge(*lists) -> list:
    seen, out = set(), []
    for lst in lists:
        for c in lst:
            if c["url"] not in seen:
                seen.add(c["url"])
                out.append(c)
    return out


def find_release_candidates(artist: str, album: str, catno: str = "", limit: int = 12):
    """Vinyl pressings that might be this record, best guess first.

    Returns (candidates, note). `note` explains anything surprising, or is None.
    Without a catalog number we first find the album itself (a Discogs "master"), because
    Discogs's name can differ from the cover (Led Zeppelin IV is "Untitled"), then list that
    album's pressings, most-owned first.
    """
    artist, album, catno = (artist or "").strip(), (album or "").strip(), (catno or "").strip()
    note = None
    if catno:
        exact = _search({"artist": artist, "catno": catno}, "release", catno, artist, limit)
        if exact:
            return rank_candidates(exact)[:limit], None
        note = f"No vinyl pressing by this artist has catalog number “{catno}” on Discogs, so here are all pressings instead."

    masters = find_master_candidates(artist, album, limit=3)
    top = masters[0] if masters and masters[0]["artist_match"] and not masters[0]["unofficial"] else None
    results = []
    if top:
        pressings = _search({"artist": artist, "release_title": top["album"]}, "release", catno, artist, 50)
        results = [c for c in pressings if c["master_id"] == top["master_id"]]
        if results and _words(top["album"]) != _words(album) and not note:
            note = f"Discogs lists this album as “{top['album']}” ({top['year'] or 'year unknown'})."
    if not results:
        strict = _search({"artist": artist, "release_title": album}, "release", catno, artist, limit)
        loose = _search({"q": f"{artist} {album}"}, "release", catno, artist, limit)
        results = [c for c in _merge(strict, loose) if c["artist_match"]] or _merge(strict, loose)
        if results and not strict and not note:
            note = "No exact title match on Discogs, so these come from a keyword search. Check them carefully."
        return rank_candidates(results, album)[:limit], note
    return rank_candidates(results)[:limit], note


def find_master_candidates(artist: str, album: str, limit: int = 6) -> list:
    """Albums (all pressings grouped) that might be this one, best guess first."""
    artist, album = (artist or "").strip(), (album or "").strip()
    strict = _search({"artist": artist, "release_title": album}, "master", "", artist, limit)
    loose = _search({"q": f"{artist} {album}"}, "master", "", artist, limit)
    results = [c for c in _merge(strict, loose) if c["artist_match"]] or _merge(strict, loose)
    return rank_candidates(results, album)[:limit]


def master_url(master_id: int) -> str:
    return f"{WEB_URL}/master/{master_id}"


def marketplace_url(release_id=None, master_id=None) -> str:
    """The Discogs page listing copies for sale (where you'd buy), cheapest first."""
    if release_id:
        return f"{WEB_URL}/sell/release/{int(release_id)}?sort=price%2Casc"
    return f"{WEB_URL}/sell/list?master_id={int(master_id)}&format=Vinyl&sort=price%2Casc"


def vinyl_versions(master_id: int, pages: int = 2) -> list:
    """Vinyl pressings of an album, most-owned first (up to 100 per page; 1 call per page).

    We price pressings one by one because the album-level "lowest price" Discogs gives counts
    every format, so it's usually a CD or cassette.
    """
    out = []
    for page in range(1, pages + 1):
        body = _get(f"/masters/{int(master_id)}/versions", {"format": "Vinyl", "per_page": 100, "page": page})
        for v in body.get("versions") or []:
            community = (v.get("stats") or {}).get("community") or {}
            year = str(v.get("released") or "")[:4]
            out.append({"release_id": v["id"], "country": v.get("country") or "", "year": year if year.isdigit() else "",
                        "catno": v.get("catno") or "", "label": v.get("label") or "", "format": v.get("format") or "",
                        "in_collection": community.get("in_collection") or 0})
        if page >= int((body.get("pagination") or {}).get("pages") or 1):
            break
    return sorted(out, key=lambda v: -v["in_collection"])


# ---------- Release details ----------

def parse_release(payload: dict) -> dict:
    images = payload.get("images") or []
    primary = next((i for i in images if i.get("type") == "primary"), images[0] if images else {})
    formats = payload.get("formats") or []
    format_desc = []
    for f in formats:
        qty = f.get("qty")
        name = f.get("name") or ""
        format_desc.append(" ".join(x for x in [f"{qty}×" if qty and qty != "1" else "", name] if x).strip())
        format_desc.extend(f.get("descriptions") or [])
        if f.get("text"):
            format_desc.append(f["text"])
    labels = payload.get("labels") or []
    lowest = payload.get("lowest_price")
    return {
        "release_id": payload["id"],
        "master_id": payload.get("master_id"),
        "artist": ", ".join(_artist_name(a.get("name", "")) for a in payload.get("artists") or []),
        "album": payload.get("title") or "",
        "year": payload.get("year") or None,
        "country": payload.get("country") or "",
        "labels": _dedupe(l.get("name") for l in labels),
        "catno": ", ".join(_dedupe(l.get("catno") for l in labels if l.get("catno") and l.get("catno") != "none")),
        "formats": _dedupe(format_desc),
        "genres": payload.get("genres") or [],
        "styles": payload.get("styles") or [],
        "cover": primary.get("uri") or "",
        "thumb": primary.get("uri150") or primary.get("uri") or "",
        "url": payload.get("uri") or release_url(payload["id"]),
        "have": (payload.get("community") or {}).get("have", 0),
        "want": (payload.get("community") or {}).get("want", 0),
        "num_for_sale": payload.get("num_for_sale"),
        "lowest_price": float(lowest) if lowest is not None else None,
    }


def get_release(release_id: int) -> dict:
    return parse_release(_get(f"/releases/{int(release_id)}", {"curr_abbr": get_currency()}))


# ---------- Prices ----------

def parse_price_suggestions(payload: dict):
    """{grade: price} and the currency. Empty dict if Discogs has no suggestions for this release."""
    prices, currency = {}, None
    for grade, entry in (payload or {}).items():
        if isinstance(entry, dict) and entry.get("value") is not None:
            prices[grade] = round(float(entry["value"]), 2)
            currency = currency or entry.get("currency")
    return prices, currency


def get_price_suggestions(release_id: int):
    """Discogs's suggested price for this pressing in each condition, based on its sales history.

    Discogs only answers this for accounts with seller settings filled in (free, and you don't
    have to sell anything). We turn that error into instructions.
    """
    try:
        payload = _get(f"/marketplace/price_suggestions/{int(release_id)}")
    except DiscogsError as exc:
        if "seller" in str(exc).lower():
            raise DiscogsPricesUnavailable(
                "Discogs only shares sale-based prices with accounts that finished seller setup "
                f"(including connecting PayPal) at {SELLER_SETTINGS_URL}."
            ) from exc
        raise
    return parse_price_suggestions(payload)


def parse_marketplace_stats(payload: dict) -> dict:
    lowest = payload.get("lowest_price") or {}
    return {
        "lowest_price": round(float(lowest["value"]), 2) if lowest.get("value") is not None else None,
        "currency": lowest.get("currency"),
        "num_for_sale": payload.get("num_for_sale") or 0,
    }


def get_marketplace_stats(release_id: int) -> dict:
    """Cheapest current listing and how many copies are for sale on Discogs right now."""
    return parse_marketplace_stats(_get(f"/marketplace/stats/{int(release_id)}", {"curr_abbr": get_currency()}))
