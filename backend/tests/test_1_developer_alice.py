"""
Independent, self-contained scenario: Alice calls the gateway directly with
just a provider, model, and her own provider key — no gateway-issued
credential needed. Verifies the audit log recorded exactly that
request/response.

Run with the backend already up (uvicorn src.main:app --port 8000) and your
own OPENAI_API_KEY set in the environment — the gateway holds no provider
keys itself, so it forwards yours from the X-Provider-Key header:
    python tests/test_1_developer_alice.py
"""

import json
import os

import requests

BASE_URL = "http://localhost:8000"


def main() -> None:
    # Make a simple single-turn chat call, naming the provider and model.
    payload = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "In one sentence, what is an API?"}],
        "temperature": 0,
    }
    resp = requests.post(
        f"{BASE_URL}/v1/call_llm",
        headers={"X-Provider-Key": os.environ["OPENAI_API_KEY"]},
        json=payload,
        timeout=30,
    )
    data = resp.json()
    print(f"\ncall_llm status: {resp.status_code}")
    print(json.dumps(data, indent=2))

    assert resp.status_code == 200
    assert data.get("object") == "chat.completion"
    assert data.get("choices")

    # Check the audit log recorded exactly this call.
    logs = requests.get(f"{BASE_URL}/audit/logs", params={"provider": "openai"}, timeout=10).json()
    assert logs, "No audit logs found for provider 'openai'"
    latest = logs[0]

    assert latest["request_payload"]["messages"] == payload["messages"]
    assert latest["response_payload"].get("id") == data.get("id")

    print("\nPASS: Alice — simple chat call logged correctly in the audit trail.")


if __name__ == "__main__":
    main()
