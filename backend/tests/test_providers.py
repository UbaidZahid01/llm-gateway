"""The gateway holds no provider keys: get_provider() builds a fresh adapter
per call from whatever key the caller passed in, and an adapter built with no
key fails fast (before any network call) when actually used."""

from src.providers import ProviderError, get_provider, supported_providers


def test_get_provider_builds_fresh_instance_with_caller_key():
    provider = get_provider("openai", "sk-caller-supplied")
    assert provider._api_key == "sk-caller-supplied"


def test_get_provider_instances_are_not_shared_across_calls():
    first = get_provider("openai", "sk-aaa")
    second = get_provider("openai", "sk-bbb")
    assert first is not second
    assert first._api_key == "sk-aaa"
    assert second._api_key == "sk-bbb"


def test_unknown_provider_returns_none():
    assert get_provider("bogus", "sk-whatever") is None


def test_no_key_raises_provider_key_missing_before_any_network_call():
    provider = get_provider("openai", None)
    try:
        provider.call({"model": "gpt-4o-mini", "messages": []})
        assert False, "expected ProviderError"
    except ProviderError as exc:
        assert exc.code == "provider_key_missing"
        assert exc.status_code == 400


def test_supported_providers_lists_all_three():
    assert supported_providers() == ["anthropic", "openai", "qwen"]
