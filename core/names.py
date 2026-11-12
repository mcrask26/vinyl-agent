"""Comparing artist and album names written different ways ("The Beatles" vs "Beatles",
"Abbey Road (Remastered)" vs "Abbey Road")."""

import re
from difflib import SequenceMatcher

_DISAMBIG = re.compile(r"\s\(\d+\)$")             # Discogs "Nirvana (2)"
_EXTRAS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")  # "(Remastered)", "[Deluxe Edition]"
_SUFFIX = re.compile(r"\s+-\s+.*(remaster|edition|version|mix|mono|stereo|anniversary|deluxe).*$", re.I)


def norm_artist(name: str) -> str:
    n = _DISAMBIG.sub("", (name or "").strip()).lower().replace("&", " and ")
    n = re.sub(r"^the\s+", "", n)
    return re.sub(r"[^a-z0-9]", "", n)


def _unwrap(title: str) -> str:
    """MusicBrainz writes untitled albums in brackets: "[Led Zeppelin IV]" -> "Led Zeppelin IV"."""
    t = (title or "").strip()
    return t[1:-1].strip() if re.fullmatch(r"\[[^\]]+\]", t) else t


def norm_album(title: str) -> str:
    t = _SUFFIX.sub("", _EXTRAS.sub("", _unwrap(title))).lower().replace("&", " and ")
    t = re.sub(r"^the\s+", "", t)
    return re.sub(r"[^a-z0-9]", "", t)


_NUMBER = re.compile(r"\b(\d+|[ivx]+)\b")


def _numbers(title: str) -> list:
    """Numbers in a title ("Led Zeppelin III" -> ["iii"]): they must match exactly."""
    return _NUMBER.findall(_SUFFIX.sub("", _EXTRAS.sub("", _unwrap(title).lower())))


def album_matches(a: str, b: str) -> bool:
    x, y = norm_album(a), norm_album(b)
    if not x or not y:
        return False
    if x == y:
        return True
    if _numbers(a) != _numbers(b):
        return False
    return SequenceMatcher(None, x, y).ratio() >= 0.88
