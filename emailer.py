import logging
import os
import smtplib
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_alert(subject: str, body: str, to: str | None = None) -> bool:
    user = os.getenv("GMAIL_USER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    recipient = to or os.getenv("ALERT_TO") or user

    if not user or not password or not recipient:
        log.error("Missing GMAIL_USER / GMAIL_APP_PASSWORD / ALERT_TO in env")
        return False

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.error("Failed to send email: %s", e)
        return False

    log.info("Sent alert to %s: %s", recipient, subject)
    return True
