import functools
import re
import unicodedata
from typing import Tuple
import markdown
import nh3

ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "code", "dd", "del", "div", "dl", "dt",
    "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "kbd",
    "li", "ol", "p", "pre", "s", "span", "strong", "sub", "sup",
    "table", "tbody", "td", "th", "thead", "tr", "ul", "section"
}

ALLOWED_ATTRIBUTES = {
    "*": {"class", "id", "aria-label", "role"},
    "a": {"href", "title", "target"},
}


def hindi_slugify(value: str, separator: str = "-") -> str:
    """
    Generates a URL/ID slug preserving Devanagari letters, numbers, and combining marks (matras, virama).
    - NFC-normalise
    - Lowercase
    - Keep characters whose Unicode category starts with L, M, or N
    - Turn spaces, hyphens, and underscores into a single '-'
    - Strip leading and trailing '-'
    - Fall back to 'section' if empty
    """
    value = unicodedata.normalize("NFC", value).lower()
    chars = []
    for c in value:
        cat = unicodedata.category(c)
        if cat and cat[0] in ("L", "M", "N"):
            chars.append(c)
        elif c in (" ", "-", "_"):
            chars.append(separator)
    text = "".join(chars)
    sep_pattern = re.escape(separator) + "+"
    text = re.sub(sep_pattern, separator, text)
    text = text.strip(separator)
    return text if text else "section"


@functools.lru_cache(maxsize=256)
def render_markdown_with_toc(md_text: str) -> Tuple[str, str]:
    """
    Renders raw Markdown into sanitized HTML and extracts Table of Contents.
    Returns an immutable tuple: (sanitized_html, toc_html)
    """
    if not md_text:
        return "", ""

    md = markdown.Markdown(
        extensions=[
            "extra",
            "toc",
            "nl2br",
            "sane_lists",
            "tables"
        ],
        extension_configs={
            "toc": {
                "title": "विषय सूची",
                "toc_depth": "2-3",
                "permalink": False,
                "slugify": hindi_slugify
            }
        }
    )

    raw_html = md.convert(md_text)
    raw_toc = getattr(md, "toc", "")

    # Sanitize content HTML with automatic safe link_rel
    clean_html = nh3.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
        url_schemes={"http", "https", "mailto"}
    )

    # Wrap every <table>...</table> in <div class="table-scroll">...</div>
    clean_html = re.sub(r'(<table\b.*?</table>)', r'<div class="table-scroll">\1</div>', clean_html, flags=re.DOTALL)

    # If the rendered TOC contains no <li, return "" for toc_html
    if not raw_toc or "<li" not in raw_toc:
        clean_toc = ""
    else:
        clean_toc = nh3.clean(
            raw_toc,
            tags={"div", "span", "ul", "li", "a", "p"},
            attributes={"*": {"class", "id"}, "a": {"href", "title"}},
            link_rel=None,
            url_schemes={"http", "https"}
        )
        if "<li" not in clean_toc:
            clean_toc = ""

    return clean_html, clean_toc
