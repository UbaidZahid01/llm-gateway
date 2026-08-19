"""
Independent, self-contained scenario: Carol calls the gateway directly with
just a provider, model, and her own provider key, making a tool-calling
request (the `tools` param, same as OpenAI's API). Verifies the audit log
recorded exactly that request/response.

Run with the backend already up (uvicorn src.main:app --port 8000) and your
own OPENAI_API_KEY set in the environment — the gateway holds no provider
keys itself, so it forwards yours from the X-Provider-Key header:
    python tests/test_3_developer_carol.py
"""

import json
import os

import requests

BASE_URL = "http://localhost:8000"


def main() -> None:
    # Make a tool-calling request, exactly like OpenAI's `tools` param.
    payload = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "What is the weather in Karachi right now?"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        "tool_choice": "auto",
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
    tool_calls = data["choices"][0]["message"].get("tool_calls")
    assert tool_calls, "Model was expected to call the get_weather tool"
    assert tool_calls[0]["function"]["name"] == "get_weather"

    # Check the audit log recorded exactly this call.
    logs = requests.get(f"{BASE_URL}/audit/logs", params={"provider": "openai"}, timeout=10).json()
    assert logs, "No audit logs found for provider 'openai'"
    latest = logs[0]

    assert latest["request_payload"]["tools"] == payload["tools"]
    assert latest["response_payload"].get("id") == data.get("id")

    print("\nPASS: Carol — tool-calling request logged correctly in the audit trail.")


if __name__ == "__main__":
    main()
