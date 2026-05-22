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

# Local loopback config inside container
PORT = os.getenv("PORT", "8002")
LOCAL_MEETING_SERVICE_URL = f"http://127.0.0.1:{PORT}"

# Initialize Redis with resilient socket parameters
redis_client = redis.from_url(
    REDIS_URL,
    socket_connect_timeout=5,
    socket_keepalive=True,
    retry_on_timeout=True
)

# Initialize Celery client (to trigger notification-service)
from celery import Celery
celery_client = Celery("ai_worker", broker=REDIS_URL)

def call_meeting_service(method, path, **kwargs):
    """
    Robust internal HTTP helper that calls MEETING_SERVICE_URL first, 
    and automatically falls back to LOCAL_MEETING_SERVICE_URL (127.0.0.1) 
    if the configured service URL fails (e.g. Hairpin NAT / loopback blocks).
    """
    url = f"{MEETING_SERVICE_URL.rstrip('/')}/{path.lstrip('/')}"
    try:
        response = httpx.request(method, url, **kwargs)
        if response.status_code < 500:
            return response
        print(f"[{method}] {url} returned HTTP {response.status_code}. Trying local fallback...")
    except Exception as e:
        print(f"[{method}] {url} failed: {e}. Trying local fallback...")
        
    local_url = f"{LOCAL_MEETING_SERVICE_URL.rstrip('/')}/{path.lstrip('/')}"
    try:
        print(f"[{method}] Falling back to local URL: {local_url}")
        return httpx.request(method, local_url, **kwargs)
    except Exception as fallback_e:
        print(f"[{method}] Local fallback to {local_url} also failed: {fallback_e}")
        raise fallback_e

def generate_meeting_report(meeting_id):
    print(f"[{meeting_id}] STARTING ANALYSIS...")
    
    # 0. Fetch meeting details via the internal endpoint (no JWT required for service-to-service)
    try:
        meeting_resp = call_meeting_service("GET", f"meetings/{meeting_id}/internal", timeout=10.0)
        if meeting_resp.status_code != 200:
            print(f"[{meeting_id}] ERROR: Failed to fetch meeting details: {meeting_resp.status_code} - {meeting_resp.text}")
            return
        meeting_data = meeting_resp.json()
        mode = meeting_data.get("mode", "general")
        print(f"[{meeting_id}] Mode detected: {mode}")
    except Exception as e:
        print(f"[{meeting_id}] EXCEPTION: Error fetching meeting mode: {e}")
        return

    # 1. Fetch transcripts
    try:
        response = call_meeting_service("GET", f"meetings/{meeting_id}/transcripts", timeout=10.0)
        if response.status_code != 200:
            print(f"[{meeting_id}] ERROR: Failed to fetch transcripts: {response.status_code}")
            return
        transcripts = response.json()
    except Exception as e:
        print(f"[{meeting_id}] EXCEPTION: Error fetching transcripts: {e}")
        return

    # 1.1 Fetch alerts (for anti-cheat detection)
    alerts_summary = ""
    try:
        alerts_resp = call_meeting_service("GET", f"meetings/{meeting_id}/alerts", timeout=5.0)
        if alerts_resp.status_code == 200:
            alerts = alerts_resp.json()
            tab_switches = [a for a in alerts if a.get("alert_type") == "tab_switch"]
            if tab_switches:
                alerts_summary = f"\nSECURITY ALERTS: The candidate switched/left the browser tab {len(tab_switches)} times during this session.\n"
    except Exception as e:
        print(f"[{meeting_id}] Warning: Could not fetch alerts: {e}")

    if not transcripts:
        print(f"[{meeting_id}] No transcripts found. Skipping analysis.")
        return

    # 2. Format transcript for LLM
    formatted_transcript = ""
    for t in transcripts:
        user_name = t.get("user_name") or t.get("user_id", "Unknown")
        formatted_transcript += f"{user_name}: {t.get('text', '')}\n"

    # 3. Construct Mode-Aware Prompt
    mode_instructions = ""
    if mode == "business":
        mode_instructions = """
        Your 'insights' object MUST include:
        - 'pain_points': A list of challenges discussed.
        - 'requirements': Specific needs or features requested.
        - 'budget': Any financial figures mentioned.
        - 'competitors': Any rival companies or products mentioned.
        """
    elif mode == "interview":
        mode_instructions = """
        Your 'insights' object MUST include:
        - 'skill_proficiency': A summary of the candidate's technical skills.
        - 'communication_score': A rating from 1 to 10.
        - 'red_flags': Any potential issues or gaps in knowledge.
        - 'hiring_recommendation': One of [Strong Hire, Hire, No Hire].
        """
    else:
        mode_instructions = "Your 'insights' object can be empty or include general observations."

    system_prompt = f"""You are a Senior Meeting Intelligence Specialist. 
Analyze the transcript of a {mode} meeting and extract key information.
Be objective, professional, and concise.
"""

    user_prompt = f"""Transcript:
{formatted_transcript}

{alerts_summary}

Please return a JSON object with the following structure:
{{
  "summary": "3-5 sentence executive summary.",
  "sentiment": "Positive, Neutral, or Negative",
  "action_items": [
    {{ "description": "Clear, actionable task (include assignee if known, e.g., 'Update docs - John')" }}
  ],
  "decisions": ["Decision 1", "Decision 2"],
  "insights": {{ ... mode specific details ... }}
}}

{mode_instructions}
"""

    headers = {
        "Authorization": f"Bearer {GROQ_API}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1 # Lower temperature for more consistent JSON
    }

    try:
        groq_res = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=45.0
        )
        if groq_res.status_code == 200:
            result_json = groq_res.json()
            content_str = result_json["choices"][0]["message"]["content"]
            ai_output = json.loads(content_str)
            print(f"[{meeting_id}] AI successfully parsed the transcript.")
        else:
            print(f"[{meeting_id}] ERROR: Groq API {groq_res.status_code} - {groq_res.text}")
            return
    except Exception as e:
        print(f"[{meeting_id}] EXCEPTION: Error calling Groq: {e}")
        return

    # 4. Save Granular Data to Meeting Service
    
    # Action Items
    for item in ai_output.get("action_items", []):
        try:
            call_meeting_service(
                "POST",
                f"meetings/{meeting_id}/action-items",
                json={"description": item.get("description", ""), "is_completed": False},
                timeout=5.0
            )
        except Exception as e:
            print(f"[{meeting_id}] Warning: Could not save action item: {e}")

    # Decisions
    for dec in ai_output.get("decisions", []):
        try:
            call_meeting_service(
                "POST",
                f"meetings/{meeting_id}/decisions",
                json={"description": dec},
                timeout=5.0
            )
        except Exception as e:
            print(f"[{meeting_id}] Warning: Could not save decision: {e}")

    # 5. Save final Analysis
    try:
        save_payload = {
            "summary": ai_output.get("summary", ""),
            "sentiment": ai_output.get("sentiment", "Neutral"),
            "mode": mode,
            "insights": ai_output.get("insights", {})
        }
        
        save_res = call_meeting_service(
            "POST",
            f"meetings/{meeting_id}/analysis",
            json=save_payload,
            timeout=10.0
        )
        if save_res.status_code == 200:
            print(f"[{meeting_id}] ANALYSIS SAVED SUCCESSFULLY.")
            
            # 6. Trigger Notifications
            report_data = {
                "title": meeting_data.get("title", f"Meeting {str(meeting_id)[:8]}"),
                "summary": ai_output.get("summary", ""),
                "action_items": [item.get("description") for item in ai_output.get("action_items", [])],
                "decisions": ai_output.get("decisions", [])
            }
            
            try:
                celery_client.send_task(
                    "send_meeting_summary_email",
                    kwargs={
                        "meeting_id": str(meeting_id),
                        "to_email": "test@example.com",
                        "report_data": report_data
                    }
                )
                print(f"[{meeting_id}] Notification task dispatched.")
            except Exception as e:
                print(f"[{meeting_id}] Warning: Notification failed: {e}")
        else:
            print(f"[{meeting_id}] ERROR: Failed to save analysis {save_res.status_code}")
    except Exception as e:
        print(f"[{meeting_id}] EXCEPTION: Error saving final analysis: {e}")

def generate_personal_analysis(meeting_id, user_id):
    print(f"[{meeting_id} - {user_id}] STARTING PERSONAL ANALYSIS...")
    
    try:
        response = call_meeting_service("GET", f"meetings/{meeting_id}/transcripts/user/{user_id}", timeout=10.0)
        if response.status_code != 200:
            print(f"[{meeting_id} - {user_id}] ERROR: Failed to fetch transcripts.")
            return
        transcripts = response.json()
    except Exception as e:
        print(f"[{meeting_id} - {user_id}] EXCEPTION: {e}")
        return

    if not transcripts:
        print(f"[{meeting_id} - {user_id}] No transcripts found for user.")
        return

    formatted_transcript = ""
    for t in transcripts:
        user_name = t.get("user_name") or t.get("user_id", "Unknown")
        formatted_transcript += f"{user_name}: {t.get('text', '')}\n"

    system_prompt = "You are a highly skilled Speech and Communication Coach. Analyze the user's transcript from a meeting."
    user_prompt = f"""Transcript:
{formatted_transcript}

Please analyze this individual's performance and return a JSON object with the following structure:
{{
  "speech_improvement": "Feedback on speech and dialect.",
  "confidence_score": "Rating from 1-10 with a short explanation.",
  "questions_and_answers": "Evaluation of their Q&A interaction.",
  "contribution": "Summary of their overall contribution.",
  "areas_of_improvement": ["Point 1", "Point 2"]
}}
"""

    headers = {
        "Authorization": f"Bearer {GROQ_API}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2
    }

    try:
        groq_res = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=45.0
        )
        if groq_res.status_code == 200:
            result_json = groq_res.json()
            content_str = result_json["choices"][0]["message"]["content"]
            ai_output = json.loads(content_str)
        else:
            print(f"[{meeting_id} - {user_id}] ERROR: Groq API {groq_res.status_code}")
            return
    except Exception as e:
        print(f"[{meeting_id} - {user_id}] EXCEPTION: Error calling Groq: {e}")
        return

    try:
        save_res = call_meeting_service(
            "POST",
            f"meetings/{meeting_id}/transcripts/user/{user_id}/analysis",
            json={"analysis_data": ai_output},
            timeout=10.0
        )
        if save_res.status_code == 200:
            print(f"[{meeting_id} - {user_id}] PERSONAL ANALYSIS SAVED SUCCESSFULLY.")
        else:
            print(f"[{meeting_id} - {user_id}] ERROR saving analysis {save_res.status_code} - {save_res.text}")
    except Exception as e:
        print(f"[{meeting_id} - {user_id}] EXCEPTION saving analysis: {e}")


def main():
    print(f"AI Service Worker started. Listening to queues...")
    
    while True:
        try:
            result = redis_client.blpop(["meeting_ended_queue", "personal_analysis_queue"], timeout=30)
            if result:
                queue_name, payload_str = result
                payload = json.loads(payload_str)
                meeting_id = payload.get("meeting_id")
                
                if queue_name.decode() == "meeting_ended_queue" and meeting_id:
                    generate_meeting_report(meeting_id)
                elif queue_name.decode() == "personal_analysis_queue" and meeting_id:
                    user_id = payload.get("user_id")
                    if user_id:
                        generate_personal_analysis(meeting_id, user_id)
        except Exception as e:
            print(f"Queue error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
