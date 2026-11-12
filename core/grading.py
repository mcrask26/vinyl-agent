"""The Goldmine grading scale that Discogs uses for record (media) and sleeve condition."""

# Best to worst. These are Discogs's exact names: the API's price suggestions use them as keys.
MEDIA_GRADES = [
    "Mint (M)",
    "Near Mint (NM or M-)",
    "Very Good Plus (VG+)",
    "Very Good (VG)",
    "Good Plus (G+)",
    "Good (G)",
    "Fair (F)",
    "Poor (P)",
]

# Sleeves can also be generic, ungraded, or missing.
SLEEVE_GRADES = MEDIA_GRADES + ["Generic", "Not Graded", "No Cover"]

GRADE_HELP = (
    "M: never played, perfect. NM: nearly perfect, no visible flaws. "
    "VG+: light signs of play, plays with no or few faint surface sounds. "
    "VG: noticeable surface noise and light scratches, but plays through. "
    "G+/G: plays with heavy noise and scratches. F/P: badly damaged. "
    "Full guide: https://www.discogs.com/selling/resources/how-to-grade-items"
)


def short(grade: str) -> str:
    """'Very Good Plus (VG+)' -> 'VG+'."""
    if grade and "(" in grade and grade.endswith(")"):
        return grade[grade.rindex("(") + 1:-1].replace("NM or M-", "NM")
    return grade or ""


def rank(grade: str) -> int:
    """0 = Mint (best). Raises ValueError for anything not on the media scale."""
    return MEDIA_GRADES.index(grade)


def neighbors(grade: str):
    """(next better grade or None, next worse grade or None)."""
    i = rank(grade)
    better = MEDIA_GRADES[i - 1] if i > 0 else None
    worse = MEDIA_GRADES[i + 1] if i + 1 < len(MEDIA_GRADES) else None
    return better, worse


def at_least(grade: str, minimum: str) -> bool:
    """Is `grade` as good as or better than `minimum`?"""
    return rank(grade) <= rank(minimum)


# ---------- Reading a grade out of messy listing text ----------
#
# Sellers write grades every which way: "VG+/VG", "NM-", "EX", "Near Mint", "Vinyl: VG++",
# "still sealed". These rules map them onto the standard scale. Free, no AI needed.

import re  # noqa: E402

_ALIASES = [  # (regex, grade). Longer / more specific spellings first.
    (r"still\s+sealed|factory\s+sealed|\bsealed\b", "Mint (M)"),
    (r"near[\s-]*mint|\bnm[-+]?(?![a-z])|\bm-(?![a-z])|\bex\+\+", "Near Mint (NM or M-)"),
    (r"\bmint\b(?!\s*-)|\bm\b(?=\s*/)", "Mint (M)"),
    (r"very\s+good\s*(?:plus|\+)|\bvg\s*\+\+?|\bvg\s*plus\b|\bex\+?(?![a-z])|\bexcellent\b", "Very Good Plus (VG+)"),
    (r"very\s+good\s*-|\bvg-", "Good Plus (G+)"),
    (r"very\s+good\b|\bvg\b", "Very Good (VG)"),
    (r"good\s*(?:plus|\+)|\bg\+", "Good Plus (G+)"),
    # A bare "good" is usually casual ("in good condition"), so it only counts when labeled
    # ("Vinyl: Good", "G/G"); to_grade() handles those.
    (r"\bg\b(?=\s*/)", "Good (G)"),
    (r"\bfair\b", "Fair (F)"),
    (r"\bpoor\b", "Poor (P)"),
]
_ALIASES = [(re.compile(p, re.I), g) for p, g in _ALIASES]

# One grade token, used in "VG+/VG" style pairs and "Vinyl: VG+" style labels.
_TOKEN = r"(?:near\s*mint|mint|m-|m|nm-?|ex\+{0,2}|vg\+{0,2}|vg-|very\s+good\s*(?:plus|\+)?|g\+|good\s*(?:plus|\+)?|g|f|p|fair|poor)"
_PAIR = re.compile(rf"(?<![a-z])({_TOKEN})\s*/\s*({_TOKEN})(?![a-z+])", re.I)
_MEDIA_LABEL = re.compile(rf"\b(?:vinyl|record|media|disc|lp|wax)\s*(?:grade|condition|is|:|-|=)*\s*[:\-=]?\s*({_TOKEN})(?![a-z])", re.I)
_SLEEVE_LABEL = re.compile(rf"\b(?:sleeve|cover|jacket)\s*(?:grade|condition|is|:|-|=)*\s*[:\-=]?\s*({_TOKEN})(?![a-z])", re.I)

# Words that contradict a high grade.
DAMAGE_WORDS = re.compile(r"\b(skips?|skipping|deep scratch(?:es)?|heavy scratch(?:es)?|warp(?:ed)?|crack(?:ed)?|"
                          r"chipped|water damage[d]?|heavy (?:surface )?noise|won'?t play|unplayable|gouge[sd]?)\b", re.I)


def to_grade(token: str):
    """Map one spelling ('VG++', 'Near Mint', 'EX') to a standard grade, or None."""
    t = (token or "").strip()
    if t.lower() in ("m", "mint"):
        return "Mint (M)"
    if t.lower() in ("g", "good"):
        return "Good (G)"
    if t.lower() == "f":
        return "Fair (F)"
    if t.lower() == "p":
        return "Poor (P)"
    for pattern, grade in _ALIASES:
        if pattern.search(t):
            return grade
    return None


def parse_listing_condition(title: str = "", description: str = "", aspects=None,
                            platform_condition: str = "") -> dict:
    """Best reading of a listing's record (media) and sleeve grades.

    Checks, in order: the marketplace's own grading fields (eBay "Record Grading"),
    "VG+/VG"-style pairs, "Vinyl: VG+"-style labels, then any grade word in the title.
    Returns {"media", "sleeve", "evidence"}; media is None if nothing could be read.
    """
    aspects = aspects or {}
    media = sleeve = None
    evidence = ""
    for key in ("Record Grading", "Vinyl Grading", "Record Condition"):
        if aspects.get(key):
            media, evidence = to_grade(aspects[key]), f"seller's {key}: {aspects[key]}"
            break
    for key in ("Sleeve Grading", "Sleeve Condition", "Cover Grading"):
        if aspects.get(key):
            sleeve = to_grade(aspects[key])
            break
    text = f"{title} \n {description}"
    if not media:
        m = _PAIR.search(text)
        if m and to_grade(m.group(1)) and to_grade(m.group(2)):
            media, sleeve = to_grade(m.group(1)), sleeve or to_grade(m.group(2))
            evidence = f"“{m.group(0).strip()}”"
    if not media:
        m = _MEDIA_LABEL.search(text)
        if m and to_grade(m.group(1)):
            media, evidence = to_grade(m.group(1)), f"“{m.group(0).strip()}”"
    if not sleeve:
        m = _SLEEVE_LABEL.search(text)
        if m:
            sleeve = to_grade(m.group(1))
    if not media:
        for source in (title, description):
            for pattern, grade in _ALIASES:
                m = pattern.search(source or "")
                if m:
                    media, evidence = grade, f"“{m.group(0).strip()}”"
                    break
            if media:
                break
    if not media and platform_condition.lower().startswith(("new", "brand new")):
        media, evidence = "Mint (M)", "listed as New"
    return {"media": media, "sleeve": sleeve, "evidence": evidence}


def damage_mentions(text: str) -> list:
    return sorted({m.group(0).lower() for m in DAMAGE_WORDS.finditer(text or "")})
