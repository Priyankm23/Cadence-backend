import os
import json
import urllib.request
from celery import Celery
from jinja2 import Environment, FileSystemLoader
from core.config import settings

# Initialize Celery
celery_app = Celery(
    "notification_worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL
)

# Setup Jinja2 templates
template_dir = os.path.join(os.path.dirname(__file__), "templates")
jinja_env = Environment(loader=FileSystemLoader(template_dir))

def send_email_brevo(to_email: str, subject: str, html_content: str):
    """Sends email using Brevo (Sendinblue) REST API. Works on both local and Render."""
    if not getattr(settings, 'BREVO_API_KEY', None):
        print("[Email] Error: BREVO_API_KEY is not set in environment.")
        return

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": settings.BREVO_API_KEY,
        "content-type": "application/json"
    }
    
    payload = {
        "sender": {"email": settings.FROM_EMAIL, "name": "Cadence-AI"},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content
    }

    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req) as response:
            res_body = response.read()
            print(f"[Email] Sent successfully to {to_email} via Brevo. Response: {res_body}")
    except urllib.error.HTTPError as e:
        error_info = e.read().decode("utf-8")
        print(f"[Email] Brevo API Error ({e.code}): {error_info}")
    except Exception as e:
        print(f"[Email] Failed to send email: {e}")

@celery_app.task(name="send_meeting_summary_email")
def send_meeting_summary_email(meeting_id: str, to_email: str, report_data: dict):
    print(f"Preparing to send summary for meeting {meeting_id} to {to_email}")
    
    # Parse action items into structured dicts with task and owner
    action_items_raw = report_data.get("action_items", [])
    action_items = []
    for item in action_items_raw:
        if isinstance(item, str):
            desc = item
        elif isinstance(item, dict):
            desc = item.get("description", "") or item.get("task", "")
        else:
            desc = str(item)
            
        if not desc:
            continue
            
        # Parse description to task and owner (splitting on last hyphen or en-dash)
        if " - " in desc:
            parts = desc.rsplit(" - ", 1)
            task = parts[0].strip()
            owner = parts[1].strip()
        elif " – " in desc:
            parts = desc.rsplit(" – ", 1)
            task = parts[0].strip()
            owner = parts[1].strip()
        else:
            task = desc
            owner = "Unassigned"
            
        action_items.append({
            "task": task,
            "owner": owner
        })

    # Render HTML template
    template = jinja_env.get_template("meeting_summary.html")
    html_content = template.render(
        meeting_id=meeting_id,
        meeting_title=report_data.get("title", "Meeting Summary"),
        date=report_data.get("date", "Recent"),
        duration=report_data.get("duration", "N/A"),
        summary=report_data.get("summary", "No summary provided."),
        action_items=action_items,
        decisions=report_data.get("decisions", []),
        dashboard_url=os.getenv("FRONTEND_URL", "http://localhost:3000")
    )
    
    subject = f"AI Summary: {report_data.get('title', 'Your Recent Meeting')}"
    
    # Use the unified API method for both dev and prod
    send_email_brevo(to_email, subject, html_content)
        
    return {"status": "success", "meeting_id": meeting_id, "to": to_email}

@celery_app.task(name="send_scheduled_meeting_invite_email")
def send_scheduled_meeting_invite_email(to_email: str, invite_data: dict):
    print(f"Preparing to send meeting invite to {to_email}")
    
    # Handle objectives (split by hyphen if they are merged)
    objectives_raw = invite_data.get("objectives", "")
    objectives_list = []
    if objectives_raw:
        if " - " in objectives_raw:
            objectives_list = [obj.strip() for obj in objectives_raw.split(" - ") if obj.strip()]
        else:
            objectives_list = [objectives_raw.strip()]
    
    template = jinja_env.get_template("scheduled_meeting_invite.html")
    html_content = template.render(
        meeting_title=invite_data.get("title", "Scheduled Meeting"),
        date=invite_data.get("date", "TBD"),
        time=invite_data.get("time", "TBD"),
        duration=invite_data.get("duration", "N/A"),
        objectives=objectives_list,
        host_name=invite_data.get("host_name", "Host"),
        join_url=invite_data.get("join_url", "#"),
        join_code=invite_data.get("join_code", "")
    )
    
    subject = f"Invitation: {invite_data.get('title', 'Scheduled Meeting')}"
    send_email_brevo(to_email, subject, html_content)
    
    return {"status": "success", "to": to_email}

@celery_app.task(name="send_meeting_started_email")
def send_meeting_started_email(to_email: str, invite_data: dict):
    print(f"Preparing to send meeting started notification to {to_email}")
    template = jinja_env.get_template("meeting_started_notification.html")
    html_content = template.render(
        meeting_title=invite_data.get("title", "Live Meeting"),
        host_name=invite_data.get("host_name", "Host"),
        join_url=invite_data.get("join_url", "#"),
        join_code=invite_data.get("join_code", "")
    )
    
    subject = f"Live Now: {invite_data.get('title', 'Your Scheduled Meeting has Started')}"
    send_email_brevo(to_email, subject, html_content)
    
    return {"status": "success", "to": to_email}
