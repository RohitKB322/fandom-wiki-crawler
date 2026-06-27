from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import streamlit as st

from fandom_crawler import CrawlConfig, DEFAULT_USER_AGENT, FandomCrawler, safe_filename


st.set_page_config(page_title="Fandom Wiki Crawler", page_icon=":mag:", layout="wide")


def main() -> None:
    st.title("Fandom Wiki Crawler")
    st.caption("Paste a game Fandom wiki page, crawl related pages, and download the result.")

    with st.form("crawler_form"):
        url = st.text_input(
            "Fandom page link",
            placeholder="Paste your link here",
        )

        left, middle, right = st.columns(3)
        with left:
            max_pages = st.number_input("Pages to fetch", min_value=1, max_value=500, value=25, step=1)
        with middle:
            delay = st.number_input("Delay between pages", min_value=0.0, max_value=10.0, value=1.0, step=0.5)
        with right:
            output_format = st.radio("Download format", ["JSON", "Paragraphs"], horizontal=True)

        include_links = st.checkbox("Include source links in paragraph output", value=True)
        started = st.form_submit_button("Start crawling", type="primary", use_container_width=True)

    if not started:
        st.info("Start with a small page count, then increase it when the preview looks right.")
        return

    if not url.strip():
        st.error("Paste a Fandom wiki page link first.")
        return

    pages = run_crawler(url.strip(), int(max_pages), float(delay))
    if not pages:
        st.warning("No pages were saved. Try a different Fandom article URL.")
        return

    st.success(f"Fetched {len(pages)} page(s).")
    show_preview(pages)

    if output_format == "JSON":
        data = build_json_zip(pages)
        st.download_button(
            "Download JSON ZIP",
            data=data,
            file_name="fandom_pages_json.zip",
            mime="application/zip",
            use_container_width=True,
        )
    else:
        text = pages_to_text(pages, include_links=include_links)
        st.download_button(
            "Download paragraphs TXT",
            data=text,
            file_name="fandom_pages.txt",
            mime="text/plain",
            use_container_width=True,
        )


def run_crawler(url: str, max_pages: int, delay: float) -> list[dict]:
    status_box = st.empty()
    progress = st.progress(0)
    messages: list[str] = []

    def on_status(message: str) -> None:
        messages.append(message)
        progress.progress(min(len([m for m in messages if m.startswith("[")]) / max_pages, 1.0))
        status_box.code("\n".join(messages[-8:]), language="text")

    config = CrawlConfig(
        start_url=url,
        output_dir=Path("streamlit_output"),
        max_pages=max_pages,
        delay=delay,
        timeout=20.0,
        user_agent=DEFAULT_USER_AGENT,
    )
    crawler = FandomCrawler(config)
    with st.spinner("Fetching wiki pages..."):
        pages = crawler.crawl_pages(on_status=on_status)

    progress.progress(1.0)
    return pages


def show_preview(pages: list[dict]) -> None:
    first = pages[0]
    st.subheader("Preview")
    st.write(f"**{first.get('title', 'Untitled')}**")
    if first.get("summary"):
        st.write(first["summary"])

    with st.expander("Fetched pages"):
        for page in pages:
            st.write(f"- [{page.get('title')}]({page.get('url')})")


def build_json_zip(pages: list[dict]) -> bytes:
    buffer = io.BytesIO()
    index = []
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for page in pages:
            filename = f"pages/{safe_filename(page.get('title') or page.get('url') or 'page')}.json"
            archive.writestr(filename, json.dumps(page, indent=2, ensure_ascii=False))
            index.append({"title": page.get("title"), "url": page.get("url"), "file": filename})
        archive.writestr("index.json", json.dumps(index, indent=2, ensure_ascii=False))
    return buffer.getvalue()


def pages_to_text(pages: list[dict], include_links: bool) -> str:
    chunks = []
    for page in pages:
        chunks.append(page_to_text(page, include_links=include_links))
    return ("\n\n" + "=" * 80 + "\n\n").join(chunks)


def page_to_text(page: dict, include_links: bool) -> str:
    lines = [page.get("title") or "Untitled", ""]
    if include_links and page.get("url"):
        lines.extend([f"Source: {page['url']}", ""])

    if page.get("summary"):
        lines.extend([page["summary"], ""])

    if page.get("infobox"):
        lines.append("Facts")
        for key, value in page["infobox"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")

    for section in page.get("sections", []):
        heading = section.get("heading", "").strip()
        text = section.get("text", "").strip()
        if not heading or not text:
            continue
        lines.extend([heading, text, ""])

    categories = page.get("categories") or []
    if categories:
        lines.extend(["Categories", ", ".join(categories), ""])

    return "\n".join(lines).strip()


if __name__ == "__main__":
    main()
