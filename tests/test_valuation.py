from core import valuation
from tests.conftest import load_fixture
from tools import discogs

PRICES, _ = discogs.parse_price_suggestions(load_fixture("discogs_price_suggestions.json"))


def test_estimate_uses_neighbouring_grades():
    v = valuation.estimate(PRICES, "Very Good Plus (VG+)", "USD")
    assert v["status"] == valuation.OK
    assert (v["low"], v["typical"], v["high"]) == (18.0, 30.26, 45.5)


def test_estimate_at_ends_of_scale():
    mint = valuation.estimate(PRICES, "Mint (M)", "USD")
    assert (mint["low"], mint["typical"], mint["high"]) == (45.5, 60.0, 60.0)
    poor = valuation.estimate(PRICES, "Poor (P)", "USD")
    assert (poor["low"], poor["typical"], poor["high"]) == (1.5, 1.5, 3.0)


def test_unknown_values_are_not_guessed():
    for suggestions, cond in [({}, "Very Good (VG)"), (PRICES, None), ({"Mint (M)": 10}, "Very Good (VG)")]:
        v = valuation.estimate(suggestions, cond, "USD")
        assert v["status"] == valuation.UNKNOWN and v["typical"] is None and v["reason"]


def test_summary_counts_unvalued_records():
    ok = {"value": valuation.estimate(PRICES, "Very Good (VG)", "USD")}
    ok2 = {"value": valuation.estimate(PRICES, "Near Mint (NM or M-)", "USD")}
    unknown = {"value": valuation.estimate({}, "Very Good (VG)", "USD")}
    unmatched = {"value": None}
    s = valuation.summarize([ok, ok2, unknown, unmatched])
    assert s["valued"] == 2 and s["unvalued"] == 2 and s["count"] == 4
    assert s["total_typical"] == 63.5
    assert s["total_low"] == 40.26 and s["total_high"] == 90.26
    assert valuation.most_valuable([ok, ok2, unknown], 1) == [ok2]
