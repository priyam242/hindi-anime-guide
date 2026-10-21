"""Dual authentication: Admin + Regular Users — with token_version support."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Request
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlmodel import Session, select

from .config import get_settings
from . import db
engine = db.engine

settings = get_settings()

# Argon2 preferred; bcrypt as fallback for existing hashes
pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    deprecated="auto",
    argon2__memory_cost=65536,
    argon2__time_cost=3,
    argon2__parallelism=1,
)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24


class TokenData(BaseModel):
    email: Optional[str] = None
    name: Optional[str] = None
    user_type: Optional[str] = None   # "admin" or "user"
    token_version: Optional[int] = None


def get_secret_key() -> str:
    s = get_settings()
    return s.secret_key or "change-me-to-something-random-2026"


DUMMY_PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=1$iLFWak1pbc2Z09obAyCk1A$aDS6QtsJf98ec9bUF2UpfiKsxzJNb+RDa6OxGgjOdxM"


def verify_password(plain_password: str, hashed_password: Optional[str]) -> bool:
    if not hashed_password or not plain_password:
        return False
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def set_auth_cookie(
    response,
    token: str,
    cookie_name: str = "auth_token",
    max_age: Optional[int] = None,
) -> None:
    """Set secure HTTP-only auth cookies across production and development."""
    s = get_settings()
    is_secure = (s.env.lower() == "production")
    if max_age is None:
        if cookie_name == "admin_token":
            max_age = 86400   # 24 hours
        elif cookie_name == "pre_2fa_token":
            max_age = 300     # 5 minutes
        else:
            max_age = 604800  # 7 days
    response.set_cookie(
        key=cookie_name,
        value=token,
        httponly=True,
        max_age=max_age,
        samesite="lax",
        secure=is_secure,
    )


def clear_auth_cookie(response, cookie_name: str = "auth_token") -> None:
    """Clear an auth cookie."""
    response.delete_cookie(cookie_name)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
    token_version: int = 1,
) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "tv": token_version})
    return jwt.encode(to_encode, get_secret_key(), algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[TokenData]:
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        name: str = payload.get("name", "User")
        user_type: str = payload.get("user_type")
        tv: int = payload.get("tv", 1)
        if not user_type:
            s = get_settings()
            user_type = "admin" if email == s.admin_email else "user"
        if email is None:
            return None
        return TokenData(email=email, name=name, user_type=user_type, token_version=tv)
    except JWTError:
        return None


def _check_token_version(token_data: TokenData, db_version: int) -> bool:
    """Return False if the JWT's token_version is stale."""
    if token_data.token_version is None:
        return True   # old tokens without version field — allow during migration
    return token_data.token_version >= db_version


async def get_current_user(request: Request) -> Optional[dict]:
    """Get current user info from cookie (admin OR regular user), with token_version check."""
    if request.url.path.startswith("/admin"):
        token = request.cookies.get("admin_token") or request.cookies.get("auth_token")
    else:
        token = request.cookies.get("auth_token") or request.cookies.get("admin_token")
    if not token:
        return None
    token_data = decode_token(token)
    if token_data is None:
        return None

    # token_version check against DB
    from .models import Admin1, User1
    with Session(db.engine) as session:
        if token_data.user_type == "admin":
            db_user = session.exec(select(Admin1).where(Admin1.email == token_data.email)).first()
        else:
            db_user = session.exec(select(User1).where(User1.email == token_data.email)).first()

        if db_user is None:
            return None
        db_version = getattr(db_user, "token_version", 1) or 1
        if not _check_token_version(token_data, db_version):
            return None   # stale cookie — revoked

    return {
        "email": token_data.email,
        "name": token_data.name,
        "user_type": token_data.user_type,
        "token_version": token_data.token_version,
        "avatar_url": getattr(db_user, "avatar_url", None),
        "auth_provider": getattr(db_user, "auth_provider", "password"),
        "has_password": bool(getattr(db_user, "hashed_password", None)),
    }


async def require_admin(request: Request) -> dict:
    """Require admin authentication."""
    user = await get_current_user(request)
    if user is None or user.get("user_type") != "admin":
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin access required")
    return user


async def require_user(request: Request) -> Optional[dict]:
    """Require any logged-in user (admin or regular)."""
    user = await get_current_user(request)
    return user


def get_user_by_email(session: Session, email: str, user_type: str) -> Optional[object]:
    """Get user by email from appropriate table."""
    from .models import Admin1, User1
    if user_type == "admin":
        return session.exec(select(Admin1).where(Admin1.email == email)).first()
    return session.exec(select(User1).where(User1.email == email)).first()


def bump_token_version(session: Session, account) -> None:
    """Increment token_version to invalidate all existing cookies instantly and remove trusted devices."""
    account.token_version = (getattr(account, "token_version", 1) or 1) + 1
    account.updated_at = datetime.now(timezone.utc)
    session.add(account)
    from .models import TrustedDevice, Admin1
    import sqlmodel
    kind = "admin" if isinstance(account, Admin1) or getattr(account, "__tablename__", "") == "admin1" else "user"
    session.exec(sqlmodel.delete(TrustedDevice).where(
        TrustedDevice.owner_kind == kind,
        TrustedDevice.owner_id == account.id,
    ))


def delete_user_completely(session: Session, user) -> None:
    """
    Completely and safely remove a user and all related database records:
    - Comments
    - Bookmarks
    - Notifications
    - Trusted devices
    - Backup codes
    - Security logs
    - Email OTPs
    - Uploaded avatar image file
    - User account record
    """
    import sqlmodel
    from sqlalchemy import func
    from .models import (
        Comment, Bookmark, Notification, TrustedDevice,
        BackupCode, SecurityLog, EmailOtp,
    )

    uid = user.id
    u_email = (getattr(user, "email", None) or "").strip().lower()

    # 1. Delete comments
    session.exec(sqlmodel.delete(Comment).where(Comment.user_id == uid))

    # 2. Delete bookmarks
    session.exec(sqlmodel.delete(Bookmark).where(Bookmark.user_id == uid))

    # 3. Delete notifications
    session.exec(sqlmodel.delete(Notification).where(Notification.user_id == uid))

    # 4. Delete trusted devices
    session.exec(sqlmodel.delete(TrustedDevice).where(
        TrustedDevice.owner_kind == "user",
        TrustedDevice.owner_id == uid,
    ))

    # 5. Delete backup codes
    session.exec(sqlmodel.delete(BackupCode).where(
        BackupCode.owner_kind == "user",
        BackupCode.owner_id == uid,
    ))

    # 6. Delete security logs
    session.exec(sqlmodel.delete(SecurityLog).where(
        SecurityLog.owner_kind == "user",
        SecurityLog.owner_id == uid,
    ))

    # 7. Delete email OTPs
    if u_email:
        session.exec(sqlmodel.delete(EmailOtp).where(func.lower(EmailOtp.email) == u_email))

    # 8. Delete uploaded avatar if local file
    avatar_url = getattr(user, "avatar_url", None)
    if avatar_url and not str(avatar_url).startswith("http"):
        try:
            from .storage import delete_avatar
            delete_avatar(avatar_url)
        except Exception:
            pass

    # 9. Delete user record and commit
    session.delete(user)
    session.commit()

