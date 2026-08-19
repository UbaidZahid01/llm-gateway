from abc import ABC, abstractmethod
from typing import Any, Dict


class ProviderError(Exception):
    """Raised by a provider adapter when an upstream call fails.

    Carries an OpenAI-style error shape so the gateway can return the same
    envelope regardless of which provider was called.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 502,
        error_type: str = "provider_error",
        code: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.code = code

    def to_payload(self) -> Dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.error_type,
                "code": self.code,
            }
        }


class LLMProvider(ABC):
    """A gateway adapter for one upstream LLM vendor.

    Every adapter accepts a request in OpenAI ``chat.completions.create`` shape
    and returns a response in OpenAI ``chat.completion`` shape, so callers can
    point an OpenAI SDK at the gateway regardless of which provider actually
    serves the request.
    """

    #: Value callers pass in the ``provider`` field to select this adapter.
    name: str

    @abstractmethod
    def call(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Forward an OpenAI-shaped request and return an OpenAI-shaped response.

        Raise :class:`ProviderError` on any upstream or translation failure.
        """
        raise NotImplementedError

    def responses(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Forward an OpenAI *Responses API* request (``input``/``output`` shape).

        Only providers that natively speak this API override it; by default a
        provider reports that it does not support it.
        """
        raise ProviderError(
            f"Provider '{self.name}' does not support the Responses API.",
            status_code=400,
            error_type="invalid_request_error",
            code="responses_not_supported",
        )
