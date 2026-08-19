from typing import Dict, List, Optional, Type

from .anthropic_provider import AnthropicProvider
from .base import LLMProvider, ProviderError
from .openai_provider import OpenAIProvider
from .qwen_provider import QwenProvider

# The gateway holds no provider keys itself, so an adapter is built fresh per
# request with whatever key the caller sent — never cached or reused across
# callers.
_PROVIDER_CLASSES: Dict[str, Type[LLMProvider]] = {
    cls.name: cls for cls in (OpenAIProvider, AnthropicProvider, QwenProvider)
}


def get_provider(name: str, api_key: Optional[str] = None) -> Optional[LLMProvider]:
    cls = _PROVIDER_CLASSES.get(name)
    return cls(api_key=api_key) if cls else None


def supported_providers() -> List[str]:
    return sorted(_PROVIDER_CLASSES.keys())


__all__ = [
    "LLMProvider",
    "ProviderError",
    "get_provider",
    "supported_providers",
]
