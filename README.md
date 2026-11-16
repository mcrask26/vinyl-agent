# Vinyl Agent

Value your vinyl collection, find good deals on records you want, and discover new records
from your collection and Spotify listening. Python + Streamlit. Everything uses free APIs.

**Build status:** All 4 steps done: **My Collection**, **Wishlist & Deals**, **Alerts** and
**Discover** (recommendations, Spotify vs your shelf, Missing Pieces).

## Setup

```bash
cd "AI AGENT 2"
python3 -m venv .venv            # already done if .venv exists
.venv/bin/pip install -r requirements.txt
cp .env.example .env             # then paste your keys into .env
.venv/bin/streamlit run app.py
```

### Keys (all free)

| Key | Where to get it | Needed for |
|---|---|---|
| `DISCOGS_TOKEN` | discogs.com → Settings → Developers → **Generate new token** | Everything Discogs |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | developer.ebay.com → Application Keys → **Production** keyset (App ID, Cert ID) | eBay listings in Wishlist & Deals (optional) |
| `EBAY_ZIP` (optional) | your ZIP code | lets eBay include shipping costs to you |
| `LASTFM_API_KEY` | last.fm/api/account/create → copy the **API key** | Discover recommendations (similar artists) |
| `SPOTIFY_CLIENT_ID` | developer.spotify.com/dashboard → Create app, Redirect URI `http://127.0.0.1:8501/discover`, tick **Web API** | Spotify vs your shelf (optional) |
| `ANTHROPIC_API_KEY` (optional, **paid**) | console.anthropic.com | Claude reads listing descriptions and picks recommendations (see “What Claude does”). Off unless set; free built-in rules otherwise |

MusicBrainz (Missing Pieces) needs no key.

Discogs only returns **price suggestions** (what the values are based on) to accounts whose
seller settings are filled in. It's free and you don't have to sell anything:
<https://www.discogs.com/settings/seller>. Pick your currency there; it should match
`DISCOGS_CURRENCY` (default USD).

Keys go in `.env` or `.streamlit/secrets.toml` (both git-ignored). Never in code.

## How it works

```
app.py                 navigation (pages listed here)
views/collection.py    My Collection page (interface only)
core/collection.py     add / match / value / refresh workflow (reused by scripts later)
core/valuation.py      low / typical / high estimate + totals (pure logic, tested)
core/ebay_value.py     value estimates from eBay listings (when Discogs prices are locked)
scripts/import_list.py bulk-add records from a CSV list (artist, album)
core/grading.py        Goldmine grading scale used by Discogs
core/storage.py        SQLite database at data/vinyl_agent.db
core/config.py         reads keys from .env or Streamlit secrets
views/wishlist.py      Wishlist & Deals page
core/wishlist.py       searches eBay + Discogs for a wishlist record, rates and saves results
core/deals.py          Great Deal / Fair / Overpriced + suspicious-listing flags (pure logic, tested)
views/alerts.py        Alerts page (Check now, Buy now links, daily-check setup)
core/alerts.py         checks the whole wishlist and saves new Great Deals as alerts
scripts/check_deals.py the daily check (run by hand or on a schedule)
scripts/schedule_daily_check.sh  turns the daily check on/off on macOS (launchd)
views/discover.py      Discover page (recommendations, Spotify, Missing Pieces)
core/discover.py       taste profile, recommendation scoring, Spotify lists, missing albums
core/names.py          matching artist/album names written different ways
tools/lastfm.py        Last.fm client (similar artists, top albums; 4 calls/sec)
tools/musicbrainz.py   MusicBrainz client (studio albums; 1 call/sec)
tools/spotify.py       Spotify login (PKCE, read-only "top items") and top artists/tracks
tools/claude.py        optional Claude: reads listings, picks recommendations (falls back to rules)
tools/discogs.py       Discogs API client (rate-limited)
tools/ebay.py          eBay Browse API client (read-only, rate-limited)
tools/ratelimit.py     shared rate limiter
tests/                 pytest tests (no network, throwaway database)
```

**Value estimate.** For each record, *typical* is Discogs's suggested price for that exact
pressing in your record's condition (based on its sales history). *Low* and *high* are the
suggestions one grade worse and one grade better, because grading is subjective. If Discogs has no
data, the value shows as unknown and isn't counted in the total. Nothing is guessed.

**Values without PayPal (eBay estimates).** If Discogs's price suggestions are locked and eBay keys
are set, "Refresh values" estimates each record from current eBay fixed-price listings: first copies
with the same catalog number, else any pressing of the album. Each listing's grade is read from its
title or the seller's grading fields; auctions, bootlegs, high grades that describe damage and (for
original pressings) reissues are left out. The value is the middle of what copies in your record's
condition are listed for (one grade either side if fewer than 3; VG to NM if condition isn't set).
These are asking prices, not sales, and need 3+ comparable listings, otherwise the value is unknown.

**Deal rating.** Each listing's total (price + shipping) is compared with Discogs's sale-based price
for its grade when your Discogs account has price data; otherwise with the middle half of what the
*other* matching listings ask (needs 5+). Under 85% of typical and at the low end = Great Deal; over
115% and above the high end = Overpriced. Listings get warnings for prices far below normal, seller
feedback under 97% or fewer than 10 ratings, a high grade with damage words in the description, or
"bootleg" in the title. Grades are read from the seller's eBay grading fields, then from text like
"VG+/VG", "Vinyl: NM-", "EX", "sealed". Discogs's API only gives its cheapest copy and how many are
for sale (not each listing), so Discogs shows as one row. The app never buys anything.

**Alerts.** "Check now" (or the daily script) searches every wishlist record and saves each new
Great Deal that fits your max price and minimum condition and has no warnings. Auctions only count
in their last 24 hours. Each listing alerts you once; a dismissed one never comes back. **Buy now**
only opens the listing; the app never buys anything.

```bash
bash scripts/schedule_daily_check.sh on 9:00   # daily at 9:00, macOS notification on new deals
bash scripts/schedule_daily_check.sh off
.venv/bin/python scripts/check_deals.py         # one check by hand
```

**Recommendations.** Your 8 most-owned artists (plus your top Spotify artists, if connected) are
looked up on Last.fm for similar artists. Each similar artist scores similarity × how many records
you own by the artist it's similar to; albums whose Discogs styles match your collection get a
bonus. The top 5 each get their most-played studio album, a reason naming the records you own that
led to it, the cheapest Discogs copy, and the best eBay listing with its deal rating.

**Spotify vs your shelf.** Compares your top 50 artists and tracks (last 4 weeks, 6 months, year)
with your collection. Spotify only shares top lists, so "owned, not streamed" means "not in your
top lists", not "never played".

**Missing Pieces.** For each artist you own 2+ records by, MusicBrainz lists their studio albums
(no live albums, compilations or bootlegs); the ones you don't own can be added to the wishlist in
one click. Last.fm and MusicBrainz answers are saved in the database for 1–2 weeks to save calls.

**Rate limits.** Discogs allows 60 calls/minute. The app stays at 55, watches Discogs's
remaining-calls header, and waits and retries on "too many requests". Refreshing values costs 2
calls per record (about 2 seconds each).

**Your data** lives in `data/vinyl_agent.db` (SQLite, git-ignored): your records, a value
snapshot per refresh (for the chart), and a price history per pressing.

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Hosted demo (Streamlit Community Cloud)

The app can run as a free hosted demo so someone else can click through it.

- A fresh copy starts from `sample_data/collection_seed.db`: the collection, values, wishlist and
  alerts, with no login data. Changes made in the demo are reset whenever the host restarts it.
- Keys are never in the code or the repository. On Streamlit Community Cloud, paste them into the
  app's **Secrets** box in the same `NAME = "value"` form as `.streamlit/secrets.toml.example`.
- Without keys the demo still shows the saved collection and values; searching and refreshing
  show a clear "key isn't set" message.
- Not available in the hosted demo: the daily 9:00 check (it uses this Mac's scheduler) and
  Spotify login (its return address points at this computer).

## What Claude does (optional, paid)

With `ANTHROPIC_API_KEY` set, Claude takes over two jobs. Without it, or whenever Claude has a
problem (no credit, offline, an odd answer), the built-in rules do the same jobs for free and the
page says so.

| Job | Where | Model | Without Claude |
|---|---|---|---|
| Reads each eBay listing's title and description, gives the record and sleeve grade with the words it's based on, and flags contradictions (“graded VG+ but mentions skips”) or wrong items | Wishlist & Deals, Check now | Claude Haiku 5.5 | Pattern matching on grade words |
| Chooses the five recommendations from the candidates the app found and writes each reason, tied to records you own | Discover → Recommended | Claude Sonnet 5.5 | A score from similarity and shared styles, with a template sentence |

Limits kept on purpose:
- Claude never sets prices or the Great Deal / Fair / Overpriced rating; those stay arithmetic on real listings.
- A seller's own grading field always beats Claude's reading.
- Claude's answers come back in a fixed JSON shape and are checked before use.
- Each listing is read once and the reading is saved for 30 days.
- The unattended daily check doesn't use Claude unless `CLAUDE_DAILY_CHECK=1`.
- Both pages show how many requests were made and roughly what they cost.
