import os
import json
import base64
import time
import uuid
import wave
import io
import redis
import httpx
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
MEETING_SERVICE_URL = os.getenv("MEETING_SERVICE_URL", "http://localhost:8002")
GROQ_API = os.getenv("GROQ_API")

if not GROQ_API:
    print("WARNING: GROQ_API environment variable is not set. Transcription will fail.")

# Initialize Redis
redis_client = redis.from_url(REDIS_URL)

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
        
        # Discard stale messages older than 5 seconds
        chunk_time = payload.get("timestamp", 0)
        if time.time() - chunk_time > 5.0:
            print(f"Skipping stale audio chunk (older than 5s) for meeting {payload.get('meeting_id')}")
            return
            
        meeting_id = payload.get("meeting_id")
        user_id = payload.get("user_id")
        user_name = payload.get("user_name", user_id)
        audio_b64 = payload.get("audio")

        if not audio_b64:
            return

        # Decode base64
        raw_bytes = base64.b64decode(audio_b64)
        
        # Local Silence Detection (skip silent chunks to save API limits)
        import array
        samples = array.array('h', raw_bytes)
        max_amplitude = max(abs(s) for s in samples) if samples else 0
        if max_amplitude < 500:  # Threshold for silence
            print(f"[{meeting_id}] Dropping silent chunk (Amplitude: {max_amplitude})")
            return
        
        # Create an in-memory WAV file from the raw PCM audio bytes
        wav_buffer = create_wav_buffer(raw_bytes)

        # Send to Groq API
        files = {
            'file': ('audio.wav', wav_buffer.read(), 'audio/wav')
        }
        data = {
            'model': 'whisper-large-v3',
            'temperature': '0.0',
            # We enforce English here because mixed-language auto-detect on short chunks
            # can cause it to output Urdu or Arabic script instead of Hindi/English.
            'language': 'en', 
            'prompt': 'This is a transcription of a live meeting. Do not output subtitles, stage directions, or generic thank you messages on silence.'
        }
        headers = {
            'Authorization': f'Bearer {GROQ_API}'
        }

        try:
            response = httpx.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                files=files,
                data=data,
                headers=headers,
                timeout=10.0
            )
            if response.status_code == 200:
                result = response.json()
                text = result.get("text", "").strip()
            else:
                print(f"Groq API Error: {response.status_code} - {response.text}")
                return
        except Exception as e:
            print(f"Error calling Groq API: {e}")
            return

        # Additional filter to ignore common whisper hallucination phrases on silence
        hallucination_phrases = [
            "You", "you", "Thank you.", "Thank you", "Subscribe to my channel", 
            "Please subscribe", "Am I audible?", "Am I audible", "Obrigado.", 
            "Obrigado", ".", "Thank you for watching.", "Thank you for watching"
        ]
        
        # Check exact matches
        if text in hallucination_phrases or not text:
            return

        if text:
            print(f"[{meeting_id}] {user_name}: {text}")
            
            # Save to database via meeting-service HTTP API
            try:
                if not meeting_id:
                    raise ValueError("No meeting_id provided in payload")
                meeting_uuid = uuid.UUID(meeting_id)
                response = httpx.post(
                    f"{MEETING_SERVICE_URL}/meetings/{meeting_uuid}/transcripts",
                    json={
                        "user_id": user_id,
                        "user_name": user_name,
                        "text": text
                    },
                    timeout=10.0
                )
                if response.status_code != 200:
                    print(f"Failed to save transcript: HTTP {response.status_code} - {response.text}")
            except ValueError as e:
                print(f"Warning: Meeting ID '{meeting_id}' is not a valid UUID ({e}), skipping API save.")
            except httpx.RequestError as e:
                print(f"HTTP Request Error connecting to meeting-service: {e}")

            # Publish back to Redis so Meeting Service can broadcast it
            transcript_update = {
                "meeting_id": meeting_id,
                "user_name": user_name,
                "text": text
            }
            redis_client.publish("transcript_updates", json.dumps(transcript_update))

    except Exception as e:
        print(f"Error processing audio chunk: {e}")

def main():
    print(f"Transcript Worker started. Sending HTTP requests to {MEETING_SERVICE_URL}")
    
    # Flush existing queue to drop old audio backlog from previous runs
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
