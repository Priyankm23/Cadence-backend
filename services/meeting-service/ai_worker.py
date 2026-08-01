import os
import json
import time
import socket
import redis
import httpx
from dotenv import load_dotenv
from core.database import SessionLocal
import models
from uuid import UUID

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_QUEUE_PREFIX = os.getenv("REDIS_QUEUE_PREFIX", "")
MEETING_SERVICE_URL = os.getenv("MEETING_SERVICE_URL", "http://localhost:8002")
GROQ_API = os.getenv("GROQ_API","")
AI_MODEL = os.getenv("AI_MODEL", "llama-3.3-70b-versatile")

if not GROQ_API:
    print("WARNING: GROQ_API environment variable is not set. AI analysis will fail.")

# Local loopback config inside container

PORT = os.getenv("PORT","8002")
LOCAL_MEETING_SERVICE_URL = f"http://127.0.0.1:{PORT}"

# Initialize Redis with resilient socket parameters
redis_client = redis.from_url(
    REDIS_URL,
    socket_connect_timeout=5,
    socket_keepalive=True,
    socket_keepalive_options={
        socket.TCP_KEEPIDLE: 60,    # start keepalives after 60s idle
        socket.TCP_KEEPINTVL: 10,   # probe every 10s
        socket.TCP_KEEPCNT: 3,      # drop after 3 failed probes
    },
    retry_on_timeout=True
)

import ssl

# Initialize Celery client (to trigger notification-service)
from celery import Celery

redis_url = REDIS_URL
if redis_url.startswith("rediss://"):
    if "ssl_cert_reqs" not in redis_url:
        separator = "&" if "?" in redis_url else "?"
        redis_url = f"{redis_url}{separator}ssl_cert_reqs=CERT_NONE"

celery_client = Celery("ai_worker", broker=redis_url)

if REDIS_QUEUE_PREFIX:
    celery_client.conf.update(
        task_default_queue=f"{REDIS_QUEUE_PREFIX}celery",
    )

if REDIS_URL.startswith("rediss://"):
    celery_client.conf.update(
        broker_use_ssl={"ssl_cert_reqs": ssl.CERT_NONE},
    )

def call_meeting_service(method, path, **kwargs):
    """
    Robust internal HTTP helper. 
    Since workers run in the same container as the meeting service, we try the 
    local loopback URL (127.0.0.1:{PORT} or localhost:{PORT}) first for maximum speed 
    and reliability, and fall back to the configured MEETING_SERVICE_URL if the local call fails.
    """
    import traceback

    # 1. Try 127.0.0.1 loopback URL
    local_url = f"{LOCAL_MEETING_SERVICE_URL.rstrip('/')}/{path.lstrip('/')}"
    try:
        # Use trust_env=False to bypass any proxy configurations in the container
        response = httpx.request(method, local_url, trust_env=False, **kwargs)
        if response.status_code < 500:
            return response
        print(f"[{method}] {local_url} returned HTTP {response.status_code}. Trying local 'localhost' fallback...")
    except Exception as e:
        print(f"[{method}] {local_url} failed: {e}. Trying local 'localhost' fallback...")
        traceback.print_exc()

    # 2. Try localhost loopback URL
    localhost_url = f"http://localhost:{PORT}/{path.lstrip('/')}"
    try:
        response = httpx.request(method, localhost_url, trust_env=False, **kwargs)
        if response.status_code < 500:
            return response
        print(f"[{method}] {localhost_url} returned HTTP {response.status_code}. Trying external fallback...")
    except Exception as e:
        print(f"[{method}] {localhost_url} failed: {e}. Trying external fallback...")
        traceback.print_exc()
        
    # 3. Fall back to external MEETING_SERVICE_URL
    url = f"{MEETING_SERVICE_URL.rstrip('/')}/{path.lstrip('/')}"
    try:
        print(f"[{method}] Falling back to external URL: {url}")
        return httpx.request(method, url, **kwargs)
    except Exception as fallback_e:
        print(f"[{method}] External fallback to {url} also failed: {fallback_e}")
        traceback.print_exc()
        raise fallback_e


def generate_meeting_report(meeting_id):
    print(f"[{meeting_id}] STARTING ANALYSIS (Direct DB Access)...")
    
    db = SessionLocal()
    meeting_data = None
    mode = "general"
    m_uuid = UUID(str(meeting_id))
    
    # 0. Fetch meeting details
    try:
        meeting = db.query(models.Meeting).filter(models.Meeting.id == m_uuid).first()
        if not meeting:
            print(f"[{meeting_id}] ERROR: Meeting not found in DB.")
            return
        
        meeting_data = {
            "id": str(meeting.id),
            "title": meeting.title,
            "status": meeting.status,
            "mode": meeting.mode,
            "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
            "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
            "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
            "duration_seconds": meeting.duration_seconds
        }
        mode = meeting.mode or "general"
        print(f"[{meeting_id}] Mode detected: {mode}")
    except Exception as e:
        print(f"[{meeting_id}] EXCEPTION: Error fetching meeting details from DB: {e}")
        db.close()
        return

    # 1. Fetch transcripts
    try:
        segments = db.query(models.TranscriptSegment)\
            .filter(models.TranscriptSegment.meeting_id == m_uuid)\
            .order_by(models.TranscriptSegment.start_time.asc()).all()
            
        transcripts = []
        for s in segments:
            transcripts.append({
                "id": str(s.id),
                "meeting_id": str(s.meeting_id),
                "user_id": str(s.user_id) if s.user_id else None,
                "user_name": s.user_name,
                "text": s.text,
                "start_time": s.start_time,
                "end_time": s.end_time
            })
    except Exception as e:
        print(f"[{meeting_id}] EXCEPTION: Error fetching transcripts from DB: {e}")
        db.close()
        return

    # 1.1 Fetch alerts (for anti-cheat detection)
    alerts_summary = ""
    try:
        alerts_list = db.query(models.MeetingAlert).filter(models.MeetingAlert.meeting_id == m_uuid).all()
        tab_switches = [a for a in alerts_list if a.alert_type == "tab_switch"]
        if tab_switches:
            alerts_summary = f"\nSECURITY ALERTS: The candidate switched/left the browser tab {len(tab_switches)} times during this session.\n"
    except Exception as e:
        print(f"[{meeting_id}] Warning: Could not fetch alerts: {e}")

    if not transcripts:
        print(f"[{meeting_id}] No transcripts found. Skipping analysis.")
        db.close()
        return

    # 2. Format transcript for LLM
    formatted_transcript = ""
    for t in transcripts:
        user_name = t.get("user_name") or t.get("user_id", "Unknown")
        
        # Format start_time (stored in milliseconds) to human-readable time [MM:SS] or [HH:MM:SS]
        start_ms = t.get("start_time")
        time_str = ""
        if start_ms is not None:
            total_seconds = int(start_ms) // 1000
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            if minutes >= 60:
                hours = minutes // 60
                minutes = minutes % 60
                time_str = f"[{hours:02d}:{minutes:02d}:{seconds:02d}] "
            else:
                time_str = f"[{minutes:02d}:{seconds:02d}] "
                
        formatted_transcript += f"{time_str}{user_name}: {t.get('text', '')}\n"

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
Be objective, professional, and detailed. Your goal is to provide a comprehensive, high-value report that helps users who missed the meeting or had connection issues quickly catch up on what was discussed, what questions were resolved, and what decisions were made. Do not include individual ratings or arbitrary scores.
"""

    user_prompt = f"""Transcript:
{formatted_transcript}

{alerts_summary}

Please return a JSON object with the following structure:
{{
  "summary": "A detailed 1-2 paragraph executive summary covering the overall meeting flow, main objectives, key discussion points, and final outcomes.",
  "sentiment": "Positive, Neutral, or Negative",
  "topic_timeline": [
    {{
      "topic": "Name of the topic/agenda item (e.g., 'Salary Negotiation' or 'Architecture Review')",
      "time_bracket": "Estimate the start and end timestamps in transcript (e.g., '11:18 - 13:05' or '00:00 - 05:20')",
      "summary": "A detailed multi-sentence description of the discussion, including key arguments, conflicting viewpoints, and resolutions.",
      "key_takeaways": [
         "Specific key detail or argument 1", 
         "Specific key detail or argument 2"
      ]
    }}
  ],
  "resolved_qna": [
    {{
      "question": "Important question asked during the meeting",
      "asked_by": "Name of the person who asked the question",
      "answer": "The answer or consensus reached, or 'Unresolved' if not answered"
    }}
  ],
  "action_items": [
    {{ "description": "Clear, actionable task (include assignee if known, e.g., 'Notify Priyanshu of the final decision - Priyansh')" }}
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
        "model": AI_MODEL,
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
            db.close()
            return
    except Exception as e:
        print(f"[{meeting_id}] EXCEPTION: Error calling Groq: {e}")
        db.close()
        return

    # 4. Save Granular Data to DB
    
    # Fetch participants to resolve assignee_ids
    participants_map = {}
    try:
        participants = db.query(models.MeetingParticipant).filter(models.MeetingParticipant.meeting_id == m_uuid).all()
        for p in participants:
            name = p.display_name
            if name:
                participants_map[name.lower()] = p.user_id
    except Exception as e:
        print(f"[{meeting_id}] Warning: Could not fetch participants: {e}")

    # Action Items
    for item in ai_output.get("action_items", []):
        desc_raw = item.get("description", "")
        if not desc_raw:
            continue
            
        task = desc_raw
        assignee_id = None
        
        # Try to parse "Task - Owner" format
        if " - " in desc_raw:
            parts = desc_raw.rsplit(" - ", 1)
            task = parts[0].strip()
            owner = parts[1].strip().lower()
            for p_name, p_id in participants_map.items():
                if owner in p_name or p_name in owner:
                    assignee_id = p_id
                    break
        elif " – " in desc_raw:
            parts = desc_raw.rsplit(" – ", 1)
            task = parts[0].strip()
            owner = parts[1].strip().lower()
            for p_name, p_id in participants_map.items():
                if owner in p_name or p_name in owner:
                    assignee_id = p_id
                    break

        try:
            from uuid import uuid4
            db_action = models.ActionItem(
                id=uuid4(),
                meeting_id=m_uuid,
                description=desc_raw,
                is_completed=False,
                assignee_id=assignee_id
            )
            db.add(db_action)
            db.flush()
        except Exception as e:
            print(f"[{meeting_id}] Warning: Could not save action item to DB: {e}")

    # Decisions
    for dec in ai_output.get("decisions", []):
        try:
            from uuid import uuid4
            db_decision = models.Decision(
                id=uuid4(),
                meeting_id=m_uuid,
                description=dec
            )
            db.add(db_decision)
            db.flush()
        except Exception as e:
            print(f"[{meeting_id}] Warning: Could not save decision to DB: {e}")

    # 5. Save final Analysis
    try:
        insights_data = ai_output.get("insights", {})
        if not isinstance(insights_data, dict):
            insights_data = {}
        if "topic_timeline" in ai_output:
            insights_data["topic_timeline"] = ai_output["topic_timeline"]
        if "resolved_qna" in ai_output:
            insights_data["resolved_qna"] = ai_output["resolved_qna"]

        existing_analysis = db.query(models.MeetingAnalysis).filter(models.MeetingAnalysis.meeting_id == m_uuid).first()
        if existing_analysis:
            existing_analysis.summary = ai_output.get("summary", "")
            existing_analysis.sentiment = ai_output.get("sentiment", "Neutral")
            existing_analysis.mode = mode
            existing_analysis.insights = insights_data
        else:
            from uuid import uuid4
            db_analysis = models.MeetingAnalysis(
                id=uuid4(),
                meeting_id=m_uuid,
                summary=ai_output.get("summary", ""),
                sentiment=ai_output.get("sentiment", "Neutral"),
                mode=mode,
                insights=insights_data
            )
            db.add(db_analysis)
        
        db.commit()
        print(f"[{meeting_id}] ANALYSIS SAVED SUCCESSFULLY TO DB.")
        
        # Calculate duration
        duration_seconds = meeting_data.get("duration_seconds")
        duration_val = "N/A"
        if duration_seconds is not None:
            duration_val = str(max(1, round(duration_seconds / 60)))
        
        # Format date beautifully
        created_at_str = meeting_data.get("created_at")
        date_formatted = "Recent"
        if created_at_str:
            try:
                clean_dt_str = created_at_str.replace("Z", "+00:00")
                from datetime import datetime
                dt = datetime.fromisoformat(clean_dt_str)
                date_formatted = dt.strftime("%B %d, %Y")
            except Exception as e:
                print(f"[{meeting_id}] Warning formatting date: {e}")
                date_formatted = "Recent"

        # 6. Trigger Notifications
        report_data = {
            "title": meeting_data.get("title", f"Meeting {str(meeting_id)[:8]}"),
            "summary": ai_output.get("summary", ""),
            "action_items": [item.get("description") for item in ai_output.get("action_items", [])],
            "decisions": ai_output.get("decisions", []),
            "duration": duration_val,
            "date": date_formatted
        }
        
        try:
            participants = db.query(models.MeetingParticipant).filter(models.MeetingParticipant.meeting_id == m_uuid).all()
            for p in participants:
                user_email = None
                if p.user_id:
                    user_record = db.query(models.User).filter(models.User.id == p.user_id).first()
                    if user_record:
                        user_email = user_record.email
                
                receive_report = p.receive_report if p.receive_report is not None else True
                if receive_report:
                    email = user_email or p.email
                    if email:
                        try:
                            celery_client.send_task(
                                "send_meeting_summary_email",
                                kwargs={
                                    "meeting_id": str(meeting_id),
                                    "to_email": email,
                                    "report_data": report_data
                                }
                            )
                            print(f"[{meeting_id}] Notification task dispatched to {email}.")
                        except Exception as e:
                            print(f"[{meeting_id}] Warning: Notification failed for {email}: {e}")
                else:
                    print(f"[{meeting_id}] Skipping summary email for {user_email or p.email or p.user_id} per report settings.")
        except Exception as e:
            print(f"[{meeting_id}] Warning: Notification dispatching failed: {e}")
    except Exception as e:
        db.rollback()
        print(f"[{meeting_id}] EXCEPTION in saving final analysis to DB: {e}")
    finally:
        db.close()


def generate_personal_analysis(meeting_id, user_id):
    print(f"[{meeting_id} - {user_id}] STARTING PERSONAL ANALYSIS (Direct DB Access)...")
    
    db = SessionLocal()
    m_uuid = UUID(str(meeting_id))
    u_uuid = UUID(str(user_id))
    
    try:
        segments = db.query(models.TranscriptSegment)\
            .filter(models.TranscriptSegment.meeting_id == m_uuid, models.TranscriptSegment.user_id == u_uuid)\
            .order_by(models.TranscriptSegment.start_time.asc()).all()
            
        transcripts = []
        for s in segments:
            transcripts.append({
                "user_name": s.user_name,
                "text": s.text,
                "start_time": s.start_time
            })
    except Exception as e:
        print(f"[{meeting_id} - {user_id}] EXCEPTION fetching transcripts: {e}")
        db.close()
        return

    if not transcripts:
        print(f"[{meeting_id} - {user_id}] No transcripts found for user.")
        db.close()
        return

    formatted_transcript = ""
    for t in transcripts:
        user_name = t.get("user_name") or "Unknown"
        
        # Format start_time (stored in milliseconds) to human-readable time [MM:SS] or [HH:MM:SS]
        start_ms = t.get("start_time")
        time_str = ""
        if start_ms is not None:
            total_seconds = int(start_ms) // 1000
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            if minutes >= 60:
                hours = minutes // 60
                minutes = minutes % 60
                time_str = f"[{hours:02d}:{minutes:02d}:{seconds:02d}] "
            else:
                time_str = f"[{minutes:02d}:{seconds:02d}] "
                
        formatted_transcript += f"{time_str}{user_name}: {t.get('text', '')}\n"

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
        "model": AI_MODEL,
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
            db.close()
            return
    except Exception as e:
        print(f"[{meeting_id} - {user_id}] EXCEPTION: Error calling Groq: {e}")
        db.close()
        return

    try:
        existing_coach = db.query(models.UserTranscriptAnalysis)\
            .filter(models.UserTranscriptAnalysis.meeting_id == m_uuid, models.UserTranscriptAnalysis.user_id == u_uuid)\
            .first()
            
        if existing_coach:
            existing_coach.analysis_data = ai_output
        else:
            from uuid import uuid4
            db_coach = models.UserTranscriptAnalysis(
                id=uuid4(),
                meeting_id=m_uuid,
                user_id=u_uuid,
                analysis_data=ai_output
            )
            db.add(db_coach)
        db.commit()
        print(f"[{meeting_id} - {user_id}] PERSONAL ANALYSIS SAVED SUCCESSFULLY TO DB.")
    except Exception as e:
        db.rollback()
        print(f"[{meeting_id} - {user_id}] EXCEPTION saving analysis to DB: {e}")
    finally:
        db.close()


def main():
    print("--------------------------------------------------")
    print(f"AI Service Worker starting...")
    print(f"PORT environment variable: {PORT}")
    print(f"LOCAL_MEETING_SERVICE_URL: {LOCAL_MEETING_SERVICE_URL}")
    print(f"MEETING_SERVICE_URL: {MEETING_SERVICE_URL}")
    print(f"REDIS_URL: {REDIS_URL}")
    print(f"REDIS_QUEUE_PREFIX: {REDIS_QUEUE_PREFIX}")
    print(f"AI_MODEL: {AI_MODEL}")
    print("--------------------------------------------------")
    print(f"AI Service Worker started. Listening to queues...")
    
    while True:
        try:
            result = redis_client.blpop([
                f"{REDIS_QUEUE_PREFIX}meeting_ended_queue",
                f"{REDIS_QUEUE_PREFIX}personal_analysis_queue"
            ], timeout=30)
            if result:
                queue_name, payload_str = result
                queue_str = queue_name.decode() if isinstance(queue_name, bytes) else queue_name
                print(f"[Queue Listener] Popped item from '{queue_str}': {payload_str}")
                
                payload = json.loads(payload_str)
                meeting_id = payload.get("meeting_id")
                print(f"[Queue Listener] Parsed meeting_id: {meeting_id}")
                
                if queue_str == f"{REDIS_QUEUE_PREFIX}meeting_ended_queue" and meeting_id:
                    print(f"[Queue Listener] Triggering generate_meeting_report({meeting_id})...")
                    generate_meeting_report(meeting_id)
                elif queue_str == f"{REDIS_QUEUE_PREFIX}personal_analysis_queue" and meeting_id:
                    user_id = payload.get("user_id")
                    print(f"[Queue Listener] Triggering generate_personal_analysis({meeting_id}, {user_id})...")
                    if user_id:
                        generate_personal_analysis(meeting_id, user_id)
            else:
                # Debug print every 30 seconds when blpop times out
                print("[Queue Listener] Idle. Waiting for queue messages...")
        except redis.exceptions.TimeoutError:
            continue
        except redis.exceptions.ConnectionError as e:
            print(f"Queue connection error: {e}")
            time.sleep(1)
        except Exception as e:
            print(f"Queue error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
