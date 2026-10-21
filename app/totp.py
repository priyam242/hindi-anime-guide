"""
2FA / TOTP service — pyotp + Fernet encryption + backup codes + trusted devices.

Security rules enforced here:
- TOTP secrets are Fernet-encrypted at rest; never returned after enrollment.
- Backup codes stored as SHA-256(salt‖code); shown exactly once.
- Trusted-device tokens stored as SHA-256(token); cookie carries raw token.
- Codes/secrets/tokens are NEVER logged.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import secrets
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import pyotp
import qrcode
from qrcode.image.pil import PilImage

from .config import get_settings

# ── Fernet lazy import (only available after `pip install cryptography`) ────────
try:
    from cryptography.fernet import Fernet, InvalidToken
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False

TOTP_ISSUER = "Hindi Anime Guide"
TOTP_WINDOW = 1           # ±1 period tolerance (30 s each)
PRE_2FA_TOKEN_TTL = 300   # seconds (5 min)
TRUSTED_DEVICE_DAYS = 30
BACKUP_CODE_COUNT = 10
MAX_FAIL_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


# ── Fernet key helpers ─────────────────────────────────────────────────────────

def _get_fernet() -> "Fernet":
    """Return a Fernet cipher from TOTP_ENC_KEY env var."""
    if not _FERNET_AVAILABLE:
        raise RuntimeError("cryptography package not installed — cannot use 2FA encryption.")
    settings = get_settings()
    raw_key = getattr(settings, "totp_enc_key", None) or os.environ.get("TOTP_ENC_KEY", "")
    if not raw_key or raw_key in ("set_in_env", ""):
        # Dev fallback: derive from SECRET_KEY (never use in production)
        secret = (settings.secret_key or "dev-fallback-key-not-for-production").encode()
        derived = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
        return Fernet(derived)
    raw_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
    return Fernet(raw_bytes)


def encrypt_totp_secret(secret: str) -> str:
    """Encrypt a base32 TOTP secret for storage."""
    f = _get_fernet()
    return f.encrypt(secret.encode()).decode()


def decrypt_totp_secret(enc: str) -> str:
    """Decrypt a stored TOTP secret."""
    f = _get_fernet()
    try:
        return f.decrypt(enc.encode()).decode()
    except Exception:
        raise ValueError("Could not decrypt TOTP secret — key mismatch or tampered data.")


# ── TOTP core ──────────────────────────────────────────────────────────────────

def generate_totp_secret() -> str:
    """Generate a fresh 32-byte base32 TOTP secret."""
    return pyotp.random_base32(length=32)


def get_totp_uri(secret: str, email: str) -> str:
    """Build the otpauth:// URI for QR code generation."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=TOTP_ISSUER)


def verify_totp_code(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code with ±1 window."""
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=TOTP_WINDOW)


def generate_qr_data_uri(totp_uri: str) -> str:
    """Return a PNG QR code as a data: URI (no external requests)."""
    img = qrcode.make(totp_uri, image_factory=PilImage, box_size=6, border=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


# ── Backup codes ───────────────────────────────────────────────────────────────

def _hash_backup_code(salt: str, code: str) -> str:
    """SHA-256(salt + code) — stored; plaintext shown once."""
    raw = f"{salt}:{code}".encode()
    return hashlib.sha256(raw).hexdigest()


def generate_backup_codes() -> tuple[list[str], str]:
    """
    Generate BACKUP_CODE_COUNT single-use codes.
    Returns (plaintext_codes, salt).
    Caller must store (salt, hash_per_code) and show plaintext exactly once.
    """
    salt = secrets.token_hex(16)
    codes = [secrets.token_hex(5).upper() for _ in range(BACKUP_CODE_COUNT)]
    return codes, salt


def hash_backup_codes(codes: list[str], salt: str) -> list[str]:
    """Return a list of hashes corresponding to each plaintext code."""
    return [_hash_backup_code(salt, c) for c in codes]


def verify_backup_code(candidate: str, stored_hashes_with_salts: list[tuple[str, str]]) -> Optional[int]:
    """
    Check candidate against a list of (salt, hash) pairs.
    Returns the index of the matching unused code, or None.
    """
    candidate = candidate.strip().upper().replace(" ", "").replace("-", "")
    for idx, (salt, h) in enumerate(stored_hashes_with_salts):
        if _hash_backup_code(salt, candidate) == h:
            return idx
    return None


# ── Trusted devices ────────────────────────────────────────────────────────────

def generate_device_token() -> str:
    """32-byte URL-safe random token placed in cookie."""
    return secrets.token_urlsafe(32)


def hash_device_token(token: str) -> str:
    """SHA-256 of the token — stored in DB."""
    return hashlib.sha256(token.encode()).hexdigest()


def trusted_device_expires() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=TRUSTED_DEVICE_DAYS)


def _check_trusted_device_raw(session, kind: str, owner_id: int, request) -> bool:
    """Check if the request carries a valid trusted-device cookie. Used in login handler."""
    from .models import TrustedDevice
    cookie_token = request.cookies.get("trusted_device")
    if not cookie_token:
        return False
    token_hash = hash_device_token(cookie_token)
    now = datetime.now(timezone.utc)
    from sqlmodel import select
    device = session.exec(
        select(TrustedDevice).where(
            TrustedDevice.token_hash == token_hash,
            TrustedDevice.owner_kind == kind,
            TrustedDevice.owner_id == owner_id,
            TrustedDevice.revoked_at == None,  # noqa: E711
            TrustedDevice.expires_at > now,
        )
    ).first()
    return device is not None


# ── Pre-2FA token (short-lived, signed) ───────────────────────────────────────

def create_pre2fa_token(
    owner_kind: str,
    owner_id: Optional[int],
    email: str,
    scope: str = "2fa_pending",
    purpose: str = "login",
    next_url: str = "/",
    ttl_seconds: Optional[int] = None,
) -> str:
    """
    Create a signed JSON token that represents a successful password/Google check
    or registration step, but not yet a full session. Expires in ttl_seconds.
    """
    import hmac as _hmac
    settings = get_settings()
    actual_ttl = PRE_2FA_TOKEN_TTL if ttl_seconds is None else ttl_seconds
    payload = {
        "kind": owner_kind,
        "id": owner_id,
        "email": email,
        "scope": scope,
        "purpose": purpose,
        "next": next_url,
        "exp": (datetime.now(timezone.utc) + timedelta(seconds=actual_ttl)).isoformat(),
    }
    data = json.dumps(payload, separators=(",", ":"))
    secret = (settings.secret_key or "dev").encode()
    sig = _hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    combined = base64.urlsafe_b64encode(f"{data}.{sig}".encode()).decode()
    return combined


def decode_pre2fa_token(token: str, expected_scope: Optional[str] = None) -> Optional[dict]:
    """Decode and verify pre-2FA / pre-OTP token. Returns payload dict or None."""
    import hmac as _hmac
    try:
        settings = get_settings()
        raw = base64.urlsafe_b64decode(token.encode() + b"==").decode()
        last_dot = raw.rfind(".")
        data, sig = raw[:last_dot], raw[last_dot + 1:]
        secret = (settings.secret_key or "dev").encode()
        expected = _hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(data)
        exp = datetime.fromisoformat(payload["exp"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp:
            return None
        if expected_scope is not None:
            if payload.get("scope") != expected_scope:
                return None
        else:
            if payload.get("scope") not in ("2fa_pending", "otp_pending"):
                return None
        return payload
    except Exception:
        return None


# ── Security log helper ────────────────────────────────────────────────────────

def log_security_event(
    session,
    action: str,
    owner_kind: Optional[str] = None,
    owner_id: Optional[int] = None,
    ip_address: str = "",
    user_agent: str = "",
    meta: str = "",
) -> None:
    """Write a row to security_log. Never log secrets/codes/tokens."""
    from .models import SecurityLog
    entry = SecurityLog(
        owner_kind=owner_kind,
        owner_id=owner_id,
        action=action,
        ip_address=ip_address[:45],
        user_agent=user_agent[:500],
        meta=meta[:1000],
        created_at=datetime.now(timezone.utc),
    )
    session.add(entry)
    # Intentionally no commit — caller commits to keep things transactional


# ── Lockout helpers ────────────────────────────────────────────────────────────

def is_locked_out(user_or_admin) -> bool:
    """True if the account is currently under a lockout."""
    lu = getattr(user_or_admin, "locked_until", None)
    if lu is None:
        return False
    if lu.tzinfo is None:
        lu = lu.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < lu


def increment_fail_count(session, user_or_admin, owner_kind: str, ip: str = "") -> bool:
    """
    Increment failed_2fa_count. If threshold reached, set locked_until.
    Returns True if account is now locked.
    """
    user_or_admin.failed_2fa_count = (user_or_admin.failed_2fa_count or 0) + 1
    locked = False
    if user_or_admin.failed_2fa_count >= MAX_FAIL_ATTEMPTS:
        user_or_admin.locked_until = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
        locked = True
        log_security_event(
            session,
            action="2fa_lockout",
            owner_kind=owner_kind,
            owner_id=user_or_admin.id,
            ip_address=ip,
            meta=f"failed_attempts={user_or_admin.failed_2fa_count}",
        )
    session.add(user_or_admin)
    return locked


def reset_fail_count(session, user_or_admin) -> None:
    """Reset counter and lockout after successful auth."""
    user_or_admin.failed_2fa_count = 0
    user_or_admin.locked_until = None
    user_or_admin.last_2fa_at = datetime.now(timezone.utc)
    session.add(user_or_admin)


# ── CSRF token helpers ─────────────────────────────────────────────────────────

def generate_csrf_token(session_email: str) -> str:
    """Generate a per-session CSRF token tied to the user's email."""
    import hmac as _hmac
    settings = get_settings()
    nonce = secrets.token_hex(16)
    secret = (getattr(settings, "csrf_secret", None) or settings.secret_key or "dev").encode()
    sig = _hmac.new(secret, f"{session_email}:{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{sig}"


def verify_csrf_token(token: str, session_email: str) -> bool:
    """Verify a CSRF token matches the session."""
    import hmac as _hmac
    try:
        settings = get_settings()
        parts = token.split(".")
        if len(parts) != 2:
            return False
        nonce, sig = parts
        secret = (getattr(settings, "csrf_secret", None) or settings.secret_key or "dev").encode()
        expected = _hmac.new(secret, f"{session_email}:{nonce}".encode(), hashlib.sha256).hexdigest()
        return _hmac.compare_digest(sig, expected)
    except Exception:
        return False
