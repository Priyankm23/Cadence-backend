import asyncio
import io
import time
import wave
import sys
import argparse
import httpx

# Pre-packaged silence WAV generator (16kHz, 16-bit mono, 3 seconds)
def create_mock_wav():
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(16000)
        # 3 seconds of silence = 3 * 16000 frames = 48000 frames (96000 bytes)
        wav_file.writeframes(b'\x00' * 96000)
    return wav_io.getvalue()

async def benchmark_whisper_single(client, groq_key, wav_bytes, request_id):
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {groq_key}"}
    files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
    data = {
        "model": "whisper-large-v3",
        "temperature": "0.0",
        "language": "en"
    }
    
    start_time = time.time()
    try:
        response = await client.post(url, headers=headers, files=files, data=data, timeout=30.0)
        latency = time.time() - start_time
        if response.status_code == 200:
            return {"id": request_id, "success": True, "latency": latency, "status": 200}
        elif response.status_code == 429:
            return {"id": request_id, "success": False, "latency": latency, "status": 429, "error": "Rate Limited"}
        else:
            return {"id": request_id, "success": False, "latency": latency, "status": response.status_code, "error": response.text[:100]}
    except Exception as e:
        latency = time.time() - start_time
        return {"id": request_id, "success": False, "latency": latency, "status": 500, "error": str(e)}

async def benchmark_chat_single(client, groq_key, model, prompt, request_id):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {groq_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a meeting assistant. Summarize the following points."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 512
    }
    
    start_time = time.time()
    try:
        response = await client.post(url, headers=headers, json=payload, timeout=30.0)
        latency = time.time() - start_time
        if response.status_code == 200:
            resp_json = response.json()
            tokens = resp_json.get("usage", {}).get("completion_tokens", 0)
            return {"id": request_id, "success": True, "latency": latency, "status": 200, "tokens": tokens}
        elif response.status_code == 429:
            return {"id": request_id, "success": False, "latency": latency, "status": 429, "error": "Rate Limited"}
        else:
            return {"id": request_id, "success": False, "latency": latency, "status": response.status_code, "error": response.text[:100]}
    except Exception as e:
        latency = time.time() - start_time
        return {"id": request_id, "success": False, "latency": latency, "status": 500, "error": str(e)}

async def run_whisper_benchmark(groq_key, concurrency, total_requests):
    print(f"\n--- Starting Groq Whisper Benchmark ({concurrency} Concurrent, {total_requests} Total Requests) ---")
    wav_bytes = create_mock_wav()
    
    limits = httpx.Limits(max_keepalive_connections=concurrency, max_connections=concurrency * 2)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = []
        sem = asyncio.Semaphore(concurrency)
        
        async def worker(req_id):
            async with sem:
                return await benchmark_whisper_single(client, groq_key, wav_bytes, req_id)
                
        start_time = time.time()
        for i in range(total_requests):
            tasks.append(worker(i))
            
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        
        successes = [r for r in results if r["success"]]
        rate_limits = [r for r in results if r["status"] == 429]
        failures = [r for r in results if not r["success"] and r["status"] != 429]
        latencies = [r["latency"] for r in successes]
        
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        min_latency = min(latencies) if latencies else 0
        max_latency = max(latencies) if latencies else 0
        
        print("\n=== Whisper Benchmark Results ===")
        print(f"Total Test Duration: {total_time:.2f} seconds")
        print(f"Total Requests: {total_requests}")
        print(f"Successful Requests: {len(successes)} ({len(successes)/total_requests*100:.1f}%)")
        print(f"Rate Limited (429): {len(rate_limits)} ({len(rate_limits)/total_requests*100:.1f}%)")
        print(f"Other Failures: {len(failures)} ({len(failures)/total_requests*100:.1f}%)")
        print(f"Throughput: {len(successes)/total_time:.2f} rps")
        if successes:
            print(f"Avg Latency: {avg_latency:.2f}s (Min: {min_latency:.2f}s, Max: {max_latency:.2f}s)")
            
        return {
            "duration": total_time,
            "success_rate": len(successes)/total_requests*100,
            "rate_limit_rate": len(rate_limits)/total_requests*100,
            "avg_latency": avg_latency,
            "throughput": len(successes)/total_time
        }

async def run_chat_benchmark(groq_key, model, concurrency, total_requests):
    print(f"\n--- Starting Groq Chat Completion Benchmark ({model}, {concurrency} Concurrent, {total_requests} Total Requests) ---")
    
    # Mock transcript text to summarize
    mock_transcript = (
        "Priyansh: Welcome everyone. Today we are testing the database direct integration.\n"
        "Mahesh: Yes, the AI worker bypasses HTTP endpoints now which makes it faster.\n"
        "Priyansh: Great. Let's make sure the Celery tasks send email alerts correctly.\n"
        "Mahesh: I will monitor the Redis queue length during testing.\n"
        "Priyansh: Perfect. Meeting adjourned."
    )
    
    limits = httpx.Limits(max_keepalive_connections=concurrency, max_connections=concurrency * 2)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = []
        sem = asyncio.Semaphore(concurrency)
        
        async def worker(req_id):
            async with sem:
                return await benchmark_chat_single(client, groq_key, model, mock_transcript, req_id)
                
        start_time = time.time()
        for i in range(total_requests):
            tasks.append(worker(i))
            
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        
        successes = [r for r in results if r["success"]]
        rate_limits = [r for r in results if r["status"] == 429]
        failures = [r for r in results if not r["success"] and r["status"] != 429]
        latencies = [r["latency"] for r in successes]
        total_tokens = sum([r["tokens"] for r in successes])
        
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        min_latency = min(latencies) if latencies else 0
        max_latency = max(latencies) if latencies else 0
        
        print("\n=== Chat completion Results ===")
        print(f"Total Test Duration: {total_time:.2f} seconds")
        print(f"Total Requests: {total_requests}")
        print(f"Successful Requests: {len(successes)} ({len(successes)/total_requests*100:.1f}%)")
        print(f"Rate Limited (429): {len(rate_limits)} ({len(rate_limits)/total_requests*100:.1f}%)")
        print(f"Other Failures: {len(failures)} ({len(failures)/total_requests*100:.1f}%)")
        print(f"Throughput: {len(successes)/total_time:.2f} rps")
        if successes:
            print(f"Avg Latency: {avg_latency:.2f}s (Min: {min_latency:.2f}s, Max: {max_latency:.2f}s)")
            print(f"Total Completion Tokens generated: {total_tokens}")
            print(f"Token Generation Speed: {total_tokens/total_time:.2f} tokens/sec")
            
        return {
            "duration": total_time,
            "success_rate": len(successes)/total_requests*100,
            "rate_limit_rate": len(rate_limits)/total_requests*100,
            "avg_latency": avg_latency,
            "throughput": len(successes)/total_time,
            "tokens_per_sec": total_tokens/total_time if total_time > 0 else 0
        }

def main():
    parser = argparse.ArgumentParser(description="Cadence AI Meeting Intelligence Benchmarking Tool")
    parser.add_argument("--groq-key", type=str, default="", help="Groq API Key")
    parser.add_argument("--model", type=str, default="openai/gpt-oss-120b", help="Groq LLM model to benchmark")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent requests")
    parser.add_argument("--total-requests", type=int, default=15, help="Total requests to make")
    
    args = parser.parse_args()
    
async def async_main(args):
    print("=========================================================")
    print("  CADENCE PERFORMANCE & SCALE BENCHMARK SUITE")
    print("=========================================================")
    print(f"Targeting Groq Model: {args.model}")
    print(f"Concurrency Load Profile: {args.concurrency} concurrent streams")
    print(f"Total Iteration Budget: {args.total_requests}")
    
    # 1. Run Whisper Ingestion Benchmark
    whisper_res = await run_whisper_benchmark(args.groq_key, args.concurrency, args.total_requests)
    
    # 2. Run Chat Report Generation Benchmark
    chat_res = await run_chat_benchmark(args.groq_key, args.model, args.concurrency, args.total_requests)
    
    # Report Markdown Output for Resume / Benchmarks log
    print("\n\n=========================================================")
    print("  EXECUTIVE SUMMARY TABLE (Markdown for your records)")
    print("=========================================================")
    print("| Metric | Groq Whisper (Transcriptions) | Groq Chat (AI Summaries) |")
    print("| :--- | :---: | :---: |")
    print(f"| **Concurrency Tested** | {args.concurrency} concurrent streams | {args.concurrency} concurrent triggers |")
    print(f"| **Average Latency** | {whisper_res['avg_latency']:.2f}s | {chat_res['avg_latency']:.2f}s |")
    print(f"| **Success Rate** | {whisper_res['success_rate']:.1f}% | {chat_res['success_rate']:.1f}% |")
    print(f"| **Rate Limit (429) Rate** | {whisper_res['rate_limit_rate']:.1f}% | {chat_res['rate_limit_rate']:.1f}% |")
    print(f"| **Ingestion Throughput** | {whisper_res['throughput']:.2f} req/s | {chat_res['throughput']:.2f} req/s |")
    print(f"| **Tokens Generation Speed** | N/A | {chat_res['tokens_per_sec']:.2f} tokens/s |")
    print("=========================================================")

def main():
    parser = argparse.ArgumentParser(description="Cadence AI Meeting Intelligence Benchmarking Tool")
    parser.add_argument("--groq-key", type=str, default="", help="Groq API Key")
    parser.add_argument("--model", type=str, default="openai/gpt-oss-120b", help="Groq LLM model to benchmark")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent requests")
    parser.add_argument("--total-requests", type=int, default=15, help="Total requests to make")
    
    args = parser.parse_args()
    
    if not args.groq_key:
        print("Error: Groq API key is required. Pass via --groq-key")
        sys.exit(1)
        
    asyncio.run(async_main(args))

if __name__ == "__main__":
    main()
