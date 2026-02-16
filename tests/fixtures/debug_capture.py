#!/usr/bin/env python3
"""Debug version to test message reception."""

import asyncio
import json
from datetime import datetime, timedelta, UTC
import websockets

WS_BASE_URL = "wss://fstream.binance.com/stream"
SYMBOLS = ["btcusdt", "ethusdt"]
TIMEFRAMES = ["1m"]
CAPTURE_DURATION = 120  # 2 minutes for testing

async def debug_capture():
    """Debug capture to see what's happening."""

    # Build stream list
    streams = [f"{s}@kline_{tf}" for s in SYMBOLS for tf in TIMEFRAMES]
    stream_names = "/".join(streams)
    ws_url = f"{WS_BASE_URL}?streams={stream_names}"

    print(f"URL: {ws_url}")
    print()

    start_time = datetime.now(UTC)
    end_time = start_time + timedelta(seconds=CAPTURE_DURATION)

    print(f"Start time: {start_time}")
    print(f"End time: {end_time}")
    print(f"Duration: {CAPTURE_DURATION} seconds")
    print()

    message_count = 0

    async with websockets.connect(ws_url) as ws:
        print("✓ Connected!")
        print(f"Will capture until {end_time.strftime('%H:%M:%S')} UTC")
        print()

        while datetime.now(UTC) < end_time:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                data = json.loads(message)

                message_count += 1

                if message_count <= 3:
                    print(f"Message {message_count}:")
                    print(f"  Keys: {data.keys()}")
                    if "stream" in data:
                        print(f"  Stream: {data['stream']}")
                    if "data" in data:
                        print(f"  Data keys: {data['data'].keys()}")
                    print()

                if message_count % 10 == 0:
                    elapsed = (datetime.now(UTC) - start_time).total_seconds()
                    remaining = (end_time - datetime.now(UTC)).total_seconds()
                    print(f"[{elapsed:.0f}s] Received {message_count} messages | {remaining:.0f}s remaining")

            except asyncio.TimeoutError:
                print("⚠️  Timeout (5s), continuing...")

        print()
        print(f"✓ Capture complete! Total messages: {message_count}")

if __name__ == "__main__":
    asyncio.run(debug_capture())
