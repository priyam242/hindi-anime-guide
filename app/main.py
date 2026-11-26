from pathlib import Path
from xml.sax.saxutils import escape

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import ping_database
from app.services.content import (
    filter_payload,
    get_anime_detail,
    get_cached_content,
    get_counts,
    get_news_detail,
    get_sitemap_urls,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

app = FastAPI(title="Hindi Anime Guide", version="v2-clean")
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.middleware("http")
async def bandwidth_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if response.status_code < 400:
        if path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path not in ("/healthz", "/robots.txt", "/sitemap.xml") and request.method == "GET":
            response.headers.setdefault("Cache-Control", "public, max-age=60, s-maxage=300, stale-while-revalidate=600")
    return response


static_dir = BASE_DIR / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/healthz", response_class=JSONResponse)
async def healthz():
    db_ok = ping_database()
    counts = get_counts() if db_ok else {}
    return {"status": "ok" if db_ok else "degraded", "version": "v2-clean", "db": db_ok, "counts": counts}


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
    ]
    urls.extend(f"{base}/anime/{s}" for s in anime_slugs)
    urls.extend(f"{base}/news/{s}" for s in news_slugs)

    items = "\n".join(f"  <url><loc>{escape(u)}</loc><changefreq>daily</changefreq></url>" for u in urls)
    xml_body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{items}\n</urlset>\n'
    return Response(content=xml_body, media_type="application/xml")


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
