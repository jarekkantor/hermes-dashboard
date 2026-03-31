"""Anthropic model pricing table. All prices are per 1M tokens in USD."""

from __future__ import annotations

PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-opus-4": {"input": 15.00, "output": 75.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}

CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def _match_model(model: str) -> dict[str, float]:
    if model in PRICING:
        return PRICING[model]
    for key, val in PRICING.items():
        if key in model or model in key:
            return val
    if "opus" in model:
        return {"input": 15.00, "output": 75.00}
    if "haiku" in model:
        return {"input": 1.00, "output": 5.00}
    return {"input": 3.00, "output": 15.00}


def calculate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    prices = _match_model(model)
    input_price = prices["input"] / 1_000_000
    output_price = prices["output"] / 1_000_000
    cache_write_price = input_price * CACHE_WRITE_MULTIPLIER
    cache_read_price = input_price * CACHE_READ_MULTIPLIER

    cost = (
        input_tokens * input_price
        + output_tokens * output_price
        + cache_creation_tokens * cache_write_price
        + cache_read_tokens * cache_read_price
    )
    return round(cost, 6)
