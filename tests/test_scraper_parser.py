"""Parser regression tests against saved eBay HTML fixtures.

If eBay changes its sold-search markup, these fail in CI instead of the
scraper silently returning zero results in production.
"""
from datetime import datetime
from pathlib import Path

import pytest

from app.scraper import _parse_page, parse_grade, is_junk

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parses_legacy_s_item_layout():
    results = _parse_page(_load("ebay_s_item.html"))
    # 3 real listings; "Shop on eBay" placeholder and the reprint lot are dropped.
    titles = [r.title for r in results]
    assert len(results) == 2, titles
    assert all("Shop on eBay" not in t for t in titles)
    assert all(not is_junk(t) for t in titles)


def test_parses_newer_s_card_layout():
    results = _parse_page(_load("ebay_s_card.html"))
    assert len(results) == 2
    first = results[0]
    assert first.sale_price == 430.00
    assert first.item_number == "444444444444"
    assert first.sale_date == datetime(2024, 11, 10)


def test_extracts_price_item_number_and_grade():
    results = {r.item_number: r for r in _parse_page(_load("ebay_s_item.html"))}
    mahomes = results["111111111111"]
    assert mahomes.sale_price == 1250.00
    assert mahomes.grade_company == "PSA"
    assert mahomes.grade == 10.0
    assert results["222222222222"].grade_company == "BGS"
    assert results["222222222222"].grade == 9.5


def test_junk_listings_filtered_out():
    item_numbers = {r.item_number for r in _parse_page(_load("ebay_s_item.html"))}
    assert "333333333333" not in item_numbers  # "Lot of 50 ... reprint"


@pytest.mark.parametrize(
    "title,company,grade",
    [
        ("2017 Prizm Mahomes PSA 10", "PSA", 10.0),
        ("Herbert RC BGS 9.5 mint", "BGS", 9.5),
        ("Barkley SGC 10", "SGC", 10.0),
        ("CGC 9 Justin Herbert", "CGC", 9.0),
        ("Mahomes PSA10 no space", "PSA", 10.0),  # eBay often omits the space
        ("Raw ungraded rookie", None, None),
    ],
)
def test_parse_grade(title, company, grade):
    assert parse_grade(title) == (company, grade)


@pytest.mark.parametrize(
    "title,junk",
    [
        ("Lot of 25 football cards", True),
        ("2017 Prizm Mahomes reprint", True),
        ("Custom Mahomes aceo art card", True),
        ("Mahomes 2017 box break", True),
        ("2017 Panini Prizm Patrick Mahomes RC PSA 10", False),
    ],
)
def test_is_junk(title, junk):
    assert is_junk(title) is junk
