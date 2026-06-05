import os
import json
import base64
import ssl
import httpx
from celery import Celery
from jinja2 import Environment, FileSystemLoader
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from core.config import settings

# Initialize Celery
redis_url = settings.REDIS_URL
if redis_url.startswith("rediss://"):
    if "ssl_cert_reqs" not in redis_url:
        separator = "&" if "?" in redis_url else "?"
        redis_url = f"{redis_url}{separator}ssl_cert_reqs=CERT_NONE"

celery_app = Celery(
    "notification_worker",
    broker=redis_url,
    backend=redis_url
)

if settings.REDIS_URL.startswith("rediss://"):
    celery_app.conf.update(
        broker_use_ssl={"ssl_cert_reqs": ssl.CERT_NONE},
        redis_backend_use_ssl={"ssl_cert_reqs": ssl.CERT_NONE},
    )

if settings.REDIS_QUEUE_PREFIX:
    celery_app.conf.update(
        task_default_queue=f"{settings.REDIS_QUEUE_PREFIX}celery",
    )

# Setup Jinja2 templates
template_dir = os.path.join(os.path.dirname(__file__), "templates")
jinja_env = Environment(loader=FileSystemLoader(template_dir))

def get_gmail_access_token():
    if not settings.GMAIL_CLIENT_ID or not settings.GMAIL_CLIENT_SECRET or not settings.GMAIL_REFRESH_TOKEN:
        print("[Email] Error: Gmail credentials are not fully set in environment (GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN).")
        return None

    url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": settings.GMAIL_CLIENT_ID,
        "client_secret": settings.GMAIL_CLIENT_SECRET,
        "refresh_token": settings.GMAIL_REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    
    try:
        response = httpx.post(url, data=payload, timeout=10.0)
        if response.status_code == 200:
            token_data = response.json()
            return token_data.get("access_token")
        else:
            print(f"[Email] Failed to refresh Gmail access token. Status code: {response.status_code}. Response: {response.text}")
            return None
    except Exception as e:
        print(f"[Email] Exception during token refresh: {e}")
        return None

def send_email_gmail(to_email: str, subject: str, html_content: str):
    """Sends email using Gmail REST API. Works on both local and Render."""
    access_token = get_gmail_access_token()
    if not access_token:
        print("[Email] Error: Unable to retrieve Gmail access token. Skipping send.")
        return

    # Construct MIME Message
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = Header(subject, 'utf-8').encode()
        msg['From'] = f"Cadence-AI <{settings.FROM_EMAIL}>"
        msg['To'] = to_email

        html_part = MIMEText(html_content, 'html', 'utf-8')
        msg.attach(html_part)

        raw_bytes = msg.as_bytes()
        encoded_message = base64.urlsafe_b64encode(raw_bytes).decode('utf-8')
    except Exception as e:
        print(f"[Email] Failed to construct MIME message: {e}")
        return

    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "raw": encoded_message
    }

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=15.0)
        if response.status_code == 200:
            print(f"[Email] Sent successfully to {to_email} via Gmail API. Msg ID: {response.json().get('id')}")
        else:
            print(f"[Email] Gmail API Error ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"[Email] Failed to send email via Gmail API: {e}")

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
    send_email_gmail(to_email, subject, html_content)
        
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
    send_email_gmail(to_email, subject, html_content)
    
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
    send_email_gmail(to_email, subject, html_content)
    
    return {"status": "success", "to": to_email}
