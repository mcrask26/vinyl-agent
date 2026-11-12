"""Saves your data in a local SQLite database (data/vinyl_agent.db) so it persists between runs.

SQLite is built into Python: no server, no install, one file. Safe to use from the app and
the daily alert script at the same time.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from core.config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS collection (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    artist           TEXT NOT NULL,          -- as you typed it
    album            TEXT NOT NULL,
    catno_entered    TEXT,
    media_condition  TEXT,
    sleeve_condition TEXT,
    notes            TEXT,
    release_id       INTEGER,                -- NULL = not matched to Discogs yet
    release_json     TEXT,                   -- pressing details from Discogs
    suggestions_json TEXT,                   -- Discogs suggested price per grade
    value_json       TEXT,                   -- latest estimate (see core/valuation.py)
    market_json      TEXT,                   -- lowest current listing + number for sale
    valued_at        TEXT,
    added_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS value_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at       TEXT NOT NULL,
    total_low      REAL,
    total_typical  REAL,
    total_high     REAL,
    currency       TEXT,
    valued_count   INTEGER,
    unvalued_count INTEGER
);
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id  INTEGER NOT NULL,
    grade       TEXT,
    typical     REAL,
    lowest_listing REAL,
    num_for_sale INTEGER,
    currency    TEXT,
    source      TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history_release ON price_history(release_id, recorded_at);
CREATE TABLE IF NOT EXISTS wishlist (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    artist         TEXT NOT NULL,             -- as you typed it (used for the eBay search)
    album          TEXT NOT NULL,
    catno          TEXT,                      -- set = you want one specific pressing
    master_id      INTEGER,                   -- Discogs album (any pressing)
    release_id     INTEGER,                   -- Discogs pressing (when you want a specific one)
    discogs_json   TEXT,                      -- cover, year, Discogs names
    max_price      REAL,
    min_condition  TEXT NOT NULL,
    notes          TEXT,
    added_at       TEXT NOT NULL,
    checked_at     TEXT,
    results_json   TEXT,                      -- latest rated listings
    errors_json    TEXT                       -- problems during the latest search
);
CREATE TABLE IF NOT EXISTS listing_prices (   -- every listing price we've seen, for price history
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    wishlist_id INTEGER NOT NULL,
    source      TEXT NOT NULL,
    listing_id  TEXT,
    total       REAL,
    grade       TEXT,
    rating      TEXT,
    seen_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listing_prices_wish ON listing_prices(wishlist_id, seen_at);
CREATE TABLE IF NOT EXISTS alerts (           -- Great Deals found by "Check now" or the daily script
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    wishlist_id   INTEGER NOT NULL,
    artist        TEXT NOT NULL,
    album         TEXT NOT NULL,
    source        TEXT NOT NULL,              -- eBay / Discogs
    listing_id    TEXT NOT NULL,
    title         TEXT,
    url           TEXT NOT NULL,              -- where "Buy now" goes (you pay there yourself)
    image         TEXT,
    total         REAL,
    currency      TEXT,
    grade         TEXT,
    sleeve_grade  TEXT,
    reason        TEXT,
    auction       INTEGER DEFAULT 0,
    ends          TEXT,
    status        TEXT NOT NULL DEFAULT 'new', -- new / seen / dismissed
    still_listed  INTEGER NOT NULL DEFAULT 1,  -- 0 = gone from the latest search (probably sold)
    found_at      TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    UNIQUE (wishlist_id, source, listing_id)
);
CREATE TABLE IF NOT EXISTS app_state (        -- small saved values: Spotify login, last Spotify data
    key        TEXT PRIMARY KEY,
    value_json TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_cache (        -- answers from Last.fm / MusicBrainz, to save calls
    key        TEXT PRIMARY KEY,
    value_json TEXT,
    fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS hidden_suggestions ( -- "Not interested" / "I own this" in Discover
    kind TEXT NOT NULL,
    key  TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS check_runs (       -- each "Check now" / daily-script run
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at      TEXT NOT NULL,
    trigger     TEXT NOT NULL,                -- button / daily script
    searched    INTEGER,
    new_alerts  INTEGER,
    errors_json TEXT
);
"""

_JSON_COLS = {"release_json": "release", "suggestions_json": "suggestions", "value_json": "value", "market_json": "market"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_record(row) -> dict:
    rec = dict(row)
    for col, key in _JSON_COLS.items():
        raw = rec.pop(col)
        rec[key] = json.loads(raw) if raw else None
    return rec


def _dump(value):
    return json.dumps(value) if value is not None else None


# ---------- Collection ----------

def add_record(artist, album, catno_entered="", media_condition=None, sleeve_condition=None, notes="") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO collection (artist, album, catno_entered, media_condition, sleeve_condition, notes, added_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artist.strip(), album.strip(), (catno_entered or "").strip(), media_condition, sleeve_condition,
             notes or "", now_iso()),
        )
        return cur.lastrowid


def get_record(record_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM collection WHERE id = ?", (record_id,)).fetchone()
    return _row_to_record(row) if row else None


def list_records() -> list:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM collection ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE").fetchall()
    return [_row_to_record(r) for r in rows]


def update_record(record_id: int, **fields) -> None:
    """Update any columns. Pass release=/suggestions=/value=/market= as Python objects."""
    cols, vals = [], []
    reverse = {v: k for k, v in _JSON_COLS.items()}
    for key, value in fields.items():
        if key in reverse:
            cols.append(reverse[key])
            vals.append(_dump(value))
        else:
            cols.append(key)
            vals.append(value)
    if not cols:
        return
    with connect() as conn:
        conn.execute(f"UPDATE collection SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?", vals + [record_id])


def delete_record(record_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM collection WHERE id = ?", (record_id,))


# ---------- Value snapshots ----------

def save_snapshot(summary: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO value_snapshots (taken_at, total_low, total_typical, total_high, currency, valued_count,"
            " unvalued_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), summary["total_low"], summary["total_typical"], summary["total_high"],
             summary["currency"], summary["valued"], summary["unvalued"]),
        )


def list_snapshots() -> list:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM value_snapshots ORDER BY taken_at").fetchall()]


# ---------- Price history ----------

def log_price(release_id, grade, typical, lowest_listing, num_for_sale, currency, source="discogs") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO price_history (release_id, grade, typical, lowest_listing, num_for_sale, currency, source,"
            " recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (release_id, grade, typical, lowest_listing, num_for_sale, currency, source, now_iso()),
        )


def price_history(release_id: int) -> list:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM price_history WHERE release_id = ? ORDER BY recorded_at", (release_id,))
        return [dict(r) for r in rows.fetchall()]


# ---------- Wishlist ----------

_WISH_JSON = {"discogs_json": "discogs", "results_json": "results", "errors_json": "errors"}


def _row_to_wish(row) -> dict:
    item = dict(row)
    for col, key in _WISH_JSON.items():
        raw = item.pop(col)
        item[key] = json.loads(raw) if raw else None
    return item


def add_wish(artist, album, min_condition, max_price=None, catno="", master_id=None, release_id=None,
             discogs=None, notes="") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO wishlist (artist, album, catno, master_id, release_id, discogs_json, max_price,"
            " min_condition, notes, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (artist.strip(), album.strip(), (catno or "").strip(), master_id, release_id, _dump(discogs),
             max_price, min_condition, notes or "", now_iso()),
        )
        return cur.lastrowid


def list_wishes() -> list:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM wishlist ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE").fetchall()
    return [_row_to_wish(r) for r in rows]


def get_wish(wish_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM wishlist WHERE id = ?", (wish_id,)).fetchone()
    return _row_to_wish(row) if row else None


def update_wish(wish_id: int, **fields) -> None:
    reverse = {v: k for k, v in _WISH_JSON.items()}
    cols, vals = [], []
    for key, value in fields.items():
        cols.append(reverse.get(key, key))
        vals.append(_dump(value) if key in reverse else value)
    if cols:
        with connect() as conn:
            conn.execute(f"UPDATE wishlist SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?", vals + [wish_id])


def delete_wish(wish_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM wishlist WHERE id = ?", (wish_id,))
        conn.execute("DELETE FROM listing_prices WHERE wishlist_id = ?", (wish_id,))
        conn.execute("DELETE FROM alerts WHERE wishlist_id = ?", (wish_id,))


def log_listings(wish_id: int, listings: list) -> None:
    seen = now_iso()
    with connect() as conn:
        conn.executemany(
            "INSERT INTO listing_prices (wishlist_id, source, listing_id, total, grade, rating, seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(wish_id, l["source"], l.get("listing_id"), l.get("total"), l.get("grade"), l.get("rating"), seen)
             for l in listings],
        )


def listing_price_history(wish_id: int) -> list:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM listing_prices WHERE wishlist_id = ? ORDER BY seen_at", (wish_id,))
        return [dict(r) for r in rows.fetchall()]


# ---------- Alerts ----------

def save_alerts(wish: dict, listings: list) -> list:
    """Saves alert-worthy listings for one wish. Returns only the ones that are new.

    A listing already alerted keeps its status (a dismissed deal never comes back) but gets its
    latest price. Earlier alerts for this wish that are no longer listed are marked as gone.
    """
    seen = now_iso()
    new, touched = [], []
    with connect() as conn:
        for l in listings:
            key = (wish["id"], l["source"], str(l.get("listing_id")))
            row = conn.execute("SELECT id FROM alerts WHERE wishlist_id = ? AND source = ? AND listing_id = ?",
                               key).fetchone()
            if row:
                conn.execute("UPDATE alerts SET total = ?, reason = ?, still_listed = 1, last_seen_at = ? WHERE id = ?",
                             (l.get("total"), l.get("reason"), seen, row["id"]))
                touched.append(row["id"])
                continue
            cur = conn.execute(
                "INSERT INTO alerts (wishlist_id, artist, album, source, listing_id, title, url, image, total,"
                " currency, grade, sleeve_grade, reason, auction, ends, found_at, last_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (wish["id"], wish["artist"], wish["album"], l["source"], key[2], l.get("title"), l["url"], l.get("image"), l.get("total"),
                           l.get("currency") or "USD", l.get("grade"), l.get("sleeve_grade"), l.get("reason"),
                           int(bool(l.get("auction"))), l.get("ends"), seen, seen),
            )
            new.append(cur.lastrowid)
        keep = touched + new
        not_kept = f" AND id NOT IN ({','.join('?' * len(keep))})" if keep else ""
        conn.execute("UPDATE alerts SET still_listed = 0 WHERE wishlist_id = ?" + not_kept, [wish["id"]] + keep)
        if not new:
            return []
        rows = conn.execute(f"SELECT * FROM alerts WHERE id IN ({','.join('?' * len(new))})", new).fetchall()
    return [dict(r) for r in rows]


def list_alerts(status=None) -> list:
    with connect() as conn:
        if status:
            rows = conn.execute("SELECT * FROM alerts WHERE status = ? ORDER BY found_at DESC", (status,))
        else:
            rows = conn.execute("SELECT * FROM alerts ORDER BY found_at DESC")
        return [dict(r) for r in rows.fetchall()]


def set_alert_status(alert_id: int, status: str) -> None:
    assert status in ("new", "seen", "dismissed")
    with connect() as conn:
        conn.execute("UPDATE alerts SET status = ? WHERE id = ?", (status, alert_id))


def count_new_alerts() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM alerts WHERE status = 'new' AND still_listed = 1").fetchone()[0]


def log_check_run(trigger: str, searched: int, new_alerts: int, errors: list) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO check_runs (ran_at, trigger, searched, new_alerts, errors_json) VALUES (?, ?, ?, ?, ?)",
                     (now_iso(), trigger, searched, new_alerts, _dump(errors)))


def last_check_run(trigger=None):
    with connect() as conn:
        if trigger:
            row = conn.execute("SELECT * FROM check_runs WHERE trigger = ? ORDER BY id DESC LIMIT 1", (trigger,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM check_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    run = dict(row)
    run["errors"] = json.loads(run.pop("errors_json") or "[]")
    return run


# ---------- Small saved values / cache / hidden suggestions ----------

def set_state(key: str, value) -> None:
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO app_state (key, value_json, updated_at) VALUES (?, ?, ?)",
                     (key, _dump(value), now_iso()))


def get_state(key: str, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value_json, updated_at FROM app_state WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value_json"]) if row and row["value_json"] else default


def state_updated_at(key: str):
    with connect() as conn:
        row = conn.execute("SELECT updated_at FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["updated_at"] if row else None


def delete_state(key: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM app_state WHERE key = ?", (key,))


def cached(key: str, max_age_s: float, fetch):
    """Returns the saved answer for `key` if younger than max_age_s, else calls fetch() and saves it."""
    import time

    with connect() as conn:
        row = conn.execute("SELECT value_json, fetched_at FROM api_cache WHERE key = ?", (key,)).fetchone()
    if row and time.time() - row["fetched_at"] < max_age_s:
        return json.loads(row["value_json"])
    value = fetch()
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO api_cache (key, value_json, fetched_at) VALUES (?, ?, ?)",
                     (key, json.dumps(value), time.time()))
    return value


def hide_suggestion(kind: str, key: str) -> None:
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO hidden_suggestions (kind, key) VALUES (?, ?)", (kind, key))


def hidden_suggestions(kind: str) -> set:
    with connect() as conn:
        return {r["key"] for r in conn.execute("SELECT key FROM hidden_suggestions WHERE kind = ?", (kind,))}
