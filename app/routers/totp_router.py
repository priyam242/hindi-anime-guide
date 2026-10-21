"""
2FA router — enrollment, verification, backup codes, trusted devices, security log.

Routes:
  GET  /2fa/verify                 — show TOTP/backup-code entry page
  POST /auth/2fa/verify            — verify TOTP or backup code, issue session
  GET  /settings/security          — security settings page
  POST /settings/security/2fa/setup    — generate secret, return QR (pending)
  POST /settings/security/2fa/confirm  — confirm code, enable 2FA
  POST /settings/security/2fa/disable  — disable 2FA (requires re-auth)
  POST /settings/security/2fa/regen-backup — regenerate backup codes
  POST /settings/security/device/revoke  — revoke a trusted device
  POST /settings/security/logout-everywhere — bump token_version
  GET  /admin/users/<id>/reset-2fa  — admin: force-disable 2FA (audited)
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from ..auth import (
    bump_token_version,
    create_access_token,
    get_current_user,
    require_user,
    verify_password,
    set_auth_cookie,
)
from .. import db
from ..models import Admin1, BackupCode, SecurityLog, TrustedDevice, User1
from ..templates_config import render
from ..totp import (
    BACKUP_CODE_COUNT,
    LOCKOUT_MINUTES,
    MAX_FAIL_ATTEMPTS,
    TRUSTED_DEVICE_DAYS,
    create_pre2fa_token,
    decode_pre2fa_token,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_backup_codes,
    generate_device_token,
    generate_qr_data_uri,
    generate_totp_secret,
    get_totp_uri,
    hash_backup_codes,
    hash_device_token,
    increment_fail_count,
    is_locked_out,
    log_security_event,
    reset_fail_count,
    trusted_device_expires,
    verify_totp_code,
)

router = APIRouter(tags=["2fa"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")


def _get_user_agent(request: Request) -> str:
    return request.headers.get("User-Agent", "")[:500]


def _resolve_account(session: Session, kind: str, owner_id: int):
    """Return the Admin1 or User1 DB object."""
    if kind == "admin":
        return session.exec(select(Admin1).where(Admin1.id == owner_id)).first()
    return session.exec(select(User1).where(User1.id == owner_id)).first()


def _validate_next(next_url: str) -> str:
    """Whitelist: only relative paths allowed (no open redirect)."""
    stripped = (next_url or "").strip()
    if stripped.startswith("/") and not stripped.startswith("//"):
        return stripped
    return "/"


def _check_trusted_device(session: Session, kind: str, owner_id: int, request: Request) -> bool:
    """Return True if the request carries a valid, non-revoked trusted-device cookie."""
    cookie_token = request.cookies.get("trusted_device")
    if not cookie_token:
        return False
    token_hash = hash_device_token(cookie_token)
    now = datetime.now(timezone.utc)
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


def _get_backup_hashes(session: Session, kind: str, owner_id: int) -> list[tuple[str, str, int]]:
    """Return list of (salt, hash, id) for unused backup codes belonging to this account."""
    codes = session.exec(
        select(BackupCode).where(
            BackupCode.owner_kind == kind,
            BackupCode.owner_id == owner_id,
            BackupCode.used_at == None,  # noqa: E711
        )
    ).all()
    # salt is embedded in the hash field as "salt:hash"
    result = []
    for c in codes:
        if ":" in c.code_hash:
            salt, h = c.code_hash.split(":", 1)
            result.append((salt, h, c.id))
    return result


def _verify_backup_code_against_db(
    session: Session, kind: str, owner_id: int, candidate: str
) -> Optional[BackupCode]:
    """Find and return the BackupCode row if candidate matches. Returns None on failure."""
    candidate = candidate.strip().upper().replace(" ", "").replace("-", "")
    rows = _get_backup_hashes(session, kind, owner_id)
    for salt, h, code_id in rows:
        test = hashlib.sha256(f"{salt}:{candidate}".encode()).hexdigest()
        if test == h:
            return session.exec(select(BackupCode).where(BackupCode.id == code_id)).first()
    return None


def _create_backup_codes_in_db(
    session: Session, kind: str, owner_id: int, plaintext_codes: list[str], salt: str
) -> None:
    """Store hashed backup codes; delete old ones first."""
    # Remove old codes
    old_codes = session.exec(
        select(BackupCode).where(
            BackupCode.owner_kind == kind,
            BackupCode.owner_id == owner_id,
        )
    ).all()
    for old in old_codes:
        session.delete(old)
    session.flush()
    # Insert new
    for code in plaintext_codes:
        h = hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()
        session.add(BackupCode(
            owner_kind=kind,
            owner_id=owner_id,
            code_hash=f"{salt}:{h}",
            created_at=datetime.now(timezone.utc),
        ))


# ── 2FA verify page & POST ─────────────────────────────────────────────────────

@router.get("/2fa/verify", response_class=HTMLResponse)
async def verify_2fa_page(request: Request, next: str = "/"):
    """Show the TOTP / backup code entry page."""
    token = request.cookies.get("pre_2fa_token")
    if not token:
        return RedirectResponse(url="/auth/login", status_code=303)
    payload = decode_pre2fa_token(token, expected_scope="2fa_pending")
    if not payload:
        response = RedirectResponse(url="/auth/login", status_code=303)
        response.delete_cookie("pre_2fa_token")
        return response
    return render(
        "auth/2fa_verify.html",
        request=request,
        next=_validate_next(next),
        error=None,
        mode="totp",
    )


@router.post("/auth/2fa/verify", response_class=HTMLResponse)
async def post_verify_2fa(
    request: Request,
    code: str = Form(""),
    backup_code: str = Form(""),
    mode: str = Form("totp"),
    next: str = Form("/"),
    remember_device: str = Form(""),
):
    """Verify TOTP or backup code; issue full session on success."""
    ip = _get_client_ip(request)
    ua = _get_user_agent(request)
    next_url = _validate_next(next)

    pre_token = request.cookies.get("pre_2fa_token")
    if not pre_token:
        return RedirectResponse(url="/auth/login", status_code=303)

    payload = decode_pre2fa_token(pre_token, expected_scope="2fa_pending")
    if not payload:
        response = RedirectResponse(url="/auth/login", status_code=303)
        response.delete_cookie("pre_2fa_token")
        return response

    kind = payload["kind"]
    owner_id = payload["id"]
    email = payload["email"]

    with Session(db.engine) as session:
        account = _resolve_account(session, kind, owner_id)
        if not account:
            return RedirectResponse(url="/auth/login", status_code=303)

        # Lockout check
        if is_locked_out(account):
            return render(
                "auth/2fa_verify.html",
                request=request,
                next=next_url,
                error="बहुत ज़्यादा प्रयास। 15 मिनट बाद फिर कोशिश करें।",
                mode=mode,
            )

        success = False
        used_backup = False

        if mode == "backup":
            candidate = backup_code.strip()
            matched = _verify_backup_code_against_db(session, kind, owner_id, candidate)
            if matched:
                matched.used_at = datetime.now(timezone.utc)
                session.add(matched)
                log_security_event(session, "backup_code_used", kind, owner_id, ip, ua,
                                   meta=f"code_id={matched.id}")
                success = True
                used_backup = True
            # Generic error — never reveal which factor failed
        else:
            # TOTP verify
            enc = account.totp_secret_enc
            if enc:
                try:
                    secret = decrypt_totp_secret(enc)
                    if verify_totp_code(secret, code.strip()):
                        success = True
                except Exception:
                    pass

        if not success:
            locked = increment_fail_count(session, account, kind, ip)
            log_security_event(session, "failed_2fa", kind, owner_id, ip, ua,
                               meta=f"mode={mode}")
            session.commit()
            error_msg = "बहुत ज़्यादा प्रयास। 15 मिनट बाद फिर कोशिश करें।" if locked else "गलत कोड। फिर से कोशिश करें।"
            return render(
                "auth/2fa_verify.html",
                request=request,
                next=next_url,
                error=error_msg,
                mode=mode,
            )

        # ── Success ────────────────────────────────────────────────────
        reset_fail_count(session, account)
        log_security_event(session, "2fa_success", kind, owner_id, ip, ua,
                           meta=f"mode={mode},backup={used_backup}")
        session.commit()

        tv = getattr(account, "token_version", 1) or 1
        name = getattr(account, "name", email)
        token = create_access_token(
            data={"sub": email, "name": name, "user_type": kind},
            expires_delta=timedelta(hours=24 * 7 if kind == "user" else 24),
            token_version=tv,
        )

        if kind == "admin":
            redirect_to = next_url if next_url != "/" else "/admin/dashboard"
        else:
            redirect_to = next_url

        response = RedirectResponse(url=redirect_to, status_code=303)
        response.delete_cookie("pre_2fa_token")

        if kind == "admin":
            set_auth_cookie(response, token, cookie_name="admin_token", max_age=86400)
        max_age = 86400 * 7 if kind == "user" else 86400
        set_auth_cookie(response, token, cookie_name="auth_token", max_age=max_age)

        # Trusted device
        if remember_device == "1":
            dev_token = generate_device_token()
            dev_hash = hash_device_token(dev_token)
            label = ua[:100] or "Unknown device"
            session.add(TrustedDevice(
                owner_kind=kind,
                owner_id=owner_id,
                token_hash=dev_hash,
                label=label,
                created_at=datetime.now(timezone.utc),
                expires_at=trusted_device_expires(),
            ))
            session.commit()
            log_security_event(session, "trusted_device_added", kind, owner_id, ip, ua)
            session.commit()
            response.set_cookie(
                "trusted_device", dev_token,
                httponly=True, samesite="lax",
                max_age=TRUSTED_DEVICE_DAYS * 86400,
            )

        return response


# ── Security settings page ─────────────────────────────────────────────────────

@router.get("/settings/security", response_class=HTMLResponse)
async def security_settings_page(request: Request, setup: str = ""):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)

    kind = "admin" if user_info.get("user_type") == "admin" else "user"
    email = user_info["email"]

    with Session(db.engine) as session:
        account = session.exec(
            select(Admin1 if kind == "admin" else User1).where(
                (Admin1 if kind == "admin" else User1).email == email
            )
        ).first()
        if not account:
            return RedirectResponse(url="/auth/login", status_code=303)

        # Trusted devices
        now = datetime.now(timezone.utc)
        devices = session.exec(
            select(TrustedDevice).where(
                TrustedDevice.owner_kind == kind,
                TrustedDevice.owner_id == account.id,
                TrustedDevice.revoked_at == None,  # noqa: E711
                TrustedDevice.expires_at > now,
            )
        ).all()

        # Security log (last 20)
        logs = session.exec(
            select(SecurityLog)
            .where(SecurityLog.owner_kind == kind, SecurityLog.owner_id == account.id)
            .order_by(SecurityLog.created_at.desc())
            .limit(20)
        ).all()

        # Backup codes count (unused)
        backup_count = len(session.exec(
            select(BackupCode).where(
                BackupCode.owner_kind == kind,
                BackupCode.owner_id == account.id,
                BackupCode.used_at == None,  # noqa: E711
            )
        ).all())

        return render(
            "auth/security.html",
            request=request,
            account=account,
            kind=kind,
            devices=devices,
            logs=logs,
            backup_count=backup_count,
            setup_required=(setup == "required"),
            error=request.query_params.get("error"),
            success=request.query_params.get("success"),
        )


# ── 2FA Setup (generate secret, show QR) ─────────────────────────────────────

@router.post("/settings/security/2fa/setup", response_class=HTMLResponse)
async def setup_2fa(request: Request):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)

    kind = "admin" if user_info.get("user_type") == "admin" else "user"
    email = user_info["email"]

    with Session(db.engine) as session:
        Model = Admin1 if kind == "admin" else User1
        account = session.exec(select(Model).where(Model.email == email)).first()
        if not account:
            return RedirectResponse(url="/auth/login", status_code=303)

        # Generate new secret (always — resets pending setup)
        raw_secret = generate_totp_secret()
        enc_secret = encrypt_totp_secret(raw_secret)
        account.totp_secret_enc = enc_secret
        account.totp_enabled = False  # still pending confirmation
        account.updated_at = datetime.now(timezone.utc)
        session.add(account)
        session.commit()

        totp_uri = get_totp_uri(raw_secret, email)
        qr_data_uri = generate_qr_data_uri(totp_uri)

        # Return QR + manual secret for template — secret shown once here only
        return render(
            "auth/security.html",
            request=request,
            account=account,
            kind=kind,
            devices=[],
            logs=[],
            backup_count=0,
            setup_required=False,
            error=None,
            success=None,
            qr_data_uri=qr_data_uri,
            manual_secret=raw_secret,
            show_qr=True,
        )


# ── 2FA Confirm (verify first code, enable) ───────────────────────────────────

@router.post("/settings/security/2fa/confirm", response_class=HTMLResponse)
async def confirm_2fa(
    request: Request,
    code: str = Form(...),
):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)

    ip = _get_client_ip(request)
    ua = _get_user_agent(request)
    kind = "admin" if user_info.get("user_type") == "admin" else "user"
    email = user_info["email"]

    with Session(db.engine) as session:
        Model = Admin1 if kind == "admin" else User1
        account = session.exec(select(Model).where(Model.email == email)).first()
        if not account or not account.totp_secret_enc:
            return RedirectResponse(url="/settings/security?error=no_pending_setup", status_code=303)

        try:
            raw_secret = decrypt_totp_secret(account.totp_secret_enc)
        except Exception:
            return RedirectResponse(url="/settings/security?error=decryption_failed", status_code=303)

        if not verify_totp_code(raw_secret, code.strip()):
            log_security_event(session, "2fa_setup_wrong_code", kind, account.id, ip, ua)
            session.commit()
            totp_uri = get_totp_uri(raw_secret, email)
            qr_data_uri = generate_qr_data_uri(totp_uri)
            return render(
                "auth/security.html",
                request=request,
                account=account,
                kind=kind,
                devices=[],
                logs=[],
                backup_count=0,
                setup_required=False,
                error="गलत कोड। अपने ऑथेंटिकेटर ऐप में फिर से देखें।",
                success=None,
                qr_data_uri=qr_data_uri,
                manual_secret=raw_secret,
                show_qr=True,
            )

        # Enable 2FA
        account.totp_enabled = True
        account.totp_enabled_at = datetime.now(timezone.utc)
        account.updated_at = datetime.now(timezone.utc)
        bump_token_version(session, account)  # revoke all existing sessions

        # Generate backup codes
        plaintext_codes, salt = generate_backup_codes()
        _create_backup_codes_in_db(session, kind, account.id, plaintext_codes, salt)

        log_security_event(session, "2fa_enabled", kind, account.id, ip, ua)
        session.commit()

        # Issue fresh cookie for this session with updated token_version
        tv = getattr(account, "token_version", 1) or 1
        account_name = getattr(account, "name", None) or getattr(account, "username", "") or "Admin"
        new_token = create_access_token(
            data={"sub": account.email, "name": account_name, "user_type": kind},
            expires_delta=timedelta(hours=24),
            token_version=tv,
        )
        content = render(
            "auth/security.html",
            request=request,
            account=account,
            kind=kind,
            devices=[],
            logs=[],
            backup_count=len(plaintext_codes),
            setup_required=False,
            error=None,
            success="2FA सफलतापूर्वक सक्षम हो गई!",
            backup_codes_plaintext=plaintext_codes,
            show_backup_codes=True,
        )
        response = HTMLResponse(content=content)
        cookie_name = "admin_token" if kind == "admin" else "auth_token"
        set_auth_cookie(response, new_token, cookie_name=cookie_name, max_age=86400)
        if kind == "admin":
            set_auth_cookie(response, new_token, cookie_name="auth_token", max_age=86400)
        return response


# ── 2FA Disable ────────────────────────────────────────────────────────────────

@router.post("/settings/security/2fa/disable", response_class=HTMLResponse)
async def disable_2fa(
    request: Request,
    current_password: str = Form(""),
    totp_code: str = Form(""),
):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)

    ip = _get_client_ip(request)
    ua = _get_user_agent(request)
    kind = "admin" if user_info.get("user_type") == "admin" else "user"
    email = user_info["email"]

    with Session(db.engine) as session:
        Model = Admin1 if kind == "admin" else User1
        account = session.exec(select(Model).where(Model.email == email)).first()
        if not account:
            return RedirectResponse(url="/auth/login", status_code=303)

        # Re-auth: password OR TOTP code required
        authed = False
        if current_password:
            from ..auth import verify_password as vp
            authed = vp(current_password, account.hashed_password)
        elif totp_code and account.totp_secret_enc:
            try:
                raw = decrypt_totp_secret(account.totp_secret_enc)
                authed = verify_totp_code(raw, totp_code.strip())
            except Exception:
                pass

        if not authed:
            return RedirectResponse(
                url="/settings/security?error=" + quote("पुष्टि विफल — पासवर्ड या TOTP कोड गलत है।"),
                status_code=303,
            )

        account.totp_enabled = False
        account.totp_secret_enc = None
        account.totp_enabled_at = None
        account.updated_at = datetime.now(timezone.utc)
        bump_token_version(session, account)

        # Delete backup codes & trusted devices
        for bc in session.exec(select(BackupCode).where(
            BackupCode.owner_kind == kind, BackupCode.owner_id == account.id
        )).all():
            session.delete(bc)
        for td in session.exec(select(TrustedDevice).where(
            TrustedDevice.owner_kind == kind, TrustedDevice.owner_id == account.id
        )).all():
            td.revoked_at = datetime.now(timezone.utc)
            session.add(td)

        log_security_event(session, "2fa_disabled", kind, account.id, ip, ua)
        session.commit()

    return RedirectResponse(url="/settings/security?success=2fa_disabled", status_code=303)


# ── Regenerate backup codes ────────────────────────────────────────────────────

@router.post("/settings/security/2fa/regen-backup", response_class=HTMLResponse)
async def regen_backup_codes(request: Request):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)

    ip = _get_client_ip(request)
    ua = _get_user_agent(request)
    kind = "admin" if user_info.get("user_type") == "admin" else "user"
    email = user_info["email"]

    with Session(db.engine) as session:
        Model = Admin1 if kind == "admin" else User1
        account = session.exec(select(Model).where(Model.email == email)).first()
        if not account or not account.totp_enabled:
            return RedirectResponse(url="/settings/security", status_code=303)

        plaintext_codes, salt = generate_backup_codes()
        _create_backup_codes_in_db(session, kind, account.id, plaintext_codes, salt)
        log_security_event(session, "backup_codes_regenerated", kind, account.id, ip, ua)
        session.commit()

        devices = session.exec(
            select(TrustedDevice).where(
                TrustedDevice.owner_kind == kind,
                TrustedDevice.owner_id == account.id,
                TrustedDevice.revoked_at == None,  # noqa: E711
            )
        ).all()
        logs = session.exec(
            select(SecurityLog)
            .where(SecurityLog.owner_kind == kind, SecurityLog.owner_id == account.id)
            .order_by(SecurityLog.created_at.desc())
            .limit(20)
        ).all()

        return render(
            "auth/security.html",
            request=request,
            account=account,
            kind=kind,
            devices=devices,
            logs=logs,
            backup_count=len(plaintext_codes),
            setup_required=False,
            error=None,
            success="नए बैकअप कोड बना दिए गए।",
            backup_codes_plaintext=plaintext_codes,
            show_backup_codes=True,
        )


# ── Revoke trusted device ──────────────────────────────────────────────────────

@router.post("/settings/security/device/revoke", response_class=HTMLResponse)
async def revoke_device(request: Request, device_id: int = Form(...)):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)

    ip = _get_client_ip(request)
    ua = _get_user_agent(request)
    kind = "admin" if user_info.get("user_type") == "admin" else "user"
    email = user_info["email"]

    with Session(db.engine) as session:
        Model = Admin1 if kind == "admin" else User1
        account = session.exec(select(Model).where(Model.email == email)).first()
        if not account:
            return RedirectResponse(url="/auth/login", status_code=303)

        device = session.exec(
            select(TrustedDevice).where(
                TrustedDevice.id == device_id,
                TrustedDevice.owner_kind == kind,
                TrustedDevice.owner_id == account.id,
            )
        ).first()
        if device:
            device.revoked_at = datetime.now(timezone.utc)
            session.add(device)
            log_security_event(session, "trusted_device_revoked", kind, account.id, ip, ua,
                               meta=f"device_id={device_id}")
            session.commit()

    return RedirectResponse(url="/settings/security?success=device_revoked", status_code=303)


# ── Logout everywhere ──────────────────────────────────────────────────────────

@router.post("/settings/security/logout-everywhere", response_class=HTMLResponse)
async def logout_everywhere(request: Request):
    user_info = await require_user(request)
    if not user_info:
        return RedirectResponse(url="/auth/login", status_code=303)

    ip = _get_client_ip(request)
    ua = _get_user_agent(request)
    kind = "admin" if user_info.get("user_type") == "admin" else "user"
    email = user_info["email"]

    with Session(db.engine) as session:
        Model = Admin1 if kind == "admin" else User1
        account = session.exec(select(Model).where(Model.email == email)).first()
        if account:
            bump_token_version(session, account)
            log_security_event(session, "logout_everywhere", kind, account.id, ip, ua)
            session.commit()

    response = RedirectResponse(url="/auth/login", status_code=303)
    response.delete_cookie("auth_token")
    response.delete_cookie("admin_token")
    response.delete_cookie("trusted_device")
    return response


# ── Admin: reset user 2FA ──────────────────────────────────────────────────────

@router.post("/admin/users/{user_id}/reset-2fa", response_class=HTMLResponse)
async def admin_reset_user_2fa(request: Request, user_id: int):
    from ..auth import require_admin
    admin_info = await require_admin(request)
    ip = _get_client_ip(request)
    ua = _get_user_agent(request)

    with Session(db.engine) as session:
        target = session.exec(select(User1).where(User1.id == user_id)).first()
        if not target:
            target = session.exec(select(Admin1).where(Admin1.id == user_id)).first()
            target_kind = "admin"
        else:
            target_kind = "user"

        if not target:
            return RedirectResponse(url="/admin/users?error=not_found", status_code=303)

        target.totp_enabled = False
        target.totp_secret_enc = None
        target.totp_enabled_at = None
        target.updated_at = datetime.now(timezone.utc)
        bump_token_version(session, target)

        for bc in session.exec(select(BackupCode).where(
            BackupCode.owner_kind == target_kind, BackupCode.owner_id == target.id
        )).all():
            session.delete(bc)
        for td in session.exec(select(TrustedDevice).where(
            TrustedDevice.owner_kind == target_kind, TrustedDevice.owner_id == target.id
        )).all():
            td.revoked_at = datetime.now(timezone.utc)
            session.add(td)

        # Audit log
        log_security_event(
            session, "admin_2fa_reset",
            owner_kind=target_kind, owner_id=target.id,
            ip_address=ip, user_agent=ua,
            meta=f"reset_by={admin_info['email']}",
        )
        session.commit()

    return RedirectResponse(url=f"/admin/users?success=2fa_reset_{user_id}", status_code=303)
