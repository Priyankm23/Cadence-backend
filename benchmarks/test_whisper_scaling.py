import asyncio
import io
import time
import wave
import sys
import httpx

# Pre-packaged silence WAV generator (16kHz, 16-bit mono, 3 seconds)
def create_mock_wav():
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b'\x00' * 96000)
    return wav_io.getvalue()

async def transcribe_chunk(client, groq_key, wav_bytes, request_id):
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

async def run_stress_level(groq_key, concurrency):
    print(f"\nRunning stress level with Concurrency: {concurrency} concurrent streams...")
    wav_bytes = create_mock_wav()
    
    # We send concurrency * 2 total requests to test sustain rate
    total_requests = concurrency * 2
    
    limits = httpx.Limits(max_keepalive_connections=concurrency, max_connections=concurrency * 2)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = []
        sem = asyncio.Semaphore(concurrency)
        
        async def worker(req_id):
            async with sem:
                return await transcribe_chunk(client, groq_key, wav_bytes, req_id)
                
        start_time = time.time()
        for i in range(total_requests):
            tasks.append(worker(i))
            
        results = await asyncio.gather(*tasks)
        duration = time.time() - start_time
        
        successes = [r for r in results if r["success"]]
        rate_limits = [r for r in results if r["status"] == 429]
        failures = [r for r in results if not r["success"] and r["status"] != 429]
        latencies = [r["latency"] for r in successes]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        
        print(f"  └─ Completed {total_requests} requests in {duration:.2f}s")
        print(f"  └─ Success: {len(successes)} ({len(successes)/total_requests*100:.1f}%) | 429s: {len(rate_limits)} | Errors: {len(failures)}")
        if successes:
            print(f"  └─ Avg Latency: {avg_latency:.2f}s | Throughput: {len(successes)/duration:.2f} rps")
            
        return {
            "concurrency": concurrency,
            "success_rate": len(successes)/total_requests*100,
            "rate_limit_count": len(rate_limits),
            "avg_latency": avg_latency,
            "throughput": len(successes)/duration
        }

async def main():
    groq_key = ""
    
    print("=========================================================")
    print("  GROQ WHISPER STRESS & SCALING BREAKPOINT TEST")
    print("=========================================================")
    print("This test steps up the concurrent Whisper transcription load")
    print("to pinpoint exactly where Groq rate-limits your streams.")
    print("=========================================================\n")
    
    # Define stress levels (number of concurrent meeting streams)
    stress_levels = [5, 10, 20, 40, 60]
    results = []
    
    for level in stress_levels:
        res = await run_stress_level(groq_key, level)
        results.append(res)
        
        # If failure rate is more than 30%, we found the breakpoint!
        if res["success_rate"] < 70:
            print(f"\n[ALERT] Breakpoint reached! Whisper service degraded at Concurrency = {level}")
            break
            
        # Give API a short 5-second rest between steps to clear dynamic burst limits
        await asyncio.sleep(5)
        
    print("\n=========================================================")
    print("                 STRESS TEST SUMMARY")
    print("=========================================================")
    print("| Concurrent Streams | Success Rate | Rate Limits (429) | Throughput | Avg Latency |")
    print("| :---: | :---: | :---: | :---: | :---: |")
    for r in results:
        print(f"| {r['concurrency']} | {r['success_rate']:.1f}% | {r['rate_limit_count']} | {r['throughput']:.2f} req/s | {r['avg_latency']:.2f}s |")
    print("=========================================================")

if __name__ == "__main__":
    asyncio.run(main())
