"""
LLM-2: Claude Client (SOLO-56)

Async wrapper around the Anthropic SDK with exponential-backoff retry on
transient failures (429, 5xx, timeout). Auth/bad-request errors propagate
immediately without retry.

No run loop — pure callable module. LLM output is a plain string; this module
has no imports from alerts/ or regime/ (Rule 1.2).
"""

from __future__ import annotations

import asyncio
from typing import Any

import anthropic
import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Module-level model name constants (not rebuilt per call)
# ---------------------------------------------------------------------------

MODEL_DAILY = "claude-sonnet-4-6"
MODEL_WEEKLY = "claude-opus-4-6"
MODEL_EVENT = "claude-sonnet-4-6"

# HTTP status codes that warrant a retry
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503})


class ClaudeClient:
    """
    Async Claude API client with retry/backoff.

    Usage:
        client = ClaudeClient(api_key="sk-ant-...")
        text = await client.complete(prompt, model=MODEL_DAILY)

    Thread safety: safe for concurrent calls — AsyncAnthropic is thread-safe.
    """

    def __init__(
        self,
        api_key: str,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        # Disable the SDK's own retry so our loop is the sole retry mechanism.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)

    async def complete(
        self,
        prompt: str,
        *,
        model: str = MODEL_DAILY,
        max_tokens: int = 2048,
        system: str | None = None,
    ) -> str:
        """
        Send a user prompt to Claude and return the text response.

        Retries on: 429 (rate limit), 5xx (server error), asyncio.TimeoutError.
        Does NOT retry on: 4xx auth/bad-request errors.
        Raises the last exception after max_retries exhausted.
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        for attempt in range(self._max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self._client.messages.create(**kwargs),
                    timeout=self._timeout,
                )
                return response.content[0].text  # type: ignore[union-attr]

            except anthropic.RateLimitError as exc:
                if attempt >= self._max_retries:
                    log.warning(
                        "claude_client.rate_limit_exhausted",
                        model=model,
                        attempts=attempt + 1,
                    )
                    raise
                log.warning(
                    "claude_client.rate_limit_retry",
                    model=model,
                    attempt=attempt + 1,
                    backoff_secs=2**attempt,
                    error=str(exc),
                )
                await asyncio.sleep(2**attempt)

            except anthropic.APIStatusError as exc:
                if exc.status_code not in _RETRYABLE_STATUS or attempt >= self._max_retries:
                    log.warning(
                        "claude_client.api_error",
                        model=model,
                        status_code=exc.status_code,
                        attempts=attempt + 1,
                        error=str(exc),
                    )
                    raise
                log.warning(
                    "claude_client.api_error_retry",
                    model=model,
                    status_code=exc.status_code,
                    attempt=attempt + 1,
                    backoff_secs=2**attempt,
                    error=str(exc),
                )
                await asyncio.sleep(2**attempt)

            except asyncio.TimeoutError:
                if attempt >= self._max_retries:
                    log.warning(
                        "claude_client.timeout_exhausted",
                        model=model,
                        timeout_secs=self._timeout,
                        attempts=attempt + 1,
                    )
                    raise
                log.warning(
                    "claude_client.timeout_retry",
                    model=model,
                    timeout_secs=self._timeout,
                    attempt=attempt + 1,
                    backoff_secs=2**attempt,
                )
                await asyncio.sleep(2**attempt)

        # Unreachable — loop always raises or returns, but satisfies type checker.
        raise RuntimeError("complete() exited retry loop without returning")  # pragma: no cover
