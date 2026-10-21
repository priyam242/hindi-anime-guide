"""Email OTP service: generation, HMAC-SHA256 hashing, rate limiting, and email dispatch."""
from __future__ import annotations
import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from .config import get_settings
from .models import EmailOtp, TrustedDevice

logger = logging.getLogger(__name__)

# In-memory test outbox for capturing sent emails without network activity
EMAIL_OUTBOX: list[dict] = []


def compute_otp_hash(code: str, secret_key: str, email: str, purpose: str) -> str:
    """Store OTPs only as HMAC-SHA256(code, secret_key + email + purpose)."""
    clean_code = str(code).strip()
    clean_email = email.strip().lower()
    clean_purpose = purpose.strip().lower()
    key = f"{secret_key}{clean_email}{clean_purpose}".encode("utf-8")
    return hmac.new(key, clean_code.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_otp_hash(submitted_code: str, stored_hash: str, secret_key: str, email: str, purpose: str) -> bool:
    """Constant-time verification of submitted code against stored HMAC-SHA256."""
    expected_hash = compute_otp_hash(submitted_code, secret_key, email, purpose)
    return hmac.compare_digest(stored_hash, expected_hash)


def generate_otp_code() -> str:
    """Cryptographically secure 6-digit OTP."""
    return f"{secrets.randbelow(1_000_000):06d}"


def mask_email(email: str) -> str:
    """Mask email for display: priyam@gmail.com -> p***@gmail.com."""
    clean = email.strip().lower()
    if "@" not in clean:
        return clean
    user_part, domain = clean.split("@", 1)
    if not user_part:
        return f"***@{domain}"
    return f"{user_part[0]}***@{domain}"


def check_otp_rate_limits(
    session: Session, email: str, purpose: str, ip: Optional[str] = None
) -> tuple[bool, Optional[str], int]:
    """
    Check OTP generation/resend rate limits:
    - 60 seconds cooldown per email
    - Max 5 codes per email per hour
    - Max 20 codes per IP per hour
    Returns: (is_allowed, error_message, cooldown_remaining_seconds)
    """
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    clean_email = email.strip().lower()

    # 1. Max 5 codes per email per hour
    hourly_email_count = len(
        session.exec(
            select(EmailOtp.id)
            .where(EmailOtp.email == clean_email, EmailOtp.created_at >= one_hour_ago)
        ).all()
    )
    if hourly_email_count >= 5:
        return False, "प्रति घंटे में अधिकतम 5 कोड सीमा समाप्त। कृपया 1 घंटे बाद पुनः प्रयास करें।", 3600

    # 2. Max 20 codes per IP per hour
    if ip:
        hourly_ip_count = len(
            session.exec(
                select(EmailOtp.id)
                .where(EmailOtp.ip == ip, EmailOtp.created_at >= one_hour_ago)
            ).all()
        )
        if hourly_ip_count >= 20:
            return False, "इस आईपी से प्रति घंटे में अधिकतम 20 कोड सीमा समाप्त। कृपया 1 घंटे बाद पुनः प्रयास करें।", 3600

    # 3. 60-second resend cooldown (for unused active codes)
    last_code = session.exec(
        select(EmailOtp)
        .where(EmailOtp.email == clean_email, EmailOtp.purpose == purpose, EmailOtp.used == False)
        .order_by(EmailOtp.created_at.desc())
    ).first()

    if last_code:
        created_at = last_code.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        elapsed = (now - created_at).total_seconds()
        if elapsed < 60:
            remaining = int(60 - elapsed)
            return False, f"कृपया {remaining} सेकंड प्रतीक्षा करें और पुनः प्रयास करें।", remaining

    return True, None, 0


async def send_otp_email(to_email: str, code: str, purpose: str = "login") -> tuple[bool, Optional[str]]:
    """
    Send OTP email.
    In development without SMTP: logs clearly to server console.
    In production without SMTP: refuses with admin-facing error.
    With SMTP: sends via aiosmtplib with STARTTLS and timeout.
    """
    settings = get_settings()
    env = settings.env.lower()
    is_prod = (env == "production")

    # Safety guard: In testing environment, NEVER open network connection, only append to in-memory outbox
    if env in ("testing", "test"):
        record = {
            "to": to_email,
            "code": code,
            "purpose": purpose,
            "subject": f"{code} — Hindi Anime Guide सत्यापन कोड",
            "sent_at": datetime.now(timezone.utc),
        }
        EMAIL_OUTBOX.append(record)
        return True, None

    # 1. Dev Mode: print code to console when SMTP not configured
    if not settings.smtp_host and not is_prod:
        print("\n" + "=" * 55)
        print(f" [DEV MODE OTP] Hindi Anime Guide")
        print(f" To: {to_email}")
        print(f" Code: {code}")
        print(f" Purpose: {purpose.upper()}")
        print(f" Valid for: 10 minutes")
        print("=" * 55 + "\n", flush=True)
        return True, None

    # 2. Production mode without SMTP: refuse to send
    if not settings.smtp_host:
        logger.error("Production mode: SMTP_HOST is not configured.")
        return False, "ईमेल सेवा (SMTP) कॉन्फ़िगर नहीं है। कृपया एडमिन से संपर्क करें।"

    # 3. Real SMTP transmission
    subject = f"{code} — Hindi Anime Guide सत्यापन कोड"
    plain_body = f"""नमस्ते,

आपका Hindi Anime Guide सत्यापन कोड है:
{code}

यह कोड 10 मिनट तक मान्य है।

अगर आपने लॉगिन या खाता बनाने की कोशिश नहीं की तो इस ईमेल को अनदेखा करें और किसी को भी कोड न बताएं।

Hindi Anime Guide टीम
"""
    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: whitesmoke; color: black; padding: 24px; margin: 0;">
  <div style="max-width: 480px; margin: 0 auto; background: white; border-radius: 12px; padding: 32px; border: 1px solid gainsboro;">
    <div style="text-align: center; margin-bottom: 24px;">
      <h2 style="color: crimson; margin: 0; font-size: 22px;">Hindi Anime Guide</h2>
      <p style="color: dimgray; font-size: 14px; margin-top: 6px;">सत्यापन कोड (Verification Code)</p>
    </div>
    <div style="background: whitesmoke; border-radius: 10px; padding: 18px; text-align: center; margin-bottom: 24px;">
      <div style="font-size: 34px; font-weight: 700; letter-spacing: 6px; color: black; font-family: monospace;">{code}</div>
    </div>
    <p style="font-size: 14px; line-height: 1.6; color: darkslategray; margin-bottom: 16px;">
      यह कोड <strong>10 मिनट</strong> तक मान्य है।
    </p>
    <p style="font-size: 13px; line-height: 1.5; color: dimgray; margin-bottom: 0;">
      अगर आपने लॉगिन या खाता बनाने की कोशिश नहीं की तो इस ईमेल को अनदेखा करें और किसी को भी कोड न बताएं।
    </p>
  </div>
</body>
</html>"""

    try:
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = settings.smtp_from or "noreply@animeguide.com"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(plain_body)
        msg.add_alternative(html_body, subtype="html")

        import aiosmtplib
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user if settings.smtp_user else None,
            password=settings.smtp_password if settings.smtp_password else None,
            start_tls=(settings.smtp_port == 587),
            timeout=10,
        )
        return True, None
    except Exception as exc:
        logger.error(f"SMTP error while sending OTP: {type(exc).__name__}")
        return False, "ईमेल भेजने में त्रुटि हुई। कृपया थोड़ी देर बाद पुनः प्रयास करें।"


async def create_and_send_email_otp(
    session: Session,
    email: str,
    purpose: str,
    ip: Optional[str] = None,
    pending_payload: Optional[str] = None,
) -> tuple[bool, Optional[str], Optional[EmailOtp], str]:
    """
    Generate new OTP, invalidate older unused codes, and dispatch via email.
    Returns: (success, error_message, email_otp_row, plain_code)
    """
    settings = get_settings()
    clean_email = email.strip().lower()

    # Rate limit check
    allowed, err_msg, _ = check_otp_rate_limits(session, clean_email, purpose, ip)
    if not allowed:
        return False, err_msg, None, ""

    # Invalidate older unused codes for this email and purpose
    older_codes = session.exec(
        select(EmailOtp).where(
            EmailOtp.email == clean_email,
            EmailOtp.purpose == purpose,
            EmailOtp.used == False,  # noqa: E712
        )
    ).all()
    for oc in older_codes:
        oc.used = True
        session.add(oc)

    # Generate 6-digit code and store HMAC-SHA256
    code = generate_otp_code()
    code_hash = compute_otp_hash(code, settings.secret_key or "dev", clean_email, purpose)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=10)

    otp_row = EmailOtp(
        email=clean_email,
        purpose=purpose,
        code_hash=code_hash,
        expires_at=expires_at,
        attempts=0,
        used=False,
        created_at=now,
        pending_payload=pending_payload,
        ip=ip,
    )
    session.add(otp_row)
    session.commit()
    session.refresh(otp_row)

    # Dispatch email
    sent, send_err = await send_otp_email(clean_email, code, purpose)
    if not sent:
        # Invalidate code if transmission failed in production
        otp_row.used = True
        session.add(otp_row)
        session.commit()
        return False, send_err, None, ""

    return True, None, otp_row, code
