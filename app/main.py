import os
from pathlib import Path
from typing import Any, Literal
from xml.sax.saxutils import escape

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response as FastAPIResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

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
    filter_payload,
    get_anime_detail,
    get_cached_content,
    get_counts,
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
    "about": ("About Us & Editorial Policy", "भारत का 100% वैध (Legal & Licensed) हिंदी एनीमे और मंगा डायरेक्टरी हब।", "EDITORIAL TRUST"),
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


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt(request: Request):
    base = str(request.base_url).rstrip("/")
    return f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n"


@app.get("/sitemap.xml")
async def sitemap_xml(request: Request):
    base = str(request.base_url).rstrip("/")
    anime_slugs, news_slugs = get_sitemap_urls()
    urls = [
        f"{base}/",
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


@app.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request, q: str = Query(default="")):
    data = filter_payload(get_cached_content(), q)
    return templates.TemplateResponse(
        request,
        "section.html",
        {
            "request": request,
            "active_nav": "schedule",
            "section": "schedule",
            "section_title": "साप्ताहिक IST एनीमे शेड्यूल",
            "q": q,
            **data,
        },
    )


@app.get("/manga", response_class=HTMLResponse)
async def manga_page(request: Request, q: str = Query(default="")):
    data = filter_payload(get_cached_content(), q)
    return templates.TemplateResponse(
        request,
        "section.html",
        {
            "request": request,
            "active_nav": "manga",
            "section": "manga",
            "section_title": "3D मंगा शेल्फ़ और हिंदी कलेक्शन",
            "q": q,
            **data,
        },
    )


@app.get("/news", response_class=HTMLResponse)
async def news_page(request: Request, q: str = Query(default="")):
    data = filter_payload(get_cached_content(), q)
    return templates.TemplateResponse(
        request,
        "section.html",
        {
            "request": request,
            "active_nav": "news",
            "section": "news",
            "section_title": "ताज़ा एनीमे न्यूज़ और हिंदी गाइड्स",
            "q": q,
            **data,
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
            "request": request,
            "active_nav": "trailers",
            "section": "trailers",
            "section_title": "आधिकारिक एनीमे ट्रेलर्स और PVs",
            "q": q,
            **data,
        },
    )
