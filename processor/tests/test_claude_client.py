"""
Tests for LLM-2: ClaudeClient (SOLO-56)

All tests are pure unit tests — no real API calls.
Anthropic SDK is mocked at the module level via unittest.mock.patch.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from llm.client import ClaudeClient, MODEL_DAILY
from llm.prompts import daily_brief, event_inflow, event_liq, event_macro, weekly_deep

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEXT = "This is Claude's response."


def _make_response(text: str = _TEXT) -> MagicMock:
    """Build a minimal anthropic Message-like object."""
    block = SimpleNamespace(text=text)
    return SimpleNamespace(content=[block])


def _make_client(mock_create: AsyncMock) -> ClaudeClient:
    """Return a ClaudeClient whose underlying SDK create is replaced by mock_create."""
    client = ClaudeClient(api_key="test-key", timeout=5.0, max_retries=3)
    client._client.messages.create = mock_create
    return client


# ---------------------------------------------------------------------------
# T1 — Successful call returns text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t1_success_returns_text() -> None:
    """Single successful API call → complete() returns the text string."""
    mock_create = AsyncMock(return_value=_make_response())
    client = _make_client(mock_create)

    result = await client.complete("Hello, Claude.")

    assert result == _TEXT
    assert mock_create.call_count == 1


# ---------------------------------------------------------------------------
# T2 — 429 rate limit then success (2 total calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_rate_limit_retry_then_success() -> None:
    """First call raises RateLimitError, second succeeds → 2 calls, result returned."""
    rate_limit_exc = anthropic.RateLimitError(
        message="rate limited",
        response=MagicMock(status_code=429, headers={}),
        body={},
    )
    mock_create = AsyncMock(side_effect=[rate_limit_exc, _make_response()])

    with patch("llm.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        client = _make_client(mock_create)
        result = await client.complete("prompt")

    assert result == _TEXT
    assert mock_create.call_count == 2
    mock_sleep.assert_awaited_once_with(1)  # 2**0 = 1s backoff on attempt 0


# ---------------------------------------------------------------------------
# T3 — 500 server error exhausted (4 total calls: 1 initial + 3 retries)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_server_error_exhausted_raises() -> None:
    """All 4 attempts raise APIStatusError(500) → exception propagated, 4 calls made."""
    server_err = anthropic.APIStatusError(
        message="server error",
        response=MagicMock(status_code=500, headers={}),
        body={},
    )
    mock_create = AsyncMock(side_effect=[server_err, server_err, server_err, server_err])

    with patch("llm.client.asyncio.sleep", new_callable=AsyncMock):
        client = _make_client(mock_create)
        with pytest.raises(anthropic.APIStatusError):
            await client.complete("prompt")

    assert mock_create.call_count == 4  # 1 initial + 3 retries


# ---------------------------------------------------------------------------
# T4 — Timeout then success (2 total calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_timeout_retry_then_success() -> None:
    """First call times out, second succeeds → 2 calls, result returned."""
    mock_create = AsyncMock(side_effect=[asyncio.TimeoutError(), _make_response()])

    with patch("llm.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        client = _make_client(mock_create)
        result = await client.complete("prompt")

    assert result == _TEXT
    assert mock_create.call_count == 2
    mock_sleep.assert_awaited_once_with(1)  # 2**0 = 1s


# ---------------------------------------------------------------------------
# T5 — Auth error propagates immediately (no retry, exactly 1 call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t5_auth_error_no_retry() -> None:
    """AuthenticationError (401) → propagated immediately, exactly 1 SDK call."""
    auth_err = anthropic.AuthenticationError(
        message="invalid api key",
        response=MagicMock(status_code=401, headers={}),
        body={},
    )
    mock_create = AsyncMock(side_effect=auth_err)

    with patch("llm.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        client = _make_client(mock_create)
        with pytest.raises(anthropic.AuthenticationError):
            await client.complete("prompt")

    assert mock_create.call_count == 1
    mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# T6 — All prompt builders return non-empty strings (graceful with empty context)
# ---------------------------------------------------------------------------


def test_t6_all_prompt_builders_produce_strings() -> None:
    """Every build() function returns a non-empty str even with an empty context dict."""
    modules = [daily_brief, event_liq, event_inflow, event_macro, weekly_deep]
    for mod in modules:
        result = mod.build({})
        assert isinstance(result, str), f"{mod.__name__}.build() did not return str"
        assert len(result) > 0, f"{mod.__name__}.build() returned empty string"

    # Also verify SYSTEM constant is a non-empty string on each module
    for mod in modules:
        assert isinstance(mod.SYSTEM, str)
        assert len(mod.SYSTEM) > 0, f"{mod.__name__}.SYSTEM is empty"
