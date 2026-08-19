"""Pytest fixtures. Sets an isolated storage dir and admin key BEFORE the app
is imported, so tests never touch real db_json data or need network access."""

import os
import tempfile

# Must run before any `src.*` import so config.Settings picks these up.
os.environ["DB_JSON_DIR"] = tempfile.mkdtemp(prefix="guardrails-test-")
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["RATE_LIMIT_PER_MINUTE"] = "1000"

import llm_usage_cost  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Cost calculation fetches live prices from OpenRouter. Pin it offline so tests
# resolve against the bundled pricing snapshot and never touch the network.
llm_usage_cost.configure(offline=True)

ADMIN_KEY = "test-admin-key"


@pytest.fixture(scope="session")
def app():
    from src.main import app as fastapi_app

    return fastapi_app


@pytest.fixture
def client(app):
    c = TestClient(app)
    c.headers.update({"X-Admin-Key": ADMIN_KEY})
    return c


class FakeProvider:
    """Stand-in provider that avoids network. Records the body (and the caller's
    X-Provider-Key, if any) it received, and returns a canned OpenAI-shaped
    response (content configurable)."""

    def __init__(self, name="openai", content="ok", api_key=None):
        self.name = name
        self.content = content
        self.api_key = api_key
        self.received = None

    def call(self, body):
        self.received = body
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": body.get("model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": self.content, "refusal": None},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def responses(self, body):
        self.received = body
        return {
            "id": "resp-test",
            "object": "response",
            "created_at": 1,
            "model": body.get("model"),
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": self.content, "annotations": []}],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


@pytest.fixture
def patch_provider(monkeypatch):
    """Route a given provider name to a FakeProvider; return the fake so tests
    can inspect what it received / set its response content."""

    def _patch(name="openai", content="ok"):
        import src.routers.llm as llm

        fake = FakeProvider(name=name, content=content)

        def _get_provider(n, api_key=None):
            if n != name:
                return None
            fake.api_key = api_key
            return fake

        monkeypatch.setattr(llm, "get_provider", _get_provider)
        return fake

    return _patch
