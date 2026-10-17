#!/usr/bin/env python3
"""Load test to compare parallel vs sequential translation processing.

This script sends requests at a realistic speech rate and measures:
1. Queue size over time
2. Total processing time
3. Final lag accumulation

Usage:
    python load_test_compare.py --workers 6   # Test parallel (current)
    python load_test_compare.py --workers 1   # Test sequential (old behavior)
"""

import argparse
import asyncio
import time

import httpx

TRANSLATOR_URL = "http://localhost:8002"
STATS_URL = f"{TRANSLATOR_URL}/stats"
TRANSLATE_URL = f"{TRANSLATOR_URL}/translate"

# Realistic speech rate: ~1 sentence every 1.5 seconds
SENTENCES_PER_SECOND = 1 / 1.5  # 0.67 sentences/sec
TOTAL_SENTENCES = 60  # Simulate ~1.5 minutes of speech


async def set_workers(client: httpx.AsyncClient, num_workers: int) -> None:
    """Unfortunately we can't change workers at runtime, so this is informational."""
    print(f"\n{'='*60}")
    print(f"NOTE: To test with {num_workers} worker(s), you need to:")
    print("  1. Stop the service: docker compose stop gemini")
    print(f"  2. Set environment: TRANSLATION_WORKERS={num_workers}")
    print("  3. Restart: docker compose up -d gemini")
    print(f"{'='*60}\n")


async def get_stats(client: httpx.AsyncClient) -> dict:
    """Get current queue statistics."""
    try:
        resp = await client.get(STATS_URL)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


async def send_translation(client: httpx.AsyncClient, text: str, ts: float) -> None:
    """Send a translation request (non-blocking, just enqueues)."""
    try:
        await client.post(
            TRANSLATE_URL,
            json={"text": text, "context": [], "is_final": True, "ts": ts},
            timeout=5.0,
        )
    except Exception as e:
        print(f"  Request error: {e}")


async def run_load_test(num_sentences: int, interval: float) -> dict:
    """Run load test and collect metrics."""
    
    async with httpx.AsyncClient() as client:
        # Get initial stats
        initial_stats = await get_stats(client)
        print(f"Initial stats: {initial_stats}")
        
        # Record metrics
        metrics = {
            "start_time": time.time(),
            "queue_sizes": [],
            "timestamps": [],
        }
        
        print(f"\nSending {num_sentences} sentences at {1/interval:.2f} sentences/sec...")
        print(f"(Simulating {num_sentences * interval:.1f} seconds of speech)\n")
        
        for i in range(num_sentences):
            ts = time.time()
            text = f"This is test sentence number {i+1} for load testing the translation system."
            
            # Send request (non-blocking)
            await send_translation(client, text, ts)
            
            # Get stats after each request
            stats = await get_stats(client)
            queue_size = stats.get("queue_size", 0)
            processed = stats.get("processed", 0)
            dropped = stats.get("dropped", 0)
            
            metrics["queue_sizes"].append(queue_size)
            metrics["timestamps"].append(time.time() - metrics["start_time"])
            
            # Progress update every 10 sentences
            if (i + 1) % 10 == 0 or i == 0:
                print(f"  [{i+1:3d}/{num_sentences}] Queue: {queue_size:2d}, Processed: {processed:3d}, Dropped: {dropped}")
            
            # Wait for next sentence (simulate speech rate)
            await asyncio.sleep(interval)
        
        # Wait for queue to drain
        print("\nWaiting for queue to drain...")
        drain_start = time.time()
        while True:
            stats = await get_stats(client)
            queue_size = stats.get("queue_size", 0)
            if queue_size == 0:
                break
            if time.time() - drain_start > 120:  # 2 minute timeout
                print("  Timeout waiting for queue to drain!")
                break
            print(f"  Queue: {queue_size}, waiting...")
            await asyncio.sleep(2)
        
        # Final stats
        final_stats = await get_stats(client)
        metrics["end_time"] = time.time()
        metrics["final_stats"] = final_stats
        metrics["duration"] = metrics["end_time"] - metrics["start_time"]
        metrics["max_queue"] = max(metrics["queue_sizes"]) if metrics["queue_sizes"] else 0
        metrics["avg_queue"] = sum(metrics["queue_sizes"]) / len(metrics["queue_sizes"]) if metrics["queue_sizes"] else 0
        
        return metrics


def print_results(metrics: dict) -> None:
    """Print test results."""
    print("\n" + "="*60)
    print("LOAD TEST RESULTS")
    print("="*60)
    
    final = metrics.get("final_stats", {})
    
    print(f"\nTest Duration: {metrics['duration']:.1f} seconds")
    print(f"Sentences Sent: {TOTAL_SENTENCES}")
    print(f"Speech Rate: {SENTENCES_PER_SECOND:.2f} sentences/sec")
    
    print("\nQueue Metrics:")
    print(f"  Max Queue Size: {metrics['max_queue']}")
    print(f"  Avg Queue Size: {metrics['avg_queue']:.1f}")
    
    print("\nFinal Stats:")
    print(f"  Processed: {final.get('processed', 'N/A')}")
    print(f"  Dropped: {final.get('dropped', 'N/A')}")
    print(f"  Summarized: {final.get('summarized', 'N/A')}")
    print(f"  Current Lag: {final.get('current_lag', 'N/A')}")
    
    # Verdict
    print("\n" + "-"*60)
    if metrics['max_queue'] <= 5:
        print("✅ PASS: Queue stayed low - no lag accumulation")
    elif metrics['max_queue'] <= 20:
        print("⚠️  WARNING: Some queue buildup observed")
    else:
        print("❌ FAIL: Significant queue accumulation - lag will grow over time")
    print("-"*60)


async def main():
    parser = argparse.ArgumentParser(description="Load test for translation service")
    parser.add_argument("--sentences", type=int, default=TOTAL_SENTENCES,
                        help=f"Number of sentences to send (default: {TOTAL_SENTENCES})")
    parser.add_argument("--rate", type=float, default=SENTENCES_PER_SECOND,
                        help=f"Sentences per second (default: {SENTENCES_PER_SECOND:.2f})")
    parser.add_argument("--workers", type=int, default=None,
                        help="Expected number of workers (informational)")
    args = parser.parse_args()
    
    interval = 1.0 / args.rate
    
    print("="*60)
    print("TRANSLATION SERVICE LOAD TEST")
    print("="*60)
    
    if args.workers:
        async with httpx.AsyncClient() as client:
            await set_workers(client, args.workers)
    
    # Check current stats
    async with httpx.AsyncClient() as client:
        stats = await get_stats(client)
        if "error" in stats:
            print(f"ERROR: Cannot connect to translator service: {stats['error']}")
            print("Make sure the service is running: docker compose up -d gemini")
            return
    
    metrics = await run_load_test(args.sentences, interval)
    print_results(metrics)


if __name__ == "__main__":
    asyncio.run(main())
