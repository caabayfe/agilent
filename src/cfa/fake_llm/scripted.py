"""Deterministic scripted "model" used when no real provider is configured.

It speaks just enough of the OpenAI chat-completions protocol (tool calls and
json_schema structured output) to exercise every seam of the system offline.
Three personalities are exposed as models:

* ``fake-fast``      - quick, accurate.
* ``fake-reasoning`` - accurate but slow (makes the latency gate fail on purpose).
* ``fake-weak``      - fast but subtly wrong (a "cheaper provider" for eval mutant M4).

This is test infrastructure, not an agent: it never sees data it was not given.
"""

import json
import re
import uuid
from decimal import Decimal
from typing import Any

LATENCY_S = {"fake-fast": 0.25, "fake-reasoning": 2.4, "fake-weak": 0.25}

_ORDER = re.compile(r"\bSO-\d{5}\b")
_INVOICE = re.compile(r"\bINV-\d{4}\b")
_SERIAL = re.compile(r"\bHX-[A-Z]{2}-\d{4}\b")
_STEP = re.compile(r"\d\)\s[^.]*\.")
_ESCALATE = re.compile(r"[^.]*service case[^.]*\.", re.IGNORECASE)
# The scripted model has no identity (by design), so it recognises third-party
# requests by the seeded names and generic phrasing.
_THIRD_PARTY = re.compile(
    r"\b(alice|bob|carol|chen|okafor|diaz|acme|novagen|someone else|colleague"
    r"|(another|other) (customer|user|company|person|people|account)s?)\b",
    re.IGNORECASE,
)
SCOPE_NOTE = (
    "I can only access your own company account, not another person's or company's data. "
    "Here is what your account shows:"
)


def classify(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ("error", "warning", "how do i", "reset", "not working", "fault", "fluctuat")):
        return "troubleshooting"
    if any(k in t for k in ("invoice", "owe", "bill", "pay", "unpaid", "balance")):
        return "billing"
    if any(k in t for k in ("service", "warranty", "maintenance", "visit")):
        return "service"
    if any(k in t for k in ("order", "deliver", "tracking", "arrive", "ship")):
        return "orders"
    return "other"


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _choose_tool(question: str, tools: set[str]) -> tuple[str, dict[str, Any]] | None:
    intent = classify(question)
    lower = question.lower()
    choice: tuple[str, dict[str, Any]] | None = None
    if intent == "troubleshooting":
        choice = ("search_troubleshooting", {"query": question})
    elif intent == "billing":
        invoice = _INVOICE.search(question)
        unpaid = any(k in lower for k in ("owe", "unpaid", "open", "outstanding"))
        choice = (
            ("get_invoice", {"invoice_id": invoice.group()}) if invoice else ("list_invoices", {"unpaid_only": unpaid})
        )
    elif intent == "service":
        serial = _SERIAL.search(question)
        choice = ("get_service_history", {"serial": serial.group()} if serial else {})
    elif intent == "orders":
        order = _ORDER.search(question)
        open_only = any(k in lower for k in ("open", "still", "pending"))
        choice = ("get_order", {"order_id": order.group()}) if order else ("list_orders", {"open_only": open_only})
    if choice and choice[0] in tools:
        return choice
    return None


class _Style:
    def __init__(self, sloppy: bool, weak: bool) -> None:
        self.sloppy = sloppy
        self.weak = weak

    def money(self, raw: str) -> str:
        value = Decimal(raw)
        if self.weak:
            value += Decimal("12.40")
        if self.sloppy:
            return f"about EUR {int(round(value, -2)):,}"
        return f"EUR {value:,.2f}"

    def status(self, raw: str) -> str:
        if self.weak and raw == "backordered":
            raw = "shipped"
        return raw.replace("_", " ")


def _describe(tool: str, data: dict[str, Any], s: _Style) -> str:  # noqa: PLR0911 - one branch per tool template
    if "error" in data:
        return f"I'm sorry, I couldn't retrieve that. {data['error']}"
    if tool == "get_order":
        lines = [
            f"Order {data['order_id']} (placed {data['placed_on']}) is {s.status(data['status'])}, "
            f"total {s.money(data['total'])}."
        ]
        for line in data["lines"]:
            detail = f"- {line['sku']} {line['description']} x{line['quantity']}: {s.status(line['status'])}"
            if line.get("tracking_number"):
                detail += f", tracking {line['tracking_number']}"
            if line.get("eta"):
                detail += f", expected {line['eta']}"
            lines.append(detail)
        return "\n".join(lines)
    if tool == "list_orders":
        items = [
            f"- {o['order_id']}: {s.status(o['status'])} (placed {o['placed_on']}), total {s.money(o['total'])}"
            for o in data["orders"]
        ]
        return "Here are your orders:\n" + "\n".join(items) if items else "You have no matching orders."
    if tool in ("get_invoice", "list_invoices"):
        invoices = [data] if tool == "get_invoice" else data["invoices"]
        items = [
            f"Invoice {i['invoice_id']} (order {i['order_id']}): total {s.money(i['total'])}, "
            f"paid {s.money(i['amount_paid'])}, outstanding {s.money(i['outstanding'])}, "
            f"status {s.status(i['status'])}, due {i['due_on']}."
            for i in invoices
        ]
        return "\n".join(items) if items else "You have no matching invoices."
    if tool == "get_service_history":
        out = []
        for inst in data["instruments"]:
            out.append(
                f"{inst['name']} {inst['serial']}: installed {inst['installed_on']}, "
                f"under warranty until {inst['warranty_until']}."
            )
            out.extend(
                f"- {v['visit_date']} {v['visit_id']} ({v['visit_type'].replace('_', ' ')}): {v['summary']}"
                for v in inst["visits"]
            )
        return "\n".join(out) if out else "No instruments found."
    if tool == "search_troubleshooting":
        if not data["articles"]:
            return "I couldn't find a matching article. I recommend opening a service case."
        article = data["articles"][0]
        steps = " ".join(_STEP.findall(article["body"]))
        answer = f"According to {article['kb_id']} ({article['title']}): {steps}"
        if article["escalate"]:
            escalation = _ESCALATE.search(article["body"])
            answer += " " + (escalation.group().strip() if escalation else "If it persists, open a service case.")
        return answer
    return json.dumps(data)


def complete(request: dict[str, Any]) -> dict[str, Any]:
    model = str(request.get("model", "fake-fast"))
    messages: list[dict[str, Any]] = request.get("messages", [])
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    question = _text(messages[last_user]["content"]) if last_user >= 0 else ""
    system = " ".join(_text(m.get("content")) for m in messages if m.get("role") == "system")
    style = _Style(sloppy="approximately" in system.lower(), weak=model == "fake-weak")

    content: str | None = None
    tool_calls: list[dict[str, Any]] = []
    if request.get("response_format"):
        content = json.dumps({"intent": classify(question)})
    else:
        tool_names = {t["function"]["name"] for t in request.get("tools") or []}
        results = [m for m in messages[last_user + 1 :] if m.get("role") == "tool"]
        if results:
            called = {
                call["id"]: call["function"]["name"]
                for m in messages[last_user + 1 :]
                for call in m.get("tool_calls") or []
            }
            content = "\n".join(
                _describe(called.get(r.get("tool_call_id", ""), ""), json.loads(_text(r["content"])), style)
                for r in results
            )
            if _THIRD_PARTY.search(question) and not content.startswith("I'm sorry"):
                content = f"{SCOPE_NOTE}\n{content}"
        elif choice := _choose_tool(question, tool_names):
            tool_calls = [
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {"name": choice[0], "arguments": json.dumps(choice[1])},
                }
            ]
        else:
            content = (
                "I can help with your orders and shipments, invoices and balances, installed instruments "
                "and service history, and troubleshooting. What would you like to know?"
            )

    prompt_tokens = len(json.dumps(messages)) // 4
    completion_tokens = len(content or json.dumps(tool_calls)) // 4
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    **({"tool_calls": tool_calls} if tool_calls else {}),
                },
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
