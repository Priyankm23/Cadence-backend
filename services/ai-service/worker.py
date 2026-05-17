import os
import json
import time
import redis
import httpx
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MEETING_SERVICE_URL = os.getenv("MEETING_SERVICE_URL", "http://localhost:8002")
GROQ_API = os.getenv("GROQ_API")

if not GROQ_API:
    print("WARNING: GROQ_API environment variable is not set. AI analysis will fail.")

# Initialize Redis
redis_client = redis.from_url(REDIS_URL)

# Initialize Celery client (to trigger notification-service)
from celery import Celery
celery_client = Celery("ai_worker", broker=REDIS_URL)

def generate_meeting_report(meeting_id):
    print(f"[{meeting_id}] Generating AI report...")
    
    # 1. Fetch transcripts
    try:
        response = httpx.get(f"{MEETING_SERVICE_URL}/meetings/{meeting_id}/transcripts", timeout=10.0)
        if response.status_code != 200:
            print(f"[{meeting_id}] Failed to fetch transcripts: {response.status_code} - {response.text}")
            return
        transcripts = response.json()
    except Exception as e:
        print(f"[{meeting_id}] Error fetching transcripts: {e}")
        return

    if not transcripts:
        print(f"[{meeting_id}] No transcripts found. Skipping analysis.")
        return

    # 2. Format transcript for LLM
    formatted_transcript = ""
    for t in transcripts:
        formatted_transcript += f"{t.get('user_id', 'Unknown')}: {t.get('text', '')}\n"
        print(formatted_transcript)

    # 3. Call Groq API
    prompt = f"""You are an expert AI meeting assistant. Analyze the following meeting transcript and provide a structured JSON output.

Transcript:
{formatted_transcript}

Output JSON format strictly:
{{
  "summary": "A concise executive summary of the meeting (3-5 sentences).",
  "action_items": ["Action item 1 with assignee if mentioned", "Action item 2"],
  "sentiment": "Positive, Neutral, or Negative"
}}
"""

    headers = {
        "Authorization": f"Bearer {GROQ_API}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2
    }

    try:
        groq_res = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=30.0
        )
        if groq_res.status_code == 200:
            result_json = groq_res.json()
            content_str = result_json["choices"][0]["message"]["content"]
            analysis_data = json.loads(content_str)
        else:
            print(f"[{meeting_id}] Groq API Error: {groq_res.status_code} - {groq_res.text}")
            return
    except Exception as e:
        print(f"[{meeting_id}] Error calling Groq LLM: {e}")
        return

    # 4. Save analysis to Meeting Service
    try:
        # Convert action items list to string for DB
        action_items_str = json.dumps(analysis_data.get("action_items", []))
        
        save_payload = {
            "summary": analysis_data.get("summary", ""),
            "action_items": action_items_str,
            "sentiment": analysis_data.get("sentiment", "Neutral")
        }
        
        save_res = httpx.post(
            f"{MEETING_SERVICE_URL}/meetings/{meeting_id}/analysis",
            json=save_payload,
            timeout=10.0
        )
        if save_res.status_code == 200:
            print(f"[{meeting_id}] Successfully saved meeting analysis.")
            
            # 5. Trigger Notification Service
            print(f"[{meeting_id}] Dispatching email notification task...")
            report_data = {
                "title": f"Meeting {str(meeting_id)[:8]}",
                "summary": analysis_data.get("summary", ""),
                "action_items": analysis_data.get("action_items", []),
                "decisions": []
            }
            
            celery_client.send_task(
                "send_meeting_summary_email",
                kwargs={
                    "meeting_id": str(meeting_id),
                    "to_email": "test@example.com", # TODO: Fetch host email from DB via Meeting Service
                    "report_data": report_data
                }
            )
            print(f"[{meeting_id}] Notification task dispatched.")
        else:
            print(f"[{meeting_id}] Failed to save analysis: {save_res.status_code} - {save_res.text}")
    except Exception as e:
        print(f"[{meeting_id}] Error saving analysis: {e}")

def main():
    print(f"AI Service Worker started. Listening to meeting_ended_queue...")
    
    while True:
        try:
            result = redis_client.blpop("meeting_ended_queue", timeout=0)
            if result:
                _, payload_str = result
                payload = json.loads(payload_str)
                meeting_id = payload.get("meeting_id")
                if meeting_id:
                    generate_meeting_report(meeting_id)
        except Exception as e:
            print(f"Queue error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
