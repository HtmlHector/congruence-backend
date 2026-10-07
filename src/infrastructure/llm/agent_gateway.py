"""Unified LLM Reasoning Gateway.

Provides structured tool dispatch, multi-provider routing (OpenRouter / OpenAI / Anthropic),
ephemeral prompt caching, and bounded exponential backoff on transient 429/5xx errors.
"""

from __future__ import annotations

import asyncio
import copy
import json
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import openai
import structlog
from openai import AsyncOpenAI

from src.config.settings import settings

logger = structlog.get_logger(__name__)

_MAX_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0


class ProviderError(Exception):
    """A bounded failure from the LLM provider (network, content filter, or malformed tool call)."""


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    default_model: str
    vision_model: str = ""
    tool_choice: str = "auto"
    temperature: float | None = 0.0
    enable_prompt_caching: bool = True


def sort_tools_deterministically(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort tools deterministically by function name to maintain exact byte prefix matching."""
    return sorted(
        tools,
        key=lambda t: (
            (t.get("function", {}).get("name") if isinstance(t.get("function"), dict) else "")
            or t.get("name", "")
            or str(t)
        ),
    )


def prepare_cached_messages_and_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    enable_caching: bool = True,
    is_openrouter: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    """Format messages and tools with prompt caching breakpoints for OpenRouter / Anthropic."""
    if not messages:
        return messages, tools

    prepared_tools = sort_tools_deterministically(tools) if tools else None

    if not enable_caching:
        return messages, prepared_tools

    cached_messages = [copy.deepcopy(m) for m in messages]

    # Apply cache_control to system prompt
    if is_openrouter:
        for m in cached_messages:
            if m.get("role") == "system":
                content = m.get("content")
                if isinstance(content, str) and content.strip():
                    m["content"] = [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                elif isinstance(content, list) and content:
                    last_block = content[-1]
                    if isinstance(last_block, dict):
                        last_block["cache_control"] = {"type": "ephemeral"}
                break

    # Apply cache_control to the last tool definition
    if prepared_tools and is_openrouter:
        prepared_tools = [copy.deepcopy(t) for t in prepared_tools]
        prepared_tools[-1]["cache_control"] = {"type": "ephemeral"}

    return cached_messages, prepared_tools


def strip_json_fence(content: str) -> str:
    """Strip leading/trailing markdown code fences (```json ... ```)."""
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


@lru_cache
def resolve_provider() -> ProviderConfig:
    """Resolve active LLM provider configuration."""
    provider_name = settings.llm.provider.lower().strip()
    openrouter_key = settings.llm.openrouter_api_key.get_secret_value()
    openai_key = settings.llm.openai_api_key.get_secret_value()

    if (provider_name == "openrouter" or not openai_key) and openrouter_key:
        return ProviderConfig(
            base_url=settings.llm.openrouter_base_url,
            api_key=openrouter_key,
            default_model=settings.llm.openrouter_model,
            vision_model=settings.llm.openrouter_vision_model,
            tool_choice="auto",
            temperature=0.0,
            enable_prompt_caching=settings.llm.enable_prompt_caching,
        )

    if openai_key:
        return ProviderConfig(
            base_url="https://api.openai.com/v1",
            api_key=openai_key,
            default_model="gpt-4o",
            vision_model="gpt-4o",
            tool_choice="auto",
            temperature=0.0,
            enable_prompt_caching=False,
        )

    # Fallback default configuration for local testing
    return ProviderConfig(
        base_url=settings.llm.openrouter_base_url,
        api_key=openrouter_key or "mock-key",
        default_model=settings.llm.openrouter_model,
        vision_model=settings.llm.openrouter_vision_model,
        tool_choice="auto",
        temperature=0.0,
        enable_prompt_caching=False,
    )


@lru_cache
def get_client() -> AsyncOpenAI:
    provider = resolve_provider()
    return AsyncOpenAI(
        base_url=provider.base_url,
        api_key=provider.api_key,
        timeout=settings.llm.request_timeout_seconds,
    )


async def create_completion_with_retry(client: AsyncOpenAI, request_kwargs: dict[str, Any]) -> Any:
    """Execute chat completion with bounded retries on 429 and 5xx."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return await client.chat.completions.create(**request_kwargs)
        except openai.RateLimitError:
            if attempt >= _MAX_RETRY_ATTEMPTS:
                raise
        except openai.APIStatusError as exc:
            if attempt >= _MAX_RETRY_ATTEMPTS or exc.status_code < 500:
                raise
        delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
        await asyncio.sleep(delay)


async def request_action(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str | None = None,
) -> tuple[str, dict[str, Any], str]:
    """Execute a single agent reasoning turn, requesting exactly one tool call.

    Returns:
        (tool_name, tool_arguments_dict, tool_call_id)
    """
    provider = resolve_provider()
    client = get_client()
    chosen_model = model or provider.default_model

    cached_messages, cached_tools = prepare_cached_messages_and_tools(
        messages,
        tools,
        enable_caching=provider.enable_prompt_caching,
        is_openrouter="openrouter" in provider.base_url,
    )

    request_kwargs: dict[str, Any] = {
        "model": chosen_model,
        "messages": cached_messages,
        "tools": cached_tools,
        "tool_choice": provider.tool_choice,
    }
    if provider.temperature is not None:
        request_kwargs["temperature"] = provider.temperature

    try:
        response = await create_completion_with_retry(client, request_kwargs)
    except Exception as exc:
        logger.error("llm_action_request_failed", error=str(exc), model=chosen_model)
        raise ProviderError(f"LLM provider error: {exc}") from exc

    choice = response.choices[0]
    message = choice.message

    if not message.tool_calls:
        raise ProviderError("Model produced no tool call")

    first_call = message.tool_calls[0]
    fn_name = first_call.function.name
    try:
        fn_args = json.loads(first_call.function.arguments)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            f"Malformed tool call arguments: {first_call.function.arguments}"
        ) from exc

    return fn_name, fn_args, first_call.id


async def generate_completion(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    temperature: float = 0.7,
) -> str:
    """Generate free-form or structured text completion."""
    provider = resolve_provider()
    client = get_client()
    chosen_model = model or provider.default_model

    request_kwargs: dict[str, Any] = {
        "model": chosen_model,
        "messages": messages,
        "temperature": temperature,
    }

    try:
        response = await create_completion_with_retry(client, request_kwargs)
        return response.choices[0].message.content or ""
    except Exception as exc:
        logger.error("llm_completion_failed", error=str(exc))
        raise ProviderError(f"Completion failed: {exc}") from exc
