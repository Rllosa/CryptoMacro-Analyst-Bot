import pytest
from src.routing import AlertRouter


@pytest.fixture
def router():
    return AlertRouter()


def test_vol_expansion_high(router):
    assert router.get_channels("VOL_EXPANSION", "HIGH") == ["alerts_all", "alerts_high"]


def test_vol_expansion_medium(router):
    assert router.get_channels("VOL_EXPANSION", "MEDIUM") == ["alerts_all"]


def test_regime_shift_high(router):
    assert router.get_channels("REGIME_SHIFT", "HIGH") == ["alerts_all", "alerts_high", "regime_shifts"]


def test_regime_shift_medium(router):
    assert router.get_channels("REGIME_SHIFT", "MEDIUM") == ["alerts_all", "regime_shifts"]


def test_exchange_inflow_risk_high(router):
    assert router.get_channels("EXCHANGE_INFLOW_RISK", "HIGH") == ["alerts_all", "alerts_high", "onchain"]


def test_netflow_shift_medium(router):
    assert router.get_channels("NETFLOW_SHIFT", "MEDIUM") == ["alerts_all", "onchain"]


def test_leadership_rotation_medium(router):
    assert router.get_channels("LEADERSHIP_ROTATION", "MEDIUM") == ["alerts_all"]
