"""
DATA ENGINE — Onboarding Email Service
Sends verification and notification emails via SMTP.
All emails are plain-text + HTML multipart for maximum deliverability.
"""

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import structlog

from configs.settings import get_settings

logger = structlog.get_logger(__name__)


def _send(to: str, subject: str, html: str, plain: str) -> None:
    """
    Internal SMTP send helper.
    Sends a multipart/alternative email (plain + HTML).
    Raises on any SMTP error so callers can handle gracefully.
    """
    s = get_settings()

    if not s.smtp_host or not s.smtp_user:
        logger.warning("email_not_configured_skipping", to=to, subject=subject)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = s.smtp_from
    msg["To"]      = to

    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html,  "html",  "utf-8"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port) as server:
            if s.smtp_use_tls:
                server.starttls(context=context)
            server.login(s.smtp_user, s.smtp_password)
            server.sendmail(s.smtp_from, to, msg.as_string())
        logger.info("email_sent", to=to, subject=subject)
    except Exception as exc:
        logger.error("email_send_failed", to=to, subject=subject, error=str(exc))
        raise


def send_verification_email(to: str, product_name: str, token: str) -> None:
    """
    Send the email verification link after a product submits the connect wizard.
    The token is a one-time UUID stored on the tenant record.
    """
    s = get_settings()
    verify_url = f"{s.engine_public_url}/onboard/verify-email?token={token}"

    subject = "Verify your email — connect your product to the Data Engine"

    plain = f"""Hi,

You're one step away from connecting {product_name} to the Tek Juice Data Engine.

Click the link below to verify your email and complete the connection:

{verify_url}

This link expires in 24 hours.

If you did not request this, ignore this email.

— Tek Juice Data Engine
"""

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,sans-serif;font-size:15px;color:#1f2328;max-width:520px;margin:40px auto;padding:0 24px;">
  <h2 style="font-size:20px;font-weight:700;margin-bottom:8px;">Verify your email</h2>
  <p style="color:#57606a;margin-bottom:24px;">
    You're one step away from connecting <strong>{product_name}</strong>
    to the Tek Juice Data Engine.
  </p>
  <a href="{verify_url}"
     style="display:inline-block;background:#3b82d4;color:#ffffff;font-weight:600;
            padding:12px 28px;border-radius:6px;text-decoration:none;font-size:14px;">
    Verify Email &amp; Continue →
  </a>
  <p style="color:#57606a;font-size:13px;margin-top:24px;">
    This link expires in 24 hours.<br>
    If you did not request this, ignore this email.
  </p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:32px 0;">
  <p style="color:#57606a;font-size:12px;">Tek Juice Data Engine</p>
</body>
</html>"""

    _send(to, subject, html, plain)


def send_onboarding_complete_email(to: str, product_name: str, dashboard_url: str) -> None:
    """
    Send a confirmation email once the injection bridge is live
    and the first crawl has been queued.
    """
    subject = f"✅ {product_name} is connected — the engine is running"

    plain = f"""Your product is now connected to the Tek Juice Data Engine.

What happens next (automatically):
- Your website is being crawled right now
- Content gaps will be detected within minutes
- AI-generated content will be published to your website automatically
- Your search visibility will begin climbing within 24–48 hours

View your dashboard:
{dashboard_url}

You do not need to do anything else. The engine runs on its own.

— Tek Juice Data Engine
"""

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,sans-serif;font-size:15px;color:#1f2328;max-width:520px;margin:40px auto;padding:0 24px;">
  <h2 style="font-size:20px;font-weight:700;margin-bottom:8px;">✅ {product_name} is connected</h2>
  <p style="color:#57606a;margin-bottom:16px;">The Data Engine is now running for your product. Here is what happens next — automatically:</p>
  <ul style="color:#57606a;padding-left:20px;line-height:1.8;">
    <li>Your website is being crawled right now</li>
    <li>Content gaps will be detected within minutes</li>
    <li>AI-generated content will be published to your website automatically</li>
    <li>Your search visibility will begin climbing within 24–48 hours</li>
  </ul>
  <p style="margin:24px 0 8px;font-weight:600;">View your dashboard:</p>
  <a href="{dashboard_url}"
     style="display:inline-block;background:#16a34a;color:#ffffff;font-weight:600;
            padding:12px 28px;border-radius:6px;text-decoration:none;font-size:14px;">
    Open Dashboard →
  </a>
  <p style="color:#57606a;font-size:13px;margin-top:24px;">
    You do not need to do anything else. The engine runs entirely on its own.
  </p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:32px 0;">
  <p style="color:#57606a;font-size:12px;">Tek Juice Data Engine</p>
</body>
</html>"""

    _send(to, subject, html, plain)
