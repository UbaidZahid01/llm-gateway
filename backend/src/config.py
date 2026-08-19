import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    # The gateway holds no provider API keys. Each caller supplies their own
    # upstream key per request via the `X-Provider-Key` header (see
    # routers/llm.py), so it's forwarded straight through and never stored.
    # Qwen is served through Alibaba DashScope's OpenAI-compatible endpoint —
    # only its base URL is server-side config, not a secret.
    QWEN_BASE_URL = os.getenv(
        "QWEN_BASE_URL",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )
    # External audit sink (SIEM / webhook). Fire-and-forget; disabled if no URL.
    AUDIT_SINK_URL = os.getenv("AUDIT_SINK_URL") or None
    AUDIT_SINK_ENABLED = (os.getenv("AUDIT_SINK_ENABLED", "true").lower() != "false")

    # Admin auth. Guards /admin and /audit. If unset, those routes stay open
    # (dev mode) and a warning is logged at startup — set this in any shared or
    # production deployment.
    ADMIN_API_KEY = os.getenv("ADMIN_API_KEY") or None

    # Per-client (IP) rate limit on /v1/call_llm (requests per minute). 0 disables.
    RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))

    # Storage location; overridable (used by tests to isolate from real data).
    _db_override = os.getenv("DB_JSON_DIR")
    DB_JSON_DIR = Path(_db_override) if _db_override else (BASE_DIR / "db_json")
    CORS_ORIGINS = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


settings = Settings()
