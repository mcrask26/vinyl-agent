"""Discover: recommendations, Spotify comparison and Missing Pieces.

Pure logic plus calls to tools/ (Last.fm, MusicBrainz, Spotify, Discogs, eBay). Answers from
Last.fm and MusicBrainz are cached in the database so repeat visits cost no calls.
"""

import re
from collections import Counter

from core import deals, storage, wishlist
from core.config import MissingKeyError
from core.names import album_matches, norm_album, norm_artist  # noqa: F401 (album_matches used by the page)
from tools import discogs, ebay, lastfm, musicbrainz

DAY = 86400
SEED_ARTISTS = 8          # your most-owned artists used to find similar ones
CANDIDATES_CHECKED = 12   # similar artists looked up on Discogs before picking the top 5
NOT_STUDIO = re.compile(r"\b(greatest hits|best of|hits|collection|anthology|essential|live|unplugged|"
                        r"compilation|singles|gold|definitive|ultimate)\b", re.I)


class DiscoverError(RuntimeError):
    """Stops a whole Discover run (e.g. Last.fm key rejected). The message is safe to show."""


# ---------- Your collection, as Discover sees it ----------

def owned_index(records: list) -> dict:
    """norm artist -> {"name", "records": [{"album", "year", "genres", "styles"}]}."""
    idx = {}
    for r in records:
        rel = r.get("release") or {}
        name = rel.get("artist") or r["artist"]
        entry = idx.setdefault(norm_artist(name), {"name": name, "records": []})
        if norm_artist(r["artist"]) != norm_artist(name):  # typed differently: index that spelling too
            idx.setdefault(norm_artist(r["artist"]), entry)
        entry["records"].append({"album": r["album"], "discogs_album": rel.get("album") or "",
                                 "year": rel.get("year"), "genres": rel.get("genres") or [],
                                 "styles": rel.get("styles") or []})
    return idx


def _unique_artists(idx: dict) -> list:
    seen, out = set(), []
    for entry in idx.values():
        if id(entry) not in seen:
            seen.add(id(entry))
            out.append(entry)
    return out


def taste_profile(records: list) -> dict:
    idx = owned_index(records)
    artists = sorted(_unique_artists(idx), key=lambda e: (-len(e["records"]), e["name"].lower()))
    genres, styles, decades = Counter(), Counter(), Counter()
    for r in records:
        rel = r.get("release") or {}
        genres.update(rel.get("genres") or [])
        styles.update(rel.get("styles") or [])
        if rel.get("year"):
            decades[f"{rel['year'] // 10 * 10}s"] += 1
    return {"artists": [(e["name"], len(e["records"])) for e in artists], "genres": genres, "styles": styles,
            "decades": decades, "count": len(records), "with_details": sum(1 for r in records if r.get("release"))}


# ---------- Prices for a suggested album ----------

def price_check(artist: str, album: str, master_id=None) -> dict:
    """Cheapest copy on Discogs, plus the best-rated eBay listing (if eBay keys are set)."""
    out = {"discogs_lowest": None, "discogs_count": 0, "discogs_url": None, "ebay": None, "notes": []}
    if master_id:
        try:
            stats = wishlist.cheapest_vinyl(master_id)
            if stats:
                out.update(discogs_lowest=stats["lowest_price"], discogs_count=stats["num_for_sale"],
                           discogs_url=discogs.marketplace_url(stats["release_id"]),
                           discogs_pressing=stats["pressing"])
        except (discogs.DiscogsError, MissingKeyError) as exc:
            out["notes"].append(f"Discogs: {exc}")
    try:
        listings = ebay.search_listings(artist, album, limit=30)
    except MissingKeyError:
        out["notes"].append("eBay keys aren't set, so there's no deal rating yet.")
        return out
    except ebay.EbayError as exc:
        out["notes"].append(f"eBay: {exc}")
        return out
    rated = deals.evaluate(listings, None, "Very Good (VG)")
    good = [l for l in rated if l["meets"] and not l["suspicious"] and l["rating"] != deals.UNRATED]
    best = (good or rated or [None])[0]
    if best:
        out["ebay"] = {k: best.get(k) for k in ("total", "rating", "reason", "url", "grade", "title")}
        out["ebay"]["count"] = len(rated)
    else:
        out["notes"].append("No eBay listings right now.")
    return out


def cached_price_check(artist, album, master_id=None) -> dict:
    return storage.cached(f"price:{norm_artist(artist)}:{norm_album(album)}:{master_id}", 6 * 3600,
                          lambda: price_check(artist, album, master_id))


# ---------- Recommendations ----------

def _similar(name):
    return storage.cached(f"lastfm:similar:{norm_artist(name)}", 7 * DAY, lambda: lastfm.similar_artists(name, 25))


def _top_album(name):
    albums = storage.cached(f"lastfm:topalbums:{norm_artist(name)}", 7 * DAY, lambda: lastfm.top_albums(name, 10))
    return next((a for a in albums if not NOT_STUDIO.search(a["name"])), albums[0] if albums else None)


def _discogs_album(artist, album):
    def fetch():
        for m in discogs.find_master_candidates(artist, album, limit=5):
            if m.get("artist_match") and not m.get("unofficial"):
                return {k: m.get(k) for k in ("master_id", "album", "year", "genres", "styles", "thumb", "url")}
        return None
    return storage.cached(f"discogs:master:{norm_artist(artist)}:{norm_album(album)}", 30 * DAY, fetch)


def _fatal(exc):
    return getattr(exc, "fatal", False) or isinstance(exc, MissingKeyError)


def _owned_albums_text(entry, limit=2) -> str:
    albums = [r["album"] for r in entry["records"]][:limit]
    more = len(entry["records"]) - len(albums)
    return ", ".join(albums) + (f" and {more} more" if more > 0 else "")


def rule_reason(item: dict, profile: dict) -> str:
    """One sentence tying the suggestion to records you own (no AI, no cost)."""
    parts = []
    for b in item["because"][:2]:
        if b.get("owned"):
            parts.append(f"{b['name']} (you own {b['owned']})")
        else:
            parts.append(f"{b['name']} (on your Spotify)")
    text = f"Fans of {' and '.join(parts)} also love {item['artist']}."
    shared = [s for s in item.get("styles") or [] if profile["styles"].get(s)]
    if shared:
        n = sum(profile["styles"][s] for s in shared[:2])
        text += f" It's {', '.join(shared[:2])}, like {n} of your records."
    elif item.get("year"):
        text += f" Released in {item['year']}."
    return text


def claude_brief(records: list, profile: dict, candidates: list):
    """(collection summary, candidate facts) to hand to Claude: only facts the app already has."""
    idx = owned_index(records)
    artists = sorted(_unique_artists(idx), key=lambda e: -len(e["records"]))[:12]
    summary = {
        "records_owned": profile["count"],
        "most_owned_artists": [{"artist": e["name"], "albums_owned": [r["album"] for r in e["records"]][:8]}
                               for e in artists],
        "top_styles": [name for name, _ in profile["styles"].most_common(8)],
        "decades": dict(sorted(profile["decades"].items())),
    }
    facts = [{"artist": c["artist"], "album": c["album"], "year": c.get("year"),
              "styles": (c.get("styles") or [])[:4],
              "similar_to_artists_they_own": [b["name"] for b in c["because"][:3] if b.get("owned")],
              "similar_to_artists_they_stream": [b["name"] for b in c["because"][:3] if not b.get("owned")],
              "already_on_wishlist": bool(c.get("on_wishlist"))} for c in candidates]
    return summary, facts


def recommend(records: list, wishes: list, spotify_artists=None, n: int = 5, on_progress=None,
              picker=None) -> dict:
    """Top-n albums by artists you don't own yet, with reasons and prices.

    Seeds: your most-owned artists (weighted by how many of their records you have), plus your
    top Spotify artists if you're logged in. Each seed's Last.fm similar artists get points
    (similarity × weight); albums whose Discogs styles match your collection get a bonus.
    """
    if not records:
        raise DiscoverError("Add some records to My Collection first: recommendations are based on what you own.")
    idx = owned_index(records)
    profile = taste_profile(records)
    hidden = storage.hidden_suggestions("artist")
    notes = []

    seeds = [{"name": e["name"], "weight": len(e["records"]), "owned": _owned_albums_text(e)}
             for e in sorted(_unique_artists(idx), key=lambda e: -len(e["records"]))[:SEED_ARTISTS]]
    for a in (spotify_artists or [])[:5]:
        if norm_artist(a) not in idx:
            seeds.append({"name": a, "weight": 1, "owned": None})

    scores = {}
    for i, seed in enumerate(seeds, start=1):
        if on_progress:
            on_progress(i / (len(seeds) + CANDIDATES_CHECKED + n), f"Finding artists similar to {seed['name']}…")
        try:
            similar = _similar(seed["name"])
        except lastfm.LastfmNotFound:
            notes.append(f"Last.fm doesn't know {seed['name']}.")
            continue
        except (lastfm.LastfmError, MissingKeyError) as exc:
            if _fatal(exc):
                raise DiscoverError(str(exc)) from exc
            notes.append(f"Last.fm ({seed['name']}): {exc}")
            continue
        for s in similar:
            key = norm_artist(s["name"])
            if not key or key in idx or key in hidden:
                continue
            c = scores.setdefault(key, {"artist": s["name"], "score": 0.0, "because": []})
            c["score"] += s["match"] * seed["weight"]
            c["because"].append({"name": seed["name"], "owned": seed["owned"], "match": s["match"]})

    candidates = sorted(scores.values(), key=lambda c: -c["score"])[:CANDIDATES_CHECKED]
    wished = {(norm_artist(w["artist"]), norm_album(w["album"])) for w in wishes}
    total_styles = sum(profile["styles"].values()) or 1
    for j, c in enumerate(candidates, start=1):
        if on_progress:
            on_progress((len(seeds) + j) / (len(seeds) + CANDIDATES_CHECKED + n), f"Looking up {c['artist']}…")
        c["because"].sort(key=lambda b: (-(b["owned"] is not None), -b["match"]))
        try:
            album = _top_album(c["artist"])
        except (lastfm.LastfmError, MissingKeyError) as exc:
            if _fatal(exc):
                raise DiscoverError(str(exc)) from exc
            album = None
        c.update(album=album["name"] if album else None, image=album["image"] if album else "",
                 master_id=None, year=None, genres=[], styles=[], discogs_url=None)
        if album:
            try:
                info = _discogs_album(c["artist"], album["name"])
            except (discogs.DiscogsError, MissingKeyError) as exc:
                if _fatal(exc):
                    raise DiscoverError(str(exc)) from exc
                info = None
            if info:
                c.update(master_id=info["master_id"], year=info["year"], genres=info["genres"] or [],
                         styles=info["styles"] or [], discogs_url=info["url"], image=info["thumb"] or c["image"])
        style_share = sum(profile["styles"].get(s, 0) for s in c["styles"]) / total_styles
        genre_hit = any(profile["genres"].get(g) for g in c["genres"])
        c["final"] = c["score"] * (1 + style_share + (0.25 if genre_hit else 0)) * (1 if c["album"] else 0.3)
        c["on_wishlist"] = (norm_artist(c["artist"]), norm_album(c["album"] or "")) in wished

    ranked = sorted(candidates, key=lambda c: -c["final"])
    top = ranked[:n]
    for c in top:
        c["reason"], c["picked_by"] = rule_reason(c, profile), "rules"
    picked_by = "rules"
    if picker:
        # Claude chooses among the candidates the rules found (albums only) and writes the reasons.
        pool = [c for c in ranked if c["album"]]
        if on_progress:
            on_progress((len(seeds) + CANDIDATES_CHECKED) / (len(seeds) + CANDIDATES_CHECKED + n),
                        "Claude is choosing the best five…")
        picks, problem = picker(*claude_brief(records, profile, pool), n)
        if picks:
            top = []
            for pick in picks:
                c = pool[pick["index"]]
                c["reason"], c["picked_by"] = pick["reason"], "Claude"
                top.append(c)
            picked_by = "Claude"
        elif problem:
            notes.append(f"{problem} The standard picks and reasons are shown instead.")
    for k, c in enumerate(top, start=1):
        if on_progress:
            on_progress((len(seeds) + CANDIDATES_CHECKED + k) / (len(seeds) + CANDIDATES_CHECKED + n),
                        f"Checking prices for {c['artist']}…")
        c["prices"] = cached_price_check(c["artist"], c["album"], c["master_id"]) if c["album"] else None
    return {"items": top, "notes": notes, "seeds": [s["name"] for s in seeds], "picked_by": picked_by}


# ---------- Adding to the wishlist from Discover ----------

def add_to_wishlist(artist: str, album: str, min_condition: str = "Very Good Plus (VG+)", master_id=None,
                    note: str = "") -> dict:
    """Adds an album (any vinyl pressing) to the wishlist, linked to Discogs when we can find it."""
    for w in storage.list_wishes():
        if norm_artist(w["artist"]) == norm_artist(artist) and album_matches(w["album"], album):
            return {"id": w["id"], "already": True, "linked": bool(w["master_id"])}
    info = None
    try:
        info = _discogs_album(artist, album)
    except (discogs.DiscogsError, MissingKeyError):
        pass
    if info and master_id is None:
        master_id = info["master_id"]
    discogs_info = None
    if info:
        discogs_info = {"artist": artist, "album": info["album"], "year": info["year"], "thumb": info["thumb"],
                        "cover": info["thumb"], "url": info["url"], "genres": info["genres"], "styles": info["styles"]}
    wid = storage.add_wish(artist, album, min_condition, None, "", master_id=master_id, discogs=discogs_info,
                           notes=note)
    return {"id": wid, "already": False, "linked": bool(master_id)}


# ---------- Spotify vs your shelf ----------

def spotify_lists(records: list, top: dict) -> dict:
    """Three lists: streamed & owned, streamed but not owned, owned but not in your Spotify top lists."""
    from tools.spotify import TIME_RANGES

    idx = owned_index(records)
    streamed = {}
    for rng, artists in top["artists"].items():
        for rank, a in enumerate(artists, start=1):
            e = streamed.setdefault(norm_artist(a["name"]), {"name": a["name"], "ranges": [], "best_rank": rank,
                                                             "image": a["image"], "url": a["url"], "tracks": 0,
                                                             "albums": Counter()})
            e["ranges"].append(TIME_RANGES[rng])
            e["best_rank"] = min(e["best_rank"], rank)
    for rng, tracks in top["tracks"].items():
        for t in tracks:
            for name in t["artists"][:1]:
                e = streamed.setdefault(norm_artist(name), {"name": name, "ranges": [], "best_rank": 999,
                                                            "image": t["image"], "url": "", "tracks": 0,
                                                            "albums": Counter()})
                e["tracks"] += 1
                if t["album_type"] == "album":
                    e["albums"][t["album"]] += 1
    both, streamed_only = [], []
    for key, e in streamed.items():
        e["ranges"] = sorted(set(e["ranges"]), key=list(TIME_RANGES.values()).index)
        e["top_album"] = e["albums"].most_common(1)[0][0] if e["albums"] else None
        e["albums"] = dict(e["albums"])
        if key in idx:
            e["owned"] = [r["album"] for r in idx[key]["records"]]
            e["owned_top_album"] = bool(e["top_album"]) and any(album_matches(e["top_album"], o) for o in e["owned"])
            both.append(e)
        else:
            streamed_only.append(e)
    owned_only = []
    for entry in _unique_artists(idx):
        spellings = [k for k, v in idx.items() if v is entry]
        if not any(k in streamed for k in spellings):
            owned_only.append({"name": entry["name"], "owned": [r["album"] for r in entry["records"]]})
    rank = lambda e: (e["best_rank"], -e["tracks"])  # noqa: E731
    return {"both": sorted(both, key=rank), "streamed_only": sorted(streamed_only, key=rank),
            "owned_only": sorted(owned_only, key=lambda e: e["name"].lower())}


# ---------- Missing Pieces ----------

def _mb_albums(name):
    def fetch():
        artist = musicbrainz.find_artist(name)
        if not artist:
            return None
        return {"artist": artist, "albums": musicbrainz.studio_albums(artist["id"])}
    return storage.cached(f"mb:discography:{norm_artist(name)}", 14 * DAY, fetch)


def missing_pieces(records: list, wishes: list, on_progress=None) -> dict:
    """Studio albums you don't own yet, for every artist you own 2+ records by."""
    idx = owned_index(records)
    artists = [e for e in _unique_artists(idx) if len(e["records"]) >= 2]
    artists.sort(key=lambda e: (-len(e["records"]), e["name"].lower()))
    owned_marked = storage.hidden_suggestions("owned_album")
    out, notes = [], []
    for i, entry in enumerate(artists, start=1):
        if on_progress:
            on_progress(i / len(artists), f"Looking up {entry['name']} on MusicBrainz…")
        try:
            found = _mb_albums(entry["name"])
        except musicbrainz.MusicBrainzError as exc:
            if exc.fatal:
                raise DiscoverError(str(exc)) from exc
            notes.append(f"{entry['name']}: {exc}")
            continue
        if not found:
            notes.append(f"MusicBrainz has no exact match for “{entry['name']}”, so it was skipped.")
            continue
        owned_titles = [t for r in entry["records"] for t in (r["album"], r["discogs_album"]) if t]
        wish_titles = [w["album"] for w in wishes if norm_artist(w["artist"]) == norm_artist(entry["name"])]
        albums = []
        for a in found["albums"]:
            key = f"{norm_artist(entry['name'])}|{norm_album(a['title'])}"
            if key in owned_marked or any(album_matches(a["title"], t) for t in owned_titles):
                status = "owned"
            elif any(album_matches(a["title"], t) for t in wish_titles):
                status = "wishlist"
            else:
                status = "missing"
            albums.append(dict(a, status=status, key=key))
        out.append({"artist": entry["name"], "owned_count": len(entry["records"]), "mb_url":
                    f"https://musicbrainz.org/artist/{found['artist']['id']}", "albums": albums})
    return {"artists": out, "notes": notes, "eligible": len(artists)}
