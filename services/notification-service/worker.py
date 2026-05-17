import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from celery import Celery
import resend
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

def send_email_dev(to_email: str, subject: str, html_content: str): 
    """Sends email using SMTP (e.g., Mailtrap, Ethereal) for development."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.FROM_EMAIL
    msg["To"] = to_email

    part = MIMEText(html_content, "html")
    msg.attach(part)

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            # server.set_debuglevel(1)
            if settings.SMTP_USER and settings.SMTP_PASS:
                server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.sendmail(settings.FROM_EMAIL, to_email, msg.as_string())
        print(f"[DEV] Email sent successfully to {to_email} via SMTP ({settings.SMTP_HOST})")
    except Exception as e:
        print(f"[DEV] Failed to send email: {e}")

def send_email_prod(to_email: str, subject: str, html_content: str):
    """Sends email using Resend API for production."""
    resend.api_key = settings.RESEND_API_KEY
    params = {
        "from": settings.FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html_content,
    }
    try:
        email = resend.Emails.send(params)
        print(f"[PROD] Email sent successfully to {to_email} via Resend. ID: {email.get('id')}")
    except Exception as e:
        print(f"[PROD] Failed to send email via Resend: {e}")

@celery_app.task(name="send_meeting_summary_email")
def send_meeting_summary_email(meeting_id: str, to_email: str, report_data: dict):
    print(f"Preparing to send summary for meeting {meeting_id} to {to_email}")
    
    # Render HTML template
    template = jinja_env.get_template("meeting_summary.html")
    html_content = template.render(
        meeting_id=meeting_id,
        meeting_title=report_data.get("title", "Meeting Summary"),
        date=report_data.get("date", "Recent"),
        duration=report_data.get("duration", "N/A"),
        summary=report_data.get("summary", "No summary provided."),
        action_items=report_data.get("action_items", []),
        decisions=report_data.get("decisions", []),
        dashboard_url=os.getenv("FRONTEND_URL", "http://localhost:3000")
    )
    
    subject = f"AI Summary: {report_data.get('title', 'Your Recent Meeting')}"
    
    if settings.ENVIRONMENT == "production":
        send_email_prod(to_email, subject, html_content)
    else:
        send_email_dev(to_email, subject, html_content)
        
    return {"status": "success", "meeting_id": meeting_id, "to": to_email}
