import secrets
from functools import lru_cache
from pathlib import Path
from typing import Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_CONFIG_PATH = BASE_DIR / "alembic.ini"
ALEMBIC_INI_PATH = ALEMBIC_CONFIG_PATH

SITE_NAME = "Hindi Anime Guide"
SECRET_KEY_PLACEHOLDER = "change-this-in-production-to-a-secure-random-string-at-least-32-chars"
STATIC_DIR = BASE_DIR / "app" / "static"


class Settings(BaseSettings):
    env: str = "production"
    debug: bool = False
    secret_key: str = ""
    database_url: str = "sqlite:///./anime_hindi.db"
    database_url_unpooled: Optional[str] = None
    neon_branch: Optional[str] = None
    host: str = "127.0.0.1"
    port: int = 8000
    app_name: str = SITE_NAME
    app_description: str = "भारत में एनीमे कहाँ देखें, हिंदी डब गाइड"
    seed_sample_data: bool = False
    ads_enabled: bool = False
    show_sample_banner: bool = True
    author_name: str = ""
    admin_email: str = "admin@animeguide.com"
    admin_password: str = "set_in_env"
    db_url: Optional[str] = None
    # 2FA / security
    totp_enc_key: str = "set_in_env"   # Fernet key — never commit real value
    csrf_secret: str = "set_in_env"    # CSRF signing secret

    # Google OAuth & Verification
    google_client_id: str = ""
    google_client_secret: str = ""
    public_base_url: str = "http://127.0.0.1:8000"
    google_site_verification: str = "YV5XGsvZulzaCWZilyv4eJFv6yo_wpJTm963Uxk43lI"

    # SMTP Configuration for Email OTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@animeguide.com"

    # HTTPS Email APIs & Provider Selection
    email_provider: str = ""        # "", "brevo", "resend", "smtp"; empty = auto-detect
    brevo_api_key: str = ""
    resend_api_key: str = ""
    mail_from_email: str = ""
    mail_from_name: str = "Hindi Anime Guide"

    # Device Trust (days to remember device and skip email OTP; 0 disables)
    otp_trust_device_days: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @model_validator(mode="after")
    def resolve_paths_and_secrets(self) -> "Settings":
        if self.google_client_id:
            self.google_client_id = self.google_client_id.replace(" ", "").strip()
        if self.google_client_secret:
            self.google_client_secret = self.google_client_secret.replace(" ", "").strip()
        if self.public_base_url:
            self.public_base_url = self.public_base_url.strip().rstrip("/")
        if self.google_site_verification:
            self.google_site_verification = self.google_site_verification.strip()
        if self.smtp_host:
            self.smtp_host = self.smtp_host.strip()
        if self.smtp_user:
            self.smtp_user = self.smtp_user.strip()
        if self.smtp_password:
            self.smtp_password = self.smtp_password.replace(" ", "").strip()
        if self.smtp_from:
            self.smtp_from = self.smtp_from.strip()
        if self.email_provider:
            self.email_provider = self.email_provider.strip().lower()
        if self.brevo_api_key:
            self.brevo_api_key = self.brevo_api_key.strip()
        if self.resend_api_key:
            self.resend_api_key = self.resend_api_key.strip()
        if self.mail_from_email:
            self.mail_from_email = self.mail_from_email.strip()
        if self.mail_from_name:
            self.mail_from_name = self.mail_from_name.strip()
        # Check DB_URL override for non-test environments
        if self.db_url:
            raw_db = self.db_url
            if "host.docker.internal" in raw_db:
                try:
                    import socket
                    socket.gethostbyname("host.docker.internal")
                except Exception:
                    raw_db = raw_db.replace("host.docker.internal", "127.0.0.1")
            self.db_url = raw_db
            if self.env.lower() not in ("test", "testing"):
                self.database_url = raw_db

        if self.database_url and "host.docker.internal" in self.database_url and self.env.lower() not in ("test", "testing"):
            try:
                import socket
                socket.gethostbyname("host.docker.internal")
            except Exception:
                self.database_url = self.database_url.replace("host.docker.internal", "127.0.0.1")

        # Resolve relative SQLite database_url against BASE_DIR
        if self.database_url.startswith("sqlite:///"):
            raw_path = self.database_url[len("sqlite:///"):]
            if raw_path != ":memory:":
                p = Path(raw_path)
                if not p.is_absolute():
                    resolved = (BASE_DIR / p).resolve()
                    self.database_url = f"sqlite:///{resolved.as_posix()}"

        # Normalize PostgreSQL driver dialect to psycopg (psycopg 3.x)
        if self.database_url:
            if self.database_url.startswith("postgresql://"):
                self.database_url = self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
            elif self.database_url.startswith("postgres://"):
                self.database_url = self.database_url.replace("postgres://", "postgresql+psycopg://", 1)

        if self.database_url_unpooled:
            if self.database_url_unpooled.startswith("postgresql://"):
                self.database_url_unpooled = self.database_url_unpooled.replace("postgresql://", "postgresql+psycopg://", 1)
            elif self.database_url_unpooled.startswith("postgres://"):
                self.database_url_unpooled = self.database_url_unpooled.replace("postgres://", "postgresql+psycopg://", 1)

        # Dev / test secret key fallback
        normalized_env = self.env.lower()
        if normalized_env in ("development", "test", "testing") and not self.secret_key:
            self.secret_key = secrets.token_urlsafe(48)

        # Force empty secrets when in test/testing mode to never read or use real credentials from .env
        if normalized_env in ("test", "testing"):
            self.smtp_host = ""
            self.smtp_user = ""
            self.smtp_password = ""
            self.google_client_id = ""
            self.google_client_secret = ""
            self.brevo_api_key = ""
            self.resend_api_key = ""

        return self

    @property
    def site_name(self) -> str:
        return self.app_name

    @property
    def app_env(self) -> str:
        return self.env

    @app_env.setter
    def app_env(self, value: str) -> None:
        self.env = value

    def validate_secret_key(self) -> None:
        validate_secret_key(self)


def validate_secret_key(settings: Optional[Settings] = None) -> None:
    if settings is None:
        settings = get_settings()
    if settings.env.lower() == "production":
        if (
            not settings.secret_key
            or len(settings.secret_key) < 32
            or settings.secret_key == SECRET_KEY_PLACEHOLDER
        ):
            raise RuntimeError("SECRET_KEY must be set to a strong value in production")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_settings() -> Settings:
    return get_settings()


settings = get_settings()

