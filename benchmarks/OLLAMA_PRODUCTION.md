# Deploying Local/Self-Hosted LLMs in Production (Ollama & vLLM)

When scaling Cadence past the free API limits of external providers (like Groq), switching to a self-hosted local model is essential. This guide explains how to host local Whisper and Llama models on dedicated GPU cloud instances (e.g., RunPod, Vast.ai, AWS, or GCP) and update the backend to target them.

---

## 1. Production Architecture Overview

Instead of hitting external APIs, the containerized services route requests to local high-throughput model endpoints:

```
[Audio stream / triggers]
           │
           ▼
   [FastAPI Gateway]
           │
     ┌─────┴──────────────┐
     ▼                    ▼
[transcript_worker]  [ai_worker]
     │                    │
     ▼                    ▼
[Faster-Whisper API]  [Ollama / vLLM API]
 (Host Port 8000)      (Host Port 11434)
```

* **Whisper Model Alternative**: [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) (re-implemented using CTranslate2, up to 4x faster than standard PyTorch Whisper).
* **LLM Model Alternative**: [Ollama](https://github.com/ollama/ollama) or [vLLM](https://github.com/vllm-project/vllm) hosting `Llama-3.1-8B-Instruct` or `Llama-3.3-70B-Instruct`.

---

## 2. Docker Compose Configuration (GPU Accelerated)

Use the following docker compose setup on your GPU-enabled production server to run the LLM endpoints alongside your backend:

```yaml
version: '3.8'

services:
  # Local LLM service (Ollama with GPU access)
  ollama:
    image: ollama/ollama:latest
    container_name: local_llm_ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_storage:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: always

  # Local Whisper API service (faster-whisper-server)
  whisper:
    image: fedirz/faster-whisper-server:latest
    container_name: local_whisper_server
    ports:
      - "8000:8000"
    environment:
      - MODEL=large-v3
      - DEVICE=cuda
      - COMPUTE_TYPE=float16
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: always

volumes:
  ollama_storage:
```

*Note: Ensure the NVIDIA Container Toolkit is installed on the host system to expose GPUs to Docker (`sudo apt install nvidia-container-toolkit`).*

---

## 3. Hardware Requirements & Model Selection

Choose the host hardware based on concurrency requirements:

| Target Concurrency | Recommended Model | Minimum GPU VRAM | Cloud Provider Cost |
| :--- | :--- | :--- | :--- |
| **1 - 3 concurrent meetings** | `Llama-3.1-8B-Instruct` + `Whisper-Medium` | 16 GB VRAM (e.g. RTX 4090 / A10G) | ~$0.20 - $0.40 / hr |
| **4 - 15 concurrent meetings** | `Llama-3.3-70B-Instruct` (Quantized) + `Whisper-Large-V3` | 48 GB VRAM (e.g. RTX 6000 Ada / A6000) | ~$0.70 - $1.00 / hr |
| **Enterprise / Multi-tenant** | `Llama-3.3-70B` (Unquantized) + Multiple Whisper instances | 80 GB VRAM (e.g. A100 / H100) | ~$2.00+ / hr |

---

## 4. Code Modifications

Switching from Groq to the self-hosted endpoints requires changing environment variables and base URLs:

### A. Updating Whisper (`transcript_worker.py`)
Faster-Whisper-Server implements the OpenAI transcription API standard. Simply redirect the HTTP request to the local container:

```python
# Change in transcript_worker.py:
def _call_groq_with_retry(wav_bytes, max_retries=3):
    # Route to local Docker service (http://whisper:8000/v1/audio/transcriptions)
    url = os.getenv("WHISPER_API_URL", "http://whisper:8000/v1/audio/transcriptions")
    
    # Rest of the standard payload remains identical
```

### B. Updating LLM Report Generation (`ai_worker.py`)
Ollama serves chat completions under `/v1/chat/completions` (OpenAI compatible).

In `ai_worker.py`, modify the post request:
```python
# In ai_worker.py:
groq_res = httpx.post(
    # Route to local Ollama API
    os.getenv("LLM_API_URL", "http://ollama:11434/v1/chat/completions"),
    json={
        "model": os.getenv("AI_MODEL", "llama3.1:8b"),
        "messages": messages,
        "temperature": 0.2
    },
    headers={"Content-Type": "application/json"},
    timeout=60.0
)
```

---

## 5. Production Setup Checklist

1. **Pull and Warm-up Model**: Run a startup script on your container to preload the model into GPU VRAM so the first request doesn't timeout:
   ```bash
   docker exec -it local_llm_ollama ollama pull llama3.1:8b
   ```
2. **Setup Rate Limiting/Queue Backpressure**: If active sessions exceed the GPU's memory or core capacity, Celery/Redis will buffer incoming tasks. Adjust worker concurrency settings in `main.py` (`concurrency: 4` instead of `concurrency: 16`) to match the GPU execution limits.
3. **Autoscaling (Optional)**: If hosting on AWS ECS or RunPod, set up autoscaling groups triggered by GPU utilization (>85%) to spin up additional replica worker nodes dynamically.
