"""Tests for the title->card matching guard used on scrape import."""
from types import SimpleNamespace

import pytest

from app.routers.scrape import _title_matches_card


def _card(name="Patrick Mahomes", year=2017, brand="Prizm"):
    return SimpleNamespace(player=SimpleNamespace(name=name), year=year, brand=brand)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("2017 Panini Prizm Patrick Mahomes II RC PSA 10", True),
        ("2017 Prizm Mahomes rookie", True),                  # last name + year + brand
        ("2018 Prizm Patrick Mahomes", False),                # wrong year
        ("2017 Optic Patrick Mahomes", False),                # wrong brand
        ("2017 Prizm Josh Allen rookie", False),              # wrong player
    ],
)
def test_title_matches_card(title, expected):
    assert _title_matches_card(title, _card()) is expected


def test_brand_not_required_when_card_has_none():
    card = _card(brand=None)
    assert _title_matches_card("2017 Donruss Patrick Mahomes", card) is True
