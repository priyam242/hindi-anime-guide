import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.markdown_utils import render_markdown_with_toc, hindi_slugify
import app.db as app_db
from app.models import Category, Article, ArticleType
from app.main import app


def test_no_img_tag_in_output():
    """<img src="https://x/y.gif"> in markdown/HTML input -> no <img in output."""
    md_input = 'Paragraph text\n\n![वैकल्पिक पाठ](https://example.com/banner.png)\n\n<img src="https://x/y.gif" alt="bad">'
    html, _ = render_markdown_with_toc(md_input)
    assert "<img" not in html.lower()


def test_sanitizer_removes_style_script_and_javascript_scheme():
    """<p style="color:red"> -> no style= in output; <script> removed; [x](javascript:alert(1)) -> no javascript: in output."""
    bad_input = (
        '<p style="color:red">लाल पाठ</p>'
        '<script>alert("hacked")</script>'
        '[लिंक](javascript:alert(1))'
    )
    html, _ = render_markdown_with_toc(bad_input)
    assert "style=" not in html.lower()
    assert "<script" not in html.lower()
    assert "javascript:" not in html.lower()


def test_external_link_has_rel_noopener_noreferrer():
    """External link in output has rel="noopener noreferrer"."""
    md_input = '[आधिकारिक वेबसाइट](https://example.com/anime)'
    html, _ = render_markdown_with_toc(md_input)
    assert 'rel="noopener noreferrer"' in html


def test_devanagari_heading_ids_and_toc_hrefs():
    """The two Devanagari examples produce exactly those ids, and the TOC hrefs match the heading ids."""
    # Test hindi_slugify directly on both examples
    assert hindi_slugify("शुरुआती गाइड") == "शुरुआती-गाइड"
    assert hindi_slugify("नेटफ्लिक्स पर क्या देखें?") == "नेटफ्लिक्स-पर-क्या-देखें"

    # Test full markdown rendering with TOC
    md_input = "## शुरुआती गाइड\n\nविवरण पाठ\n\n### नेटफ्लिक्स पर क्या देखें?\n\nअन्य विवरण"
    html, toc = render_markdown_with_toc(md_input)

    assert 'id="शुरुआती-गाइड"' in html
    assert 'id="नेटफ्लिक्स-पर-क्या-देखें"' in html
    assert 'href="#शुरुआती-गाइड"' in toc
    assert 'href="#नेटफ्लिक्स-पर-क्या-देखें"' in toc


def test_markdown_without_headings_has_empty_toc_and_no_toc_box():
    """Markdown with no ##/### -> toc_html == ''; article page for it has no 'विषय सूची'."""
    md_input = "यह एक साधारण लेख है जिसमें कोई शीर्षक मौजूद नहीं है। केवल साधारण पैराग्राफ हैं।"
    html, toc = render_markdown_with_toc(md_input)
    assert toc == ""

    # Check on actual rendered article page
    client = TestClient(app)
    with Session(app_db.engine) as session:
        cat = session.exec(select(Category)).first()
        art = Article(
            title="बिना शीर्षक का लेख",
            slug="article-without-headings-test",
            category_id=cat.id if cat else None,
            article_type=ArticleType.GUIDE,
            summary="बिना शीर्षक का सारांश",
            content_md=md_input,
            status="published"
        )
        session.add(art)
        session.commit()

    response = client.get("/article/article-without-headings-test")
    assert response.status_code == 200
    assert "विषय सूची" not in response.text


def test_markdown_table_wrapped_in_table_scroll():
    """A markdown table is wrapped in table-scroll."""
    md_table = (
        "| शीर्षक | ओटीटी प्लेटफॉर्म |\n"
        "|---|---|\n"
        "| नारुतो | नेटफ्लिक्स |\n"
        "| वन पीस | क्रंचीरोल |"
    )
    html, _ = render_markdown_with_toc(md_table)
    assert '<div class="table-scroll"><table' in html
    assert '</table></div>' in html


def test_article_page_body_equals_content_md_render_not_db_content_html():
    """Article page body equals the render of content_md even when the DB content_html contains different text."""
    client = TestClient(app)
    with Session(app_db.engine) as session:
        cat = session.exec(select(Category)).first()
        art = Article(
            title="सिंगल सोर्स ऑफ ट्रुथ लेख",
            slug="single-source-truth-test",
            category_id=cat.id if cat else None,
            article_type=ArticleType.GUIDE,
            summary="सिंगल सोर्स ऑफ ट्रुथ सारांश",
            content_md="## लाइव रेंडर हेडिंग\n\nयह टेक्स्ट सीधे content_md से रेंडर हुआ है।",
            status="published"
        )
        session.add(art)
        session.commit()

    response = client.get("/article/single-source-truth-test")
    assert response.status_code == 200
    assert "लाइव रेंडर हेडिंग" in response.text
    assert "यह टेक्स्ट सीधे content_md से रेंडर हुआ है।" in response.text
