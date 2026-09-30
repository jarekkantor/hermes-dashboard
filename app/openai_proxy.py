"""OpenAI-compatible API proxy with usage logging."""

from __future__ import annotations

import json
import time
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import get_api_base
from .database import log_request

router = APIRouter()

FORWARD_HEADERS = {
    "authorization",
    "content-type",
    "openai-organization",
    "openai-project",
}

_client: httpx.AsyncClient | None = None


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30))
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


def _filter_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() in FORWARD_HEADERS}


def _extract_usage_non_streaming(data: dict) -> dict:
    """Extract token usage from an OpenAI non-streaming response."""
    usage = data.get("usage", {}) or {}
    result = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
    }
    # OpenAI may include cached tokens in prompt_tokens_details
    details = usage.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        result["cache_read_tokens"] = details.get("cached_tokens", 0)
    return result


@router.api_route("/v1/chat/completions", methods=["POST"])
async def proxy_openai_chat(request: Request):
    '''Proxy POST /v1/chat/completions to configured API endpoint.'''
    start = time.monotonic()
    target_url = f"{get_api_base()}/v1/chat/completions"
    body = await request.body()
    headers = _filter_headers(dict(request.headers))
    client = await get_client()

    model = "unknown"
    is_streaming = False

    if body:
        try:
            payload = json.loads(body)
            model = payload.get("model", "unknown")
            is_streaming = payload.get("stream", False)
        except (json.JSONDecodeError, AttributeError):
            pass

    if is_streaming:
        return await _handle_streaming(client, target_url, headers, body, model, start)
    return await _handle_non_streaming(client, target_url, headers, body, model, start)



async def _handle_non_streaming(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    body: bytes,
    model: str,
    start: float,
) -> JSONResponse:
    try:
        resp = await client.request("POST", url, headers=headers, content=body)
    except httpx.HTTPError as e:
        latency = int((time.monotonic() - start) * 1000)
        await log_request(
            model=model, input_tokens=0, output_tokens=0,
            latency_ms=latency, status_code=502,
            error_type=type(e).__name__, provider="openai",
        )
        return JSONResponse(
            {"error": {"type": "proxy_error", "message": str(e)}},
            status_code=502,
        )

    latency = int((time.monotonic() - start) * 1000)

    try:
        data = resp.json()
        usage = _extract_usage_non_streaming(data)
        await log_request(
            model=data.get("model", model),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_creation_tokens=usage["cache_creation_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            latency_ms=latency,
            status_code=resp.status_code,
            is_streaming=False,
            stop_reason=_get_stop_reason(data),
            error_type=data.get("error", {}).get("type") if resp.status_code >= 400 else None,
            provider="openai",
        )
    except Exception:
        await log_request(
            model=model, input_tokens=0, output_tokens=0,
            latency_ms=latency, status_code=resp.status_code,
            error_type="parse_error", provider="openai",
        )

    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
    }
    return JSONResponse(
        content=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
        status_code=resp.status_code,
        headers=resp_headers,
    )


def _get_stop_reason(data: dict) -> str | None:
    """Extract the first finish_reason from an OpenAI response."""
    choices = data.get("choices") or []
    for c in choices:
        reason = c.get("finish_reason")
        if reason:
            return reason
    return None


async def _handle_streaming(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    body: bytes,
    model: str,
    start: float,
) -> StreamingResponse:
    usage_data: dict = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
    }
    stop_reason = None
    actual_model = model
    status_code = 200
    error_type = None

    async def stream_with_logging() -> AsyncIterator[bytes]:
        nonlocal usage_data, stop_reason, actual_model, status_code, error_type
        parse_buf = b""
        try:
            async with client.stream("POST", url, headers=headers, content=body) as resp:
                status_code = resp.status_code
                async for chunk in resp.aiter_bytes():
                    yield chunk
                    parse_buf += chunk
                    # OpenAI streaming: lines prefixed with "data: "
                    while b"\n" in parse_buf:
                        line, parse_buf = parse_buf.split(b"\n", 1)
                        raw = line.decode("utf-8", errors="replace").strip()
                        if not raw.startswith("data: "):
                            continue
                        data_str = raw[6:].strip()
                        if data_str == "[DONE]":
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        # Extract model from first chunk
                        event_model = event.get("model")
                        if event_model and actual_model == "unknown":
                            actual_model = event_model

                        # Extract usage from the final chunk (non-streaming summary)
                        usage = event.get("usage")
                        if usage:
                            usage_data["input_tokens"] = usage.get("prompt_tokens", 0)
                            usage_data["output_tokens"] = usage.get("completion_tokens", 0)
                            details = usage.get("prompt_tokens_details") or {}
                            if isinstance(details, dict):
                                usage_data["cache_read_tokens"] = details.get("cached_tokens", 0)

                        # Extract finish_reason from choices
                        choices = event.get("choices") or []
                        for c in choices:
                            fr = c.get("finish_reason")
                            if fr:
                                stop_reason = fr

                        # Extract model from first chunk (stream_options: include_usage)
                        if not actual_model or actual_model == "unknown":
                            event_model = event.get("model")
                            if event_model:
                                actual_model = event_model

        except httpx.HTTPError as e:
            status_code = 502
            error_type = type(e).__name__
            yield f"data: {json.dumps({'error': {'type': 'proxy_error', 'message': str(e)}})}\n\n".encode()
        finally:
            latency = int((time.monotonic() - start) * 1000)
            await log_request(
                model=actual_model,
                input_tokens=usage_data["input_tokens"],
                output_tokens=usage_data["output_tokens"],
                cache_creation_tokens=usage_data["cache_creation_tokens"],
                cache_read_tokens=usage_data["cache_read_tokens"],
                latency_ms=latency,
                status_code=status_code,
                is_streaming=True,
                stop_reason=stop_reason,
                error_type=error_type,
                provider="openai",
            )

    return StreamingResponse(
        stream_with_logging(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Re-export for lifespan management from main
__all__ = ["router", "close_client"]
