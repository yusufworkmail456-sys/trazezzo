"""LLM client — reuse 9router from Knowledge Center config."""

from __future__ import annotations

import os
import json
import logging
from typing import AsyncGenerator

import httpx

from trazezzo.config import LLM_BASE_URL, LLM_MODEL, LLM_ENV_KEY_NAME, LLM_ENV_PATH

log = logging.getLogger("trazezzo.llm")

_api_key: str | None = None


def _get_api_key() -> str:
    global _api_key
    if _api_key is not None:
        return _api_key
    # Read from Hermes .env
    try:
        with open(LLM_ENV_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{LLM_ENV_KEY_NAME}=") and not line.startswith("#"):
                    _api_key = line.split("=", 1)[1].strip()
                    return _api_key
    except Exception as exc:
        log.error("Cannot read API key from %s: %s", LLM_ENV_PATH, exc)
    return _api_key or os.environ.get(LLM_ENV_KEY_NAME, "")


async def chat_completion(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> str:
    """Non-streaming chat completion via 9router."""
    api_key = _get_api_key()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        text = resp.text.strip()
        # 9router returns text/event-stream even for non-streaming
        if "data: [DONE]" in text:
            text = text.split("data: [DONE]")[0].strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(text)
        return data["choices"][0]["message"]["content"]


async def chat_completion_stream(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> AsyncGenerator[str, None]:
    """Streaming chat completion. Yields text chunks."""
    api_key = _get_api_key()
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{LLM_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
