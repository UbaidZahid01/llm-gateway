import json
import time
from typing import Any, Dict, List, Optional, Tuple

from anthropic import Anthropic, APIError, APIStatusError

from .base import LLMProvider, ProviderError

# Anthropic requires max_tokens; OpenAI treats it as optional. Use this when the
# caller didn't specify one.
_DEFAULT_MAX_TOKENS = 4096

# Anthropic stop_reason -> OpenAI finish_reason.
_FINISH_REASON = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}


class AnthropicProvider(LLMProvider):
    """Anthropic (Claude). Translates the OpenAI ``chat.completions`` request
    shape to the Anthropic Messages API and maps the response back, so callers
    stay drop-in against the OpenAI wire format."""

    name = "anthropic"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key
        self._client: Optional[Anthropic] = None

    def _get_client(self) -> Anthropic:
        if not self._api_key:
            raise ProviderError(
                f"No API key provided for provider '{self.name}'. Send the "
                "caller's own key in the 'X-Provider-Key' header.",
                status_code=400,
                error_type="invalid_request_error",
                code="provider_key_missing",
            )
        if self._client is None:
            self._client = Anthropic(api_key=self._api_key)
        return self._client

    def call(self, body: Dict[str, Any]) -> Dict[str, Any]:
        client = self._get_client()
        try:
            params = _to_anthropic_request(body)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any translation bug cleanly
            raise ProviderError(
                f"Could not translate request for Anthropic: {exc}",
                status_code=400,
                error_type="invalid_request_error",
                code="translation_error",
            )

        try:
            message = client.messages.create(**params)
        except APIStatusError as exc:
            payload = _error_message(exc)
            raise ProviderError(
                payload,
                status_code=exc.status_code,
                error_type=exc.__class__.__name__,
                code=getattr(exc, "code", None),
            )
        except APIError as exc:
            raise ProviderError(
                str(exc),
                status_code=502,
                error_type=exc.__class__.__name__,
                code=None,
            )

        return _to_openai_response(message)


def _error_message(exc: APIStatusError) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return str(exc)


def _to_anthropic_request(body: Dict[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}

    model = body.get("model")
    if not model:
        raise ProviderError(
            "Missing required field 'model'.",
            status_code=400,
            error_type="invalid_request_error",
            code="missing_model",
        )
    params["model"] = model

    params["max_tokens"] = (
        body.get("max_tokens") or body.get("max_completion_tokens") or _DEFAULT_MAX_TOKENS
    )

    system, messages = _convert_messages(body.get("messages") or [])
    if system:
        params["system"] = system
    params["messages"] = messages

    if body.get("tools"):
        params["tools"] = _convert_tools(body["tools"])
    tool_choice = _convert_tool_choice(body.get("tool_choice"))
    if tool_choice is not None:
        params["tool_choice"] = tool_choice

    # Sampling params that Anthropic accepts on older models. Passed through as
    # given; Anthropic validates them for the target model.
    for key in ("temperature", "top_p"):
        if body.get(key) is not None:
            params[key] = body[key]
    if body.get("stop") is not None:
        stop = body["stop"]
        params["stop_sequences"] = [stop] if isinstance(stop, str) else stop

    return params


def _convert_messages(
    messages: List[Dict[str, Any]]
) -> Tuple[str, List[Dict[str, Any]]]:
    system_parts: List[str] = []
    out: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            system_parts.append(_flatten_text(content))
            continue

        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id"),
                "content": _flatten_text(content),
            }
            # Merge consecutive tool results into one user message.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue

        if role == "assistant":
            blocks: List[Dict[str, Any]] = []
            text = _flatten_text(content)
            if text:
                blocks.append({"type": "text", "text": text})
            for call in msg.get("tool_calls") or []:
                fn = call.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id"),
                        "name": fn.get("name"),
                        "input": args,
                    }
                )
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            continue

        # user (or anything else) -> user message
        out.append({"role": "user", "content": _convert_user_content(content)})

    return "\n\n".join(p for p in system_parts if p), out


def _convert_user_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return _flatten_text(content)

    blocks: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            blocks.append({"type": "text", "text": str(part)})
            continue
        ptype = part.get("type")
        if ptype == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif ptype == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            blocks.append(_image_block(url))
        else:
            blocks.append({"type": "text", "text": part.get("text", "")})
    return blocks


def _image_block(url: str) -> Dict[str, Any]:
    if url.startswith("data:"):
        # data:<media_type>;base64,<data>
        try:
            header, data = url.split(",", 1)
            media_type = header.split(";")[0][len("data:") :]
        except ValueError:
            media_type, data = "image/png", ""
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


def _flatten_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return str(content)


def _convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        fn = tool.get("function", {})
        out.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _convert_tool_choice(choice: Any) -> Optional[Dict[str, Any]]:
    if choice is None:
        return None
    if choice == "auto":
        return {"type": "auto"}
    if choice == "required":
        return {"type": "any"}
    if choice == "none":
        return None
    if isinstance(choice, dict) and choice.get("type") == "function":
        name = (choice.get("function") or {}).get("name")
        if name:
            return {"type": "tool", "name": name}
    return None


def _to_openai_response(message: Any) -> Dict[str, Any]:
    data = message.model_dump() if hasattr(message, "model_dump") else dict(message)

    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for block in data.get("content") or []:
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id"),
                    "type": "function",
                    "function": {
                        "name": block.get("name"),
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )

    text = "".join(text_parts)
    msg: Dict[str, Any] = {"role": "assistant", "refusal": None}
    if tool_calls:
        msg["content"] = text or None
        msg["tool_calls"] = tool_calls
    else:
        msg["content"] = text

    usage = data.get("usage") or {}
    prompt_tokens = usage.get("input_tokens", 0) or 0
    completion_tokens = usage.get("output_tokens", 0) or 0
    # Cache tokens have no OpenAI-shape equivalent, so they pass through under
    # Anthropic's own names. Kept out of prompt_tokens (which, per Anthropic,
    # already excludes them) so cost calculation doesn't double-count.
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    cache_write = usage.get("cache_creation_input_tokens", 0) or 0

    return {
        "id": data.get("id"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": data.get("model"),
        "choices": [
            {
                "index": 0,
                "message": msg,
                "logprobs": None,
                "finish_reason": _FINISH_REASON.get(data.get("stop_reason"), "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        },
    }
