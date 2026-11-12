from tools import ebay

SUMMARY = {
    "itemId": "v1|123|0", "title": "Led Zeppelin IV LP 1971 Atlantic SD 7208 VG+/VG",
    "price": {"value": "35.00", "currency": "USD"},
    "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "5.50", "currency": "USD"}}],
    "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"], "condition": "Used", "conditionId": "3000",
    "seller": {"username": "someone", "feedbackPercentage": "99.6", "feedbackScore": 1520},
    "itemWebUrl": "https://www.ebay.com/itm/123", "image": {"imageUrl": "https://i.ebayimg.com/x.jpg"},
    "itemLocation": {"country": "US"},
}


def test_parse_summary():
    l = ebay.parse_summary(SUMMARY)
    assert l["price"] == 35.0 and l["shipping"] == 5.5 and not l["auction"]
    assert l["seller_feedback_pct"] == 99.6 and l["seller_feedback_score"] == 1520
    assert "someone" not in str(l)  # seller usernames are not stored


def test_calculated_shipping_and_auction():
    item = dict(SUMMARY, shippingOptions=[{"shippingCostType": "CALCULATED"}], buyingOptions=["AUCTION"],
                currentBidPrice={"value": "12.50", "currency": "USD"})
    l = ebay.parse_summary(item)
    assert l["shipping"] is None and l["shipping_note"] == "Shipping calculated at checkout"
    assert l["auction"] and l["price"] == 12.5


def test_relevance_filter():
    assert ebay.is_relevant("LED ZEPPELIN IV vinyl LP Atlantic", "Led Zeppelin", "Led Zeppelin IV")
    assert not ebay.is_relevant("Led Zeppelin IV CD remastered", "Led Zeppelin", "Led Zeppelin IV")
    assert not ebay.is_relevant("Led Zeppelin IV poster 24x36", "Led Zeppelin", "Led Zeppelin IV")
    assert not ebay.is_relevant("Led Zeppelin II LP", "Led Zeppelin", "Led Zeppelin IV")
    assert ebay.is_relevant("Fleetwood Mac - Rumours LP 1977", "Fleetwood Mac", "Rumours")


def test_parse_details():
    d = ebay.parse_details({"localizedAspects": [{"name": "Record Grading", "value": "Very Good Plus (VG+)"}],
                            "conditionDescription": "Light scuffs", "description": "<p>Plays <b>great</b></p>"})
    assert d["aspects"]["Record Grading"] == "Very Good Plus (VG+)"
    assert d["description"] == "Light scuffs Plays great"
