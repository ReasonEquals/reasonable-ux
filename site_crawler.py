"""
site_crawler.py — fast internal-link discovery with requests fallback to Playwright.
Returns a sorted list of same-domain path strings.
"""
import asyncio
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin


def _filter_links(base_url: str, html: str) -> list:
    """Parse html, extract same-domain paths, return sorted deduplicated list."""
    base_domain = urlparse(base_url).netloc
    soup = BeautifulSoup(html, "html.parser")
    paths = {"/"}
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        parsed = urlparse(urljoin(base_url, href))
        if parsed.netloc != base_domain:
            continue
        paths.add(parsed.path or "/")
    return sorted(paths)


def _crawl_with_requests(base_url: str):
    """
    Fetch base_url with requests and extract same-domain paths.
    Returns a sorted list of path strings on success.
    Returns None if the request fails or returns 4xx/5xx — signals "blocked",
    not "no links found", so the caller can fall back.
    """
    try:
        resp = requests.get(
            base_url, timeout=10,
            headers={"User-Agent": "reasonable-ux-crawler/1.0"},
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    return _filter_links(base_url, resp.text)


async def _crawl_with_playwright(base_url: str) -> list:
    """
    Navigate to base_url with headless Chromium, extract same-domain paths.
    Returns a sorted list of path strings, or [] on failure.
    """
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(base_url, wait_until="load", timeout=20000)
            html = await page.content()
            await browser.close()
        return _filter_links(base_url, html)
    except Exception as e:
        print(f"⚠️  Playwright crawler failed: {e}")
        return []


def crawl(base_url: str) -> list:
    """
    Fetch base_url, extract all <a href> links, filter to same-domain paths,
    deduplicate, and return a sorted list of path strings (e.g. ["/", "/about"]).

    Tries requests first (fast, cheap). Falls back to headless Playwright if
    the site blocks the requests User-Agent (returns None from _crawl_with_requests).
    """
    result = _crawl_with_requests(base_url)
    if result is None:
        print("⚠️  requests blocked — falling back to Playwright crawler")
        result = asyncio.run(_crawl_with_playwright(base_url))
    return result
