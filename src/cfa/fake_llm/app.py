"""OpenAI-compatible HTTP facade over the scripted fake model."""

import asyncio
import hmac
import os
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from cfa.fake_llm.scripted import LATENCY_S, complete

app = FastAPI(title="Fake LLM provider", docs_url=None, redoc_url=None)
_bearer = HTTPBearer()


def _authorised(credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)]) -> None:
    expected = os.environ.get("FAKE_LLM_API_KEY", "")
    if not expected or not hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")


@app.post("/v1/chat/completions", dependencies=[Depends(_authorised)])
async def chat_completions(request: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(LATENCY_S.get(str(request.get("model")), 0.25))
    return complete(request)


@app.get("/v1/models", dependencies=[Depends(_authorised)])
async def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": name, "object": "model"} for name in LATENCY_S]}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
