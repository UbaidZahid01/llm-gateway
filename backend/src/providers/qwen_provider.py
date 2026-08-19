from typing import Optional

from ..config import settings
from .openai_provider import OpenAIProvider


class QwenProvider(OpenAIProvider):
    """Alibaba Qwen via DashScope's OpenAI-compatible endpoint.

    DashScope speaks the OpenAI ``chat.completions`` wire format, so we reuse
    the OpenAI adapter with a different base URL. Callers just send a Qwen
    ``model`` (e.g. ``qwen-plus``), ``provider: "qwen"``, and their own
    DashScope key in the ``X-Provider-Key`` header.
    """

    name = "qwen"

    def __init__(self, api_key: Optional[str] = None):
        super().__init__(api_key=api_key, base_url=settings.QWEN_BASE_URL)
