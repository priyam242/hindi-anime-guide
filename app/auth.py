import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any

from fastapi import Request
from sqlalchemy import text

from app.db import engine

SESSION_COOKIE_NAME = "hag_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600

_users_lock = threading.Lock()
_users_ready = False


def _get_secret() -> str:
    return (
        os.getenv("SECRET_KEY")
        or os.getenv("JWT_SECRET")
        or os.getenv("SESSION_SECRET")
        or "hag-v2-obsidian-crimson-fallback-signing-key"
    )


def ensure_hag_users() -> None:
    """Create hag_users table in Supabase if it does not already exist."""
    global _users_ready
    if _users_ready:
        return
    with _users_lock:
        if _users_ready:
            return
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS hag_users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(60) NOT NULL,
                        email VARCHAR(160) UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        watchlist_json TEXT DEFAULT '[]',
                        hunter_xp INTEGER DEFAULT 20,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
            )
        _users_ready = True


def hash_password(password: str) -> str:
    """Hash password cleanly using Python stdlib PBKDF2-HMAC-SHA256."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored PBKDF2-HMAC-SHA256 hash."""
    try:
        scheme, salt_hex, hash_hex = stored_hash.split("$", 2)
        if scheme != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def create_session_token(user_id: int, email: str) -> str:
    """Sign HTTP-only cookie payload using HMAC-SHA256."""
    payload = {"uid": int(user_id), "email": email, "exp": int(time.time()) + SESSION_MAX_AGE_SECONDS}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(_get_secret().encode("utf-8"), b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def verify_session_token(token: str | None) -> dict[str, Any] | None:
    """Verify HMAC signature & expiry of hag_session cookie."""
    if not token or "." not in token:
        return None
    try:
        b64, sig = token.rsplit(".", 1)
        expected = hmac.new(_get_secret().encode("utf-8"), b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        padded = b64 + "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def _parse_watchlist(raw: str | None) -> list[dict[str, Any]]:
    try:
        arr = json.loads(raw or "[]")
        return arr if isinstance(arr, list) else []
    except Exception:
        return []


def _merge_watchlists(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for item in primary + secondary:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        merged.append(item)
    return merged[:100]


def _format_user_row(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "email": row["email"],
        "watchlist": _parse_watchlist(row["watchlist_json"]),
        "hunter_xp": int(row["hunter_xp"] or 20),
    }


def register_user(
    username: str,
    email: str,
    password: str,
    guest_watchlist: list[dict[str, Any]] | None = None,
    guest_xp: int = 20,
) -> dict[str, Any] | None:
    """Register new user in hag_users; returns None if email already exists."""
    ensure_hag_users()
    clean_email = email.strip().lower()
    clean_name = username.strip()[:60]
    pw_hash = hash_password(password)
    wl_str = json.dumps(_merge_watchlists(guest_watchlist or [], []), ensure_ascii=False)
    xp_val = max(20, int(guest_xp or 20))

    with engine.begin() as conn:
        exists = conn.execute(text("SELECT id FROM hag_users WHERE email = :e"), {"e": clean_email}).fetchone()
        if exists:
            return None
        row = conn.execute(
            text(
                """
                INSERT INTO hag_users (username, email, password_hash, watchlist_json, hunter_xp)
                VALUES (:u, :e, :p, :w, :x)
                RETURNING id, username, email, watchlist_json, hunter_xp
                """
            ),
            {"u": clean_name, "e": clean_email, "p": pw_hash, "w": wl_str, "x": xp_val},
        ).mappings().first()
    return _format_user_row(row)


def authenticate_user(
    email: str,
    password: str,
    guest_watchlist: list[dict[str, Any]] | None = None,
    guest_xp: int = 20,
) -> dict[str, Any] | None:
    """Authenticate user by email + PBKDF2 hash and optionally merge guest state."""
    ensure_hag_users()
    clean_email = email.strip().lower()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, username, email, password_hash, watchlist_json, hunter_xp FROM hag_users WHERE email = :e"),
            {"e": clean_email},
        ).mappings().first()
        if not row or not verify_password(password, row["password_hash"]):
            return None

        if guest_watchlist or (guest_xp and int(guest_xp) > int(row["hunter_xp"] or 20)):
            merged_wl = _merge_watchlists(_parse_watchlist(row["watchlist_json"]), guest_watchlist or [])
            merged_xp = max(int(row["hunter_xp"] or 20), int(guest_xp or 20))
            updated = conn.execute(
                text(
                    """
                    UPDATE hag_users
                    SET watchlist_json = :w, hunter_xp = :x
                    WHERE id = :id
                    RETURNING id, username, email, watchlist_json, hunter_xp
                    """
                ),
                {"w": json.dumps(merged_wl, ensure_ascii=False), "x": merged_xp, "id": row["id"]},
            ).mappings().first()
            return _format_user_row(updated)

    return _format_user_row(row)


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """Fetch user profile cleanly by ID."""
    ensure_hag_users()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, username, email, watchlist_json, hunter_xp FROM hag_users WHERE id = :id"),
            {"id": int(user_id)},
        ).mappings().first()
    return _format_user_row(row) if row else None


def sync_user_state(user_id: int, watchlist: list[dict[str, Any]], hunter_xp: int) -> dict[str, Any] | None:
    """Persist updated watchlist_json and hunter_xp for logged-in user."""
    ensure_hag_users()
    clean_wl = _merge_watchlists(watchlist, [])
    clean_xp = max(20, int(hunter_xp or 20))
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE hag_users
                SET watchlist_json = :w, hunter_xp = :x
                WHERE id = :id
                RETURNING id, username, email, watchlist_json, hunter_xp
                """
            ),
            {"w": json.dumps(clean_wl, ensure_ascii=False), "x": clean_xp, "id": int(user_id)},
        ).mappings().first()
    return _format_user_row(row) if row else None


def get_current_user(request: Request) -> dict[str, Any] | None:
    """Resolve signed hag_session cookie to user dict, or None if unauthenticated."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    payload = verify_session_token(token)
    if not payload or not payload.get("uid"):
        return None
    return get_user_by_id(int(payload["uid"]))
