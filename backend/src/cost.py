"""Per-request LLM cost calculation.

Thin wrapper over the ``llm-usage-cost`` SDK: pulls token counts out of a
provider's usage block, maps them onto the four dimensions the calculator prices
(input / output / cache read / cache write), and returns a serializable cost
block.

Cost is advisory metadata attached *after* the upstream call already succeeded,
so nothing here is allowed to fail the request — a lookup that misses reports the
reason inside the cost block instead of raising.
"""

import logging
import re
from typing import Any, Dict, Optional

from llm_usage_cost import cost_finder

logger = logging.getLogger("guardrails.cost")

# Callers name Anthropic models with a dated or aliased suffix
# (claude-sonnet-4-5-20250929, claude-3-5-haiku-latest) but the pricing catalog
# keys on the bare version (claude-sonnet-4-5).
# simplification: suffix strip only. A model the catalog genuinely lacks still
# misses; supply its prices via ~/.llm-usage-cost/config.json in that case.
_ALIAS_SUFFIX = re.compile(r"-(?:latest|\d{8})$")


def calculate_cost(
    provider: str,
    model: Optional[str],
    response_payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Price one completed LLM call.

    Returns ``None`` when the response carries no usage block (nothing to
    price). When pricing fails, returns the token counts plus an ``error``
    string so the caller can see *why* a cost is missing.
    """
    usage = response_payload.get("usage") if isinstance(response_payload, dict) else None
    if not isinstance(usage, dict):
        return None

    tokens = _extract_tokens(provider, usage)
    if tokens is None:
        return None

    model = model or response_payload.get("model")
    if not model:
        return {"tokens": tokens, "error": "No model on the request or response; cannot price."}

    try:
        result = cost_finder(
            provider,
            _ALIAS_SUFFIX.sub("", model),
            input_tokens=tokens["input"],
            output_tokens=tokens["output"],
            cache_read=tokens["cache_read"],
            cache_write=tokens["cache_write"],
        )
    # Broad by design: the SDK reaches the network for live prices. The upstream
    # LLM call is already paid for at this point, so a pricing failure must
    # degrade to "cost unknown" rather than lose the response.
    except Exception as exc:  # noqa: BLE001
        logger.warning("cost lookup failed for %s/%s: %s", provider, model, exc)
        return {"tokens": tokens, "error": str(exc)}

    return {**result.to_dict(), "tokens": tokens}


def _extract_tokens(provider: str, usage: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Map a usage block onto the calculator's four token dimensions.

    Anthropic reports non-cached prompt tokens directly, while OpenAI-shaped
    providers fold cached tokens into the prompt total — those must be
    subtracted, or cached tokens get billed twice (once at the input rate and
    again at the cache-read rate).
    """
    if "prompt_tokens" in usage:  # chat.completions shape
        prompt = _count(usage.get("prompt_tokens"))
        output = _count(usage.get("completion_tokens"))
        details = usage.get("prompt_tokens_details")
    elif "input_tokens" in usage:  # Responses API shape
        prompt = _count(usage.get("input_tokens"))
        output = _count(usage.get("output_tokens"))
        details = usage.get("input_tokens_details")
    else:
        return None

    if provider == "anthropic":
        cache_read = _count(usage.get("cache_read_input_tokens"))
        cache_write = _count(usage.get("cache_creation_input_tokens"))
        input_tokens = prompt
    else:
        cache_read = _count((details or {}).get("cached_tokens"))
        # OpenAI/Qwen caching is implicit — there is no write step to bill.
        cache_write = 0
        input_tokens = max(prompt - cache_read, 0)

    return {
        "input": input_tokens,
        "output": output,
        "cache_read": cache_read,
        "cache_write": cache_write,
    }


def _count(value: Any) -> int:
    """Coerce a usage field to the non-negative int the calculator requires."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
