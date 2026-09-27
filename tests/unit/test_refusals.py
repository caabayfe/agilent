import httpx2 as httpx
import openai

from cfa.agent.refusals import REFUSAL_ANSWER, is_provider_refusal, refusal_message


def _bad_request(body: dict[str, object]) -> openai.BadRequestError:
    request = httpx.Request("POST", "http://litellm:4000/v1/chat/completions")
    response = httpx.Response(400, json=body, request=request)
    error = body["error"]
    assert isinstance(error, dict)
    return openai.BadRequestError(str(error["message"]), response=response, body=error)


def test_gateway_normalised_content_policy_error_is_a_refusal() -> None:
    exc = _bad_request(
        {"error": {"message": "litellm.BadRequestError: litellm.ContentPolicyViolationError: filtered", "code": "400"}}
    )
    assert is_provider_refusal(exc)


def test_direct_azure_content_filter_is_a_refusal() -> None:
    assert is_provider_refusal(_bad_request({"error": {"message": "filtered", "code": "content_filter"}}))


def test_other_bad_requests_are_not_refusals() -> None:
    assert not is_provider_refusal(_bad_request({"error": {"message": "invalid tool schema", "code": "400"}}))
    assert not is_provider_refusal(ValueError("ContentPolicyViolationError"))


def test_refusal_message_names_the_alias_that_refused() -> None:
    message = refusal_message("assistant-fast")
    assert message.text == REFUSAL_ANSWER
    assert message.response_metadata["model_name"] == "assistant-fast (provider_refused)"
