"""Parse inert Open Graph and Twitter Card metadata from bounded HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


@dataclass(frozen=True)
class PreviewMetadata:
    title: str
    description: str
    image_url: str
    site: str


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.properties: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.in_title = False
        self.ignored_depth = 0
        self.base_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag in ("script", "style"):
            self.ignored_depth += 1
        elif tag == "title":
            self.in_title = True
        elif tag == "base" and not self.base_href:
            self.base_href = values.get("href", "").strip()
        elif tag == "meta":
            content = clean_text(values.get("content", ""))
            prop = values.get("property", "").strip().lower()
            name = values.get("name", "").strip().lower()
            if content and prop and prop not in self.properties:
                self.properties[prop] = content
            if content and name and name not in self.names:
                self.names[name] = content

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("script", "style") and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title and not self.ignored_depth:
            self.title_parts.append(data)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_metadata(body: bytes, content_type: str, page_url: str) -> PreviewMetadata:
    charset_match = re.search(r"charset\s*=\s*['\"]?([^\s;'\"]+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        html = body.decode(charset, errors="replace")
    except LookupError:
        html = body.decode("utf-8", errors="replace")

    parser = MetadataParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    title = clean_text(
        parser.properties.get("og:title", "")
        or parser.names.get("twitter:title", "")
        or " ".join(parser.title_parts)
    )[:300]
    description = clean_text(
        parser.properties.get("og:description", "")
        or parser.names.get("twitter:description", "")
        or parser.names.get("description", "")
    )[:1000]
    image = clean_text(
        parser.properties.get("og:image:secure_url", "")
        or parser.properties.get("og:image", "")
        or parser.names.get("twitter:image", "")
        or parser.names.get("twitter:image:src", "")
    )
    base_url = urljoin(page_url, parser.base_href) if parser.base_href else page_url
    image_url = urljoin(base_url, image) if image else ""
    if image_url and urlsplit(image_url).scheme.lower() not in ("http", "https"):
        image_url = ""

    return PreviewMetadata(
        title=title,
        description=description,
        image_url=image_url,
        site=clean_text(parser.properties.get("og:site_name", "") or (urlsplit(page_url).hostname or ""))[:200],
    )
