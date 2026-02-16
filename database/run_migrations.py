#!/usr/bin/env python3
"""
Database migration runner for CryptoMacro Analyst Bot.

Runs numbered SQL migration files in order from database/migrations/.
Migrations are idempotent and can be run multiple times safely.

Usage:
    python database/run_migrations.py

Environment variables:
    DATABASE_URL or individual POSTGRES_* variables (see .env.example)
"""

import os
import sys
from pathlib import Path
from typing import List

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


def get_migration_files(migrations_dir: Path) -> List[Path]:
    """Get all .sql migration files sorted by filename."""
    if not migrations_dir.exists():
        print(f"❌ Migrations directory not found: {migrations_dir}")
        sys.exit(1)

    migrations = sorted(migrations_dir.glob("*.sql"))
    if not migrations:
        print(f"❌ No migration files found in: {migrations_dir}")
        sys.exit(1)

    return migrations


def run_migration(conn, migration_file: Path) -> None:
    """Run a single migration file."""
    print(f"🔄 Running migration: {migration_file.name}")

    try:
        with open(migration_file, "r") as f:
            sql = f.read()

        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        cursor.close()

        print(f"✅ Migration completed: {migration_file.name}")
    except Exception as e:
        print(f"❌ Migration failed: {migration_file.name}")
        print(f"   Error: {e}")
        raise


def verify_hypertables(conn) -> None:
    """Verify that all expected hypertables were created."""
    expected_hypertables = [
        "market_candles",
        "derivatives_metrics",
        "macro_data",
        "onchain_exchange_flows",
        "onchain_features",
        "computed_features",
        "cross_features",
        "regime_state",
        "alerts",
    ]

    cursor = conn.cursor()
    cursor.execute("""
        SELECT hypertable_name
        FROM timescaledb_information.hypertables
        ORDER BY hypertable_name
    """)
    actual_hypertables = [row[0] for row in cursor.fetchall()]
    cursor.close()

    print(f"\n📊 Hypertables verification:")
    for name in expected_hypertables:
        if name in actual_hypertables:
            print(f"   ✅ {name}")
        else:
            print(f"   ❌ {name} (MISSING!)")

    missing = set(expected_hypertables) - set(actual_hypertables)
    if missing:
        print(f"\n❌ Missing hypertables: {', '.join(missing)}")
        sys.exit(1)


def verify_continuous_aggregates(conn) -> None:
    """Verify that continuous aggregates were created."""
    expected_caggs = ["candles_5m", "candles_1h"]

    cursor = conn.cursor()
    cursor.execute("""
        SELECT view_name
        FROM timescaledb_information.continuous_aggregates
        ORDER BY view_name
    """)
    actual_caggs = [row[0] for row in cursor.fetchall()]
    cursor.close()

    print(f"\n📊 Continuous aggregates verification:")
    for name in expected_caggs:
        if name in actual_caggs:
            print(f"   ✅ {name}")
        else:
            print(f"   ❌ {name} (MISSING!)")

    missing = set(expected_caggs) - set(actual_caggs)
    if missing:
        print(f"\n❌ Missing continuous aggregates: {', '.join(missing)}")
        sys.exit(1)


def main():
    """Run all migrations and verify database state."""
    print("🚀 CryptoMacro Database Migration Runner")
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

    # Get migration files
    project_root = Path(__file__).parent.parent
    migrations_dir = project_root / "database" / "migrations"
    migration_files = get_migration_files(migrations_dir)

    print(f"📂 Found {len(migration_files)} migration files\n")

    # Run migrations
    for migration_file in migration_files:
        run_migration(conn, migration_file)

    # Verify database state
    print("\n" + "=" * 60)
    verify_hypertables(conn)
    verify_continuous_aggregates(conn)

    # Clean up
    conn.close()

    print("\n" + "=" * 60)
    print("✅ All migrations completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
