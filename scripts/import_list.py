"""Adds many records at once from a CSV list (columns: artist, album; optional: catno, check).

    .venv/bin/python scripts/import_list.py shelf_import.csv

For each record: adds it to My Collection, finds it on Discogs, and links it to the most-owned
US vinyl pressing of that album (or the most-owned overall if there's no US one). The pressing is a best guess (a photo or list can't tell an original
from a reissue), so each one gets the note "Pressing picked automatically: please check", which
you can fix with "Re-match" on My Collection. Condition starts as not set.

Safe to run again: records already in your collection (same artist and album) are skipped.
Respects Discogs's rate limit (about 5 calls per record, ~55 calls a minute).
"""

import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import collection, storage  # noqa: E402
from core.config import MissingKeyError  # noqa: E402
from core.names import album_matches, norm_artist  # noqa: E402
from tools import discogs  # noqa: E402

AUTO_NOTE = "Pressing picked automatically from your shelf video: please check it."
DISCOGS_ARTIST = {"various artists": "Various", "original broadway cast": "Various"}
PREFERRED_COUNTRY = "US"  # an inherited American collection: most copies were bought in the US


def pick(cands: list) -> dict:
    """Most-owned US pressing if there is one among the matches, else the most-owned overall."""
    return next((c for c in cands if c.get("country") == PREFERRED_COUNTRY), cands[0])


def already_owned(artist: str, album: str, records: list) -> bool:
    return any(norm_artist(r["artist"]) == norm_artist(artist) and album_matches(r["album"], album) for r in records)


def import_rows(rows: list, log=print) -> dict:
    added, skipped, unmatched, failed = 0, 0, [], []
    prices_locked = False
    records = storage.list_records()
    start = time.time()
    for i, row in enumerate(rows, start=1):
        artist, album = row["artist"].strip(), row["album"].strip()
        if not artist or not album:
            continue
        if already_owned(artist, album, records):
            skipped += 1
            continue
        rid = storage.add_record(artist, album, (row.get("catno") or "").strip(), notes=AUTO_NOTE)
        search_artist = DISCOGS_ARTIST.get(artist.lower(), artist)
        try:
            cands, _ = discogs.find_release_candidates(search_artist, album, row.get("catno") or "", limit=15)
            if not cands and search_artist != artist:
                cands, _ = discogs.find_release_candidates(artist, album, limit=15)
            if cands:
                best = pick(cands)
                rec = collection.confirm_match(rid, best["release_id"], skip_prices=prices_locked)
                prices_locked = prices_locked or rec.get("prices_locked", False)
                status = f"→ {best['album']} ({best['year'] or '?'}, {best['country'] or '?'}, {best['catno'] or '?'})"
            else:
                storage.update_record(rid, notes="Not found on Discogs: use Re-match to find it by hand.")
                unmatched.append(f"{artist} – {album}")
                status = "→ not found on Discogs"
        except (discogs.DiscogsError, MissingKeyError) as exc:
            if getattr(exc, "fatal", True):
                log(f"Stopped: {exc}\nRun the same command again later; it continues where it left off.")
                failed.append(f"{artist} – {album}: {exc}")
                break
            storage.update_record(rid, notes=f"Discogs problem while matching ({exc}): use Re-match.")
            failed.append(f"{artist} – {album}: {exc}")
            status = f"→ problem: {exc}"
        added += 1
        records.append({"artist": artist, "album": album})
        mins = (time.time() - start) / 60
        log(f"[{i}/{len(rows)}] {artist} – {album} {status}  ({mins:.1f} min)")
    return {"added": added, "skipped": skipped, "unmatched": unmatched, "failed": failed}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    with open(argv[0], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    result = import_rows(rows, log=lambda m: print(m, flush=True))
    print(f"\nDone. Added {result['added']}, skipped {result['skipped']} already in your collection.")
    if result["unmatched"]:
        print(f"Not found on Discogs ({len(result['unmatched'])}): " + "; ".join(result["unmatched"]))
    if result["failed"]:
        print(f"Problems ({len(result['failed'])}): " + "; ".join(result["failed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
