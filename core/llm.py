from __future__ import annotations

import os
import re
import asyncio
from typing import Any, AsyncGenerator

import openai
from dotenv import load_dotenv

load_dotenv()


class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str = "claude-4.6-sonnet",
    ) -> None:
        self.model = model
        self._client = openai.AsyncOpenAI(
            base_url=base_url or os.getenv("LLM_GATEWAY_BASE_URL"),
            api_key=api_key or os.getenv("LLM_GATEWAY_API_KEY"),
        )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 16000,
        max_retries: int = 3,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        for attempt in range(max_retries):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except openai.RateLimitError as e:
                wait = 20
                match = re.search(r"retry after (\d+)", str(e), re.IGNORECASE)
                if match:
                    wait = int(match.group(1)) + 2
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait)
                    continue
                raise

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 16000,
        max_retries: int = 3,
    ) -> AsyncGenerator:
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        for attempt in range(max_retries):
            try:
                stream = await self._client.chat.completions.create(**kwargs)
                async for chunk in stream:
                    yield chunk
                return
            except openai.RateLimitError as e:
                wait = 20
                match = re.search(r"retry after (\d+)", str(e), re.IGNORECASE)
                if match:
                    wait = int(match.group(1)) + 2
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait)
                    continue
                raise
