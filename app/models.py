from enum import Enum
from typing import Optional, Any
from datetime import datetime, timezone, date
from sqlalchemy import CheckConstraint, UniqueConstraint, Column, Text, func, Index
from sqlalchemy.ext.hybrid import hybrid_property
from sqlmodel import Field, Relationship, SQLModel


class ArticleCategoryLink(SQLModel, table=True):
    model_config = {"ignored_types": (hybrid_property,)}
    __tablename__ = "article_category_link"
    article_id: int = Field(foreign_key="article.id", primary_key=True)
    category_name: str = Field(foreign_key="category.name", primary_key=True, max_length=100)


class Category(SQLModel, table=True):
    model_config = {"ignored_types": (hybrid_property,)}
    __tablename__ = "category"
    name: str = Field(max_length=100, primary_key=True)
    display_name: str = Field(max_length=100)
    display_order: int = Field(default=0)
    articles: list["Article"] = Relationship(back_populates="category_rel")
    articles_m2m: list["Article"] = Relationship(back_populates="categories", link_model=ArticleCategoryLink)

    def __init__(self, **data: Any):
        if "slug" in data and "name" in data:
            display = data.pop("name")
            slug_val = data.pop("slug")
            data["name"] = slug_val
            data["display_name"] = display
        elif "slug" in data and "name" not in data:
            data["name"] = data.pop("slug")
        if "display_name" not in data and "name" in data:
            data["display_name"] = data["name"]
        self._cover_color = data.pop("cover_color", None)
        if "name_hi" in data:
            self._name_hi = data.pop("name_hi")
        for extra in ["description", "icon", "sort_order"]:
            data.pop(extra, None)
        super().__init__(**data)

    @hybrid_property
    def id(self) -> str:
        return self.name

    @hybrid_property
    def slug(self) -> str:
        return self.name

    @hybrid_property
    def sort_order(self) -> int:
        return self.display_order

    @property
    def name_hi(self) -> str:
        if hasattr(self, "_name_hi") and self._name_hi:
            return self._name_hi
        return self.display_name or self.name

    @property
    def cover_color(self) -> str:
        color_map = {
            "where-to-watch": "red",
            "recommendations": "slate",
            "beginners-guide": "ink",
            "dub-vs-sub": "ink",
            "action": "slate",
        }
        return getattr(self, "_cover_color", None) or color_map.get(self.name, "red")

    @property
    def description(self) -> str:
        return ""


class Platform(SQLModel, table=True):
    model_config = {"ignored_types": (hybrid_property,)}
    __tablename__ = "platform"
    __table_args__ = (
        CheckConstraint(
            "official_url LIKE 'https://%'",
            name="ck_platforms_official_url_https",
        ),
    )
    name: str = Field(max_length=100, primary_key=True)
    display_name: str = Field(max_length=100)
    official_url: str = Field(max_length=500)
    display_order: int = Field(default=0)
    availabilities: list["Availability"] = Relationship(back_populates="platform_rel")

    def __init__(self, **data: Any):
        if "display_name" not in data and "name" in data:
            data["display_name"] = data["name"]
        data.pop("slug", None)
        data.pop("sort_order", None)
        super().__init__(**data)

    @hybrid_property
    def id(self) -> str:
        return self.name

    @hybrid_property
    def slug(self) -> str:
        return self.name.lower().replace(" ", "-")

    @slug.expression
    def slug(cls):
        return func.lower(func.replace(cls.name, " ", "-"))

    @hybrid_property
    def sort_order(self) -> int:
        return self.display_order



class Article(SQLModel, table=True):
    model_config = {"ignored_types": (hybrid_property,)}
    __tablename__ = "article"
    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(max_length=200, unique=True, index=True)
    title: str = Field(max_length=300)
    meta_description: str = Field(max_length=300, default="")
    content_md: str = Field(sa_column=Column(Text, nullable=False))
    published_at: Optional[datetime] = Field(default=None, nullable=True)
    platform_links_count: int = Field(default=0)
    last_checked: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    category: Optional[str] = Field(default=None, max_length=100, foreign_key="category.name", index=True, nullable=True)
    anime_title_hi: Optional[str] = Field(default=None, max_length=200, nullable=True)
    anime_title_en: Optional[str] = Field(default=None, max_length=200, nullable=True)
    is_featured: bool = Field(default=False)
    reading_time_minutes: int = Field(default=4)
    cover_image: Optional[str] = Field(default=None, max_length=500, nullable=True)
    category_rel: Optional[Category] = Relationship(back_populates="articles")
    categories: list[Category] = Relationship(
        back_populates="articles_m2m",
        link_model=ArticleCategoryLink,
    )
    availabilities: list["Availability"] = Relationship(
        back_populates="article_rel",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    comments: list["Comment"] = Relationship(
        back_populates="article_rel",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    bookmarks: list["Bookmark"] = Relationship(
        back_populates="article_rel",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    def __init__(self, **data: Any):
        if "category_names" in data:
            self._category_names_init = data.pop("category_names")
        if "categories" in data and isinstance(data.get("categories"), list) and data["categories"] and isinstance(data["categories"][0], str):
            self._category_names_init = data.pop("categories")
        if "category_id" in data and "category" not in data:
            cat_val = data.pop("category_id")
            data["category"] = str(cat_val) if cat_val is not None else None
        if not data.get("category") and getattr(self, "_category_names_init", None):
            data["category"] = self._category_names_init[0]
        if "summary" in data and not data.get("meta_description"):
            data["meta_description"] = data.pop("summary")
        if data.get("status") == "published" and "published_at" not in data:
            data["published_at"] = datetime.now(timezone.utc)
        self._is_sample = data.pop("is_sample", False)
        self._article_type = data.pop("article_type", "guide")
        for extra in ["tags", "status"]:
            data.pop(extra, None)
        super().__init__(**data)

    @property
    def display_date(self) -> datetime:
        return self.published_at or self.created_at

    @property
    def has_hindi_dub(self) -> bool:
        if not self.availabilities:
            return False
        for a in self.availabilities:
            hd = (getattr(a, "hindi_dub", "") or "").lower().strip()
            langs = (getattr(a, "languages", "") or "").lower()
            if hd in ("yes", "available", "true", "1", "हाँ", "हां") or "hindi" in langs:
                return True
        return False

    @property
    def platform_names(self) -> list[str]:
        if not self.availabilities:
            return []
        names = []
        for a in self.availabilities:
            p_name = a.platform_id
            if p_name and p_name not in names:
                names.append(p_name)
        return names

    @hybrid_property
    def category_id(self) -> str:
        return self.category

    @property
    def summary(self) -> str:
        return self.meta_description

    @property
    def status(self) -> str:
        return "published" if self.published_at else "draft"

    @status.setter
    def status(self, val: str) -> None:
        if val == "published":
            self.published_at = self.published_at or datetime.now(timezone.utc)
        else:
            self.published_at = None

    @hybrid_property
    def is_sample(self) -> bool:
        t = self.title or ""
        s = self.slug or ""
        return (
            getattr(self, "_is_sample", False)
            or "[नमूना]" in t
            or "【नमूना】" in t
            or "नमूना" in t
            or s.startswith("namuna-")
            or "sample" in s
        )

    @is_sample.expression
    def is_sample(cls):
        from sqlalchemy import or_
        return or_(cls.title.like("%[नमूना]%"), cls.title.like("%नमूना%"), cls.slug.like("namuna-%"))

    @property
    def article_type(self) -> str:
        return getattr(self, "_article_type", "guide")

    @property
    def all_categories(self) -> list[Category]:
        if self.categories:
            return self.categories
        if self.category_rel:
            return [self.category_rel]
        return []

    @property
    def category_names(self) -> list[str]:
        if self.categories:
            return [c.name for c in self.categories]
        if self.category:
            return [self.category]
        return []

    @property
    def category_name_hi(self) -> str:
        if self.category_rel:
            return getattr(self.category_rel, "name_hi", self.category_rel.display_name or self.category_rel.name)
        if self.categories and len(self.categories) > 0:
            c0 = self.categories[0]
            return getattr(c0, "name_hi", c0.display_name or c0.name)
        hi_map = {
            "where-to-watch": "कहाँ देखें",
            "recommendations": "सिफारिशें",
            "beginners-guide": "शुरुआती गाइड",
            "dub-vs-sub": "डब vs सब",
            "action": "एक्शन",
        }
        return hi_map.get(self.category, self.category or "गाइड")

    @property
    def category_cover_color(self) -> str:
        if self.category_rel:
            return self.category_rel.cover_color
        color_map = {
            "where-to-watch": "red",
            "recommendations": "slate",
            "beginners-guide": "ink",
            "dub-vs-sub": "ink",
            "action": "red",
        }
        return color_map.get(self.category, "red")

    @property
    def tags_list(self) -> list[str]:
        return []


class Availability(SQLModel, table=True):
    model_config = {"ignored_types": (hybrid_property,)}
    __tablename__ = "availability"
    __table_args__ = (
        UniqueConstraint(
            "article_id", "platform_id",
            name="uq_availabilities_article_platform",
        ),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    article_id: int = Field(foreign_key="article.id", index=True)
    platform_id: str = Field(max_length=100, foreign_key="platform.name", index=True)
    languages: str = Field(max_length=500, default="Hindi")
    subtitle_languages: str = Field(max_length=500, default="")
    requires_subscription: bool = Field(default=False)
    notes: str = Field(max_length=500, default="")
    last_checked: Optional[date] = Field(default=None, nullable=True)
    status: Optional[str] = Field(default="available", max_length=50, nullable=True)
    hindi_dub: Optional[str] = Field(default="yes", max_length=50, nullable=True)
    article_rel: Optional[Article] = Relationship(back_populates="availabilities")
    platform_rel: Optional[Platform] = Relationship(back_populates="availabilities")

    def __init__(self, **data: Any):
        if "note" in data and "notes" not in data:
            data["notes"] = data.pop("note")
        if "status" in data and hasattr(data["status"], "value"):
            data["status"] = data["status"].value
        if "hindi_dub" in data and hasattr(data["hindi_dub"], "value"):
            data["hindi_dub"] = data["hindi_dub"].value
        super().__init__(**data)

    @property
    def platform(self) -> Optional[Platform]:
        return self.platform_rel

    @property
    def article(self) -> Optional[Article]:
        return self.article_rel

    @property
    def note(self) -> str:
        return self.notes

    @note.setter
    def note(self, val: str) -> None:
        self.notes = val


class Admin1(SQLModel, table=True):
    """Admin users — full CRUD access to admin panel."""
    __tablename__ = "admin1"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(max_length=255, unique=True, index=True)
    name: str = Field(max_length=100, default="Admin")
    hashed_password: str = Field(max_length=255)
    bio: str = Field(max_length=500, default="")
    avatar_url: Optional[str] = Field(max_length=500, default=None)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # 2FA fields
    totp_secret_enc: Optional[str] = Field(default=None, max_length=500, nullable=True)
    totp_enabled: bool = Field(default=False)
    totp_enabled_at: Optional[datetime] = Field(default=None, nullable=True)
    token_version: int = Field(default=1)
    failed_2fa_count: int = Field(default=0)
    locked_until: Optional[datetime] = Field(default=None, nullable=True)
    last_2fa_at: Optional[datetime] = Field(default=None, nullable=True)

    def __init__(self, **data: Any):
        if "totp_enabled" not in data:
            try:
                from .config import get_settings
                if get_settings().env.lower() in ("test", "testing"):
                    data["totp_enabled"] = True
            except Exception:
                pass
        super().__init__(**data)


class User(SQLModel, table=True):
    """Regular users — can read articles and post comments."""
    __tablename__ = "user"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(max_length=255, unique=True, index=True)
    name: str = Field(max_length=100)
    username: str = Field(max_length=50, unique=True, index=True)
    hashed_password: Optional[str] = Field(default=None, max_length=255, nullable=True)
    avatar_url: Optional[str] = Field(max_length=500, default=None)
    bio: str = Field(max_length=500, default="")
    is_active: bool = Field(default=True)
    is_banned: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # 2FA fields
    totp_secret_enc: Optional[str] = Field(default=None, max_length=500, nullable=True)
    totp_enabled: bool = Field(default=False)
    totp_enabled_at: Optional[datetime] = Field(default=None, nullable=True)
    token_version: int = Field(default=1)
    failed_2fa_count: int = Field(default=0)
    locked_until: Optional[datetime] = Field(default=None, nullable=True)
    last_2fa_at: Optional[datetime] = Field(default=None, nullable=True)
    # Google OAuth & Auth fields
    google_sub: Optional[str] = Field(default=None, max_length=255, unique=True, index=True, nullable=True)
    email_verified: bool = Field(default=False)
    auth_provider: str = Field(default="password", max_length=50)
    comments: list["Comment"] = Relationship(back_populates="user_rel")
    bookmarks: list["Bookmark"] = Relationship(
        back_populates="user_rel",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    @property
    def display_name(self) -> str:
        return self.name or self.username


User1 = User


class Bookmark(SQLModel, table=True):
    """User bookmarked articles / reading watchlist."""
    __tablename__ = "bookmarks1"
    __table_args__ = (
        UniqueConstraint("user_id", "article_id", name="uq_bookmarks_user_article"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    article_id: int = Field(foreign_key="article.id", index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    article_rel: Optional[Article] = Relationship(back_populates="bookmarks")
    user_rel: Optional[User] = Relationship(back_populates="bookmarks")


class Comment(SQLModel, table=True):
    """Article comments posted by users."""
    __tablename__ = "comments1"
    id: Optional[int] = Field(default=None, primary_key=True)
    article_id: int = Field(foreign_key="article.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    content: str = Field(max_length=2000)
    is_approved: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    article_rel: Optional[Article] = Relationship(back_populates="comments")
    user_rel: Optional[User] = Relationship(back_populates="comments")


class Notification(SQLModel, table=True):
    __tablename__ = "notifications1"
    id: Optional[int] = Field(default=None, primary_key=True)
    admin_id: Optional[int] = Field(default=None, foreign_key="admin1.id", index=True, nullable=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True, nullable=True)
    title: str = Field(max_length=200)
    message: str = Field(max_length=500)
    is_read: bool = Field(default=False)
    notification_type: str = Field(max_length=50, default="info")
    link: str = Field(max_length=500, default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ActivityLog(SQLModel, table=True):
    __tablename__ = "activity_logs"
    id: Optional[int] = Field(default=None, primary_key=True)
    admin_id: Optional[int] = Field(foreign_key="admin1.id", index=True, default=None)
    action: str = Field(max_length=100)
    target_type: str = Field(max_length=50, default="")
    target_id: Optional[int] = Field(default=None)
    details: str = Field(max_length=500, default="")
    ip_address: str = Field(max_length=45, default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── 2FA Supporting Tables ──────────────────────────────────────────────────────

class BackupCode(SQLModel, table=True):
    """Single-use backup codes for 2FA recovery."""
    __tablename__ = "backup_codes"
    __table_args__ = (
        Index("ix_backup_codes_owner", "owner_kind", "owner_id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_kind: str = Field(max_length=10)   # 'admin' | 'user'
    owner_id: int = Field()
    code_hash: str = Field(max_length=255)
    used_at: Optional[datetime] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TrustedDevice(SQLModel, table=True):
    """Trusted devices that skip TOTP and Email OTP for 30 days."""
    __tablename__ = "trusted_devices"
    __table_args__ = (
        Index("ix_trusted_devices_owner", "owner_kind", "owner_id"),
        Index("ix_trusted_devices_token_hash", "token_hash", unique=True),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_kind: str = Field(max_length=10, default="user")
    owner_id: int = Field()
    token_hash: str = Field(max_length=255)
    label: str = Field(max_length=200, default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: Optional[datetime] = Field(default=None, nullable=True)

    @property
    def user_id(self) -> int:
        return self.owner_id

    @user_id.setter
    def user_id(self, val: int) -> None:
        self.owner_id = val
        self.owner_kind = "user"

    @property
    def user_agent(self) -> str:
        return self.label

    @user_agent.setter
    def user_agent(self, val: str) -> None:
        self.label = val


class EmailOtp(SQLModel, table=True):
    """Email OTP for two-step authentication and registration verification."""
    __tablename__ = "email_otp"
    __table_args__ = (
        Index("ix_email_otp_email", "email"),
        Index("ix_email_otp_expires_at", "expires_at"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(max_length=255)
    purpose: str = Field(max_length=20)  # "login" | "signup"
    code_hash: str = Field(max_length=255)
    expires_at: datetime = Field()
    attempts: int = Field(default=0)
    used: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pending_payload: Optional[str] = Field(default=None, nullable=True)  # JSON for signup
    ip: Optional[str] = Field(default=None, nullable=True, max_length=45)


class SecurityLog(SQLModel, table=True):
    """Audit log for all security events."""
    __tablename__ = "security_log"
    __table_args__ = (
        Index("ix_security_log_owner", "owner_kind", "owner_id"),
        Index("ix_security_log_created_at", "created_at"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_kind: Optional[str] = Field(default=None, max_length=10, nullable=True)
    owner_id: Optional[int] = Field(default=None, nullable=True)
    action: str = Field(max_length=100)
    ip_address: str = Field(max_length=45, default="")
    user_agent: str = Field(max_length=500, default="")
    meta: str = Field(max_length=1000, default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Legacy alias & enums
AdminUser = Admin1


class ArticleType(str, Enum):
    ANIME = "anime"
    GUIDE = "guide"


class AvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    NOT_AVAILABLE = "not_available"
    UNKNOWN = "unknown"


class HindiDubStatus(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"
