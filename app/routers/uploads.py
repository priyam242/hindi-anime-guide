"""File upload handlers for avatars and other media."""
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from ..auth import get_current_user
from ..models import User1, Admin1
from .. import db
from ..storage import save_avatar, delete_avatar, MAX_AVATAR_SIZE, ALLOWED_EXTENSIONS

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("/avatar")
async def upload_avatar(request: Request, file: UploadFile = File(...)):
    user_info = await get_current_user(request)
    if not user_info:
        raise HTTPException(status_code=401, detail="लॉगिन आवश्यक है (Authentication required)")

    original_filename = file.filename or ""
    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="अमान्य फ़ाइल प्रकार। केवल JPG, JPEG, PNG, WEBP समर्थित हैं (Allowed: JPG, JPEG, PNG, WEBP)"
        )

    content = await file.read()
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(
            status_code=400,
            detail="फ़ोटो का आकार 10MB से अधिक नहीं होना चाहिए (Max file size: 10MB)"
        )

    with Session(db.engine) as session:
        user_record = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        if not user_record:
            user_record = session.exec(select(Admin1).where(Admin1.email == user_info["email"])).first()

        if not user_record:
            raise HTTPException(status_code=404, detail="उपयोगकर्ता नहीं मिला (User not found)")

        user_id = user_record.id or "default"
        old_avatar = user_record.avatar_url

        try:
            new_avatar_url = save_avatar(
                file_bytes=content,
                user_id=user_id,
                original_filename=original_filename,
                old_avatar_url=old_avatar,
            )
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

        user_record.avatar_url = new_avatar_url
        session.add(user_record)
        session.commit()

        return JSONResponse({
            "url": new_avatar_url,
            "filename": Path(new_avatar_url).name,
            "success": True,
            "message": "अवतार सफलतापूर्वक अपडेट किया गया"
        })


@router.post("/avatar/remove")
async def remove_avatar(request: Request):
    user_info = await get_current_user(request)
    if not user_info:
        raise HTTPException(status_code=401, detail="लॉगिन आवश्यक है (Authentication required)")

    with Session(db.engine) as session:
        user_record = session.exec(select(User1).where(User1.email == user_info["email"])).first()
        if not user_record:
            user_record = session.exec(select(Admin1).where(Admin1.email == user_info["email"])).first()

        if not user_record:
            raise HTTPException(status_code=404, detail="उपयोगकर्ता नहीं मिला (User not found)")

        if user_record.avatar_url:
            delete_avatar(user_record.avatar_url)
            user_record.avatar_url = None
            session.add(user_record)
            session.commit()

        return JSONResponse({
            "success": True,
            "message": "अवतार फ़ोटो सफलतापूर्वक हटा दी गई"
        })
