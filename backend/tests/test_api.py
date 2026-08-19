from fastapi.testclient import TestClient


# --------------------------------------------------------------------------- #
# Admin auth
# --------------------------------------------------------------------------- #

def test_admin_routes_require_key(app):
    anon = TestClient(app)  # no X-Admin-Key
    assert anon.get("/admin/policy").status_code == 401
    assert anon.get("/audit/logs").status_code == 401


def test_admin_routes_ok_with_key(client):
    assert client.get("/admin/policy").status_code == 200
    assert client.get("/audit/logs").status_code == 200


def test_call_llm_does_not_require_admin_key(app, patch_provider):
    patch_provider("openai", content="ok")
    # A bare client (no admin key) can call the gateway directly.
    bare = TestClient(app)
    r = bare.post(
        "/v1/call_llm",
        json={"provider": "openai", "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Provider routing
# --------------------------------------------------------------------------- #

def test_provider_is_stripped_before_forwarding(client, patch_provider):
    fake = patch_provider("openai", content="hello")
    r = client.post(
        "/v1/call_llm",
        json={"provider": "openai", "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    assert "provider" not in fake.received  # stripped before forwarding


def test_missing_provider_returns_400(client):
    r = client.post(
        "/v1/call_llm",
        json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "provider_not_supported"


def test_unknown_provider_returns_400(client):
    r = client.post(
        "/v1/call_llm",
        json={"provider": "bogus", "model": "x", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "provider_not_supported"


def test_missing_provider_key_header_returns_400(client):
    # No patch_provider here: this hits the real OpenAI adapter, whose
    # _get_client() must reject before any network call is attempted.
    r = client.post(
        "/v1/call_llm",
        json={"provider": "openai", "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "provider_key_missing"


def test_provider_key_header_is_forwarded_to_provider(client, patch_provider):
    fake = patch_provider("openai", content="ok")
    r = client.post(
        "/v1/call_llm",
        headers={"X-Provider-Key": "sk-caller-owned-key"},
        json={"provider": "openai", "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    assert fake.api_key == "sk-caller-owned-key"


# --------------------------------------------------------------------------- #
# Guardrail integration through the endpoint
# --------------------------------------------------------------------------- #

def test_request_with_pii_is_sanitized_and_logged(client, patch_provider):
    fake = patch_provider("openai", content="ok")
    r = client.post(
        "/v1/call_llm",
        json={"provider": "openai", "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "mail me a@b.com"}]},
    )
    assert r.status_code == 200
    # Provider received the sanitized prompt, not the raw email.
    assert "a@b.com" not in fake.received["messages"][0]["content"]
    # Audit log records the redact decision.
    logs = client.get("/audit/logs").json()
    assert logs[0]["request_guardrail"]["decision"] == "redact"


def test_blocked_request_returns_403_and_no_provider_call(client, patch_provider):
    fake = patch_provider("openai", content="ok")
    r = client.post(
        "/v1/call_llm",
        json={"provider": "openai", "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "card 4111 1111 1111 1111"}]},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "request_blocked"
    assert fake.received is None  # provider never called


# --------------------------------------------------------------------------- #
# Responses API endpoint
# --------------------------------------------------------------------------- #

def test_responses_endpoint_forwards_and_returns_output(client, patch_provider):
    fake = patch_provider("openai", content="hello there")
    r = client.post(
        "/v1/responses",
        json={"provider": "openai", "model": "gpt-4o-mini", "input": "hi"},
    )
    assert r.status_code == 200
    assert fake.received["input"] == "hi"
    assert r.json()["output"][0]["content"][0]["text"] == "hello there"


def test_responses_request_pii_sanitized_and_logged(client, patch_provider):
    fake = patch_provider("openai", content="ok")
    r = client.post(
        "/v1/responses",
        json={"provider": "openai", "model": "gpt-4o-mini", "input": "mail me a@b.com"},
    )
    assert r.status_code == 200
    assert "a@b.com" not in fake.received["input"]  # redacted before forwarding
    logs = client.get("/audit/logs").json()
    assert logs[0]["endpoint"] == "/v1/responses"
    assert logs[0]["request_guardrail"]["decision"] == "redact"


def test_responses_blocked_request_returns_403(client, patch_provider):
    fake = patch_provider("openai", content="ok")
    r = client.post(
        "/v1/responses",
        json={"provider": "openai", "model": "gpt-4o-mini", "input": "card 4111 1111 1111 1111"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "request_blocked"
    assert fake.received is None  # provider never called


def test_responses_unsupported_provider_returns_400(client):
    # Anthropic has no Responses API — the base provider rejects it.
    r = client.post(
        "/v1/responses",
        json={"provider": "anthropic", "model": "claude-x", "input": "hi"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "responses_not_supported"


# --------------------------------------------------------------------------- #
# Policy API
# --------------------------------------------------------------------------- #

def test_policy_get_and_update_roundtrip(client):
    pol = client.get("/admin/policy").json()
    assert "pii" in pol and "email" in pol["pii"]
    pol["pii"]["email"]["action"] = "mask"
    saved = client.put("/admin/policy", json=pol).json()
    assert saved["pii"]["email"]["action"] == "mask"
    # restore default so other tests are unaffected
    pol["pii"]["email"]["action"] = "redact"
    client.put("/admin/policy", json=pol)


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #

def test_rate_limit_returns_429(client, patch_provider, monkeypatch):
    from src.ratelimit import rate_limiter

    patch_provider("openai", content="ok")
    # All TestClient requests share the same "testclient" identity, so clear
    # hits accumulated by earlier tests before tightening the limit.
    rate_limiter._hits.clear()
    monkeypatch.setattr(rate_limiter, "limit", 1)
    payload = {"provider": "openai", "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}

    r1 = client.post("/v1/call_llm", json=payload)
    r2 = client.post("/v1/call_llm", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 429
    assert r2.json()["error"]["code"] == "rate_limit_exceeded"
    assert "Retry-After" in r2.headers


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #

def test_cost_returned_with_response_and_logged(client, patch_provider):
    patch_provider("openai", content="ok")
    r = client.post(
        "/v1/call_llm",
        json={"provider": "openai", "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    cost = r.json()["cost"]
    assert cost["currency"] == "USD"
    assert float(cost["total_cost"]) > 0
    # FakeProvider reports 1 prompt + 1 completion token.
    assert cost["tokens"] == {"input": 1, "output": 1, "cache_read": 0, "cache_write": 0}

    logs = client.get("/audit/logs").json()
    assert logs[0]["cost"]["total_cost"] == cost["total_cost"]


def test_responses_endpoint_also_returns_cost(client, patch_provider):
    patch_provider("openai", content="ok")
    r = client.post(
        "/v1/responses",
        json={"provider": "openai", "model": "gpt-4o-mini", "input": "hi"},
    )
    assert r.status_code == 200
    assert float(r.json()["cost"]["total_cost"]) > 0


def test_blocked_request_has_no_cost(client, patch_provider):
    patch_provider("openai", content="ok")
    # A denylisted term blocks before any provider call, so there is nothing to price.
    client.put("/admin/policy", json={
        "pii": {}, "denylist": [{"term": "forbidden", "is_regex": False}],
        "apply_to_request": True, "apply_to_response": True,
    })
    try:
        r = client.post(
            "/v1/call_llm",
            json={"provider": "openai", "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "forbidden"}]},
        )
        assert r.status_code == 403
        assert "cost" not in r.json()
        logs = client.get("/audit/logs").json()
        assert logs[0]["cost"] is None
    finally:
        client.put("/admin/policy", json={
            "pii": {}, "denylist": [], "apply_to_request": True, "apply_to_response": True,
        })
