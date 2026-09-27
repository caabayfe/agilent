"""Provider safety refusals.

A model provider's own safety layer (Azure Prompt Shields, Bedrock Guardrails, ...)
can block a request with an error instead of an answer. Which deployments have which
filters is a property of the deployment, not of the model, so a provider swap can
change it. The gateway normalises every vendor's block to one error class
(``ContentPolicyViolationError``) and never fails it over (``content_policy_fallbacks``
in ``gateway/profiles``); the agent turns it into a safe, audited refusal instead of a
crashed run. This module is the only place that knows the error's shape.
"""

import openai
from langchain_core.messages import AIMessage

REFUSED = "provider_refused"
REFUSAL_ANSWER = (
    "I can't help with that request. I can only help with your own company account's orders, "
    "invoices, installed instruments and troubleshooting."
)


def is_provider_refusal(exc: BaseException) -> bool:
    if not isinstance(exc, openai.BadRequestError):
        return False
    return exc.code == "content_filter" or "ContentPolicyViolationError" in str(exc)


def refusal_message(alias: str) -> AIMessage:
    return AIMessage(REFUSAL_ANSWER, response_metadata={"model_name": f"{alias} ({REFUSED})"})
