import pytest

from core.grading import damage_mentions, parse_listing_condition, short


@pytest.mark.parametrize("title, description, media, sleeve", [
    ("Led Zeppelin IV LP 1971 Atlantic SD 7208 VG+/VG", "", "VG+", "VG"),
    ("LED ZEPPELIN IV vinyl NM-", "", "NM", None),
    ("Led Zeppelin IV vinyl record", "Vinyl: VG++ plays great. Cover: G+ ring wear", "VG+", "G+"),
    ("Led Zeppelin IV SEALED", "", "M", None),
    ("Led Zeppelin IV EX/EX", "", "VG+", "VG+"),
    ("Led Zeppelin IV - Very Good Plus", "", "VG+", None),
    ("Led Zeppelin IV", "Record is in good condition, some pops", None, None),  # casual "good" isn't a grade
    ("Led Zeppelin IV", "Vinyl: Good. Cover: Fair", "G", "F"),
    ("Led Zeppelin IV Atlantic 1971 original", "", None, None),
])
def test_reads_messy_grades(title, description, media, sleeve):
    r = parse_listing_condition(title, description)
    assert (short(r["media"]) if r["media"] else None) == media
    assert (short(r["sleeve"]) if r["sleeve"] else None) == sleeve


def test_marketplace_grading_fields_win():
    r = parse_listing_condition("Zep IV G/G", "", {"Record Grading": "Near Mint (NM or M-)",
                                                    "Sleeve Grading": "Very Good Plus (VG+)"})
    assert short(r["media"]) == "NM" and short(r["sleeve"]) == "VG+"


def test_new_listing_counts_as_mint():
    assert short(parse_listing_condition("Zep IV", "", platform_condition="Brand New")["media"]) == "M"


def test_damage_words():
    assert damage_mentions("Light wear. Skips on side B, slightly warped") == ["skips", "warped"]
    assert damage_mentions("plays perfectly") == []
