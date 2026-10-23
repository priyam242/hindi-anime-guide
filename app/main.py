from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from sqlmodel import Session
from .config import settings, STATIC_DIR, get_settings
from .db import engine, init_db
from .routers import pages, admin, users, uploads
from .routers.pages import get_categories
from .routers import totp_router
from .templates_config import render
from .auth import get_current_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        with Session(engine) as session:
            admin.ensure_admin_user(session)
    except Exception:
        pass
    yield


app = FastAPI(
    title=settings.app_name,
    description=settings.app_description,
    lifespan=lifespan,
    docs_url=None,
    openapi_url=None,
    redoc_url=None,
)

# ---------------------------------------------------------
# Performance & Security Middleware
# ---------------------------------------------------------

app.add_middleware(GZipMiddleware, minimum_size=500)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key or "session-secret-key-fallback-32chars",
    same_site="lax",
    https_only=(settings.env.lower() == "production"),
)


@app.middleware("http")
async def performance_and_security_headers(request: Request, call_next):
    """
    Phase 9 & 10: Performance & AdSense Verification Headers
    """
    response = await call_next(request)
    path = request.url.path.lower()

    if path.startswith("/static/") or path.startswith("/media/"):
        if any(path.endswith(ext) for ext in (
            ".css", ".js", ".woff2", ".woff", ".ttf", ".otf",
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico"
        )):
            response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=86400"
    elif path in ("/favicon.ico", "/health", "/ads.txt"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    else:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if "Content-Security-Policy" not in response.headers:
        response.headers["X-XSS-Protection"] = "1; mode=block"

    return response


@app.middleware("http")
async def inject_user_context(request: Request, call_next):
    """Make current user info available to all templates."""
    user = await get_current_user(request)
    request.state.user = user
    response = await call_next(request)
    return response


@app.get("/docs", include_in_schema=False)
def get_docs():
    cur = get_settings()
    if not cur.debug or cur.env.lower() == "production":
        raise HTTPException(status_code=404, detail="Not Found")
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{cur.app_name} - Swagger UI")


@app.get("/openapi.json", include_in_schema=False)
def get_openapi_endpoint():
    cur = get_settings()
    if not cur.debug or cur.env.lower() == "production":
        raise HTTPException(status_code=404, detail="Not Found")
    from fastapi.openapi.utils import get_openapi
    return get_openapi(title=cur.app_name, version="1.0.0", routes=app.routes)


async def _custom_http_exception_handler(request: Request, exc):
    status_code = getattr(exc, "status_code", 500)
    detail = getattr(exc, "detail", "सर्वर पर कुछ अप्रत्याशित समस्या आई है। कृपया कुछ समय बाद पुनः प्रयास करें।")
    user = getattr(request.state, "user", None)
    try:
        with Session(engine) as session:
            categories = get_categories(session)
    except Exception:
        categories = []
    if status_code == 404:
        return HTMLResponse(status_code=404, content=render("not_found.html", categories=categories, user=user, request=request))
    return HTMLResponse(
        status_code=status_code,
        content=render(
            "error.html",
            status_code=status_code,
            error_title="कुछ गड़बड़ हो गई (Server Error)",
            error_message=str(detail),
            categories=categories,
            user=user,
            request=request
        )
    )


app.add_exception_handler(404, _custom_http_exception_handler)
app.add_exception_handler(500, _custom_http_exception_handler)
app.add_exception_handler(Exception, _custom_http_exception_handler)

# Static & Media mounts
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

from .storage import MEDIA_DIR
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(str(STATIC_DIR / "favicon.svg"), media_type="image/svg+xml")


@app.get("/ads.txt", include_in_schema=False)
def ads_txt():
    """Serves ads.txt for Google AdSense Crawler Verification."""
    ads_file = STATIC_DIR / "ads.txt"
    if ads_file.exists():
        return FileResponse(str(ads_file), media_type="text/plain")
    return Response(content="google.com, pub-0000000000000000, DIRECT, f08c47fec0942fa0\n", media_type="text/plain")


@app.get("/health", tags=["System"])
def health_check():
    """Health check endpoint for UptimeRobot keep-alive."""
    return {"status": "healthy", "app": settings.app_name, "version": "1.0.0"}


# Routers
app.include_router(pages.router)
app.include_router(admin.router)
app.include_router(users.router)
app.include_router(uploads.router)
app.include_router(totp_router.router)