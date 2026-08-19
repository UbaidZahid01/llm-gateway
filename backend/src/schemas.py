from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class CallLLMRequest(BaseModel):
    """OpenAI ``chat.completions.create``-shaped request. `provider` and
    `model` are left optional here (rather than pydantic-required) so a
    missing/unknown provider still gets the gateway's own structured
    `provider_not_supported` error instead of a generic 422. Unlisted fields
    are still accepted and passed through untouched (`extra="allow"`)."""

    model_config = ConfigDict(extra="allow")

    provider: Optional[str] = Field(
        default=None, description="Which upstream provider serves this request: 'openai', 'anthropic', or 'qwen'."
    )
    model: Optional[str] = Field(default=None, description="Upstream model name, e.g. 'gpt-4o-mini'.")
    messages: Optional[List[Dict[str, Any]]] = Field(default=None, description="Chat messages, OpenAI shape.")
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    n: Optional[int] = None
    stop: Optional[Union[str, List[str]]] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    logit_bias: Optional[Dict[str, float]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    response_format: Optional[Dict[str, Any]] = None
    seed: Optional[int] = None
    user: Optional[str] = None
    stream: Optional[bool] = Field(default=None, description="Not supported by this gateway yet.")


class ResponsesRequest(BaseModel):
    """OpenAI Responses API (``input``/``output`` shape) request. Same
    optional-provider/model rationale as CallLLMRequest."""

    model_config = ConfigDict(extra="allow")

    provider: Optional[str] = Field(
        default=None, description="Which upstream provider serves this request. Only 'openai' currently supports this API."
    )
    model: Optional[str] = Field(default=None, description="Upstream model name, e.g. 'gpt-4o-mini'.")
    input: Optional[Union[str, List[Dict[str, Any]]]] = Field(
        default=None, description="Prompt text, or a list of message / function_call_output items."
    )
    instructions: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None
    stream: Optional[bool] = Field(default=None, description="Not supported by this gateway yet.")


class AuditLogEntry(BaseModel):
    log_id: str
    client_ip: str
    provider: str | None = None
    model: str | None = None
    endpoint: str
    request_payload: dict
    response_payload: dict
    request_guardrail: dict | None = None
    response_guardrail: dict | None = None
    cost: dict | None = None
    status_code: int
    latency_ms: float
    created_at: str
