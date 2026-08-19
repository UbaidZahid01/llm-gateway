from typing import Any, Dict, Optional

from openai import OpenAI, OpenAIError

from .base import LLMProvider, ProviderError


class OpenAIProvider(LLMProvider):
    """Native OpenAI. The gateway's request/response shape is OpenAI's, so this
    is a straight pass-through with no translation."""

    name = "openai"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self._api_key = api_key
        self._base_url = base_url
        self._client: Optional[OpenAI] = None

    def _get_client(self) -> OpenAI:
        if not self._api_key:
            raise ProviderError(
                f"No API key provided for provider '{self.name}'. Send the "
                "caller's own key in the 'X-Provider-Key' header.",
                status_code=400,
                error_type="invalid_request_error",
                code="provider_key_missing",
            )
        if self._client is None:
            kwargs: Dict[str, Any] = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def call(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._invoke(lambda client: client.chat.completions.create(**body))

    def responses(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """OpenAI Responses API. Like :meth:`call`, a straight pass-through: the
        gateway's request/response shape for this route is the Responses shape."""
        # The Responses API landed in the OpenAI SDK at 1.66.0; older pins lack it.
        if not hasattr(self._get_client(), "responses"):
            raise ProviderError(
                "The installed OpenAI SDK is too old for the Responses API. "
                "Upgrade to openai>=1.66.0.",
                status_code=501,
                error_type="invalid_request_error",
                code="responses_unavailable",
            )
        return self._invoke(lambda client: client.responses.create(**body))

    def _invoke(self, create) -> Dict[str, Any]:
        client = self._get_client()
        try:
            result = create(client)
            return result.model_dump()
        except OpenAIError as exc:
            raise ProviderError(
                str(exc),
                status_code=getattr(exc, "status_code", None) or 502,
                error_type=exc.__class__.__name__,
                code=getattr(exc, "code", None),
            )
        except TypeError as exc:
            raise ProviderError(
                f"Invalid request payload: {exc}",
                status_code=400,
                error_type="invalid_request_error",
                code="invalid_payload",
            )
