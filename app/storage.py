"""Storage helper service for profile avatars and media files.

Handles image validation, Pillow auto-orientation (EXIF), 1:1 center cropping,
resizing to max 400x400, optimizing to WebP (quality=85), and orphan cleanup.
Cloud-ready architecture allowing clean future switch to S3 / R2 / Cloudinary.
"""
from __future__ import annotations
import io
import os
import uuid
from pathlib import Path
from typing import Optional
from PIL import Image, ImageOps

from .config import BASE_DIR

MEDIA_DIR = BASE_DIR / "media"
AVATARS_DIR = MEDIA_DIR / "avatars"
AVATARS_DIR.mkdir(parents=True, exist_ok=True)

COVERS_DIR = MEDIA_DIR / "covers"
COVERS_DIR.mkdir(parents=True, exist_ok=True)

MAX_AVATAR_SIZE = 10 * 1024 * 1024  # 10MB
MAX_COVER_SIZE = 10 * 1024 * 1024   # 10MB
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def delete_avatar(avatar_url: Optional[str]) -> bool:
    """Safely delete an existing avatar file from disk if it lives under /media/avatars/."""
    if not avatar_url or not isinstance(avatar_url, str):
        return False
    
    clean_url = avatar_url.strip()
    prefix = "/media/avatars/"
    if not clean_url.startswith(prefix):
        return False
    
    filename = clean_url[len(prefix):].strip()
    # Prevent directory traversal attacks
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return False
    
    file_path = AVATARS_DIR / filename
    try:
        if file_path.is_file():
            file_path.unlink()
            return True
    except OSError:
        pass
    return False


def save_avatar(
    file_bytes: bytes,
    user_id: int | str,
    original_filename: str = "",
    old_avatar_url: Optional[str] = None
) -> str:
    """
    Validates, crops, resizes, and converts an uploaded avatar to optimized WebP.
    
    1. Checks file size up to 10MB.
    2. Validates extension and image integrity.
    3. Auto-corrects EXIF rotation.
    4. Center-crops to 1:1 square.
    5. Resizes smoothly to 400x400 max via LANCZOS.
    6. Saves as WebP (quality=85).
    7. Cleans up previous local avatar file.
    8. Returns the relative URL path /media/avatars/...
    """
    if len(file_bytes) > MAX_AVATAR_SIZE:
        raise ValueError("फ़ोटो का आकार 10MB से अधिक नहीं होना चाहिए (File exceeds 10MB)")

    if original_filename:
        ext = Path(original_filename).suffix.lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            raise ValueError("केवल JPG, JPEG, PNG, या WEBP प्रारूप समर्थित हैं")

    # Validate image integrity with Pillow
    try:
        raw_stream = io.BytesIO(file_bytes)
        img = Image.open(raw_stream)
        img.load()  # Forces full decode to catch corrupted files
    except Exception as exc:
        raise ValueError("अमान्य या दूषित छवि फ़ाइल (Invalid image file)") from exc

    # 1. Correct EXIF rotation
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    # 2. Color mode normalization
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")

    # 3. Center crop to 1:1 square
    w, h = img.size
    min_dim = min(w, h)
    left = (w - min_dim) // 2
    top = (h - min_dim) // 2
    img = img.crop((left, top, left + min_dim, top + min_dim))

    # 4. Smooth resize to max 400x400
    if img.width > 400 or img.height > 400:
        img = img.resize((400, 400), Image.Resampling.LANCZOS)

    # 5. Clean up old avatar if exists
    if old_avatar_url:
        delete_avatar(old_avatar_url)

    # 6. Save optimized WebP
    unique_suffix = uuid.uuid4().hex[:12]
    filename = f"user_{user_id}_{unique_suffix}.webp"
    target_path = AVATARS_DIR / filename
    
    # Save as WebP
    img.save(target_path, format="WEBP", quality=85, method=6)

    return f"/media/avatars/{filename}"


def delete_cover(cover_url: Optional[str]) -> bool:
    """Safely delete an existing article cover file from disk if it lives under /media/covers/."""
    if not cover_url or not isinstance(cover_url, str):
        return False
    
    clean_url = cover_url.strip()
    prefix = "/media/covers/"
    if not clean_url.startswith(prefix):
        return False
    
    filename = clean_url[len(prefix):].strip()
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return False
    
    file_path = COVERS_DIR / filename
    try:
        if file_path.is_file():
            file_path.unlink()
            return True
    except OSError:
        pass
    return False


def save_cover(
    file_bytes: bytes,
    slug: str,
    original_filename: str = "",
    old_cover_url: Optional[str] = None
) -> str:
    """
    Validates, crops/resizes to 16:9 widescreen (up to 1200x675), and converts to WebP.
    """
    if len(file_bytes) > MAX_COVER_SIZE:
        raise ValueError("कवर फ़ोटो का आकार 10MB से अधिक नहीं होना चाहिए (File exceeds 10MB)")

    if original_filename:
        ext = Path(original_filename).suffix.lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            raise ValueError("केवल JPG, JPEG, PNG, या WEBP प्रारूप समर्थित हैं")

    try:
        raw_stream = io.BytesIO(file_bytes)
        img = Image.open(raw_stream)
        img.load()
    except Exception as exc:
        raise ValueError("अमान्य या दूषित छवि फ़ाइल (Invalid image file)") from exc

    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")

    # Center crop to 16:9 widescreen ratio
    w, h = img.size
    target_ratio = 16.0 / 9.0
    current_ratio = w / h if h > 0 else target_ratio

    if current_ratio > target_ratio:
        # Image is too wide: crop left & right
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    elif current_ratio < target_ratio:
        # Image is too tall: crop top & bottom
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))

    # Resize smoothly up to max 1200x675
    max_w, max_h = 1200, 675
    if img.width > max_w or img.height > max_h:
        img = img.resize((max_w, max_h), Image.Resampling.LANCZOS)

    if old_cover_url:
        delete_cover(old_cover_url)

    clean_slug = "".join(c if c.isalnum() or c == "-" else "_" for c in slug)[:40] or "cover"
    unique_suffix = uuid.uuid4().hex[:10]
    filename = f"article_{clean_slug}_{unique_suffix}.webp"
    target_path = COVERS_DIR / filename

    img.save(target_path, format="WEBP", quality=85, method=6)

    return f"/media/covers/{filename}"

