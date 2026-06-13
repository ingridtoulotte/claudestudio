"""Deterministic, local model pricing for cost estimation.

Prices are USD per 1,000,000 tokens. Source: Anthropic public pricing.
Cache writes bill at 1.25x the input rate (5-minute TTL); cache reads at 0.10x.
Models not in the table are flagged and cost $0 — never silently guessed.

This table is the single place to edit when prices change. Everything that
reports a dollar figure routes through `cost_for_usage`.
"""

from __future__ import annotations

# (input_per_mtok, output_per_mtok)
PRICES = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4-0": (15.0, 75.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4-0": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-3-7-sonnet": (3.0, 15.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-3-5-haiku": (0.80, 4.0),
    "claude-3-haiku": (0.25, 1.25),
    "claude-3-opus": (15.0, 75.0),
}

CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10

# Short, human label for grouping in the UI.
FAMILY = (
    ("fable", "Fable"),
    ("mythos", "Mythos"),
    ("opus", "Opus"),
    ("sonnet", "Sonnet"),
    ("haiku", "Haiku"),
)


def normalize(model: str | None) -> str:
    """Strip date suffixes so `claude-opus-4-8-20260101` resolves to the table."""
    if not model:
        return "unknown"
    m = model.strip().lower()
    # exact match first
    if m in PRICES:
        return m
    # progressively trim trailing -YYYYMMDD or -tier suffixes
    parts = m.split("-")
    while parts:
        candidate = "-".join(parts)
        if candidate in PRICES:
            return candidate
        parts.pop()
    return m


def is_priced(model: str | None) -> bool:
    return normalize(model) in PRICES


def family_of(model: str | None) -> str:
    m = (model or "").lower()
    for key, label in FAMILY:
        if key in m:
            return label
    return "Other"


def cost_for_usage(
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Return the USD cost for one usage record. Unknown model -> 0.0."""
    key = normalize(model)
    price = PRICES.get(key)
    if price is None:
        return 0.0
    inp, outp = price
    cost = (
        input_tokens * inp
        + cache_write_tokens * inp * CACHE_WRITE_MULT
        + cache_read_tokens * inp * CACHE_READ_MULT
        + output_tokens * outp
    ) / 1_000_000.0
    return cost
