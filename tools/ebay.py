"""eBay Browse API: current listings for records on your wishlist.

Docs: https://developer.ebay.com/api-docs/buy/browse/overview.html
Free. Needs a Production keyset (EBAY_CLIENT_ID + EBAY_CLIENT_SECRET in .env). We use an
"application token" (no eBay login needed) and only read public listings.
Rate limit: 5,000 Browse calls a day. We pace calls at 4 per second, and each wishlist search
costs 1 search call plus 1 detail call for each of the (up to 12) most promising listings.

We never buy anything: the app only links to listings. You click through and pay on eBay.
"""

import base64
import html
import re
import threading
import time
from urllib.parse import quote

import requests

from core.config import get_ebay_credentials, get_ebay_zip
from tools.ratelimit import RateLimiter

API = "https://api.ebay.com"
SCOPE = "https://api.ebay.com/oauth/api_scope"
MARKETPLACE = "EBAY_US"
RECORDS_CATEGORY = "176985"  # Music > Records (vinyl)
TIMEOUT_S = 20
RETRY_WAITS_S = (3, 10)

_limiter = RateLimiter(calls=4, period=1)
_token = {"value": None, "expires": 0.0}
_token_lock = threading.Lock()

# Titles with these words are not vinyl records (or not the record itself).
NOT_A_RECORD = re.compile(
    r"\b(cd|cds|cassette|8[\s-]?track|reel[\s-]?to[\s-]?reel|dvd|blu[\s-]?ray|poster|t[\s-]?shirt|shirt|"
    r"sticker|slipmat|patch|pin|button|book|sheet music|frame[d]?|empty sleeve|sleeve only|cover only|"
    r"jacket only|no record|no vinyl|lot of|record lot|bundle)\b", re.I)
STOPWORDS = {"the", "a", "an", "and", "of", "&", "lp", "vinyl", "record", "album"}


class EbayError(RuntimeError):
    """Something went wrong talking to eBay. The message is safe to show in the app."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


# ---------- Auth ----------

def _get_token() -> str:
    """Application token (client credentials). Cached until 2 minutes before it expires."""
    with _token_lock:
        if _token["value"] and time.time() < _token["expires"]:
            return _token["value"]
        client_id, secret = get_ebay_credentials()
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        try:
            resp = requests.post(
                f"{API}/identity/v1/oauth2/token",
                headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials", "scope": SCOPE},
                timeout=TIMEOUT_S,
            )
        except requests.RequestException as exc:
            raise EbayError(f"Couldn't reach eBay (are you offline, or is eBay down?). Details: {exc}", fatal=True) from exc
        if resp.status_code in (400, 401):
            raise EbayError("eBay rejected your keys. Check EBAY_CLIENT_ID and EBAY_CLIENT_SECRET in .env "
                            "(use the Production keyset, not Sandbox), then restart the app.", fatal=True)
        if not resp.ok:
            raise EbayError(f"eBay sign-in failed (HTTP {resp.status_code}). Try again later.", fatal=True)
        body = resp.json()
        _token["value"] = body["access_token"]
        _token["expires"] = time.time() + int(body.get("expires_in", 7200)) - 120
        return _token["value"]


def _get(path: str, params=None) -> dict:
    headers = {"Authorization": f"Bearer {_get_token()}", "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE}
    zip_code = get_ebay_zip()
    if zip_code:  # lets eBay calculate shipping to you
        headers["X-EBAY-C-ENDUSERCTX"] = "contextualLocation=" + quote(f"country=US,zip={zip_code}")
    resp = None
    for wait_s in (0,) + RETRY_WAITS_S:
        if wait_s:
            time.sleep(wait_s)
        _limiter.wait()
        try:
            resp = requests.get(API + path, params=params, headers=headers, timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            raise EbayError(f"Couldn't reach eBay (are you offline, or is eBay down?). Details: {exc}", fatal=True) from exc
        if resp.status_code != 429 and resp.status_code < 500:
            break
    if resp.status_code == 429:
        raise EbayError("eBay's daily limit for this app was reached (5,000 calls). Try again tomorrow.", fatal=True)
    if resp.status_code == 401:
        _token["value"] = None
        raise EbayError("eBay rejected the app's access token. Try again; if it keeps happening, check your keys.", fatal=True)
    if resp.status_code == 404:
        raise EbayError("That eBay listing no longer exists.")
    if resp.status_code >= 500:
        raise EbayError(f"eBay is having problems right now (HTTP {resp.status_code}). Try again later.", fatal=True)
    if resp.status_code >= 400:
        try:
            msg = (resp.json().get("errors") or [{}])[0].get("message", "")
        except ValueError:
            msg = resp.text[:200]
        raise EbayError(f"eBay said: {msg} (HTTP {resp.status_code})")
    try:
        return resp.json()
    except ValueError as exc:
        raise EbayError("eBay returned a response that wasn't JSON.") from exc


# ---------- Parsing ----------

def _money(obj):
    if not obj or obj.get("value") in (None, ""):
        return None
    return round(float(obj["value"]), 2)


def _words(text: str) -> list:
    return [w for w in re.findall(r"[a-z0-9]+", html.unescape(text or "").lower()) if w not in STOPWORDS]


def is_relevant(title: str, artist: str, album: str) -> bool:
    """Does this listing title look like the record we want (and not a CD, poster, etc.)?"""
    if NOT_A_RECORD.search(title or ""):
        return False
    have = set(_words(title))
    artist_words, album_words = _words(artist), _words(album)
    if artist_words and not set(artist_words) <= have:
        return False
    return set(album_words) <= have


def parse_summary(item: dict) -> dict:
    """One search result -> our listing dict (no seller username stored: it's eBay user data)."""
    shipping, shipping_note = None, None
    options = item.get("shippingOptions") or []
    if options:
        first = options[0]
        if first.get("shippingCostType") == "CALCULATED" and not first.get("shippingCost"):
            shipping_note = "Shipping calculated at checkout"
        else:
            shipping = _money(first.get("shippingCost"))
    else:
        shipping_note = "Shipping not listed"
    buying = item.get("buyingOptions") or []
    is_auction = "AUCTION" in buying and "FIXED_PRICE" not in buying
    price = _money(item.get("currentBidPrice")) if is_auction and item.get("currentBidPrice") else _money(item.get("price"))
    seller = item.get("seller") or {}
    fb_pct = seller.get("feedbackPercentage")
    return {
        "source": "eBay",
        "listing_id": item.get("itemId"),
        "title": html.unescape(item.get("title") or ""),
        "url": item.get("itemWebUrl") or "",
        "image": (item.get("image") or {}).get("imageUrl") or "",
        "price": price,
        "shipping": shipping,
        "shipping_note": shipping_note,
        "currency": (item.get("price") or {}).get("currency") or "USD",
        "auction": is_auction,
        "bids": item.get("bidCount"),
        "ends": item.get("itemEndDate"),
        "ebay_condition": item.get("condition") or "",
        "ebay_condition_id": item.get("conditionId") or "",
        "seller_feedback_pct": float(fb_pct) if fb_pct not in (None, "") else None,
        "seller_feedback_score": seller.get("feedbackScore"),
        "location": (item.get("itemLocation") or {}).get("country") or "",
        "description": item.get("shortDescription") or "",
        "aspects": {},
    }


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_details(item: dict) -> dict:
    """Extra fields from a single listing: seller's grading, condition notes, description text."""
    aspects = {a.get("name"): ", ".join(a.get("value", "").split("|")) if isinstance(a.get("value"), str) else a.get("value")
               for a in item.get("localizedAspects") or [] if a.get("name")}
    text = " ".join(x for x in [item.get("conditionDescription") or "", item.get("shortDescription") or "",
                                _strip_html(item.get("description") or "")] if x)
    return {"aspects": aspects, "description": text[:2500]}


# ---------- Calls ----------

def search_listings(artist: str, album: str, extra: str = "", limit: int = 50) -> list:
    """Active vinyl listings that ship to the US, filtered to ones that look like this record."""
    query = " ".join(x for x in [artist, album, extra, "vinyl"] if x)
    payload = _get("/buy/browse/v1/item_summary/search", {
        "q": query[:100],
        "category_ids": RECORDS_CATEGORY,
        "limit": min(limit, 200),
        "filter": "buyingOptions:{FIXED_PRICE|AUCTION},deliveryCountry:US,priceCurrency:USD",
    })
    listings = [parse_summary(i) for i in payload.get("itemSummaries") or []]
    return [l for l in listings if l["price"] is not None and is_relevant(l["title"], artist, album)]


def add_details(listing: dict) -> dict:
    """Fetches the listing page data (1 call) to read the seller's grading and description."""
    details = parse_details(_get(f"/buy/browse/v1/item/{quote(listing['listing_id'], safe='')}"))
    return dict(listing, **details)
