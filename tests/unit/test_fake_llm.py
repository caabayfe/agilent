import pytest

from cfa.fake_llm.scripted import classify


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
