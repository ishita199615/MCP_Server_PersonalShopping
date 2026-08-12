"""Offline parser tests against a captured Target search payload.

These are the early warning system: when Target changes their JSON, these fail
loudly instead of the server quietly returning nulls. The fixture is a real
`pages/slp` response for "whole milk", trimmed to the products array but
keeping the envelope shape so extraction is exercised on the true structure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from target_mcp import target

FIXTURE = Path(__file__).parent / "fixtures" / "target_slp_whole_milk.json"


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def raw_products(payload) -> list[dict]:
    return payload["data_source_modules"][0]["module_data"]["search_response"]["products"]


# --- extraction ------------------------------------------------------------


def test_fixture_has_products(raw_products):
    assert len(raw_products) == 30


def test_extract_finds_products(payload):
    assert len(target.extract_products(payload, 40)) > 0


def test_extract_respects_limit(payload):
    assert len(target.extract_products(payload, 5)) == 5


def test_extract_drops_sponsored(payload, raw_products):
    sponsored = {str(p["tcin"]) for p in raw_products if p.get("is_sponsored_sku")}
    assert sponsored, "fixture should contain sponsored results to exercise this"
    got = {p["item_id"] for p in target.extract_products(payload, 40)}
    assert not (got & sponsored)


def test_extract_dedups(payload):
    ids = [p["item_id"] for p in target.extract_products(payload, 40)]
    assert len(ids) == len(set(ids))


def test_extract_survives_path_change(raw_products):
    """The products are found by walking, not by a hardcoded path -- so a
    reshuffled envelope must still parse. This is the failure mode that
    silently returns zero results."""
    moved = {"something": {"unexpected": [{"deeply": {"nested": raw_products}}]}}
    assert len(target.extract_products(moved, 40)) > 0


def test_sponsored_copy_elsewhere_still_excluded():
    """Regression: a sponsored product also appears in the payload as an
    unflagged copy. Filtering node-by-node let the ad back in through the side
    door -- 91536134 leaked exactly this way."""
    item = {"product_description": {"title": "Sponsored thing"}}
    payload = {
        "ads": [{"tcin": "999", "item": item, "is_sponsored_sku": True}],
        "carousel": [{"tcin": "999", "item": item}],  # same product, unflagged
    }
    assert target.extract_products(payload, 10) == []


def test_extract_ignores_non_products():
    assert target.extract_products({"a": [{"tcin": "1"}, {"item": {}}]}, 10) == []


# --- normalization ---------------------------------------------------------


@pytest.fixture(scope="module")
def products(payload) -> list[dict]:
    return target.extract_products(payload, 40)


def test_every_product_has_id_and_name(products):
    for p in products:
        assert p["item_id"] and p["item_id"].isdigit()
        assert p["name"]


def test_html_entities_are_unescaped(products):
    """Target ships "Good &#38; Gather&#8482;" inside JSON strings."""
    names = " ".join(p["name"] for p in products)
    assert "&#" not in names and "&amp;" not in names
    assert any("Good & Gather" in p["name"] for p in products)


def test_brand_is_populated(products):
    """Unlike Walmart's payload, Target actually fills this in."""
    assert sum(1 for p in products if p["brand"]) > len(products) // 2


def test_prices_are_numeric_and_formatted(products):
    priced = [p for p in products if p["price"] is not None]
    assert priced
    for p in priced:
        assert isinstance(p["price"], (int, float))
        assert p["price_string"].startswith("$")


def test_unit_price_present_for_some(products):
    """The field that makes grocery comparison work."""
    assert any(p["unit_price"] for p in products)


def test_urls_are_absolute_product_links(products):
    for p in products:
        assert p["url"].startswith("https://www.target.com/")
        assert f"A-{p['item_id']}" in p["url"]


def test_rating_and_review_count(products):
    rated = [p for p in products if p["rating"] is not None]
    assert rated
    for p in rated:
        assert 0 <= p["rating"] <= 5
        assert isinstance(p["review_count"], int)


# --- fulfillment: the Target-specific trap ---------------------------------


def test_fulfillment_has_all_three_channels(products):
    for p in products:
        assert set(p["fulfillment"]) == {"pickup", "delivery", "shipping"}


def test_groceries_are_in_stock_despite_shipping_being_out(raw_products):
    """The bug this guards: milk does not ship, so shipping_options is
    OUT_OF_STOCK for most of the dairy aisle. Reading that field as `in_stock`
    marks buyable items unavailable."""
    milk = next(
        p for p in raw_products
        if p["tcin"] == "49174886"  # Organic Valley, pickup IN_STOCK, shipping OUT
    )
    norm = target.normalize_product(milk)
    assert norm["fulfillment"]["shipping"] is False
    assert norm["fulfillment"]["pickup"] is True
    assert norm["in_stock"] is True


def test_sold_out_forces_everything_false():
    norm = target.normalize_product(
        {
            "tcin": "1",
            "item": {"product_description": {"title": "x"}},
            "fulfillment": {
                "sold_out": True,
                "store_options": [{"order_pickup": {"availability_status": "IN_STOCK"}}],
            },
        }
    )
    assert norm["in_stock"] is False
    assert not any(norm["fulfillment"].values())


def test_unknown_fulfillment_is_none_not_false():
    """Absent data must not masquerade as 'out of stock'."""
    norm = target.normalize_product(
        {"tcin": "1", "item": {"product_description": {"title": "x"}}}
    )
    assert norm["in_stock"] is None


# --- helpers ---------------------------------------------------------------


def test_clean_handles_none():
    assert target._clean(None) is None
    assert target._clean("") is None


def test_first_walks_dotted_paths():
    d = {"a": {"b": {"c": 7}}}
    assert target._first(d, "a.b.c") == 7
    assert target._first(d, "x.y", "a.b.c") == 7
    assert target._first(d, "a.b.missing") is None
    assert target._first({"a": 1}, "a.b.c") is None  # non-dict mid-path


def test_price_regex():
    assert target.PRICE_RE.search("now $3.49 each").group(1) == "3.49"
    assert target.PRICE_RE.search("$1,299.00").group(1) == "1,299.00"
    assert target.PRICE_RE.search("no price here") is None


def test_sponsored_detection():
    assert target._is_sponsored({"is_sponsored_sku": True})
    assert target._is_sponsored({"sponsored_ad": {"x": 1}})
    assert not target._is_sponsored({"is_sponsored_sku": False})
