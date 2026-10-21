"""Utility functions for validation, formatting, helpers."""
from __future__ import annotations
import re
from typing import Optional

# English-only validation patterns
ENGLISH_NAME_PATTERN = re.compile(r'^[a-zA-Z\s]{2,50}$')
USERNAME_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]{2,29}$')
PASSWORD_STRENGTH_PATTERN = re.compile(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$')


def validate_english_name(name: str) -> tuple[bool, str]:
    """Validate name is English-only. Returns (is_valid, error_message)."""
    if not name or not ENGLISH_NAME_PATTERN.match(name.strip()):
        return False, "Name must contain only English letters (2-50 characters)"
    return True, ""


def validate_username(username: str) -> tuple[bool, str]:
    """Validate username format. Returns (is_valid, error_message)."""
    if not username or not USERNAME_PATTERN.match(username.strip()):
        return False, "Username must start with a letter and contain only letters, numbers, underscores (3-30 chars)"
    return True, ""


COMMON_PASSWORDS = {
    "password", "password123", "12345678", "123456789", "1234567890",
    "qwerty123", "admin123", "welcome123", "letmein123", "iloveyou123",
    "password1", "password12", "pass1234", "admin@123", "priyam123",
    "anime123", "anime@123", "otaku123",
}


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validate password strength. Returns (is_valid, error_message)."""
    if not password or len(password) < 8:
        return False, "पासवर्ड कम से कम 8 वर्णों का होना चाहिए (Password must be at least 8 characters)"
    if len(password) > 128:
        return False, "पासवर्ड अधिकतम 128 वर्णों का हो सकता है (Password must not exceed 128 characters)"
    if password.lower() in COMMON_PASSWORDS:
        return False, "यह पासवर्ड बहुत सामान्य और असुरक्षित है (Password is too common)"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least 1 uppercase letter"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least 1 lowercase letter"
    if not re.search(r'\d', password):
        return False, "Password must contain at least 1 number"
    if not re.search(r'[@$!%*?&#^()_+\-=\[\]{}|;:,.<>?]', password):
        return False, "Password must contain at least 1 special character"
    return True, ""


def get_password_strength(password: str) -> str:
    """Return password strength: weak, medium, strong."""
    if len(password) < 8:
        return "weak"
    score = 0
    if len(password) >= 12:
        score += 1
    if re.search(r'[A-Z]', password):
        score += 1
    if re.search(r'[a-z]', password):
        score += 1
    if re.search(r'\d', password):
        score += 1
    if re.search(r'[@$!%*?&#^()_+\-=\[\]{}|;:,.<>?]', password):
        score += 1
    if score <= 2:
        return "weak"
    if score <= 4:
        return "medium"
    return "strong"


def generate_avatar_url(name: str) -> Optional[str]:
    """Generate a colored initials avatar URL (None to use initials fallback in template)."""
    return None


def get_initials(name: str) -> str:
    """Get initials from name (first letter of each word, max 2)."""
    if not name:
        return "?"
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name.strip()[0].upper()


AVATAR_COLORS = [
    "var(--admin-crimson)",
    "var(--info)",
    "var(--success)",
    "var(--warning)",
    "var(--purple)",
    "var(--pink)",
    "var(--teal)",
    "var(--orange)",
]


def get_avatar_color(name: str) -> str:
    """Get consistent color for a name."""
    if not name:
        return AVATAR_COLORS[0]
    idx = sum(ord(c) for c in name) % len(AVATAR_COLORS)
    return AVATAR_COLORS[idx]
