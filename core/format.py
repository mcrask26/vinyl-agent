"""Small display helpers shared by the pages."""

from datetime import datetime, timezone

from core import grading

_SYMBOLS = {"USD": "$", "CAD": "CA$", "AUD": "A$", "EUR": "€", "GBP": "£", "JPY": "¥"}


def money(value, currency="USD") -> str:
    if value is None:
        return "Unknown"
    symbol = _SYMBOLS.get(currency or "USD")
    amount = f"{value:,.0f}" if value >= 1000 else f"{value:,.2f}"
    return f"{symbol}{amount}" if symbol else f"{amount} {currency}"


def money_range(low, high, currency="USD") -> str:
    if low is None or high is None:
        return "Unknown"
    if round(low, 2) == round(high, 2):
        return money(low, currency)
    return f"{money(low, currency)} – {money(high, currency)}"


def ago(iso: str) -> str:
    if not iso:
        return "never"
    then = datetime.fromisoformat(iso)
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    return f"{int(secs // 86400)} days ago"


def pressing(release: dict) -> str:
    """'Atlantic SD 7208 · US · LP, Album' — the details that identify one pressing."""
    if not release:
        return ""
    label = " ".join(x for x in [(release.get("labels") or [""])[0], release.get("catno") or ""] if x)
    formats = [f for f in release.get("formats") or [] if f not in ("Vinyl",)]
    return " · ".join(x for x in [label, release.get("country") or "", ", ".join(formats)] if x)


def condition(media, sleeve) -> str:
    m = grading.short(media) if media else "?"
    s = grading.short(sleeve) if sleeve else "?"
    return f"{m} / {s}"
