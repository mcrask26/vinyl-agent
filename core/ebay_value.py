"""Values a record from current eBay listings, for when Discogs's sale-based prices are locked.

For each record: search eBay for the artist, album and catalog number (the same pressing);
if that finds too few, search the album in general (any pressing). Read each listing's grade
from its title, then (for up to DETAIL_LIMIT listings whose title has no grade) from the seller's
grading fields. Skips auctions (early bids say little), bootlegs and "damaged" listings graded
high. About 2-8 eBay calls per record; eBay allows 5,000 a day.
"""

import re

from core import deals, grading, valuation
from tools import ebay

DETAIL_LIMIT = 6
MIN_SAME_PRESSING = 5
REISSUE_WORDS = re.compile(r"\b(reissue|re-issue|repress|re-press|180\s?g(ram)?|remaster(ed)?|anniversary|"
                           r"limited edition|colou?red vinyl|sealed)\b", re.I)


GENERIC_ARTISTS = {"various", "various artists", "original broadway cast", "original cast", "soundtrack"}
MODERN_FROM = 1995  # pressings from here on are usually sold new, so "sealed" and "180g" listings count


def _search_artist(artist: str) -> str:
    """Sellers don't write "Various Artists" in titles, so search those by album name alone."""
    return "" if (artist or "").strip().lower() in GENERIC_ARTISTS else artist


def _short_album(album: str) -> str:
    """"A Foot in the Door: The Best of Pink Floyd" -> "A Foot in the Door"; "Woodstock (Soundtrack)" -> "Woodstock"."""
    return re.split(r"[:(]", album or "")[0].strip()


def is_original(release: dict) -> bool:
    """An older first pressing, where reissue listings must not count toward its value."""
    formats, year = release.get("formats") or [], release.get("year")
    return not {"Reissue", "Repress"} & set(formats) and (not year or year < MODERN_FROM)


def is_modern(release: dict) -> bool:
    return bool(release.get("year")) and release["year"] >= MODERN_FROM


def _first_catno(catno: str) -> str:
    return (catno or "").split(",")[0].strip()


def _graded(listing: dict) -> dict:
    cond = grading.parse_listing_condition(listing.get("title", ""), listing.get("description", ""),
                                           listing.get("aspects"), listing.get("ebay_condition", ""))
    return dict(listing, grade=cond["media"])


def _usable(l: dict, original: bool) -> bool:
    if l.get("auction") or l.get("price") is None or deals.NOT_OFFICIAL.search(l.get("title", "")):
        return False
    if original and REISSUE_WORDS.search(l.get("title", "")):
        return False  # valuing an original pressing: newer reissues (same catalog number) don't count
    if l.get("grade") and grading.at_least(l["grade"], "Very Good Plus (VG+)") and \
            grading.damage_mentions(f"{l.get('title', '')} {l.get('description', '')}"):
        return False  # graded high but describes damage: don't trust its grade
    return True


def gather(artist: str, album: str, catno: str, original: bool = True):
    """(listings [{price, grade, title, url}], basis text). Raises ebay errors / MissingKeyError."""
    cat = _first_catno(catno)
    artist = _search_artist(artist)
    found, basis = [], "any pressing of the album"
    if cat:
        found = ebay.search_listings(artist, album, extra=cat, limit=50)
        found = [l for l in found if re.sub(r"[^a-z0-9]", "", cat.lower()) in re.sub(r"[^a-z0-9]", "", l["title"].lower())]
        basis = f"same catalog number ({cat})"
    if len(found) < MIN_SAME_PRESSING:
        found = ebay.search_listings(artist, album, limit=50)
        basis = "any pressing of the album"
        short = _short_album(album)
        if len(found) < MIN_SAME_PRESSING and short and short != album:
            found = ebay.search_listings(artist, short, limit=50)  # long titles: sellers often shorten them
    found = [l for l in (_graded(l) for l in found) if not l.get("auction")]
    detailed, budget = [], DETAIL_LIMIT
    for l in found:
        if not l["grade"] and budget > 0:
            budget -= 1
            try:
                l = _graded(ebay.add_details(l))
            except ebay.EbayError as exc:
                if exc.fatal:
                    raise
        detailed.append(l)
    usable = [l for l in detailed if _usable(l, original)]
    return [{"price": l["price"], "grade": l["grade"], "title": l["title"][:90], "url": l["url"]} for l in usable], basis


def value_record(record: dict, currency: str = "USD") -> dict:
    """A value dict (see core/valuation.py) for one record, from eBay."""
    rel = record.get("release") or {}
    original = is_original(rel)
    listings, basis = gather(record["artist"], record["album"], rel.get("catno", ""), original)
    value = valuation.estimate_from_listings(listings, record.get("media_condition"), currency, basis,
                                             modern=is_modern(rel))
    value["basis"] = basis
    return value
