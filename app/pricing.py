"""Model pricing table. All prices are per 1M tokens in USD."""

from __future__ import annotations

# Single unified pricing table — no provider routing, no ambiguity.
# Every model that can appear in api_requests should be here.
PRICING: dict[str, dict[str, float]] = {
    # ── Anthropic models ──
    "claude-haiku-4-5":          {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5":         {"input": 3.00, "output": 15.00},
    "claude-sonnet-4":           {"input": 3.00, "output": 15.00},
    "claude-opus-4":             {"input": 15.00, "output": 75.00},
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00},

    # ── Custom models ──
    "deepseek-v4.1-flash":       {"input": 0.10, "output": 0.20},
    "minimax-m2.7":              {"input": 0.30, "output": 1.20},
    "kimi-k2.6":                 {"input": 0.75, "output": 3.50},
}

# Anthropic prompt caching: cache writes cost more than input, cache reads cost less.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def _detect_provider(model: str) -> str:
    """Return 'anthropic' or 'openai' — used only for the DB provider column (metadata)."""
    if model.startswith(("claude-",)) or model == "unknown":
        return "anthropic"
    return "openai"


def _is_anthropic_cache(model: str) -> bool:
    """Return True if the model uses Anthropic-style prompt caching pricing."""
    return model.startswith("claude-") or model == "unknown"


def _match_model(model: str) -> dict[str, float]:
    """Find pricing for a model, with fuzzy fallback.

    Tries exact match first, then substring match, then falls back
    to sensible defaults.
    """
    if model in PRICING:
        return PRICING[model]

    for key, val in PRICING.items():
        if key in model or model in key:
            return val

    if model == "unknown" or not model:
        return {"input": 0.0, "output": 0.0}

    if "opus" in model:
        return {"input": 15.00, "output": 75.00}
    if "haiku" in model:
        return {"input": 1.00, "output": 5.00}
    if "sonnet" in model:
        return {"input": 3.00, "output": 15.00}

    return {"input": 3.00, "output": 15.00}  # sonnet as global default


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

    # Anthropic models have dedicated cache pricing tiers.
    # Everyone else: cache creation priced at input rate, cache reads free.
    if _is_anthropic_cache(model):
        cache_write_price = input_price * CACHE_WRITE_MULTIPLIER
        cache_read_price = input_price * CACHE_READ_MULTIPLIER
    else:
        cache_write_price = input_price
        cache_read_price = 0.0

    cost = (
        input_tokens * input_price
        + output_tokens * output_price
        + cache_creation_tokens * cache_write_price
        + cache_read_tokens * cache_read_price
    )
    return round(cost, 6)
