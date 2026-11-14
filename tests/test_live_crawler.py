"""Comprehensive live crawler test across the Anime Hindi web platform.

Visits every primary page and pagination target:
- / (Home)
- /schedule, /schedule?page=2
- /manga, /manga?page=2
- /news, /news?page=2
- /awards, /awards?page=2
- /trailers, /trailers?page=2

Extracts every unique internal link and detail link:
- /updates/...
- /news/...
- /anime/...
- /article/...
- /category/...
- /explore...

Asserts all return HTTP 200 OK (0 non-200 status codes).
"""
import re
import pytest
from html.parser import HTMLParser
from fastapi.testclient import TestClient
from app.main import app


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            href = d.get("href", "").strip()
            if href and href.startswith("/") and not href.startswith("//"):
                # Exclude administrative actions, logout, auth modals, static files
                if any(href.startswith(ex) for ex in [
                    "/static", "/admin", "/auth", "/logout", "/media", "/api"
                ]):
                    return
                # Exclude anchor jumps
                clean_href = href.split("#")[0].strip()
                if clean_href:
                    self.links.add(clean_href)


def test_deep_site_crawl():
    client = TestClient(app, follow_redirects=True)

    seed_urls = [
        "/",
        "/schedule",
        "/schedule?page=2",
        "/manga",
        "/manga?page=2",
        "/news",
        "/news?page=2",
        "/awards",
        "/awards?page=2",
        "/trailers",
        "/trailers?page=2",
    ]

    all_links_to_crawl = set(seed_urls)
    visited = set()
    results = {}
    non_200 = {}

    # Step 1: Crawl seeds and collect all embedded detail links
    for seed in seed_urls:
        res = client.get(seed)
        visited.add(seed)
        results[seed] = res.status_code
        if res.status_code != 200:
            non_200[seed] = res.status_code

        parser = LinkExtractor()
        parser.feed(res.text)
        for link in parser.links:
            all_links_to_crawl.add(link)

    # Step 2: Crawl every extracted link
    for link in sorted(all_links_to_crawl):
        if link in visited:
            continue
        visited.add(link)
        res = client.get(link)
        results[link] = res.status_code
        if res.status_code != 200:
            non_200[link] = res.status_code

    total_tested = len(visited)
    print("\n" + "=" * 70)
    print(f"CRAWLER PROOF OUTPUT: {total_tested} TOTAL UNIQUE LINKS TESTED")
    print("=" * 70)
    for link, code in sorted(results.items()):
        status_symbol = "✓" if code == 200 else "✗"
        print(f"[{status_symbol}] HTTP {code} : {link}")

    print("-" * 70)
    print(f"Total links tested : {total_tested}")
    print(f"HTTP 200 OK links  : {total_tested - len(non_200)}")
    print(f"Non-200 links      : {len(non_200)}")
    if non_200:
        print("FAILURES:")
        for l, c in non_200.items():
            print(f"  {l} -> HTTP {c}")
    print("=" * 70 + "\n")

    assert len(non_200) == 0, f"Expected 0 non-200 links, but found {len(non_200)}: {non_200}"


if __name__ == "__main__":
    test_deep_site_crawl()
