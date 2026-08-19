"""Guardrail engine: interceptor (request) and processor (response).

Applies the configured :mod:`policy` to message text — redacting, masking, or
blocking PII and denylisted terms. The same engine runs on the request before
it is forwarded upstream and on the response before it is returned to the
caller. Findings are reported as type/action/counts only — never raw PII.
"""

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import detectors
from .policy import ALLOW, BLOCK, MASK, REDACT

# Decision, most-severe wins.
D_ALLOW = "allow"
D_MASK = "mask"
D_REDACT = "redact"
D_BLOCK = "block"


@dataclass
class GuardrailResult:
    decision: str
    findings: List[Dict[str, Any]] = field(default_factory=list)
    blocked: bool = False
    block_reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "findings": self.findings,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
        }


class GuardrailBlocked(Exception):
    """Raised when a request must be blocked before reaching the provider."""

    def __init__(self, result: GuardrailResult):
        super().__init__(result.block_reason or "Request blocked by guardrails")
        self.result = result


def _mask(value: str) -> str:
    stripped = value.strip()
    if len(stripped) <= 4:
        return "*" * len(stripped)
    return "*" * (len(stripped) - 4) + stripped[-4:]


def _enabled_types(policy: Dict[str, Any]) -> List[str]:
    return [t for t, rule in policy["pii"].items() if rule.get("enabled")]


class _Accumulator:
    """Collects findings and the running worst decision across many texts."""

    def __init__(self, policy: Dict[str, Any]):
        self.policy = policy
        self.enabled = _enabled_types(policy)
        # (type, action) -> count, insertion-ordered for stable output.
        self.counts: "OrderedDict[Tuple[str, str], int]" = OrderedDict()
        self.blocked = False
        self.block_reason: Optional[str] = None

    def transform(self, text: str) -> str:
        if not text:
            return text
        matches = detectors.detect(text, self.enabled)
        # Apply right-to-left so earlier match offsets stay valid.
        for m in sorted(matches, key=lambda x: x.start, reverse=True):
            action = self.policy["pii"][m.type]["action"]
            self.counts[(m.type, action)] = self.counts.get((m.type, action), 0) + 1
            if action == BLOCK:
                self.blocked = True
                if not self.block_reason:
                    self.block_reason = f"Blocked: request contains {m.type} content."
                # Redact the span too, so the raw value never reaches the audit
                # log even though the whole request is being rejected.
                text = text[: m.start] + f"[REDACTED_{m.type.upper()}]" + text[m.end :]
            elif action == REDACT:
                text = text[: m.start] + f"[REDACTED_{m.type.upper()}]" + text[m.end :]
            elif action == MASK:
                text = text[: m.start] + _mask(m.value) + text[m.end :]
            # ALLOW: leave as-is
        return text

    def check_denylist(self, text: str) -> None:
        if not text or self.blocked:
            return
        for entry in self.policy.get("denylist", []):
            term = entry.get("term", "")
            if not term:
                continue
            hit = (
                re.search(term, text, re.IGNORECASE)
                if entry.get("is_regex")
                else term.lower() in text.lower()
            )
            if hit:
                self.counts[("denylist", BLOCK)] = self.counts.get(("denylist", BLOCK), 0) + 1
                self.blocked = True
                self.block_reason = "Blocked: request matched a denylisted term."
                return

    def result(self) -> GuardrailResult:
        findings = [
            {"type": t, "action": a, "count": c} for (t, a), c in self.counts.items()
        ]
        if self.blocked:
            decision = D_BLOCK
        elif any(a == REDACT for (_t, a) in self.counts):
            decision = D_REDACT
        elif any(a == MASK for (_t, a) in self.counts):
            decision = D_MASK
        else:
            decision = D_ALLOW
        return GuardrailResult(
            decision=decision,
            findings=findings,
            blocked=self.blocked,
            block_reason=self.block_reason,
        )


# --------------------------------------------------------------------------- #
# Request interceptor
# --------------------------------------------------------------------------- #

def run_guardrails(body: Dict[str, Any], policy: Dict[str, Any]) -> GuardrailResult:
    """Scan and sanitize the request payload in place. Raises GuardrailBlocked
    if the request must not be forwarded."""
    if not policy.get("apply_to_request", True):
        return GuardrailResult(decision=D_ALLOW)

    acc = _Accumulator(policy)
    for msg in body.get("messages") or []:
        acc.check_denylist(_collect_text(msg.get("content")))
        msg["content"] = _transform_content(msg.get("content"), acc)

    result = acc.result()
    if result.blocked:
        raise GuardrailBlocked(result)
    return result


# --------------------------------------------------------------------------- #
# Response processor
# --------------------------------------------------------------------------- #

def process_response(
    response_payload: Dict[str, Any], policy: Dict[str, Any]
) -> GuardrailResult:
    """Scan and sanitize the provider's response in place. Never raises — a
    block-action finding replaces the offending message content with a notice."""
    if not policy.get("apply_to_response", True):
        return GuardrailResult(decision=D_ALLOW)
    if not isinstance(response_payload, dict) or "choices" not in response_payload:
        return GuardrailResult(decision=D_ALLOW)

    acc = _Accumulator(policy)
    for choice in response_payload.get("choices") or []:
        message = choice.get("message") or {}
        if isinstance(message.get("content"), str):
            acc.check_denylist(message["content"])
            message["content"] = acc.transform(message["content"])
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            if isinstance(fn.get("arguments"), str):
                fn["arguments"] = acc.transform(fn["arguments"])

    result = acc.result()
    if result.blocked:
        # We already spent the upstream call; withhold the content instead.
        for choice in response_payload.get("choices") or []:
            msg = choice.get("message") or {}
            msg["content"] = "[BLOCKED BY GUARDRAILS] The response was withheld due to policy."
            msg.pop("tool_calls", None)
            choice["finish_reason"] = "content_filter"
    return result


# --------------------------------------------------------------------------- #
# Responses API (input/output shape)
# --------------------------------------------------------------------------- #

def run_guardrails_responses(
    body: Dict[str, Any], policy: Dict[str, Any]
) -> GuardrailResult:
    """Request interceptor for the OpenAI Responses API shape.

    Walks ``instructions`` and ``input`` (a string, or a list of message /
    ``function_call_output`` items) instead of ``messages``. Sanitizes in place;
    raises :class:`GuardrailBlocked` if the request must not be forwarded."""
    if not policy.get("apply_to_request", True):
        return GuardrailResult(decision=D_ALLOW)

    acc = _Accumulator(policy)

    if isinstance(body.get("instructions"), str):
        acc.check_denylist(body["instructions"])
        body["instructions"] = acc.transform(body["instructions"])

    inp = body.get("input")
    if isinstance(inp, str):
        acc.check_denylist(inp)
        body["input"] = acc.transform(inp)
    elif isinstance(inp, list):
        for item in inp:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call_output":
                if isinstance(item.get("output"), str):
                    acc.check_denylist(item["output"])
                    item["output"] = acc.transform(item["output"])
            elif "content" in item:  # message item ({role, content} or {type:"message", ...})
                acc.check_denylist(_collect_text(item.get("content")))
                item["content"] = _transform_content(item.get("content"), acc)

    result = acc.result()
    if result.blocked:
        raise GuardrailBlocked(result)
    return result


def process_responses_response(
    response_payload: Dict[str, Any], policy: Dict[str, Any]
) -> GuardrailResult:
    """Response processor for the Responses API shape. Walks ``output`` items
    (message text parts and ``function_call`` arguments). Never raises — a block
    replaces the whole ``output`` with a withheld-content notice."""
    if not policy.get("apply_to_response", True):
        return GuardrailResult(decision=D_ALLOW)
    if not isinstance(response_payload, dict) or not isinstance(response_payload.get("output"), list):
        return GuardrailResult(decision=D_ALLOW)

    acc = _Accumulator(policy)
    for item in response_payload["output"]:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    acc.check_denylist(part["text"])
                    part["text"] = acc.transform(part["text"])
        elif item.get("type") == "function_call":
            if isinstance(item.get("arguments"), str):
                item["arguments"] = acc.transform(item["arguments"])

    result = acc.result()
    if result.blocked:
        notice = "[BLOCKED BY GUARDRAILS] The response was withheld due to policy."
        response_payload["output"] = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": notice, "annotations": []}],
            }
        ]
        if "output_text" in response_payload:
            response_payload["output_text"] = notice
    return result


# --------------------------------------------------------------------------- #
# Content walking helpers (content may be a string or a list of parts)
# --------------------------------------------------------------------------- #

def _transform_content(content: Any, acc: _Accumulator) -> Any:
    if isinstance(content, str):
        return acc.transform(content)
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    part["text"] = acc.transform(part["text"])
                elif isinstance(part.get("content"), str):  # e.g. tool_result parts
                    part["content"] = acc.transform(part["content"])
        return content
    return content


def _collect_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part.get("content"), str):
                    parts.append(part["content"])
        return " ".join(parts)
    return ""
