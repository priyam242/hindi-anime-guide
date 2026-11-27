import os
from pathlib import Path
from typing import Any, Literal
from xml.sax.saxutils import escape

from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response as FastAPIResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    authenticate_user,
    create_session_token,
    get_current_user,
    register_user,
    sync_user_state,
)
from app.db import ping_database
from app.services.content import (
    DEFAULT_POLL_KEY,
    cast_poll_vote,
    explore_catalog,
    filter_payload,
    get_anime_detail,
    get_awards_data,
    get_cached_content,
    get_counts,
    get_guide_detail,
    get_guides_list,
    get_news_detail,
    get_poll_state,
    get_roulette_pool,
    get_sitemap_urls,
    save_contact_message,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

app = FastAPI(title="Hindi Anime Guide", version="v2-clean")
app.add_middleware(GZipMiddleware, minimum_size=500)


class PollVoteRequest(BaseModel):
    poll_key: str = Field(default=DEFAULT_POLL_KEY, min_length=1, max_length=100)
    choice: Literal["a", "b"]


class ContactRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=200)
    subject: str = Field(min_length=2, max_length=80)
    message: str = Field(min_length=5, max_length=4000)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=60)
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=6, max_length=128)
    watchlist: list[dict[str, Any]] = Field(default_factory=list)
    hunter_xp: int = Field(default=20, ge=0, le=1000000)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=1, max_length=128)
    watchlist: list[dict[str, Any]] = Field(default_factory=list)
    hunter_xp: int = Field(default=20, ge=0, le=1000000)


class SyncStateRequest(BaseModel):
    watchlist: list[dict[str, Any]] = Field(default_factory=list)
    hunter_xp: int = Field(default=20, ge=0, le=1000000)


LEGAL_PAGE_META = {
    "about": ("About Us & Editorial Policy", "भारत का 100% वैध (Legal Streaming Guide) हिंदी और अंग्रेज़ी एनीमे व मंगा हब।", "EDITORIAL TRUST"),
    "privacy": ("Privacy Policy", "Google AdSense (DoubleClick DART), Client Storage व भारतीय DPDP Act 2023 नीति।", "PRIVACY & DPDP"),
    "terms": ("Terms & Conditions", "उपयोग की शर्तें, बौद्धिक संपदा व भारतीय न्यायिक क्षेत्राधिकार (Ahmedabad, Gujarat)।", "TERMS OF USE"),
    "dmca": ("DMCA & Copyright Compliance", "Zero-Piracy नीति व भारतीय कॉपीराइट एक्ट 1957 / DMCA 17 U.S.C. § 512 अनुपालन।", "DMCA SAFE HARBOR"),
    "disclaimer": ("Affiliate & Advertising Disclaimer", "ASCI India व FTC अनुरूप Amazon India एफिलिएट और AdSense प्रकटीकरण।", "AFFILIATE DISCLOSURE"),
    "contact": ("Contact & Business Sponsorships", "ब्रांड स्पॉन्सरशिप, कॉपीराइट सूचना और फीडबैक के लिए सीधे संपर्क करें।", "CONTACT & BUSINESS"),
}


@app.middleware("http")
async def bandwidth_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if response.status_code < 400:
        if path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif (
            path not in ("/healthz", "/robots.txt", "/sitemap.xml", "/ads.txt", "/login", "/api/psychology/state")
            and not path.startswith("/api/")
            and request.method == "GET"
        ):
            response.headers.setdefault("Cache-Control", "public, max-age=60, s-maxage=300, stale-while-revalidate=600")
    return response


static_dir = BASE_DIR / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

DEFAULT_CANONICAL_URL = "https://hindi-anime-guide.vercel.app"


def _resolve_base_url(request: Request) -> str:
    """Resolve canonical public origin, never exposing testserver in production URLs."""
    env_url = os.getenv("PUBLIC_SITE_URL", "").strip().rstrip("/")
    if env_url:
        return env_url
    host = (request.headers.get("x-forwarded-host") or request.url.netloc or "").lower()
    if not host or "testserver" in host or os.getenv("VERCEL"):
        return DEFAULT_CANONICAL_URL
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    return f"{proto}://{host}".rstrip("/")


def _amazon_affiliate_tag() -> str:
    return os.getenv("AMAZON_AFFILIATE_TAG", "").strip()


def _adsense_client_id() -> str:
    return os.getenv("ADSENSE_CLIENT_ID", "").strip()


templates.env.globals["get_amazon_tag"] = _amazon_affiliate_tag
templates.env.globals["get_adsense_client_id"] = _adsense_client_id
templates.env.globals["resolve_base_url"] = _resolve_base_url


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    status = exc.status_code if exc.status_code in (404, 500) else 404
    return templates.TemplateResponse(
        request,
        "error.html",
        {"request": request, "status_code": status, "active_nav": "home", "q": ""},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def custom_server_exception_handler(request: Request, exc: Exception):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    return templates.TemplateResponse(
        request,
        "error.html",
        {"request": request, "status_code": 500, "active_nav": "home", "q": ""},
        status_code=500,
    )


def _set_session_cookie(response: FastAPIResponse, user: dict[str, Any]) -> None:
    token = create_session_token(user["id"], user["email"])
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


ALLOWED_IMAGE_PROXY_HOSTS = (
    "animenewsnetwork.com",
    "cdn.myanimelist.net",
    "myanimelist.net",
    "storyblok.com",
    "livechart.me",
    "anilist.co",
    "img.youtube.com",
    "i.ytimg.com",
)


@app.get("/api/img-proxy")
async def image_proxy(url: str = Query(default="", max_length=700)):
    """Serve hotlink-protected external anime/news CDN images safely with stripped Referer."""
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == h or host.endswith(f".{h}") for h in ALLOWED_IMAGE_PROXY_HOSTS):
        raise HTTPException(status_code=400, detail="Disallowed image host")
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"},
            )
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if resp.status_code != 200 or not ctype.startswith("image/") or len(resp.content) > 8_000_000:
            raise HTTPException(status_code=502, detail="Upstream image unavailable")
        return Response(
            content=resp.content,
            media_type=ctype,
            headers={"Cache-Control": "public, max-age=86400, s-maxage=86400"},
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Image proxy error")


@app.get("/healthz", response_class=JSONResponse)
async def healthz():
    db_ok = ping_database()
    counts = get_counts() if db_ok else {}
    return {"status": "ok" if db_ok else "degraded", "version": "v2-clean", "db": db_ok, "counts": counts}


@app.get("/ads.txt", response_class=PlainTextResponse)
async def ads_txt():
    pub_id = os.getenv("ADSENSE_PUB_ID", "").strip()
    if pub_id:
        clean_id = pub_id if pub_id.startswith("pub-") else f"pub-{pub_id}"
        return f"google.com, {clean_id}, DIRECT, f08c47fec0942fa0\n"
    return "# Hindi Anime Guide ads.txt\n# Set ADSENSE_PUB_ID environment variable to publish google.com seller entry.\n"


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "auth.html",
        {"request": request, "active_nav": "login", "q": ""},
    )


@app.post("/api/auth/register", response_class=JSONResponse)
async def auth_register(payload: RegisterRequest, response: FastAPIResponse):
    if "@" not in payload.email or "." not in payload.email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="कृपया सही ईमेल पता दर्ज करें।")
    user = register_user(
        payload.username,
        payload.email,
        payload.password,
        payload.watchlist,
        payload.hunter_xp,
    )
    if not user:
        raise HTTPException(status_code=409, detail="यह ईमेल पहले से रजिस्टर्ड है। कृपया लॉगिन करें।")
    _set_session_cookie(response, user)
    return {"ok": True, "user": user}


@app.post("/api/auth/login", response_class=JSONResponse)
async def auth_login(payload: LoginRequest, response: FastAPIResponse):
    user = authenticate_user(payload.email, payload.password, payload.watchlist, payload.hunter_xp)
    if not user:
        raise HTTPException(status_code=401, detail="ईमेल या पासवर्ड गलत है।")
    _set_session_cookie(response, user)
    return {"ok": True, "user": user}


@app.post("/api/auth/logout", response_class=JSONResponse)
async def auth_logout(response: FastAPIResponse):
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/auth/me", response_class=JSONResponse)
async def auth_me(request: Request):
    user = get_current_user(request)
    return {"authenticated": bool(user), "user": user}


@app.post("/api/auth/sync", response_class=JSONResponse)
async def auth_sync(payload: SyncStateRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    updated = sync_user_state(user["id"], payload.watchlist, payload.hunter_xp)
    return {"ok": True, "user": updated}


@app.get("/api/psychology/state", response_class=JSONResponse)
async def psychology_state():
    return {
        "poll": get_poll_state(DEFAULT_POLL_KEY),
        "roulette_pool": get_roulette_pool(),
    }


@app.post("/api/poll/vote", response_class=JSONResponse)
async def poll_vote(payload: PollVoteRequest):
    return cast_poll_vote(payload.poll_key, payload.choice)


@app.post("/api/contact", response_class=JSONResponse)
async def contact_submit(payload: ContactRequest):
    if "@" not in payload.email or "." not in payload.email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="Please enter a valid email address.")
    row_id = save_contact_message(payload.name, payload.email, payload.subject, payload.message)
    return {
        "ok": True,
        "id": row_id,
        "subject": payload.subject,
        "message": "Contact inquiry saved successfully.",
    }


@app.get("/favicon.svg")
async def favicon_svg():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="10" fill="#06070c"/>
  <path d="M10 10 L56 10 L50 54 L4 54 Z" fill="#cf0f46" stroke="#ffffff" stroke-width="2.5"/>
  <text x="16" y="40" fill="#ffffff" font-family="sans-serif" font-size="24" font-weight="900">ह</text>
  <line x1="35" y1="16" x2="29" y2="48" stroke="#ffffff" stroke-width="3"/>
  <text x="36" y="42" fill="#ffffff" font-family="sans-serif" font-size="23" font-weight="900">E</text>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/favicon.ico")
async def favicon_ico():
    return RedirectResponse(url="/favicon.svg", status_code=307)


@app.get("/manifest.json", response_class=JSONResponse)
async def pwa_manifest():
    return {
        "name": "Hindi Anime Guide",
        "short_name": "AnimeGuide",
        "description": "India's Legal Hindi & English Anime Hub, Live IST Schedule & 3D Manga Shelf",
        "start_url": "/",
        "display": "standalone",
        "theme_color": "#06070c",
        "background_color": "#06070c",
        "icons": [
            {"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"},
            {"src": "/static/media/emblem_icon.webp", "sizes": "512x512", "type": "image/webp"},
        ],
    }


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt(request: Request):
    base = _resolve_base_url(request)
    return f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n"


@app.get("/sitemap.xml")
async def sitemap_xml(request: Request):
    base = _resolve_base_url(request)
    anime_slugs, news_slugs, guide_slugs = get_sitemap_urls()
    urls = [
        f"{base}/",
        f"{base}/explore",
        f"{base}/guides",
        f"{base}/awards",
        f"{base}/schedule",
        f"{base}/manga",
        f"{base}/news",
        f"{base}/trailers",
        f"{base}/about",
        f"{base}/privacy-policy",
        f"{base}/terms",
        f"{base}/dmca",
        f"{base}/disclaimer",
        f"{base}/contact",
    ]
    urls.extend(f"{base}/guides/{s}" for s in guide_slugs)
    urls.extend(f"{base}/anime/{s}" for s in anime_slugs)
    urls.extend(f"{base}/news/{s}" for s in news_slugs)

    items = "\n".join(f"  <url><loc>{escape(u)}</loc><changefreq>daily</changefreq></url>" for u in urls)
    xml_body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{items}\n</urlset>\n'
    return Response(content=xml_body, media_type="application/xml")


def _render_legal(request: Request, page_key: str) -> HTMLResponse:
    title, hi_summary, badge = LEGAL_PAGE_META[page_key]
    return templates.TemplateResponse(
        request,
        "legal.html",
        {
            "request": request,
            "active_nav": page_key,
            "legal_page": page_key,
            "page_title": title,
            "hindi_summary": hi_summary,
            "badge_label": badge,
            "q": "",
        },
    )


@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    return _render_legal(request, "about")


@app.get("/privacy-policy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return _render_legal(request, "privacy")


@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return _render_legal(request, "terms")


@app.get("/dmca", response_class=HTMLResponse)
async def dmca_page(request: Request):
    return _render_legal(request, "dmca")


@app.get("/disclaimer", response_class=HTMLResponse)
async def disclaimer_page(request: Request):
    return _render_legal(request, "disclaimer")


@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    return _render_legal(request, "contact")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, q: str = Query(default="")):
    data = filter_payload(get_cached_content(), q)
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "active_nav": "home",
            "q": q,
            "counts": get_counts(),
            "daily_poll": get_poll_state(DEFAULT_POLL_KEY),
            **data,
        },
    )


@app.get("/api/catalog/schedule", response_class=JSONResponse)
async def api_catalog_schedule(day: str = Query(default="ALL"), q: str = Query(default="")):
    data = filter_payload(get_cached_content(), q)
    if day and day != "ALL":
        items = data["schedule_by_day"].get(day, [])
    else:
        items = data["schedule"]
    return {"items": items, "total": len(items), "day": day}


@app.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request, q: str = Query(default="")):
    data = filter_payload(get_cached_content(), q)
    return templates.TemplateResponse(
        request,
        "section.html",
        {
            **data,
            "request": request,
            "active_nav": "schedule",
            "section": "schedule",
            "section_title": "साप्ताहिक IST एनीमे शेड्यूल",
            "section_title_en": "Weekly IST Anime Schedule",
            "q": q,
        },
    )


@app.get("/explore", response_class=HTMLResponse)
async def explore_page(
    request: Request,
    q: str = Query(default=""),
    type: str = Query(default="all"),
    hindi: int = Query(default=0),
):
    catalog = explore_catalog(q, type, bool(hindi))
    return templates.TemplateResponse(
        request,
        "explore.html",
        {
            "request": request,
            "active_nav": "explore",
            "q": q,
            **catalog,
        },
    )


@app.get("/guides", response_class=HTMLResponse)
async def guides_page(request: Request, cat: str = Query(default="")):
    bundle = get_guides_list(cat)
    return templates.TemplateResponse(
        request,
        "guides.html",
        {
            "request": request,
            "active_nav": "guides",
            "q": "",
            **bundle,
        },
    )


@app.get("/guides/{slug}", response_class=HTMLResponse)
async def guide_detail_page(request: Request, slug: str):
    bundle = get_guide_detail(slug)
    if not bundle:
        raise HTTPException(status_code=404, detail="Editorial guide not found")
    guide = bundle["guide"]
    base = _resolve_base_url(request)
    json_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": guide["title"],
        "description": guide["meta_description"],
        "image": guide.get("cover_image") or f"{base}/static/media/emblem_icon.webp",
        "author": {"@type": "Organization", "name": "Hindi Anime Guide Editorial Desk"},
        "publisher": {"@type": "Organization", "name": "Hindi Anime Guide"},
        "mainEntityOfPage": f"{base}/guides/{guide['slug']}",
        "wordCount": guide["word_count"],
        "inLanguage": ["hi", "en"],
    }
    return templates.TemplateResponse(
        request,
        "guide_detail.html",
        {
            "request": request,
            "active_nav": "guides",
            "json_ld": json_ld,
            "q": "",
            **bundle,
        },
    )


@app.get("/awards", response_class=HTMLResponse)
async def awards_page(request: Request):
    awards = get_awards_data()
    return templates.TemplateResponse(
        request,
        "awards.html",
        {
            "request": request,
            "active_nav": "awards",
            "q": "",
            **awards,
        },
    )


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "request": request,
            "active_nav": "profile",
            "user": user,
            "q": "",
        },
    )


@app.get("/api/catalog/manga", response_class=JSONResponse)
async def api_catalog_manga(
    q: str = Query(default=""),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=24, ge=1, le=200),
):
    data = filter_payload(get_cached_content(), q)
    all_manga = data["manga"]
    chunk = all_manga[offset : offset + limit]
    return {"items": chunk, "total": len(all_manga), "offset": offset, "has_more": offset + limit < len(all_manga)}


@app.get("/manga", response_class=HTMLResponse)
async def manga_page(request: Request, q: str = Query(default="")):
    data = filter_payload(get_cached_content(), q)
    total_manga = len(data["manga"])
    initial_manga = data["manga"][:24]
    return templates.TemplateResponse(
        request,
        "section.html",
        {
            **data,
            "manga": initial_manga,
            "total_manga_count": total_manga,
            "has_more_manga": total_manga > len(initial_manga),
            "request": request,
            "active_nav": "manga",
            "section": "manga",
            "section_title": "3D मंगा शेल्फ़ और हिंदी कलेक्शन",
            "section_title_en": "3D Manga Shelf & Collection",
            "q": q,
        },
    )


@app.get("/news", response_class=HTMLResponse)
async def news_page(request: Request, q: str = Query(default="")):
    data = filter_payload(get_cached_content(), q)
    return templates.TemplateResponse(
        request,
        "section.html",
        {
            **data,
            "request": request,
            "active_nav": "news",
            "section": "news",
            "section_title": "ताज़ा एनीमे न्यूज़ और हिंदी गाइड्स",
            "section_title_en": "Fresh Anime News & Hindi Guides",
            "q": q,
        },
    )


@app.get("/news/{slug}", response_class=HTMLResponse)
async def news_detail_page(request: Request, slug: str):
    item = get_news_detail(slug)
    if not item:
        raise HTTPException(status_code=404, detail="News item not found")
    json_ld = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": item.get("title_hi") or item.get("title_en") or "",
        "description": item.get("summary") or "",
        "image": item.get("image_url") or "",
        "publisher": {"@type": "Organization", "name": "Hindi Anime Guide"},
    }
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "request": request,
            "active_nav": "news",
            "item": item,
            "related": item.get("related", []),
            "json_ld": json_ld,
            "badge_text": item.get("category", "ANIME NEWS"),
            "back_url": "/news",
            "back_label": "सभी एनीमे न्यूज़ पर वापस जाएं",
            "q": "",
        },
    )


@app.get("/anime/{slug}", response_class=HTMLResponse)
async def anime_detail_page(request: Request, slug: str):
    item = get_anime_detail(slug)
    if not item:
        raise HTTPException(status_code=404, detail="Anime not found")
    json_ld = {
        "@context": "https://schema.org",
        "@type": "TVSeries",
        "name": item.get("title_en") or item.get("title_hi") or "",
        "alternateName": item.get("title_hi") or "",
        "description": item.get("tagline") or item.get("summary") or "",
        "image": item.get("cover_url") or item.get("image_url") or "",
        "inLanguage": "hi",
    }
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "request": request,
            "active_nav": "home",
            "item": item,
            "related": item.get("related", []),
            "json_ld": json_ld,
            "badge_text": item.get("badge") or "ANIME SPOTLIGHT",
            "back_url": "/",
            "back_label": "होमपेज पर वापस जाएं",
            "q": "",
        },
    )


@app.get("/trailers", response_class=HTMLResponse)
async def trailers_page(request: Request, q: str = Query(default="")):
    data = filter_payload(get_cached_content(), q)
    return templates.TemplateResponse(
        request,
        "section.html",
        {
            **data,
            "request": request,
            "active_nav": "trailers",
            "section": "trailers",
            "section_title": "लाइसेंस्ड एनीमे ट्रेलर्स और PVs",
            "section_title_en": "Licensed Anime Trailers & PVs",
            "q": q,
        },
    )
