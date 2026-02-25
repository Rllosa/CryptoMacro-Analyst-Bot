from __future__ import annotations


def compute_funding_zscore(
    current: float,
    mean: float | None,
    std: float | None,
    n_samples: int,
    min_samples: int,
) -> float:
    """
    Compute funding rate z-score vs historical distribution.

    Returns 0.0 when:
    - fewer than min_samples time-buckets are available
    - standard deviation is None or zero (constant history)
    """
    if n_samples < min_samples or std is None or std == 0.0 or mean is None:
        return 0.0
    return (current - mean) / std


def compute_oi_change_pct(
    current_oi: float | None,
    oi_1h_ago: float | None,
) -> float | None:
    """
    Compute percentage change in open interest vs 1 hour ago.

    Returns None when historical OI is unavailable or zero (prevents divide-by-zero).
    """
    if current_oi is None or oi_1h_ago is None or oi_1h_ago == 0.0:
        return None
    return (current_oi - oi_1h_ago) / oi_1h_ago


def compute_oi_drop_1h(
    oi_change_pct: float | None,
    threshold_pct: float,
) -> float:
    """
    Binary flag: 1.0 if OI fell by at least |threshold_pct| in the last hour.

    Returns 0.0 when oi_change_pct is None (insufficient history).
    threshold_pct should be negative (e.g. -0.05 for a 5% drop).
    """
    if oi_change_pct is None:
        return 0.0
    return 1.0 if oi_change_pct <= threshold_pct else 0.0
