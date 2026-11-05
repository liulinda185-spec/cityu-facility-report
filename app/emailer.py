"""SMTP email — reads config from env vars.
If SMTP_USER/SMTP_PASS are not set, emails are logged to console only (dev mode).
Set PILOT_CATCHALL to one address to force ALL emails there during pilot testing."""
import os, ssl, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_ADDR = os.getenv("FROM_ADDR", SMTP_USER or "facility-report@cityu.local")
PILOT_CATCHALL = os.getenv("PILOT_CATCHALL", "")  # e.g. "liulinda185@gmail.com"

def send(to_addr: str, subject: str, body: str):
    # Pilot testing: override recipient
    if PILOT_CATCHALL:
        to_addr = PILOT_CATCHALL
    if not to_addr:
        return
    if not SMTP_USER or not SMTP_PASS:
        print(f"[DEV EMAIL] to={to_addr} subject={subject}")
        print(body)
        return
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = FROM_ADDR
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain", "utf-8"))
    ctx = ssl.create_default_context()
    # A hung SMTP server used to hang the request that triggered the email.
    # Every socket now has a deadline of its own.
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=10) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(FROM_ADDR, to_addr, msg.as_string())


def send_safe(to_addr: str, subject: str, body: str) -> bool:
    """Email must never turn a saved report into a 500.

    Called from a background task, so the reporter's request has already
    returned; a failure is logged and swallowed here.
    """
    try:
        send(to_addr, subject, body)
        return True
    except Exception as exc:  # noqa: BLE001 - any SMTP failure is non-fatal
        print(f"[EMAIL FAILED] to={to_addr} subject={subject!r}: {exc}", flush=True)
        return False

