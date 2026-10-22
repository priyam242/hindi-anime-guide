"""Public pages router: Home, Explore/Search, Article, Category, Info pages, Sitemap, Robots."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, Request, Query, Form, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from sqlmodel import Session, select
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from .. import db
from ..db import get_session
from ..models import Category, Article, Availability, Comment, Platform, Bookmark, Notification, Admin1
from ..markdown_utils import render_markdown_with_toc
from ..templates_config import render

router = APIRouter()

# In-memory rate-limiting for reporting outdated availability info (IP -> list of timestamps)
_report_rate_limit: Dict[str, List[float]] = {}


def get_categories(session: Session) -> List[Category]:
    return list(session.exec(select(Category).order_by(Category.display_order)).all())


def _related_articles(session: Session, article: Article) -> List[Article]:
    order_expr = func.coalesce(Article.published_at, Article.created_at).desc()
    cat_names = [c.name for c in (article.categories or [])] or ([article.category] if article.category else [])
    related = list(session.exec(
        select(Article)
        .where(
            or_(
                Article.category.in_(cat_names),
                Article.categories.any(Category.name.in_(cat_names))
            ),
            Article.id != article.id
        )
        .options(
            selectinload(Article.category_rel),
            selectinload(Article.categories),
            selectinload(Article.availabilities),
        )
        .order_by(order_expr, Article.id.desc())
        .limit(3)
    ).all())
    if len(related) < 3:
        excluded = {article.id}.union(a.id for a in related)
        top_up = list(session.exec(
            select(Article)
            .where(Article.id.notin_(excluded))
            .options(
                selectinload(Article.category_rel),
                selectinload(Article.categories),
                selectinload(Article.availabilities),
            )
            .order_by(order_expr, Article.id.desc())
            .limit(3 - len(related))
        ).all())
        related.extend(top_up)
    return related


@router.get("/", response_class=HTMLResponse)
def home_page(request: Request, session: Session = Depends(get_session)):
    articles = session.exec(
        select(Article)
        .where(Article.published_at.is_not(None))
        .options(
            selectinload(Article.category_rel),
            selectinload(Article.categories),
            selectinload(Article.availabilities),
        )
        .order_by(Article.created_at.desc(), Article.id.desc())
        .limit(24)
    ).all()
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    featured_article = None
    side_articles = []
    grid_articles = []
    if articles:
        featured_idx = 0
        for idx, art in enumerate(articles):
            if getattr(art, "is_featured", False):
                featured_idx = idx
                break
        featured_article = articles[featured_idx]
        other_articles = [a for i, a in enumerate(articles) if i != featured_idx]
        side_articles = other_articles[:3]
        grid_articles = other_articles[3:]
    return render(
        "home.html",
        articles=articles,
        featured_article=featured_article,
        side_articles=side_articles,
        grid_articles=grid_articles,
        categories=categories,
        user=user,
        active_nav="home",
        request=request,
    )


# ---------- EXPLORE & SEARCH ----------
@router.get("/explore", response_class=HTMLResponse)
@router.get("/search", response_class=HTMLResponse)
def explore_page(
    request: Request,
    q: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    hindi_dub: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    sort: Optional[str] = Query("latest"),
    session: Session = Depends(get_session)
):
    query = (
        select(Article)
        .where(Article.published_at.is_not(None))
        .options(
            selectinload(Article.category_rel),
            selectinload(Article.categories),
            selectinload(Article.availabilities).selectinload(Availability.platform_rel),
        )
    )

    clean_q = (q or "").strip()
    if clean_q:
        q_filter = f"%{clean_q}%"
        query = query.where(
            or_(
                Article.title.ilike(q_filter),
                Article.content_md.ilike(q_filter),
                Article.meta_description.ilike(q_filter),
                Article.category.ilike(q_filter),
                Article.categories.any(Category.name.ilike(q_filter)),
                Article.categories.any(Category.display_name.ilike(q_filter)),
            )
        )

    if category and category.strip():
        c_clean = category.strip()
        query = query.where(
            or_(
                Article.category == c_clean,
                Article.categories.any(Category.name == c_clean)
            )
        )

    if platform and platform.strip():
        query = query.where(
            Article.availabilities.any(Availability.platform_id == platform.strip())
        )

    is_hindi_dub = str(hindi_dub).lower() in ("1", "true", "yes", "on")
    if is_hindi_dub:
        query = query.where(
            Article.availabilities.any(
                or_(
                    func.lower(Availability.hindi_dub).in_(["yes", "available", "true", "1", "हाँ", "हां"]),
                    Availability.languages.ilike("%hindi%")
                )
            )
        )

    if sort == "az":
        query = query.order_by(Article.title.asc())
    else:
        order_expr = func.coalesce(Article.published_at, Article.created_at).desc()
        query = query.order_by(order_expr, Article.id.desc())

    articles = list(session.exec(query).all())
    categories = get_categories(session)
    platforms = list(session.exec(select(Platform).order_by(Platform.display_order)).all())
    user = getattr(request.state, "user", None)

    return render(
        "explore.html",
        articles=articles,
        total_count=len(articles),
        q=clean_q,
        selected_platform=platform or "",
        selected_hindi_dub=is_hindi_dub,
        selected_category=category or "",
        selected_sort=sort or "latest",
        categories=categories,
        platforms=platforms,
        user=user,
        active_nav="explore",
        request=request,
    )


# ---------- ARTICLE DETAIL ----------
@router.get("/article/{slug}", response_class=HTMLResponse)
def article_page(request: Request, slug: str, session: Session = Depends(get_session)):
    stmt = (
        select(Article)
        .where(Article.slug == slug)
        .options(
            selectinload(Article.category_rel),
            selectinload(Article.categories),
            selectinload(Article.availabilities).selectinload(Availability.platform_rel),
        )
    )
    article = session.exec(stmt).first()
    if article is None:
        raise HTTPException(status_code=404, detail="लेख नहीं मिला")
    
    # Load approved comments with user info
    comments = session.exec(
        select(Comment)
        .where(Comment.article_id == article.id)
        .where(Comment.is_approved == True)
        .order_by(Comment.created_at.desc())
    ).all()
    for c in comments:
        _ = c.user_rel
    
    categories = get_categories(session)
    related = _related_articles(session, article)
    user = getattr(request.state, "user", None)
    content_html, toc_html = render_markdown_with_toc(article.content_md)
    valid_availabilities = [a for a in article.availabilities if getattr(a, "last_checked", None) is not None]

    is_bookmarked = False
    if user and getattr(user, "id", None):
        bm = session.exec(
            select(Bookmark).where(Bookmark.user_id == user.id, Bookmark.article_id == article.id)
        ).first()
        is_bookmarked = bm is not None

    return render(
        "article.html",
        article=article,
        availabilities=article.availabilities,
        valid_availabilities=valid_availabilities,
        related_articles=related,
        categories=categories,
        comments=comments,
        user=user,
        is_bookmarked=is_bookmarked,
        content_html=content_html,
        toc_html=toc_html,
        request=request,
    )


# ---------- REPORT OUTDATED STREAMING INFO ----------
@router.post("/article/{slug}/report-outdated")
async def report_outdated_info(
    request: Request,
    slug: str,
    note: str = Form(...),
    platform_name: Optional[str] = Form(""),
    session: Session = Depends(get_session)
):
    article = session.exec(select(Article).where(Article.slug == slug)).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    # Simple IP rate limiting: max 5 reports per 10 minutes
    client_ip = request.client.host if request.client else "unknown"
    now_ts = datetime.now().timestamp()
    timestamps = _report_rate_limit.get(client_ip, [])
    timestamps = [ts for ts in timestamps if now_ts - ts < 600]
    if len(timestamps) >= 5:
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", ""):
            return JSONResponse(status_code=429, content={"error": "Too many requests. Please wait a few minutes."})
        return HTMLResponse(status_code=429, content="Too many requests. Please wait a few minutes.")
    timestamps.append(now_ts)
    _report_rate_limit[client_ip] = timestamps

    clean_note = note.strip()[:1000]
    if not clean_note:
        return JSONResponse(status_code=400, content={"error": "Note cannot be empty."})
        
    p_info = f" (प्लेटफॉर्म: {platform_name.strip()})" if platform_name and platform_name.strip() else ""
    user = getattr(request.state, "user", None)
    user_str = f"User @{user.username}" if user and getattr(user, "username", None) else f"Guest ({client_ip})"
    
    admins = session.exec(select(Admin1)).all()
    if admins:
        for admin in admins:
            notif = Notification(
                admin_id=admin.id,
                user_id=None,
                title="स्ट्रीमिंग अपडेट रिपोर्ट",
                message=f"{user_str} ने '{article.title}' के लिए रिपोर्ट किया: {clean_note}{p_info}",
                notification_type="warning",
                link=f"/article/{article.slug}",
                is_read=False,
                created_at=datetime.now(timezone.utc)
            )
            session.add(notif)
    else:
        notif = Notification(
            admin_id=None,
            user_id=None,
            title="स्ट्रीमिंग अपडेट रिपोर्ट",
            message=f"{user_str} ने '{article.title}' के लिए रिपोर्ट किया: {clean_note}{p_info}",
            notification_type="warning",
            link=f"/article/{article.slug}",
            is_read=False,
            created_at=datetime.now(timezone.utc)
        )
        session.add(notif)
    session.commit()

    if request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"success": True, "message": "स्ट्रीमिंग जानकारी रिपोर्ट दर्ज कर ली गई है। धन्यवाद!"})
    return RedirectResponse(url=f"/article/{slug}?reported=1", status_code=303)


# ---------- CATEGORY PAGE ----------
@router.get("/category/{slug}", response_class=HTMLResponse)
def category_page(request: Request, slug: str, session: Session = Depends(get_session)):
    category = session.exec(select(Category).where(Category.name == slug)).first()
    categories = get_categories(session)
    if not category:
        raise HTTPException(status_code=404, detail="श्रेणी नहीं मिली")
    articles = session.exec(
        select(Article)
        .where(
            or_(
                Article.category == category.name,
                Article.categories.any(Category.name == category.name)
            )
        )
        .options(
            selectinload(Article.category_rel),
            selectinload(Article.categories),
            selectinload(Article.availabilities),
        )
        .order_by(Article.created_at.desc(), Article.id.desc())
    ).all()
    user = getattr(request.state, "user", None)
    return render(
        "category.html",
        category=category,
        articles=articles,
        categories=categories,
        user=user,
        active_nav=slug,
        request=request,
    )


# ---------- LEGAL / INFO PAGES ----------
# Last-updated dates keyed by route slug.
# To update a page's date: change only the matching entry here.
_LEGAL_DATES = {
    "privacy":               ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "terms":                 ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "cookies":               ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "community-guidelines":  ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "copyright":             ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "streaming-disclosure":  ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "disclaimer":            ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "about":                 ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "editorial-policy":      ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
    "contact":               ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"),
}

# Sidebar-style related-links shown at bottom of each legal page.
_RELATED_LINKS = [
    {"url": "/privacy",              "label_hi": "गोपनीयता नीति",       "label_en": "Privacy Policy"},
    {"url": "/terms",                "label_hi": "उपयोग की शर्तें",     "label_en": "Terms of Use"},
    {"url": "/cookies",              "label_hi": "कुकी नीति",           "label_en": "Cookie Policy"},
    {"url": "/community-guidelines", "label_hi": "सामुदायिक दिशानिर्देश", "label_en": "Community Guidelines"},
    {"url": "/copyright",            "label_hi": "कॉपीराइट",           "label_en": "Copyright"},
    {"url": "/streaming-disclosure", "label_hi": "Streaming प्रकटीकरण", "label_en": "Streaming Disclosure"},
    {"url": "/disclaimer",           "label_hi": "अस्वीकरण",           "label_en": "Disclaimer"},
    {"url": "/about",                "label_hi": "हमारे बारे में",        "label_en": "About Us"},
    {"url": "/editorial-policy",     "label_hi": "संपादकीय नीति",        "label_en": "Editorial Policy"},
    {"url": "/contact",              "label_hi": "संपर्क",             "label_en": "Contact Us"},
]


def _legal_ctx(
    request: Request,
    session: Session,
    slug: str,
    page_title_hi: str,
    page_title_en: str,
    toc: list = None,
) -> dict:
    """Build common context dict for a legal page."""
    lang = request.cookies.get("site_lang", "hi")
    date_entry = _LEGAL_DATES.get(slug, ("21 अक्टूबर 2026", "October 21, 2026", "2026-10-21"))
    last_updated_hi = date_entry[0]
    last_updated_en = date_entry[1]
    last_updated_iso = date_entry[2]
    page_title = page_title_hi if lang == "hi" else page_title_en
    categories = get_categories(session)
    user = getattr(request.state, "user", None)
    related = [lnk for lnk in _RELATED_LINKS if lnk["url"] != f"/{slug}"]
    return dict(
        page_title=page_title,
        page_title_hi=page_title_hi,
        page_title_en=page_title_en,
        lang=lang,
        last_updated=last_updated_hi,
        last_updated_hi=last_updated_hi,
        last_updated_en=last_updated_en,
        last_updated_iso=last_updated_iso,
        related_links=related,
        categories=categories,
        user=user,
        request=request,
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "data-collected",   "title": "डेटा संग्रह" if lang=="hi" else "Data Collected"},
        {"id": "purpose",          "title": "उद्देश्य" if lang=="hi" else "Purpose"},
        {"id": "cookies-section",  "title": "कुकीज़" if lang=="hi" else "Cookies"},
        {"id": "sharing",          "title": "साझाकरण" if lang=="hi" else "Sharing"},
        {"id": "retention",        "title": "संधारण" if lang=="hi" else "Retention"},
        {"id": "rights",           "title": "आपके अधिकार" if lang=="hi" else "Your Rights"},
        {"id": "children",         "title": "बच्चों की गोपनीयता" if lang=="hi" else "Children"},
        {"id": "changes",          "title": "परिवर्तन" if lang=="hi" else "Changes"},
        {"id": "contact-priv",     "title": "संपर्क" if lang=="hi" else "Contact"},
    ]
    ctx = _legal_ctx(request, session, "privacy",
                     "गोपनीयता नीति", "Privacy Policy", toc)
    return render("legal/privacy.html", **ctx)


@router.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "acceptance",   "title": "स्वीकृति" if lang=="hi" else "Acceptance"},
        {"id": "account",      "title": "खाता" if lang=="hi" else "Account"},
        {"id": "comments",     "title": "टिप्पणियाँ" if lang=="hi" else "Comments"},
        {"id": "prohibited",   "title": "निषिद्ध" if lang=="hi" else "Prohibited"},
        {"id": "suspension",   "title": "निलंबन" if lang=="hi" else "Suspension"},
        {"id": "streaming",    "title": "Streaming" if lang=="hi" else "Streaming"},
        {"id": "liability",    "title": "दायित्व" if lang=="hi" else "Liability"},
        {"id": "law",          "title": "शासकीय कानून" if lang=="hi" else "Governing Law"},
    ]
    ctx = _legal_ctx(request, session, "terms",
                     "उपयोग की शर्तें", "Terms of Use", toc)
    return render("legal/terms.html", **ctx)


@router.get("/cookies", response_class=HTMLResponse)
def cookies_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "what-are-cookies", "title": "कुकीज़ क्या हैं" if lang=="hi" else "What Are Cookies"},
        {"id": "our-cookies",      "title": "हमारी कुकीज़" if lang=="hi" else "Our Cookies"},
        {"id": "control",          "title": "नियंत्रण" if lang=="hi" else "Control"},
    ]
    ctx = _legal_ctx(request, session, "cookies",
                     "कुकी नीति", "Cookie Policy", toc)
    return render("legal/cookies.html", **ctx)


@router.get("/community-guidelines", response_class=HTMLResponse)
def community_guidelines_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "purpose-cg",   "title": "उद्देश्य" if lang=="hi" else "Purpose"},
        {"id": "allowed",      "title": "प्रोत्साहित" if lang=="hi" else "Encouraged"},
        {"id": "prohibited-cg","title": "निषिद्ध" if lang=="hi" else "Prohibited"},
        {"id": "reporting",    "title": "रिपोर्ट" if lang=="hi" else "Reporting"},
        {"id": "moderation",   "title": "Moderation" if lang=="hi" else "Moderation"},
    ]
    ctx = _legal_ctx(request, session, "community-guidelines",
                     "सामुदायिक दिशानिर्देश", "Community Guidelines", toc)
    return render("legal/community-guidelines.html", **ctx)


@router.get("/copyright", response_class=HTMLResponse)
def copyright_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "scope",         "title": "दायरा" if lang=="hi" else "Scope"},
        {"id": "site-content",  "title": "साइट सामग्री" if lang=="hi" else "Site Content"},
        {"id": "takedown",      "title": "Takedown" if lang=="hi" else "Takedown"},
        {"id": "counter-notice","title": "Counter-Notice" if lang=="hi" else "Counter-Notice"},
    ]
    ctx = _legal_ctx(request, session, "copyright",
                     "कॉपीराइट और Takedown", "Copyright & Takedown", toc)
    return render("legal/copyright.html", **ctx)


@router.get("/streaming-disclosure", response_class=HTMLResponse)
def streaming_disclosure_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "what-we-do",   "title": "हम क्या करते हैं" if lang=="hi" else "What We Do"},
        {"id": "no-piracy",    "title": "पायरेसी नीति" if lang=="hi" else "Piracy Policy"},
        {"id": "platforms",    "title": "Platforms" if lang=="hi" else "Platforms"},
        {"id": "accuracy",     "title": "सटीकता" if lang=="hi" else "Accuracy"},
        {"id": "no-affiliate", "title": "Affiliate" if lang=="hi" else "Affiliate"},
    ]
    ctx = _legal_ctx(request, session, "streaming-disclosure",
                     "Streaming प्रकटीकरण", "Streaming Disclosure", toc)
    return render("legal/streaming-disclosure.html", **ctx)


@router.get("/disclaimer", response_class=HTMLResponse)
def disclaimer_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "what-is-disclaimer", "title": "उद्देश्य" if lang=="hi" else "Purpose"},
        {"id": "informational",      "title": "केवल सूचनात्मक" if lang=="hi" else "Informational Only"},
        {"id": "third-party",        "title": "तृतीय पक्ष" if lang=="hi" else "Third Party"},
        {"id": "accuracy-dis",       "title": "सटीकता" if lang=="hi" else "Accuracy"},
        {"id": "copyright-dis",      "title": "Anime ट्रेडमार्क" if lang=="hi" else "Anime Trademarks"},
        {"id": "liability-dis",      "title": "दायित्व" if lang=="hi" else "Liability"},
    ]
    ctx = _legal_ctx(request, session, "disclaimer",
                     "अस्वीकरण", "Disclaimer", toc)
    return render("legal/disclaimer.html", **ctx)


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "who-we-are",    "title": "हम कौन हैं" if lang=="hi" else "Who We Are"},
        {"id": "mission",       "title": "मिशन" if lang=="hi" else "Mission"},
        {"id": "team",          "title": "टीम" if lang=="hi" else "Team"},
        {"id": "contact-about", "title": "संपर्क" if lang=="hi" else "Contact"},
    ]
    ctx = _legal_ctx(request, session, "about",
                     "हमारे बारे में", "About Us", toc)
    return render("legal/about.html", **ctx)


@router.get("/editorial-policy", response_class=HTMLResponse)
def editorial_policy_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "purpose-ep",  "title": "उद्देश्य" if lang=="hi" else "Purpose"},
        {"id": "sourcing",    "title": "स्रोत" if lang=="hi" else "Sourcing"},
        {"id": "corrections", "title": "सुधार" if lang=="hi" else "Corrections"},
        {"id": "independence","title": "स्वतंत्रता" if lang=="hi" else "Independence"},
        {"id": "ai-use",      "title": "AI" if lang=="hi" else "AI"},
    ]
    ctx = _legal_ctx(request, session, "editorial-policy",
                     "संपादकीय नीति", "Editorial Policy", toc)
    return render("legal/editorial-policy.html", **ctx)


@router.get("/contact", response_class=HTMLResponse)
def contact_page(request: Request, session: Session = Depends(get_session)):
    lang = request.cookies.get("site_lang", "hi")
    toc = [
        {"id": "contact-intro",      "title": "परिचय" if lang=="hi" else "Intro"},
        {"id": "contact-email",      "title": "ईमेल" if lang=="hi" else "Email"},
        {"id": "contact-categories", "title": "श्रेणियाँ" if lang=="hi" else "Categories"},
        {"id": "response",           "title": "प्रतिक्रिया" if lang=="hi" else "Response"},
    ]
    ctx = _legal_ctx(request, session, "contact",
                     "संपर्क", "Contact", toc)
    return render("legal/contact.html", **ctx)


# ---------- SEO: ROBOTS.TXT & SITEMAP.XML ----------
@router.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt(request: Request):
    from ..config import get_settings
    settings = get_settings()
    base_url = (settings.public_base_url or str(request.base_url)).rstrip("/")
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /settings/",
        "Disallow: /auth/",
        f"Sitemap: {base_url}/sitemap.xml",
    ]
    return PlainTextResponse("\n".join(lines), media_type="text/plain")


@router.get("/sitemap.xml")
def sitemap_xml(request: Request, session: Session = Depends(get_session)):
    from ..config import get_settings
    settings = get_settings()
    base_url = (settings.public_base_url or str(request.base_url)).rstrip("/")
    raw_articles = session.exec(select(Article).where(Article.published_at.is_not(None))).all()
    articles = [a for a in raw_articles if not a.is_sample]
    categories = session.exec(select(Category)).all()

    # Static legal pages added to sitemap
    _LEGAL_SLUGS = [
        "privacy", "terms", "cookies", "community-guidelines",
        "copyright", "streaming-disclosure", "disclaimer",
        "about", "editorial-policy", "contact",
    ]

    urls = [
        f"""  <url>
    <loc>{base_url}/</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>""",
        f"""  <url>
    <loc>{base_url}/explore</loc>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>""",
    ]

    for slug in _LEGAL_SLUGS:
        date_entry = _LEGAL_DATES.get(slug, ("", "", "2026-10-21"))
        iso = date_entry[2]
        urls.append(
            f"""  <url>
    <loc>{base_url}/{slug}</loc>
    <lastmod>{iso}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>"""
        )

    for cat in categories:
        urls.append(
            f"""  <url>
    <loc>{base_url}/category/{cat.slug}</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>"""
        )

    for art in articles:
        lastmod = art.updated_at or art.published_at or art.created_at
        lastmod_str = lastmod.strftime("%Y-%m-%d") if lastmod else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        urls.append(
            f"""  <url>
    <loc>{base_url}/article/{art.slug}</loc>
    <lastmod>{lastmod_str}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>"""
        )

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""

    return Response(content=xml_content, media_type="application/xml")
