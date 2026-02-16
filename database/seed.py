#!/usr/bin/env python3
"""
Database seed script for CryptoMacro Analyst Bot.

Inserts test fixtures into all database tables for development and testing.

Usage:
    python database/seed.py

Environment variables:
    DATABASE_URL or individual POSTGRES_* variables (see .env.example)
"""

import os
import sys
import json
from datetime import datetime, timedelta
from typing import Any, Dict

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT


def get_database_url() -> str:
    """Get database URL from environment variables."""
    # Try DATABASE_URL first
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return db_url

    # Construct from individual variables
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "cryptomacro")
    user = os.getenv("POSTGRES_USER", "cryptomacro")
    password = os.getenv("POSTGRES_PASSWORD", "cryptomacro_dev_password")

    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def seed_market_candles(conn) -> None:
    """Seed market_candles with sample OHLCV data."""
    print("🌱 Seeding market_candles...")

    symbols = ["BTC", "ETH", "SOL", "HYPE"]
    base_time = datetime.utcnow() - timedelta(hours=1)

    cursor = conn.cursor()

    # Insert 60 1-minute candles for each symbol (last 1 hour)
    for symbol in symbols:
        for i in range(60):
            time = base_time + timedelta(minutes=i)
            # Simulate price movement
            base_price = {"BTC": 42000, "ETH": 2500, "SOL": 100, "HYPE": 20}[symbol]
            price_variance = base_price * 0.001  # 0.1% variance

            open_price = base_price + (i * price_variance * 0.1)
            high_price = open_price + price_variance
            low_price = open_price - price_variance
            close_price = open_price + (price_variance * 0.5)
            volume = 1000000 + (i * 10000)

            cursor.execute("""
                INSERT INTO market_candles
                    (time, symbol, timeframe, open, high, low, close, volume, quote_volume, num_trades)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (time, symbol, timeframe) DO NOTHING
            """, (
                time, symbol, "1m",
                open_price, high_price, low_price, close_price,
                volume, volume * close_price, int(volume / 100)
            ))

    conn.commit()
    cursor.close()
    print(f"   ✅ Inserted {len(symbols) * 60} candles")


def seed_derivatives_metrics(conn) -> None:
    """Seed derivatives_metrics with sample funding and OI data."""
    print("🌱 Seeding derivatives_metrics...")

    symbols = ["BTC", "ETH", "SOL", "HYPE"]
    exchanges = ["binance", "bybit", "okx"]
    base_time = datetime.utcnow() - timedelta(hours=1)

    cursor = conn.cursor()

    # Insert data every 5 minutes for last hour
    for symbol in symbols:
        for exchange in exchanges:
            for i in range(0, 60, 5):
                time = base_time + timedelta(minutes=i)

                cursor.execute("""
                    INSERT INTO derivatives_metrics
                        (time, symbol, exchange, funding_rate, funding_rate_8h, open_interest,
                         open_interest_change_24h, long_short_ratio, long_liquidations_24h,
                         short_liquidations_24h, total_liquidations_24h)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (time, symbol, exchange) DO NOTHING
                """, (
                    time, symbol, exchange,
                    0.01 + (i * 0.001),  # funding_rate
                    0.08 + (i * 0.008),  # funding_rate_8h
                    100000000 + (i * 1000000),  # open_interest
                    5.5,  # open_interest_change_24h
                    1.2,  # long_short_ratio
                    500000,  # long_liquidations_24h
                    300000,  # short_liquidations_24h
                    800000,  # total_liquidations_24h
                ))

    conn.commit()
    cursor.close()
    print(f"   ✅ Inserted {len(symbols) * len(exchanges) * 12} derivatives metrics")


def seed_macro_data(conn) -> None:
    """Seed macro_data with sample macro indicators."""
    print("🌱 Seeding macro_data...")

    indicators = [
        ("DXY", 104.5, "yahoo"),
        ("SPX", 4500.0, "yahoo"),
        ("VIX", 15.2, "yahoo"),
        ("US10Y", 4.25, "fred"),
        ("EFFR", 5.33, "fred"),
    ]
    base_time = datetime.utcnow() - timedelta(days=7)

    cursor = conn.cursor()

    # Insert daily data for last 7 days
    for indicator, base_value, source in indicators:
        for i in range(7):
            time = base_time + timedelta(days=i)
            value = base_value + (i * 0.1)  # Simulate small changes

            cursor.execute("""
                INSERT INTO macro_data
                    (time, indicator, value, source, metadata)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (time, indicator, source) DO NOTHING
            """, (
                time, indicator, value, source,
                json.dumps({"unit": "index" if indicator != "VIX" else "volatility"})
            ))

    conn.commit()
    cursor.close()
    print(f"   ✅ Inserted {len(indicators) * 7} macro data points")


def seed_onchain_tables(conn) -> None:
    """Seed on-chain tables with sample exchange flows and features (BTC, ETH only)."""
    print("🌱 Seeding onchain_exchange_flows...")

    symbols = ["BTC", "ETH"]  # Only BTC and ETH per SCOPE.md
    exchanges = ["binance", "coinbase", "kraken"]
    base_time = datetime.utcnow() - timedelta(hours=24)

    cursor = conn.cursor()

    # Insert hourly data for last 24 hours
    for symbol in symbols:
        for exchange in exchanges:
            for i in range(24):
                time = base_time + timedelta(hours=i)
                price = 42000 if symbol == "BTC" else 2500

                cursor.execute("""
                    INSERT INTO onchain_exchange_flows
                        (time, symbol, exchange, inflow, outflow, netflow,
                         inflow_usd, outflow_usd, netflow_usd, entity_confidence, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (time, symbol, exchange, source) DO NOTHING
                """, (
                    time, symbol, exchange,
                    100 + i,  # inflow (coins)
                    80 + i,   # outflow (coins)
                    20,       # netflow (coins)
                    (100 + i) * price,  # inflow_usd
                    (80 + i) * price,   # outflow_usd
                    20 * price,         # netflow_usd
                    0.95,     # entity_confidence
                    "glassnode"
                ))

    conn.commit()
    print(f"   ✅ Inserted {len(symbols) * len(exchanges) * 24} exchange flows")

    print("🌱 Seeding onchain_features...")

    # Insert sample on-chain features
    feature_names = ["netflow_7d_ma", "exchange_balance_change", "whale_ratio"]
    for symbol in symbols:
        for feature_name in feature_names:
            for i in range(24):
                time = base_time + timedelta(hours=i)

                cursor.execute("""
                    INSERT INTO onchain_features
                        (time, symbol, feature_name, value, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (time, symbol, feature_name) DO NOTHING
                """, (
                    time, symbol, feature_name,
                    100.5 + i,
                    json.dumps({"window": "7d", "source": "glassnode"})
                ))

    conn.commit()
    cursor.close()
    print(f"   ✅ Inserted {len(symbols) * len(feature_names) * 24} on-chain features")


def seed_feature_tables(conn) -> None:
    """Seed computed_features and cross_features with sample data."""
    print("🌱 Seeding computed_features...")

    symbols = ["BTC", "ETH", "SOL", "HYPE"]
    feature_names = ["rsi_14", "atr_20", "bollinger_upper", "bollinger_lower", "macd"]
    base_time = datetime.utcnow() - timedelta(hours=1)

    cursor = conn.cursor()

    # Insert features every 5 minutes for last hour
    for symbol in symbols:
        for feature_name in feature_names:
            for i in range(0, 60, 5):
                time = base_time + timedelta(minutes=i)

                cursor.execute("""
                    INSERT INTO computed_features
                        (time, symbol, feature_name, value, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (time, symbol, feature_name) DO NOTHING
                """, (
                    time, symbol, feature_name,
                    50.0 + i,
                    json.dumps({"period": 14 if "14" in feature_name else 20})
                ))

    conn.commit()
    print(f"   ✅ Inserted {len(symbols) * len(feature_names) * 12} computed features")

    print("🌱 Seeding cross_features...")

    # Insert cross-asset features
    cross_feature_names = [
        ("btc_eth_corr_30d", ["BTC", "ETH"]),
        ("btc_spx_corr_90d", ["BTC", "SPX"]),
        ("eth_sol_corr_30d", ["ETH", "SOL"]),
    ]

    for feature_name, assets in cross_feature_names:
        for i in range(0, 60, 5):
            time = base_time + timedelta(minutes=i)

            cursor.execute("""
                INSERT INTO cross_features
                    (time, feature_name, value, assets_involved, metadata)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (time, feature_name) DO NOTHING
            """, (
                time, feature_name,
                0.7 + (i * 0.001),  # correlation value
                assets,
                json.dumps({"window": "30d" if "30d" in feature_name else "90d"})
            ))

    conn.commit()
    cursor.close()
    print(f"   ✅ Inserted {len(cross_feature_names) * 12} cross features")


def seed_regime_state(conn) -> None:
    """Seed regime_state with sample regime classifications."""
    print("🌱 Seeding regime_state...")

    regimes = ["RISK_ON_TREND", "CHOP_RANGE", "VOL_EXPANSION"]
    base_time = datetime.utcnow() - timedelta(hours=1)

    cursor = conn.cursor()

    # Insert regime states every 5 minutes
    for i, regime_idx in enumerate(range(0, 60, 20)):
        time = base_time + timedelta(minutes=regime_idx)
        regime = regimes[i % len(regimes)]

        cursor.execute("""
            INSERT INTO regime_state
                (time, regime, confidence, contributing_factors, previous_regime, regime_duration_minutes)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (time) DO NOTHING
        """, (
            time, regime, 0.85,
            json.dumps({
                "volatility": "high" if regime == "VOL_EXPANSION" else "normal",
                "trend": "up" if regime == "RISK_ON_TREND" else "ranging",
            }),
            regimes[(i - 1) % len(regimes)] if i > 0 else None,
            20
        ))

    conn.commit()
    cursor.close()
    print(f"   ✅ Inserted 3 regime states")


def seed_alerts(conn) -> None:
    """Seed alerts table with sample alerts."""
    print("🌱 Seeding alerts...")

    alert_types = [
        ("VOL_EXPANSION", "HIGH", "BTC"),
        ("BREAKOUT", "MEDIUM", "ETH"),
        ("REGIME_SHIFT", "HIGH", None),
    ]
    base_time = datetime.utcnow() - timedelta(hours=1)

    cursor = conn.cursor()

    for i, (alert_type, severity, symbol) in enumerate(alert_types):
        time = base_time + timedelta(minutes=i * 20)

        cursor.execute("""
            INSERT INTO alerts
                (time, alert_type, severity, symbol, title, description,
                 trigger_conditions, context, regime_at_trigger)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            time, alert_type, severity, symbol,
            f"{alert_type.replace('_', ' ').title()} Alert",
            f"Sample {alert_type} alert triggered at {time}",
            json.dumps({"threshold": 2.5, "actual": 3.2}),
            json.dumps({"volume_spike": True, "price_breakout": False}),
            "VOL_EXPANSION"
        ))

    conn.commit()
    cursor.close()
    print(f"   ✅ Inserted {len(alert_types)} alerts")


def seed_analysis_reports(conn) -> None:
    """Seed analysis_reports with sample LLM reports."""
    print("🌱 Seeding analysis_reports...")

    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO analysis_reports
            (created_at, report_type, title, content, regime_context, model_used, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        datetime.utcnow(),
        "daily_brief",
        "Daily Market Brief - Test",
        "# Daily Market Brief\n\n## Summary\nThis is a test daily brief.\n\n## Key Observations\n- BTC trending up\n- ETH consolidating\n",
        json.dumps({"regime": "RISK_ON_TREND", "confidence": 0.85}),
        "claude-sonnet-4.5",
        json.dumps({"tokens": 250, "duration_ms": 1500})
    ))

    conn.commit()
    cursor.close()
    print(f"   ✅ Inserted 1 analysis report")


def verify_seed_data(conn) -> None:
    """Verify that data was inserted into all tables."""
    print("\n📊 Verifying seed data:")

    tables = [
        "market_candles",
        "derivatives_metrics",
        "macro_data",
        "onchain_exchange_flows",
        "onchain_features",
        "computed_features",
        "cross_features",
        "regime_state",
        "alerts",
        "analysis_reports",
    ]

    cursor = conn.cursor()

    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        status = "✅" if count > 0 else "❌"
        print(f"   {status} {table}: {count} rows")

    cursor.close()


def main():
    """Run all seed operations."""
    print("🚀 CryptoMacro Database Seed Script")
    print("=" * 60)

    # Get database connection
    db_url = get_database_url()
    print(f"📡 Connecting to database...")

    try:
        conn = psycopg2.connect(db_url)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        print(f"✅ Connected to database\n")
    except Exception as e:
        print(f"❌ Failed to connect to database: {e}")
        sys.exit(1)

    # Seed all tables
    try:
        seed_market_candles(conn)
        seed_derivatives_metrics(conn)
        seed_macro_data(conn)
        seed_onchain_tables(conn)
        seed_feature_tables(conn)
        seed_regime_state(conn)
        seed_alerts(conn)
        seed_analysis_reports(conn)

        # Verify
        verify_seed_data(conn)

    except Exception as e:
        print(f"\n❌ Seeding failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()

    print("\n" + "=" * 60)
    print("✅ All seed data inserted successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
