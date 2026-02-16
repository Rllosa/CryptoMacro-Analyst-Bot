#!/usr/bin/env python3
"""
Binance Kline Data Capture Script

Connects to Binance Futures WebSocket and records 1 hour of kline data
for BTC, ETH, SOL, and HYPE (1m, 5m, 1h timeframes).

Output: JSONL files under tests/fixtures/binance/
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Set

try:
    import websockets
except ImportError:
    print("ERROR: websockets library not installed")
    print("Install with: pip install websockets")
    sys.exit(1)

# Binance Futures WebSocket endpoint
WS_URL = "wss://fstream.binance.com/ws"

# Symbols to capture
SYMBOLS = ["btcusdt", "ethusdt", "solusdt", "hypeusdt"]

# Timeframes to capture
TIMEFRAMES = ["1m", "5m", "1h"]

# Capture duration (in seconds)
CAPTURE_DURATION = 3600  # 1 hour

# Output directory
OUTPUT_DIR = Path(__file__).parent / "binance"


async def capture_klines():
    """Connect to Binance WebSocket and capture kline data."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build stream subscription list
    streams = []
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            streams.append(f"{symbol}@kline_{tf}")

    stream_names = "/".join(streams)
    ws_url = f"{WS_URL}/{stream_names}"

    print(f"Connecting to Binance WebSocket...")
    print(f"Symbols: {', '.join(s.upper() for s in SYMBOLS)}")
    print(f"Timeframes: {', '.join(TIMEFRAMES)}")
    print(f"Capture duration: {CAPTURE_DURATION // 60} minutes")
    print(f"Output directory: {OUTPUT_DIR}")
    print()

    # Track message counts
    message_counts: dict[str, int] = {f"{s}_{tf}": 0 for s in SYMBOLS for tf in TIMEFRAMES}
    start_time = datetime.utcnow()
    end_time = start_time + timedelta(seconds=CAPTURE_DURATION)

    # Open output files (one per symbol-timeframe combination)
    output_files = {}
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            filename = OUTPUT_DIR / f"{symbol}_{tf}.jsonl"
            output_files[f"{symbol}_{tf}"] = open(filename, "w")

    try:
        async with websockets.connect(ws_url) as ws:
            print(f"✓ Connected to Binance WebSocket")
            print(f"Capturing data until {end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC...")
            print()

            while datetime.utcnow() < end_time:
                try:
                    # Receive message with timeout
                    message = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    data = json.loads(message)

                    # Extract stream name and kline data
                    if "stream" in data and "data" in data:
                        stream = data["stream"]
                        kline_data = data["data"]

                        # Parse stream name (format: btcusdt@kline_1m)
                        if "@kline_" in stream:
                            symbol = stream.split("@")[0]
                            tf = stream.split("_")[1]
                            key = f"{symbol}_{tf}"

                            # Write to corresponding file
                            if key in output_files:
                                output_files[key].write(json.dumps(kline_data) + "\n")
                                output_files[key].flush()  # Ensure data is written
                                message_counts[key] += 1

                                # Print progress every 10 messages per stream
                                if message_counts[key] % 10 == 0:
                                    elapsed = (datetime.utcnow() - start_time).total_seconds()
                                    remaining = (end_time - datetime.utcnow()).total_seconds()
                                    print(f"[{elapsed:.0f}s] {symbol.upper()} {tf}: {message_counts[key]} messages | {remaining/60:.1f} min remaining")

                except asyncio.TimeoutError:
                    print(f"⚠️  No message received for 10 seconds, continuing...")
                    continue
                except json.JSONDecodeError as e:
                    print(f"⚠️  Failed to parse message: {e}")
                    continue

            print()
            print("✓ Capture complete!")
            print()
            print("Summary:")
            total_messages = 0
            for key, count in sorted(message_counts.items()):
                print(f"  {key}: {count} messages")
                total_messages += count
            print(f"  Total: {total_messages} messages")

    except Exception as e:
        print(f"✗ Error during capture: {e}")
        raise
    finally:
        # Close all output files
        for f in output_files.values():
            f.close()
        print()
        print(f"Output files written to: {OUTPUT_DIR}")


if __name__ == "__main__":
    try:
        asyncio.run(capture_klines())
    except KeyboardInterrupt:
        print()
        print("⚠️  Capture interrupted by user")
        sys.exit(1)
