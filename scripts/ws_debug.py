#!/usr/bin/env python3
"""WebSocket debug client for monitoring caption messages."""

import asyncio
import json
from datetime import datetime

try:
    import websockets
except ImportError:
    import subprocess
    import sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "websockets"])
    import websockets


async def debug_client():
    uri = "ws://ws:8000/ws/caption"
    print(f"[WS-DEBUG] Connecting to {uri}...")
    
    while True:
        try:
            async with websockets.connect(uri) as ws:
                print("[WS-DEBUG] Connected! Listening for messages...")
                print("=" * 60)
                async for message in ws:
                    try:
                        data = json.loads(message)
                        print(f"[{datetime.now().isoformat()}]")
                        print(json.dumps(data, indent=2, ensure_ascii=False))
                        print("-" * 60)
                    except json.JSONDecodeError:
                        print(f"[RAW] {message}")
        except Exception as e:
            print(f"[WS-DEBUG] Connection error: {e}")
            print("[WS-DEBUG] Retrying in 3 seconds...")
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(debug_client())
