"""Pricing: determinism, boundaries, and table-staleness signalling."""

from __future__ import annotations

import datetime

import pytest

from claudestudio import pricing


def test_normalize_strips_date_suffix():
    assert pricing.normalize("claude-opus-4-8-20260101") == "claude-opus-4-8"


def test_known_model_is_priced_and_positive():
    assert pricing.is_priced("claude-opus-4-8")
    c = pricing.cost_for_usage("claude-opus-4-8", input_tokens=1000, output_tokens=500)
    assert c > 0
    assert isinstance(c, float)


def test_cost_is_deterministic():
    a = pricing.cost_for_usage("claude-sonnet-4-6", 1234, 567, 89, 1011)
    b = pricing.cost_for_usage("claude-sonnet-4-6", 1234, 567, 89, 1011)
    assert a == b


def test_cache_read_cheaper_than_cache_write():
    write = pricing.cost_for_usage("claude-opus-4-8", 0, 0, 1_000_000, 0)
    read = pricing.cost_for_usage("claude-opus-4-8", 0, 0, 0, 1_000_000)
    assert read < write


def test_unknown_model_not_priced_and_zero_cost():
    assert not pricing.is_priced("claude-fictional-9999")
    assert pricing.cost_for_usage("claude-fictional-9999", 1_000_000, 1_000_000) == 0.0


def test_zero_tokens_zero_cost():
    assert pricing.cost_for_usage("claude-opus-4-8", 0, 0, 0, 0) == 0.0


@pytest.mark.parametrize(
    "tokens,expected",
    [
        ((1_000_000, 0, 0, 0), 5.0),    # opus input $5/Mtok
        ((0, 1_000_000, 0, 0), 25.0),   # opus output $25/Mtok
        ((0, 0, 1_000_000, 0), 6.25),   # cache write 1.25x input
        ((0, 0, 0, 1_000_000), 0.5),    # cache read 0.10x input
    ],
)
def test_exact_opus_rates(tokens, expected):
    assert pricing.cost_for_usage("claude-opus-4-8", *tokens) == pytest.approx(expected)


def test_price_table_age_helpers():
    today = pricing.PRICE_TABLE_DATE + datetime.timedelta(days=10)
    assert pricing.price_table_age_days(today) == 10
    assert not pricing.is_price_table_stale(today)


def test_price_table_goes_stale_past_max_age():
    old = pricing.PRICE_TABLE_DATE + datetime.timedelta(
        days=pricing.PRICE_TABLE_MAX_AGE_DAYS + 1
    )
    assert pricing.is_price_table_stale(old)


def test_bundled_price_table_is_currently_fresh():
    # If this fails, the shipped table is older than its max age — update it.
    assert not pricing.is_price_table_stale(), (
        f"pricing table dated {pricing.PRICE_TABLE_DATE} is older than "
        f"{pricing.PRICE_TABLE_MAX_AGE_DAYS} days — refresh claudestudio/pricing.py"
    )
