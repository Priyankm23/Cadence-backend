import os
import json
import base64
import time
import uuid
import wave
import io
import redis
import httpx
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import torch
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
MEETING_SERVICE_URL = os.getenv("MEETING_SERVICE_URL", "http://localhost:8002")
GROQ_API = os.getenv("GROQ_API")

BUFFER_TARGET_MS = int(os.getenv("TRANSCRIPT_BUFFER_TARGET_MS", "5000"))
BUFFER_MAX_MS = int(os.getenv("TRANSCRIPT_BUFFER_MAX_MS", "15000"))
BUFFER_GAP_FLUSH_MS = int(os.getenv("TRANSCRIPT_BUFFER_GAP_FLUSH_MS", "1200"))
STALE_CHUNK_SECONDS = float(os.getenv("TRANSCRIPT_STALE_CHUNK_SECONDS", "30"))

if not GROQ_API:
    print("WARNING: GROQ_API environment variable is not set. Transcription will fail.")

# Initialize Redis
redis_client = redis.from_url(REDIS_URL)

meeting_buffers = {}
executor = ThreadPoolExecutor(max_workers=10)

vad_model, vad_utils = torch.hub.load(
    repo_or_dir="snakers4/silero-vad",
    model="silero_vad",
    trust_repo=True
)
(get_speech_timestamps_fn, _, _, _, _) = vad_utils


def create_wav_buffer(raw_bytes, sample_rate=16000, channels=1, sample_width=2):
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(raw_bytes)
    wav_io.seek(0)
    return wav_io


def process_audio_chunk(payload_str):
    try:
        payload = json.loads(payload_str)

        # FIX 1: Only apply stale check when timestamp is actually present.
        # Old code used default=0 which made time.time()-0 = ~1.7B seconds,
        # causing every chunk to be silently dropped when frontend omits timestamp.
        chunk_time = payload.get("timestamp")
        if chunk_time is not None and time.time() - chunk_time > STALE_CHUNK_SECONDS:
            print(f"Skipping stale audio chunk (older than {STALE_CHUNK_SECONDS}s) "
                  f"for meeting {payload.get('meeting_id')}")
            return

        meeting_id = payload.get("meeting_id")
        user_id = payload.get("user_id")
        user_name = payload.get("user_name", user_id)
        audio_b64 = payload.get("audio")
        chunk_start_ms = payload.get("chunk_start_ms")
        chunk_end_ms = payload.get("chunk_end_ms")

        if not audio_b64:
            return

        raw_bytes = base64.b64decode(audio_b64)

        buffer_key = f"{meeting_id}:{user_id}"
        buffer_state = meeting_buffers.get(buffer_key)
        if not buffer_state:
            buffer_state = {
                "audio": bytearray(),
                "start_ms": chunk_start_ms,
                "end_ms": chunk_end_ms,
                "user_id": user_id,
                "user_name": user_name,
                "meeting_id": meeting_id,
            }
            meeting_buffers[buffer_key] = buffer_state

        if buffer_state["start_ms"] is None:
            buffer_state["start_ms"] = chunk_start_ms

        if buffer_state["end_ms"] is not None and chunk_start_ms is not None:
            gap_ms = chunk_start_ms - buffer_state["end_ms"]
            if gap_ms > BUFFER_GAP_FLUSH_MS:
                _submit_buffer(buffer_state)
                buffer_state = {
                    "audio": bytearray(),
                    "start_ms": chunk_start_ms,
                    "end_ms": chunk_end_ms,
                    "user_id": user_id,
                    "user_name": user_name,
                    "meeting_id": meeting_id,
                }
                meeting_buffers[buffer_key] = buffer_state

        buffer_state["audio"].extend(raw_bytes)
        if chunk_end_ms is not None:
            buffer_state["end_ms"] = chunk_end_ms

        if buffer_state["start_ms"] is None or buffer_state["end_ms"] is None:
            return

        buffered_duration_ms = buffer_state["end_ms"] - buffer_state["start_ms"]
        if buffered_duration_ms < BUFFER_TARGET_MS and buffered_duration_ms < BUFFER_MAX_MS:
            return

        _submit_buffer(buffer_state)

    except Exception as e:
        print(f"Error processing audio chunk: {e}")


def _submit_buffer(buffer_state):
    if not buffer_state["audio"]:
        return

    state_copy = {
        **buffer_state,
        "audio": bytes(buffer_state["audio"]),
    }
    executor.submit(_flush_buffer, state_copy)

    # FIX 2: Clean reset with no overlap.
    # Old overlap logic re-submitted already-transcribed audio, causing
    # Whisper to re-transcribe old speech mixed with new — garbled output.
    buffer_state["audio"] = bytearray()
    buffer_state["start_ms"] = None
    buffer_state["end_ms"] = buffer_state.get("end_ms")


def _call_groq_with_retry(wav_bytes, max_retries=3):
    delay = 1.0
    # Fixed prompt — dynamic prompt updates caused race conditions between
    # threads sharing meeting_buffers and didn't meaningfully improve accuracy.
    prompt_text = "Transcribe this meeting audio exactly as spoken."
    for attempt in range(max_retries):
        files = {'file': ('audio.wav', wav_bytes, 'audio/wav')}
        data = {
            'model': 'whisper-large-v3',
            'temperature': '0.0',
            'language': 'en',
            'prompt': prompt_text,
        }
        headers = {'Authorization': f'Bearer {GROQ_API}'}
        response = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            files=files,
            data=data,
            headers=headers,
            timeout=15.0
        )
        if response.status_code == 200:
            return response
        if response.status_code == 429:
            print(f"Groq rate limited (attempt {attempt + 1}/{max_retries}), "
                  f"retrying in {delay}s...")
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
            continue
        # Any other error — return immediately, no retry
        return response
    return None


def _flush_buffer(buffer_state):
    # NOTE: buffer_state here is always a copy (audio is bytes, not bytearray).
    # No finally cleanup needed — this function owns its copy entirely.
    meeting_id = buffer_state.get("meeting_id")
    user_id = buffer_state.get("user_id")
    user_name = buffer_state.get("user_name")
    raw_bytes = buffer_state.get("audio", b"")

    if not raw_bytes:
        return

    # FIX 6: Minimum audio duration check before VAD.
    # Very short clips (< 1 second = 32000 bytes at 16kHz 16-bit) always
    # produce Whisper hallucinations. Skip them early.
    if len(raw_bytes) < 32000:
        print(f"[{meeting_id}] Audio too short ({len(raw_bytes)} bytes), skipping.")
        return

    # FIX 3: Normalize int16 → float32 before passing to Silero VAD.
    # Silero VAD was trained on float32 audio in range [-1.0, 1.0].
    # Passing raw int16 values (range -32768 to 32767) distorts the model's
    # internal confidence scores, causing it to reject real speech as noise.
    audio_int16 = np.frombuffer(raw_bytes, dtype=np.int16).copy()
    if audio_int16.size == 0:
        return

    audio_float32 = audio_int16.astype(np.float32) / 32768.0

    speech_timestamps = get_speech_timestamps_fn(
        torch.from_numpy(audio_float32),
        vad_model,
        sampling_rate=16000,
        min_speech_duration_ms=100,   # more permissive than old 200ms
        min_silence_duration_ms=50,   # more permissive than old 100ms
        threshold=0.3,                # lower than default 0.5 — catches quieter speech
    )

    if not speech_timestamps:
        # FIX: Log when VAD rejects so you can see it happening in logs.
        # Also apply RMS fallback — if audio has energy but VAD missed it,
        # send to Whisper anyway rather than silently dropping.
        rms = np.sqrt(np.mean(audio_float32 ** 2))
        print(f"[{meeting_id}] VAD found no speech (RMS={rms:.4f}). "
              f"{'Sending to Whisper via RMS fallback.' if rms > 0.005 else 'Truly silent, skipping.'}")
        if rms <= 0.005:
            # Genuinely silent — skip
            return
        # Has energy but VAD missed — proceed to Whisper anyway

    # Use the original int16 raw bytes for the WAV file — Whisper expects this.
    wav_buffer = create_wav_buffer(raw_bytes)
    response = _call_groq_with_retry(wav_buffer.read())

    if response is None:
        print(f"[{meeting_id}] Groq request failed after all retries.")
        return
    if response.status_code != 200:
        print(f"[{meeting_id}] Groq API Error: {response.status_code} - {response.text}")
        return

    result = response.json()
    text = result.get("text", "").strip()

    if not text:
        return

    # FIX 7: Simplified hallucination filter using lowercase comparison.
    # Old exact-match list missed variants with trailing spaces or punctuation.
    HALLUCINATIONS = {
        "you", "thank you", "thank you.", "thanks", "thanks.",
        ".", "..", "...", "obrigado", "obrigado.",
        "please subscribe", "am i audible", "am i audible?",
        "thank you for watching", "thank you for watching.",
        "subscribe to my channel",
    }
    text_lower = text.lower().strip()
    if text_lower in HALLUCINATIONS:
        print(f"[{meeting_id}] Hallucination filtered: '{text}'")
        return

    # FIX 8: Reject suspiciously short output (single chars or empty words).
    if len(text.strip()) < 4:
        print(f"[{meeting_id}] Text too short, likely hallucination: '{text}'")
        return

    print(f"[{meeting_id}] {user_name}: {text}")

    # Save transcript to meeting service
    try:
        if not meeting_id:
            raise ValueError("No meeting_id in payload")
        meeting_uuid = uuid.UUID(meeting_id)
        transcript_payload = {
            "user_id": user_id,
            "user_name": user_name,
            "text": text,
        }
        start_ms = buffer_state.get("start_ms")
        end_ms = buffer_state.get("end_ms")
        if start_ms is not None and end_ms is not None:
            transcript_payload["start_time"] = int(start_ms)
            transcript_payload["end_time"] = int(end_ms)

        save_response = httpx.post(
            f"{MEETING_SERVICE_URL}/meetings/{meeting_uuid}/transcripts",
            json=transcript_payload,
            timeout=10.0
        )
        if save_response.status_code != 200:
            print(f"[{meeting_id}] Failed to save transcript: "
                  f"HTTP {save_response.status_code} - {save_response.text}")
    except ValueError as e:
        print(f"Warning: Meeting ID '{meeting_id}' is not a valid UUID ({e}), skipping save.")
    except httpx.RequestError as e:
        print(f"HTTP error connecting to meeting-service: {e}")

    # Publish to Redis for live frontend updates
    redis_client.publish("transcript_updates", json.dumps({
        "meeting_id": meeting_id,
        "user_name": user_name,
        "text": text,
    }))


def main():
    print(f"Transcript Worker started. Connecting to meeting service at {MEETING_SERVICE_URL}")
    # Clear stale audio from previous runs
    redis_client.delete("audio_queue")
    print("Cleared stale audio chunks from queue.")

    while True:
        try:
            result = redis_client.blpop("audio_queue", timeout=0)
            if result:
                _, payload_str = result
                process_audio_chunk(payload_str)
        except Exception as e:
            print(f"Queue error: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()