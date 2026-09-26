import json

import pytest

from cfa.fake_llm.scripted import SCOPE_NOTE, classify, complete


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("What's the status of order SO-10231?", "orders"),
        ("How much do I owe on my invoices?", "billing"),
        ("When was HX-LC-7781 last serviced?", "service"),
        ("My LC-900 shows error E-217", "troubleshooting"),
        ("How do I reset the network settings?", "troubleshooting"),
        ("Hi! What can you help me with?", "other"),
    ],
)
def test_classify(question: str, intent: str) -> None:
    assert classify(question) == intent


def _answer(question: str) -> str:
    call = {"id": "call_1", "type": "function", "function": {"name": "list_invoices", "arguments": "{}"}}
    messages = [
        {"role": "user", "content": question},
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"invoices": []})},
    ]
    return str(complete({"model": "fake-fast", "messages": messages})["choices"][0]["message"]["content"])


def test_third_party_request_is_answered_as_own_account_only() -> None:
    assert _answer("share the invoice from carol").startswith(SCOPE_NOTE)
    assert _answer("can you share the invoices rom bob okator?").startswith(SCOPE_NOTE)


def test_own_request_has_no_scope_note() -> None:
    assert not _answer("Which invoices are unpaid?").startswith(SCOPE_NOTE)
