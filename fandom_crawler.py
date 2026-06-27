from __future__ import annotations

import argparse
import json
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, unquote, urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SKIP_PATH_PREFIXES = (
    "/wiki/Special:",
    "/wiki/File:",
    "/wiki/Category:",
    "/wiki/Template:",
    "/wiki/Help:",
    "/wiki/User:",
    "/wiki/Talk:",
    "/wiki/Forum:",
)


@dataclass(frozen=True)
class CrawlConfig:
    start_url: str
    output_dir: Path
    max_pages: int
    delay: float
    timeout: float
    user_agent: str


class FandomCrawler:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self.allowed_netloc = urlparse(config.start_url).netloc.lower()
        self.output_pages_dir = config.output_dir / "pages"
        self.seen: set[str] = set()
        self.index: list[dict[str, str]] = []

    def crawl(self) -> None:
        self.output_pages_dir.mkdir(parents=True, exist_ok=True)
        pages = self.crawl_pages()

        for page in pages:
            filename = safe_filename(page["title"] or page["url"]) + ".json"
            page_path = self.output_pages_dir / filename
            page_path.write_text(json.dumps(page, indent=2, ensure_ascii=False), encoding="utf-8")

            self.index.append(
                {
                    "title": page["title"],
                    "url": page["url"],
                    "file": str(page_path.relative_to(self.config.output_dir)),
                }
            )

        index_path = self.config.output_dir / "index.json"
        index_path.write_text(json.dumps(self.index, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Done. Saved {len(self.index)} pages to {self.config.output_dir}")

    def crawl_pages(self, on_status: Callable[[str], None] | None = None) -> list[dict]:
        queue: deque[str] = deque([normalize_url(self.config.start_url)])
        pages: list[dict] = []

        while queue and len(pages) < self.config.max_pages:
            url = queue.popleft()
            if url in self.seen or not self.is_allowed_page(url):
                continue

            message = f"[{len(pages) + 1}/{self.config.max_pages}] Fetching {url}"
            emit_status(message, on_status)
            self.seen.add(url)

            try:
                page = self.fetch_page(url)
            except requests.RequestException as exc:
                emit_status(f"  Request failed: {exc}", on_status)
                continue
            except ValueError as exc:
                emit_status(f"  Parse skipped: {exc}", on_status)
                continue

            pages.append(page)

            for link in page["links"]:
                if link not in self.seen and self.is_allowed_page(link):
                    queue.append(link)

            time.sleep(self.config.delay)

        return pages

    def fetch_page(self, url: str) -> dict:
        try:
            html = self.fetch(url)
            return parse_fandom_page(url, html)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code != 403:
                raise
            print("  Direct page blocked with 403; using MediaWiki API fallback")
            return self.fetch_page_from_api(url)

    def fetch(self, url: str) -> str:
        response = self.session.get(url, timeout=self.config.timeout)
        response.raise_for_status()
        if "text/html" not in response.headers.get("Content-Type", ""):
            raise ValueError("URL did not return HTML")
        return response.text

    def fetch_page_from_api(self, url: str) -> dict:
        parsed = urlparse(url)
        api_url = f"{parsed.scheme}://{parsed.netloc}/api.php"
        title = wiki_title_from_url(url)
        params = {
            "action": "parse",
            "page": title,
            "prop": "text|displaytitle|categories|images|links",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
        }
        response = self.session.get(api_url, params=params, timeout=self.config.timeout)
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            raise ValueError(payload["error"].get("info", "API returned an error"))

        parsed_page = payload["parse"]
        page_url = wiki_url_from_title(url, parsed_page.get("title", title))
        page = parse_fandom_page(
            page_url,
            parsed_page.get("text", ""),
            title_hint=strip_tags(parsed_page.get("displaytitle") or parsed_page.get("title") or title),
            categories_hint=api_categories(parsed_page),
        )
        api_links = [wiki_url_from_title(url, item["title"]) for item in parsed_page.get("links", []) if "title" in item]
        page["links"] = sorted(set(page["links"]) | set(api_links))
        return page

    def is_allowed_page(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.netloc.lower() != self.allowed_netloc:
            return False
        if not parsed.path.startswith("/wiki/"):
            return False
        return not any(parsed.path.startswith(prefix) for prefix in SKIP_PATH_PREFIXES)


def parse_fandom_page(
    url: str,
    html: str,
    title_hint: str = "",
    categories_hint: list[str] | None = None,
) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one(".mw-parser-output") or soup
    if content is None:
        raise ValueError("Could not find main wiki content")

    cleanup_content(content)

    title = title_hint or get_text(soup.select_one("h1.page-header__title") or soup.select_one("#firstHeading"))
    summary = extract_summary(content)
    sections = extract_sections(content)
    infobox = extract_infobox(content)
    categories = categories_hint or [get_text(node) for node in soup.select(".page-footer__categories a, .catlinks a")]
    images = extract_images(content, url)
    links = sorted(extract_links(content, url))

    return {
        "title": title,
        "url": normalize_url(url),
        "summary": summary,
        "infobox": infobox,
        "sections": sections,
        "categories": categories,
        "images": images,
        "links": links,
    }


def emit_status(message: str, on_status: Callable[[str], None] | None = None) -> None:
    if on_status is None:
        print(message)
        return
    on_status(message)


def cleanup_content(content: Tag) -> None:
    selectors = [
        "script",
        "style",
        ".reference",
        ".mw-editsection",
        ".portable-infobox .pi-edit-data",
        ".toc",
        ".noprint",
        ".mw-empty-elt",
    ]
    for node in content.select(",".join(selectors)):
        node.decompose()


def extract_summary(content: Tag) -> str:
    paragraphs = []
    for child in content.children:
        if isinstance(child, Tag) and child.name and re.fullmatch(r"h[2-6]", child.name):
            break
        if isinstance(child, Tag) and child.name == "p":
            text = get_text(child)
            if text:
                paragraphs.append(text)
    return "\n\n".join(paragraphs)


def extract_sections(content: Tag) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    buffers: list[str] = []

    for child in content.children:
        if not isinstance(child, Tag):
            continue
        if child.name and re.fullmatch(r"h[2-4]", child.name):
            if current is not None:
                current["text"] = "\n\n".join(buffers).strip()
                sections.append(current)
            current = {"heading": get_text(child), "text": ""}
            buffers = []
            continue
        if current is not None and child.name in {"p", "ul", "ol", "table"}:
            text = get_text(child)
            if text:
                buffers.append(text)

    if current is not None:
        current["text"] = "\n\n".join(buffers).strip()
        sections.append(current)
    return sections


def extract_infobox(content: Tag) -> dict[str, str]:
    infobox = content.select_one(".portable-infobox, aside.portable-infobox")
    if infobox is None:
        return {}

    data: dict[str, str] = {}
    for item in infobox.select(".pi-item"):
        label = get_text(item.select_one(".pi-data-label"))
        value = get_text(item.select_one(".pi-data-value"))
        if label and value:
            data[label] = value
    return data


def extract_images(content: Tag, base_url: str) -> list[dict[str, str]]:
    images = []
    for img in content.select("img"):
        src = img.get("data-src") or img.get("src")
        if not src:
            continue
        images.append(
            {
                "src": urljoin(base_url, src),
                "alt": img.get("alt", "").strip(),
            }
        )
    return dedupe_dicts(images, "src")


def extract_links(content: Tag, base_url: str) -> Iterable[str]:
    for anchor in content.select('a[href^="/wiki/"], a[href^="http"]'):
        href = anchor.get("href")
        if not href:
            continue
        yield normalize_url(urljoin(base_url, href))


def normalize_url(url: str) -> str:
    without_fragment, _ = urldefrag(url)
    parsed = urlparse(without_fragment)
    return parsed._replace(query="").geturl()


def wiki_title_from_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.path.startswith("/wiki/"):
        raise ValueError("URL must point to a /wiki/ page")
    return unquote(parsed.path.removeprefix("/wiki/")).replace("_", " ")


def wiki_url_from_title(base_url: str, title: str) -> str:
    parsed = urlparse(base_url)
    path_title = quote(title.replace(" ", "_"), safe="()':,!")
    return f"{parsed.scheme}://{parsed.netloc}/wiki/{path_title}"


def api_categories(parsed_page: dict) -> list[str]:
    categories = []
    for item in parsed_page.get("categories", []):
        category = item.get("category") or item.get("*")
        if category:
            categories.append(category)
    return categories


def strip_tags(value: str) -> str:
    return get_text(BeautifulSoup(value, "html.parser"))


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", value.strip(), flags=re.UNICODE)
    return cleaned.strip("._")[:120] or "page"


def get_text(node: Tag | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def dedupe_dicts(items: list[dict[str, str]], key: str) -> list[dict[str, str]]:
    seen = set()
    result = []
    for item in items:
        marker = item[key]
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def parse_args() -> CrawlConfig:
    parser = argparse.ArgumentParser(description="Crawl a game Fandom wiki into structured JSON files.")
    parser.add_argument("url", help="Starting Fandom wiki page, for example https://example.fandom.com/wiki/Page")
    parser.add_argument("-o", "--output", default="output", help="Directory where JSON files are saved")
    parser.add_argument("--max-pages", type=int, default=50, help="Maximum number of wiki pages to crawl")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between requests")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP request timeout in seconds")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="User-Agent sent with each request")
    args = parser.parse_args()

    return CrawlConfig(
        start_url=args.url,
        output_dir=Path(args.output),
        max_pages=args.max_pages,
        delay=args.delay,
        timeout=args.timeout,
        user_agent=args.user_agent,
    )


def main() -> None:
    config = parse_args()
    FandomCrawler(config).crawl()


if __name__ == "__main__":
    main()
