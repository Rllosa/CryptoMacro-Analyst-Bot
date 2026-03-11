"""
Tests for OPS-3: Derivatives Degrade Path

Pure unit tests — no real NATS or network calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ops.degrade import DegradePublisher, STATUS_DEGRADED, STATUS_DOWN, STATUS_HEALTHY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nc() -> MagicMock:
    nc = MagicMock()
    js = AsyncMock()
    js.publish = AsyncMock()
    nc.jetstream = MagicMock(return_value=js)
    return nc


# ---------------------------------------------------------------------------
# T1 — first report publishes unconditionally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t1_first_report_always_publishes() -> None:
    """First status report for a component must always publish."""
    nc = _make_nc()
    publisher = DegradePublisher(nc)

    await publisher.report("coinglass", STATUS_DOWN, "API unreachable")

    nc.jetstream().publish.assert_called_once()
    call_args = nc.jetstream().publish.call_args
    import json
    payload = json.loads(call_args.args[1].decode())
    assert payload["component"] == "coinglass"
    assert payload["status"] == STATUS_DOWN


# ---------------------------------------------------------------------------
# T2 — same status does NOT republish (no transition)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_same_status_no_republish() -> None:
    """Reporting the same status twice must not publish a second time."""
    nc = _make_nc()
    publisher = DegradePublisher(nc)

    await publisher.report("coinglass", STATUS_DOWN, "failure 1")
    await publisher.report("coinglass", STATUS_DOWN, "failure 2")

    assert nc.jetstream().publish.call_count == 1


# ---------------------------------------------------------------------------
# T3 — transition DOWN → HEALTHY publishes recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_recovery_transition_publishes() -> None:
    """DOWN → HEALTHY transition must publish a recovery event."""
    nc = _make_nc()
    publisher = DegradePublisher(nc)

    await publisher.report("coinglass", STATUS_DOWN, "API down")
    await publisher.report("coinglass", STATUS_HEALTHY, "API recovered")

    assert nc.jetstream().publish.call_count == 2
    import json
    last_payload = json.loads(nc.jetstream().publish.call_args.args[1].decode())
    assert last_payload["status"] == STATUS_HEALTHY


# ---------------------------------------------------------------------------
# T4 — independent state per component
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_independent_state_per_component() -> None:
    """Each component tracks state independently."""
    nc = _make_nc()
    publisher = DegradePublisher(nc)

    await publisher.report("coinglass", STATUS_DEGRADED, "slow")
    await publisher.report("derivatives_engine", STATUS_DEGRADED, "no data")
    # Same status again for both — should NOT publish
    await publisher.report("coinglass", STATUS_DEGRADED, "still slow")
    await publisher.report("derivatives_engine", STATUS_DEGRADED, "still no data")

    # Only 2 publishes — one per component's first report
    assert nc.jetstream().publish.call_count == 2


# ---------------------------------------------------------------------------
# T5 — NATS publish failure is swallowed (graceful degradation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t5_publish_failure_swallowed() -> None:
    """NATS publish failure must not propagate — degrade reporting is non-fatal."""
    nc = _make_nc()
    nc.jetstream().publish = AsyncMock(side_effect=Exception("NATS unavailable"))

    publisher = DegradePublisher(nc)

    # Must not raise
    await publisher.report("coinglass", STATUS_DOWN, "API down")
