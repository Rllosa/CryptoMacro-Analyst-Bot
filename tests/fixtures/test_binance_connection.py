#!/usr/bin/env python3
"""Quick test to verify Binance WebSocket connection."""

import asyncio
import json
import websockets

async def test_connection():
    """Test connection to Binance Futures WebSocket."""

    # Try simple connection first
    url = "wss://fstream.binance.com/ws/btcusdt@kline_1m"

    print(f"Testing connection to: {url}")
    print("Attempting to connect...")

    try:
        async with websockets.connect(url) as ws:
            print("✓ Connected successfully!")
            print("Waiting for first message...")

            # Wait for one message
            message = await asyncio.wait_for(ws.recv(), timeout=10.0)
            data = json.loads(message)

            print("✓ Received message!")
            print(f"Stream: {data.get('stream', 'N/A')}")
            print(f"Event type: {data.get('e', data.get('data', {}).get('e', 'N/A'))}")
            print(f"Symbol: {data.get('s', data.get('data', {}).get('s', 'N/A'))}")
            print()
            print("First message content:")
            print(json.dumps(data, indent=2)[:500])

    except asyncio.TimeoutError:
        print("✗ Timeout waiting for message")
    except Exception as e:
        print(f"✗ Error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(test_connection())
