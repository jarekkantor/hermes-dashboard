"""Transparent Anthropic API proxy with usage logging."""

from __future__ import annotations

import json
import time
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import ANTHROPIC_API_BASE
from .database import log_request

router = APIRouter()

FORWARD_HEADERS = {
    "x-api-key",
    "anthropic-version",
    "anthropic-beta",
    "content-type",
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


def _extract_usage(event: dict, usage_data: dict) -> None:
    etype = event.get("type", "")
    if etype == "message_start":
        u = event.get("message", {}).get("usage", {})
        usage_data["input_tokens"] = u.get("input_tokens", 0)
        usage_data["cache_creation_input_tokens"] = u.get("cache_creation_input_tokens", 0)
        usage_data["cache_read_input_tokens"] = u.get("cache_read_input_tokens", 0)
    elif etype == "message_delta":
        u = event.get("usage", {})
        if "output_tokens" in u:
            usage_data["output_tokens"] = u["output_tokens"]


@router.api_route("/v1/{path:path}", methods=["POST", "GET", "PUT", "DELETE"])
async def proxy_anthropic(request: Request, path: str):
    start = time.monotonic()
    target_url = f"{ANTHROPIC_API_BASE}/v1/{path}"
    body = await request.body()
    headers = _filter_headers(dict(request.headers))
    client = await get_client()

    is_messages = "messages" in path
    is_streaming = False
    model = "unknown"

    if is_messages and body:
        try:
            payload = json.loads(body)
            model = payload.get("model", "unknown")
            is_streaming = payload.get("stream", False)
        except (json.JSONDecodeError, AttributeError):
            pass

    if is_streaming and is_messages:
        return await _handle_streaming(client, target_url, headers, body, model, start)
    else:
        return await _handle_non_streaming(
            client, request.method, target_url, headers, body, model, is_messages, start
        )


async def _handle_non_streaming(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict,
    body: bytes,
    model: str,
    is_messages: bool,
    start: float,
) -> JSONResponse:
    try:
        resp = await client.request(method, url, headers=headers, content=body)
    except httpx.HTTPError as e:
        latency = int((time.monotonic() - start) * 1000)
        if is_messages:
            await log_request(
                model=model, input_tokens=0, output_tokens=0,
                latency_ms=latency, status_code=502,
                error_type=type(e).__name__,
            )
        return JSONResponse(
            {"error": {"type": "proxy_error", "message": str(e)}},
            status_code=502,
        )

    latency = int((time.monotonic() - start) * 1000)

    if is_messages:
        try:
            data = resp.json()
            usage = data.get("usage", {})
            await log_request(
                model=data.get("model", model),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
                cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                latency_ms=latency,
                status_code=resp.status_code,
                is_streaming=False,
                stop_reason=data.get("stop_reason"),
                error_type=data.get("error", {}).get("type") if resp.status_code >= 400 else None,
            )
        except Exception:
            await log_request(
                model=model, input_tokens=0, output_tokens=0,
                latency_ms=latency, status_code=resp.status_code,
                error_type="parse_error",
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
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
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
                    while b"\n\n" in parse_buf:
                        event_block, parse_buf = parse_buf.split(b"\n\n", 1)
                        for raw_line in event_block.split(b"\n"):
                            line = raw_line.decode("utf-8", errors="replace").strip()
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                continue
                            try:
                                event = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            _extract_usage(event, usage_data)
                            etype = event.get("type", "")
                            if etype == "message_start":
                                actual_model = event.get("message", {}).get("model", model)
                            elif etype == "message_delta":
                                sr = event.get("delta", {}).get("stop_reason")
                                if sr:
                                    stop_reason = sr
                            elif etype == "error":
                                error_type = event.get("error", {}).get("type", "stream_error")

        except httpx.HTTPError as e:
            status_code = 502
            error_type = type(e).__name__
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'proxy_error', 'message': str(e)}})}\n\n".encode()
        finally:
            latency = int((time.monotonic() - start) * 1000)
            await log_request(
                model=actual_model,
                input_tokens=usage_data["input_tokens"],
                output_tokens=usage_data["output_tokens"],
                cache_creation_tokens=usage_data["cache_creation_input_tokens"],
                cache_read_tokens=usage_data["cache_read_input_tokens"],
                latency_ms=latency,
                status_code=status_code,
                is_streaming=True,
                stop_reason=stop_reason,
                error_type=error_type,
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
