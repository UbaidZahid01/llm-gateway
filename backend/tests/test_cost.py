from decimal import Decimal

from src import cost


def _capture(monkeypatch):
    """Replace cost_finder with a recorder, so these tests assert the mapping this
    module does rather than the SDK's pricing math."""
    calls = {}

    def fake(provider, model_id, **kwargs):
        calls["provider"] = provider
        calls["model_id"] = model_id
        calls.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(cost, "cost_finder", fake)
    return calls


class _FakeResult:
    def to_dict(self):
        return {"total_cost": "0.0000000000", "currency": "USD"}


def test_openai_cached_tokens_subtracted_from_input(monkeypatch):
    # OpenAI folds cached tokens into prompt_tokens; billing them as input too
    # would charge the same tokens twice.
    calls = _capture(monkeypatch)
    cost.calculate_cost(
        "openai",
        "gpt-4o-mini",
        {"usage": {"prompt_tokens": 1000, "completion_tokens": 200,
                   "prompt_tokens_details": {"cached_tokens": 400}}},
    )
    assert calls["input_tokens"] == 600
    assert calls["cache_read"] == 400
    assert calls["output_tokens"] == 200
    assert calls["cache_write"] == 0


def test_anthropic_cache_tokens_do_not_reduce_input(monkeypatch):
    # Anthropic's input_tokens already excludes cache tokens, so prompt_tokens
    # passes through untouched.
    calls = _capture(monkeypatch)
    cost.calculate_cost(
        "anthropic",
        "claude-sonnet-4-5",
        {"usage": {"prompt_tokens": 1000, "completion_tokens": 200,
                   "cache_read_input_tokens": 5000,
                   "cache_creation_input_tokens": 300}},
    )
    assert calls["input_tokens"] == 1000
    assert calls["cache_read"] == 5000
    assert calls["cache_write"] == 300


def test_responses_api_usage_shape(monkeypatch):
    calls = _capture(monkeypatch)
    cost.calculate_cost(
        "openai",
        "gpt-4o",
        {"usage": {"input_tokens": 500, "output_tokens": 50,
                   "input_tokens_details": {"cached_tokens": 100}}},
    )
    assert calls["input_tokens"] == 400
    assert calls["cache_read"] == 100
    assert calls["output_tokens"] == 50


def test_dated_and_latest_model_aliases_are_stripped(monkeypatch):
    for given, expected in [
        ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
        ("claude-sonnet-4-5-latest", "claude-sonnet-4-5"),
        ("gpt-4o-mini", "gpt-4o-mini"),
    ]:
        calls = _capture(monkeypatch)
        cost.calculate_cost("anthropic", given, {"usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        assert calls["model_id"] == expected


def test_no_usage_block_means_no_cost():
    assert cost.calculate_cost("openai", "gpt-4o", {"error": {"message": "boom"}}) is None
    assert cost.calculate_cost("openai", "gpt-4o", {"usage": None}) is None


def test_unpriceable_model_reports_error_and_keeps_tokens():
    result = cost.calculate_cost(
        "openai",
        "no-such-model-xyz",
        {"usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    )
    assert result["tokens"] == {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0}
    assert "error" in result
    assert "total_cost" not in result


def test_known_model_prices_to_a_positive_total():
    # End-to-end against the offline snapshot (see conftest): a real lookup, real math.
    result = cost.calculate_cost(
        "openai",
        "gpt-4o-mini",
        {"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}},
    )
    assert "error" not in result
    assert Decimal(result["total_cost"]) > 0
    assert result["currency"] == "USD"
    assert result["tokens"]["input"] == 1_000_000
