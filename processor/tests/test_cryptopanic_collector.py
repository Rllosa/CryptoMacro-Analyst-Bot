"""
Unit tests for CryptoppanicCollector (cryptopanic/collector.py) and cryptopanic/db.py.

6 deterministic test vectors — no live I/O:

  T1  _parse_posts() with 2 valid posts within age window → 2 correct tuples
  T2  _parse_posts() with a post older than max_age_minutes → that post skipped
  T3  _parse_posts() with a post missing title → that post skipped
  T4  _parse_posts() with votes["important"] > 0 → importance == "high"
  T5  insert_news_events() with 0 rows → returns 0, no DB call
  T6  Settings default → cryptopanic_poll_interval_secs == 300
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from cryptopanic.collector import _parse_posts
from cryptopanic.db import insert_news_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _now() -> datetime:
    return datetime(2026, 2, 28, 14, 0, 0, tzinfo=timezone.utc)


def _published(minutes_ago: int, now: datetime) -> str:
    """ISO string for a post published N minutes ago."""
    return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post(title: str, minutes_ago: int, now: datetime, important_votes: int = 0) -> dict:
    return {
        "title": title,
        "url": f"https://cryptopanic.com/news/{title.replace(' ', '-')}/",
        "published_at": _published(minutes_ago, now),
        "kind": "news",
        "votes": {"positive": 10, "negative": 0, "important": important_votes},
        "currencies": [{"code": "BTC"}, {"code": "ETH"}],
        "source": {"domain": "bitcoinmagazine.com"},
    }


# ---------------------------------------------------------------------------
# T1: _parse_posts with 2 valid posts → 2 tuples
# ---------------------------------------------------------------------------


def test_t1_parse_posts_two_valid() -> None:
    """2 valid posts within the age window → 2 correct (source, headline, url, ...) tuples."""
    now = _now()
    results = [
        _post("Bitcoin Breaks 70K", minutes_ago=5, now=now),
        _post("Ethereum ETF Approved", minutes_ago=15, now=now),
    ]
    rows = _parse_posts(results, now, max_age_minutes=30)

    assert len(rows) == 2
    source, headline, url, published_at, currencies, importance = rows[0]
    assert source == "cryptopanic"
    assert headline == "Bitcoin Breaks 70K"
    assert url is not None
    assert published_at.tzinfo is not None
    assert "BTC" in currencies
    assert "ETH" in currencies
    assert importance == "medium"


# ---------------------------------------------------------------------------
# T2: _parse_posts skips posts older than max_age_minutes
# ---------------------------------------------------------------------------


def test_t2_parse_posts_stale_post_skipped() -> None:
    """Post published 45 minutes ago is skipped when max_age_minutes=30."""
    now = _now()
    results = [
        _post("Fresh Post", minutes_ago=10, now=now),
        _post("Stale Post", minutes_ago=45, now=now),  # older than 30m → skip
    ]
    rows = _parse_posts(results, now, max_age_minutes=30)

    assert len(rows) == 1
    assert rows[0][1] == "Fresh Post"


# ---------------------------------------------------------------------------
# T3: _parse_posts skips posts missing title
# ---------------------------------------------------------------------------


def test_t3_parse_posts_missing_title_skipped() -> None:
    """Post with missing title is skipped; valid posts are returned normally."""
    now = _now()
    results = [
        {"title": None, "url": "https://cryptopanic.com/news/1/",
         "published_at": _published(5, now), "votes": {}, "currencies": []},
        _post("Valid Headline", minutes_ago=5, now=now),
    ]
    rows = _parse_posts(results, now, max_age_minutes=30)

    assert len(rows) == 1
    assert rows[0][1] == "Valid Headline"


# ---------------------------------------------------------------------------
# T4: _parse_posts sets importance="high" when important votes > 0
# ---------------------------------------------------------------------------


def test_t4_parse_posts_high_importance() -> None:
    """Post with important_votes > 0 gets importance='high'; others get 'medium'."""
    now = _now()
    results = [
        _post("Normal Post", minutes_ago=5, now=now, important_votes=0),
        _post("Important Post", minutes_ago=5, now=now, important_votes=3),
    ]
    rows = _parse_posts(results, now, max_age_minutes=30)

    assert len(rows) == 2
    importances = {row[1]: row[5] for row in rows}
    assert importances["Normal Post"] == "medium"
    assert importances["Important Post"] == "high"


# ---------------------------------------------------------------------------
# T5: insert_news_events with 0 rows → returns 0, no DB call
# ---------------------------------------------------------------------------


def test_t5_insert_zero_rows_no_db_call() -> None:
    """insert_news_events([]) → returns 0 without touching the DB pool."""
    pool = MagicMock()
    result = _run(insert_news_events(pool, []))
    assert result == 0
    pool.connection.assert_not_called()


# ---------------------------------------------------------------------------
# T6: Settings default → cryptopanic_poll_interval_secs == 300
# ---------------------------------------------------------------------------


def test_t6_settings_default_poll_interval() -> None:
    """cryptopanic_poll_interval_secs defaults to 300 (5-minute news polling)."""
    from config import Settings

    s = Settings(_env_file=None)
    assert s.cryptopanic_poll_interval_secs == 300
