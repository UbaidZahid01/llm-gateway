import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from ..cost import calculate_cost
from ..guardrails import (
    GuardrailBlocked,
    process_response,
    process_responses_response,
    run_guardrails,
    run_guardrails_responses,
)
from ..policy import policy_store
from ..providers import ProviderError, get_provider, supported_providers
from ..ratelimit import get_client_ip, rate_limiter
from ..schemas import CallLLMRequest, ResponsesRequest
from ..sink import audit_sink
from ..storage import audit_store

router = APIRouter(tags=["llm"])

_PROVIDER_KEY_HEADER = Header(
    default=None,
    alias="X-Provider-Key",
    description="The caller's own API key for the selected provider. Forwarded straight through, never stored.",
)


@router.post("/v1/call_llm", response_model=Dict[str, Any])
async def call_llm(
    request: Request,
    payload: CallLLMRequest,
    x_provider_key: Optional[str] = _PROVIDER_KEY_HEADER,
):
    start = time.perf_counter()
    created_at = datetime.now(timezone.utc).isoformat()
    log_id = str(uuid.uuid4())
    client_ip = get_client_ip(request)

    prepared = _prepare(
        payload.model_dump(exclude_none=True), x_provider_key, client_ip, log_id, start, created_at, "/v1/call_llm"
    )
    if isinstance(prepared, JSONResponse):
        return prepared
    body, provider, provider_name = prepared

    policy = policy_store.get()

    # --- Interceptor: scan/redact/mask/block the request ---
    try:
        request_guardrail = run_guardrails(body, policy).as_dict()
    except GuardrailBlocked as gb:
        request_guardrail = gb.result.as_dict()
        error_payload = {
            "error": {
                "message": gb.result.block_reason or "Request blocked by guardrails.",
                "type": "guardrail_blocked",
                "code": "request_blocked",
            }
        }
        _log(
            log_id, client_ip, provider_name, body, error_payload, 403, start, created_at,
            request_guardrail=request_guardrail,
        )
        return JSONResponse(status_code=403, content=error_payload)

    # --- Forward to the selected provider ---
    try:
        response_payload = provider.call(body)
        status_code = 200
    except ProviderError as exc:
        status_code = exc.status_code
        response_payload = exc.to_payload()

    # --- Processor: scan/redact/mask/block the response ---
    response_guardrail = None
    cost = None
    if status_code == 200:
        response_guardrail = process_response(response_payload, policy).as_dict()
        cost = calculate_cost(provider_name, body.get("model"), response_payload)

    _log(
        log_id, client_ip, provider_name, body, response_payload, status_code, start, created_at,
        request_guardrail=request_guardrail, response_guardrail=response_guardrail, cost=cost,
    )
    return JSONResponse(status_code=status_code, content=_with_cost(response_payload, cost))


@router.post("/v1/responses", response_model=Dict[str, Any])
async def responses(
    request: Request,
    payload: ResponsesRequest,
    x_provider_key: Optional[str] = _PROVIDER_KEY_HEADER,
):
    """OpenAI Responses API endpoint. Same guardrail/audit pipeline as
    /v1/call_llm, but the payload uses the Responses `input`/`output` shape and
    only providers that speak it (OpenAI) will serve it."""
    start = time.perf_counter()
    created_at = datetime.now(timezone.utc).isoformat()
    log_id = str(uuid.uuid4())
    client_ip = get_client_ip(request)

    prepared = _prepare(
        payload.model_dump(exclude_none=True), x_provider_key, client_ip, log_id, start, created_at, "/v1/responses"
    )
    if isinstance(prepared, JSONResponse):
        return prepared
    body, provider, provider_name = prepared

    policy = policy_store.get()

    # --- Interceptor: scan/redact/mask/block the request ---
    try:
        request_guardrail = run_guardrails_responses(body, policy).as_dict()
    except GuardrailBlocked as gb:
        request_guardrail = gb.result.as_dict()
        error_payload = {
            "error": {
                "message": gb.result.block_reason or "Request blocked by guardrails.",
                "type": "guardrail_blocked",
                "code": "request_blocked",
            }
        }
        _log(
            log_id, client_ip, provider_name, body, error_payload, 403, start, created_at,
            request_guardrail=request_guardrail, endpoint="/v1/responses",
        )
        return JSONResponse(status_code=403, content=error_payload)

    # --- Forward to the selected provider ---
    try:
        response_payload = provider.responses(body)
        status_code = 200
    except ProviderError as exc:
        status_code = exc.status_code
        response_payload = exc.to_payload()

    # --- Processor: scan/redact/mask/block the response ---
    response_guardrail = None
    cost = None
    if status_code == 200:
        response_guardrail = process_responses_response(response_payload, policy).as_dict()
        cost = calculate_cost(provider_name, body.get("model"), response_payload)

    _log(
        log_id, client_ip, provider_name, body, response_payload, status_code, start, created_at,
        request_guardrail=request_guardrail, response_guardrail=response_guardrail, cost=cost,
        endpoint="/v1/responses",
    )
    return JSONResponse(status_code=status_code, content=_with_cost(response_payload, cost))


def _prepare(
    body: Dict[str, Any],
    provider_key: Optional[str],
    client_ip: str,
    log_id: str,
    start: float,
    created_at: str,
    endpoint: str,
):
    """Shared preamble for both LLM endpoints: per-client rate limit,
    provider selection, and stream rejection. Returns ``(body, provider,
    provider_name)`` on success, or a JSONResponse to return immediately
    (rate-limit / bad-provider / stream errors are logged here)."""
    # Per-client rate limit, keyed by source IP (before any upstream work).
    allowed, retry_after = rate_limiter.check(client_ip)
    if not allowed:
        error_payload = {
            "error": {
                "message": (
                    f"Rate limit exceeded ({rate_limiter.limit} requests/min). "
                    f"Retry in {retry_after}s."
                ),
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
            }
        }
        return JSONResponse(
            status_code=429, content=error_payload, headers={"Retry-After": str(retry_after)}
        )

    # Provider is chosen per request via the required `provider` field. Strip
    # it before forwarding upstream.
    provider_name = body.pop("provider", None)
    # The gateway holds no provider keys — the caller's own key for whichever
    # provider this call targets travels in this header and is never stored.
    provider = get_provider(provider_name, provider_key) if provider_name else None

    if provider is None:
        error_payload = {
            "error": {
                "message": (
                    f"Missing or unsupported 'provider'. "
                    f"Supported providers: {', '.join(supported_providers())}."
                    if not provider_name
                    else f"Unknown or unsupported provider '{provider_name}'. "
                    f"Supported providers: {', '.join(supported_providers())}."
                ),
                "type": "invalid_request_error",
                "code": "provider_not_supported",
            }
        }
        _log(log_id, client_ip, provider_name, body, error_payload, 400, start, created_at, endpoint=endpoint)
        return JSONResponse(status_code=400, content=error_payload)

    if body.get("stream"):
        error_payload = {
            "error": {
                "message": "Streaming responses are not supported by this gateway yet.",
                "type": "invalid_request_error",
                "code": "stream_not_supported",
            }
        }
        _log(log_id, client_ip, provider_name, body, error_payload, 400, start, created_at, endpoint=endpoint)
        return JSONResponse(status_code=400, content=error_payload)

    return body, provider, provider_name


def _with_cost(
    response_payload: Dict[str, Any], cost: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Return the payload with the cost block attached, as a copy.

    A copy, not an in-place update: the original dict was already handed to the
    audit sink, which serializes it on a background thread.
    """
    if cost is None:
        return response_payload
    return {**response_payload, "cost": cost}


def _log(
    log_id: str,
    client_ip: str,
    provider: str,
    request_payload: Dict[str, Any],
    response_payload: Dict[str, Any],
    status_code: int,
    start: float,
    created_at: str,
    request_guardrail: Optional[Dict[str, Any]] = None,
    response_guardrail: Optional[Dict[str, Any]] = None,
    cost: Optional[Dict[str, Any]] = None,
    endpoint: str = "/v1/call_llm",
) -> None:
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    record = {
        "log_id": log_id,
        "client_ip": client_ip,
        "provider": provider,
        "model": request_payload.get("model") if isinstance(request_payload, dict) else None,
        "endpoint": endpoint,
        # request_payload is already sanitized by the interceptor (redacted /
        # masked in place); findings below carry only type/action/counts.
        "request_payload": request_payload,
        "response_payload": response_payload,
        "request_guardrail": request_guardrail,
        "response_guardrail": response_guardrail,
        # Computed from the response's usage block; None when the call returned
        # no usage (errors, blocks) — see cost.calculate_cost.
        "cost": cost,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "created_at": created_at,
    }
    audit_store.append(record)
    audit_sink.send(record)
