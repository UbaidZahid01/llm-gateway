import copy

import pytest

from src.guardrails import (
    GuardrailBlocked,
    process_response,
    process_responses_response,
    run_guardrails,
    run_guardrails_responses,
)
from src.policy import DEFAULT_POLICY


def default_policy():
    return copy.deepcopy(DEFAULT_POLICY)


def test_email_redacted_ip_masked():
    body = {
        "messages": [
            {"role": "user", "content": "email jane@acme.com ip 10.0.0.9"},
        ]
    }
    res = run_guardrails(body, default_policy())
    assert res.decision == "redact"
    content = body["messages"][0]["content"]
    assert "[REDACTED_EMAIL]" in content
    assert "jane@acme.com" not in content
    assert "10.0.0.9" not in content  # masked


def test_credit_card_blocks_and_redacts_in_place():
    body = {"messages": [{"role": "user", "content": "card 4111 1111 1111 1111"}]}
    with pytest.raises(GuardrailBlocked) as exc:
        run_guardrails(body, default_policy())
    assert exc.value.result.decision == "block"
    # Even though blocked, the raw PAN must not remain in the payload.
    assert "4111" not in body["messages"][0]["content"]


def test_denylist_blocks():
    policy = default_policy()
    policy["denylist"] = [{"term": "top secret", "is_regex": False}]
    body = {"messages": [{"role": "user", "content": "this is TOP SECRET"}]}
    with pytest.raises(GuardrailBlocked):
        run_guardrails(body, policy)


def test_multipart_content_transformed():
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "reach me at x@y.com"}]},
        ]
    }
    run_guardrails(body, default_policy())
    assert "[REDACTED_EMAIL]" in body["messages"][0]["content"][0]["text"]


def test_apply_to_request_disabled_is_noop():
    policy = default_policy()
    policy["apply_to_request"] = False
    body = {"messages": [{"role": "user", "content": "card 4111 1111 1111 1111"}]}
    res = run_guardrails(body, policy)  # must not raise
    assert res.decision == "allow"
    assert body["messages"][0]["content"] == "card 4111 1111 1111 1111"


def test_response_redacts_email():
    resp = {"choices": [{"message": {"role": "assistant", "content": "mail bob@x.io"}, "finish_reason": "stop"}]}
    res = process_response(resp, default_policy())
    assert res.decision == "redact"
    assert "bob@x.io" not in resp["choices"][0]["message"]["content"]


def test_response_block_withholds_content():
    resp = {"choices": [{"message": {"role": "assistant", "content": "ssn 123-45-6789"}, "finish_reason": "stop"}]}
    res = process_response(resp, default_policy())
    assert res.decision == "block"
    assert resp["choices"][0]["message"]["content"].startswith("[BLOCKED BY GUARDRAILS]")
    assert resp["choices"][0]["finish_reason"] == "content_filter"


# --------------------------------------------------------------------------- #
# Responses API (input/output) shape
# --------------------------------------------------------------------------- #

def test_responses_string_input_redacted():
    body = {"instructions": "be terse", "input": "email jane@acme.com"}
    res = run_guardrails_responses(body, default_policy())
    assert res.decision == "redact"
    assert "jane@acme.com" not in body["input"]


def test_responses_list_input_message_and_tool_output_transformed():
    body = {
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "reach me at x@y.com"}]},
            {"type": "function_call_output", "call_id": "c1", "output": "sent to a@b.com"},
        ]
    }
    run_guardrails_responses(body, default_policy())
    assert "[REDACTED_EMAIL]" in body["input"][0]["content"][0]["text"]
    assert "a@b.com" not in body["input"][1]["output"]


def test_responses_input_blocks_on_credit_card():
    body = {"input": "card 4111 1111 1111 1111"}
    with pytest.raises(GuardrailBlocked):
        run_guardrails_responses(body, default_policy())
    assert "4111" not in body["input"]


def test_responses_output_message_redacted():
    resp = {
        "output": [
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "mail bob@x.io", "annotations": []}]},
        ]
    }
    res = process_responses_response(resp, default_policy())
    assert res.decision == "redact"
    assert "bob@x.io" not in resp["output"][0]["content"][0]["text"]


def test_responses_output_block_withholds_content():
    resp = {
        "output": [
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "ssn 123-45-6789", "annotations": []}]},
        ]
    }
    res = process_responses_response(resp, default_policy())
    assert res.decision == "block"
    assert resp["output"][0]["content"][0]["text"].startswith("[BLOCKED BY GUARDRAILS]")
