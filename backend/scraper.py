"""Small, deliberately conservative HTML-to-video manifest scraper.

The scraper handles public, server-rendered pages. It never executes page
JavaScript, logs in, or attempts to defeat DRM or access controls.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
from collections import deque
from dataclasses import dataclass
from html import unescape
from pathlib import PurePosixPath
from typing import Callable, Iterable
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_CRAWL_DEPTH = 5
MAX_CRAWL_PAGES = 500
DEFAULT_CRAWL_DEPTH = 3
DEFAULT_CRAWL_PAGES = 100
MAX_SCAN_SECONDS = 120
MAX_REDIRECTS = 5
MAX_URL_LENGTH = 2048
REQUEST_TIMEOUT = (5, 15)
REDIRECT_CODES = {301, 302, 303, 307, 308}
STREAM_EXTENSIONS = {".mp4": "mp4", ".m3u8": "hls"}
NON_PAGE_EXTENSIONS = {
    ".7z", ".avi", ".bmp", ".css", ".csv", ".doc", ".docx", ".gif", ".gz",
    ".ico", ".jpeg", ".jpg", ".js", ".json", ".m4a", ".m4v", ".mov", ".mp3",
    ".pdf", ".png", ".ppt", ".pptx", ".svg", ".tar", ".webp", ".woff", ".woff2",
    ".xls", ".xlsx", ".xml", ".zip",
}
IGNORED_PATH_SEGMENTS = {"delete", "logout", "remove", "signout", "unsubscribe"}


class ScrapeError(Exception):
    """Base class for expected scraping errors."""


class InvalidTargetError(ScrapeError):
    """The requested URL is malformed or points at a non-public address."""


class RemotePageError(ScrapeError):
    """The upstream page could not be fetched or was not HTML."""


@dataclass
class VideoItem:
    title: str
    thumb: str
    url: str
    stream_format: str
    attribution: str = ""
    page_url: str = ""

    def to_dict(self) -> dict[str, str]:
        """Return the wire format consumed by the Roku and Android clients."""
        item = {
            "title": self.title,
            "thumb": self.thumb,
            "url": self.url,
            "streamFormat": self.stream_format,
        }
        if self.attribution:
            item["attribution"] = self.attribution
        if self.page_url:
            item["pageUrl"] = self.page_url
        return item


@dataclass
class ScanResult:
    items: list[VideoItem]
    pages_scanned: int
    truncated: bool


def _clean_text(value: object, limit: int = 300) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", unescape(str(value))).strip()
    return text[:limit]


def _absolute_http_url(value: object, base_url: str) -> str:
    """Resolve a markup URL and discard non-web schemes and fragments."""
    if value is None:
        return ""
    raw = unescape(str(value)).strip()
    if not raw or raw.startswith(("#", "data:", "blob:", "javascript:", "mailto:")):
        return ""
    try:
        absolute = urljoin(base_url, raw)
        parsed = urlsplit(absolute)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return ""
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, ""))
    except (TypeError, ValueError):
        return ""


def _stream_format(url: str, mime_type: str | None = None) -> str | None:
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if "mpegurl" in mime or mime in {"application/m3u", "application/vnd.apple.mpegurl"}:
        return "hls"
    if mime in {"video/mp4", "application/mp4"}:
        return "mp4"
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return None
    for extension, stream_format in STREAM_EXTENSIONS.items():
        if path.endswith(extension):
            return stream_format
    return None


def _tag_url(tag: Tag, *names: str) -> str:
    for name in names:
        value = tag.get(name)
        if isinstance(value, list):
            value = " ".join(str(part) for part in value)
        if value:
            text = str(value).strip()
            if name == "srcset":
                # The first srcset candidate is a useful, conservative poster fallback.
                text = text.split(",", 1)[0].strip().split(" ", 1)[0]
            return text
    return ""


def _tag_title(tag: Tag) -> str:
    for name in ("title", "aria-label", "data-title", "data-name"):
        value = _clean_text(tag.get(name))
        if value:
            return value
    text = _clean_text(tag.get_text(" ", strip=True))
    if text:
        return text
    image = tag.find("img")
    if isinstance(image, Tag):
        return _clean_text(image.get("alt"))
    return ""


def _tag_thumbnail(tag: Tag, base_url: str) -> str:
    for candidate in (tag, *tag.find_all("img")):
        if not isinstance(candidate, Tag):
            continue
        raw = _tag_url(candidate, "poster", "data-poster", "data-src", "data-lazy-src", "src", "srcset")
        resolved = _absolute_http_url(raw, base_url)
        if resolved:
            return resolved
    return ""


def _filename_title(url: str) -> str:
    try:
        filename = PurePosixPath(unquote(urlsplit(url).path)).stem
    except ValueError:
        filename = ""
    title = re.sub(r"[_-]+", " ", filename).strip()
    return _clean_text(title) or "Untitled video"


def _page_origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return "", "", None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def _is_same_origin(first: str, second: str) -> bool:
    return _page_origin(first) == _page_origin(second) and bool(_page_origin(first)[1])


def _is_player_link(tag: Tag, absolute_url: str) -> bool:
    path = urlsplit(absolute_url).path.lower()
    path_cue = bool(re.search(r"(?:^|[/_-])(watch|videos?|player|play|episodes?)(?:[/_-]|$)", path))
    class_value = tag.get("class", [])
    if isinstance(class_value, str):
        class_value = class_value.split()
    cues = " ".join(
        [
            str(tag.get("id", "")),
            " ".join(str(value) for value in class_value),
            str(tag.get("title", "")),
            str(tag.get("aria-label", "")),
            _clean_text(tag.get_text(" ", strip=True), limit=150),
        ]
    ).lower()
    return path_cue or bool(re.search(r"\b(watch|video|player|play|episode)\b", cues))


def _is_crawlable_page_url(url: str) -> bool:
    if _stream_format(url):
        return False
    try:
        path = unquote(urlsplit(url).path).lower()
    except ValueError:
        return False
    suffix = PurePosixPath(path).suffix
    if suffix in NON_PAGE_EXTENSIONS:
        return False
    segments = {segment for segment in path.split("/") if segment}
    return not bool(segments & IGNORED_PATH_SEGMENTS)


def _player_link_hints(html: str, page_url: str) -> dict[str, tuple[str, str]]:
    """Collect title/poster hints attached to same-origin player links."""
    soup = BeautifulSoup(html, "html.parser")
    base_url = page_url
    base_tag = soup.find("base", href=True)
    if isinstance(base_tag, Tag):
        candidate_base = _absolute_http_url(base_tag.get("href"), page_url)
        if candidate_base:
            base_url = candidate_base
    hints: dict[str, tuple[str, str]] = {}
    for tag in soup.find_all(["a", "iframe"]):
        raw_url = tag.get("href") if tag.name == "a" else tag.get("src")
        absolute = _absolute_http_url(raw_url, base_url)
        if not absolute or not _is_same_origin(absolute, page_url):
            continue
        if tag.name == "a" and not _is_player_link(tag, absolute):
            continue
        title = _tag_title(tag)
        thumb = _tag_thumbnail(tag, base_url)
        if title or thumb:
            hints[absolute] = (title, thumb)
    return hints


def _jsonld_thumbnail(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for part in value:
            found = _jsonld_thumbnail(part)
            if found:
                return found
    if isinstance(value, dict):
        return str(value.get("url") or value.get("contentUrl") or "")
    return ""


def extract_page(html: str, page_url: str) -> tuple[list[VideoItem], list[str]]:
    """Extract direct MP4/HLS URLs and same-origin crawlable page links from HTML.

    This function is intentionally independent of network I/O so it can be
    tested with fixtures and embedded in other middleware if needed.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_url = page_url
    base_tag = soup.find("base", href=True)
    if isinstance(base_tag, Tag):
        candidate_base = _absolute_http_url(base_tag.get("href"), page_url)
        if candidate_base:
            base_url = candidate_base

    page_title = ""
    page_thumb = ""
    og_video_mime = ""
    for meta in soup.find_all("meta"):
        key = str(meta.get("property") or meta.get("name") or "").lower()
        raw_content = meta.get("content")
        content = _clean_text(raw_content)
        if key in {"og:title", "twitter:title"} and content and not page_title:
            page_title = content
        if key in {"og:image", "og:image:url", "twitter:image"} and raw_content and not page_thumb:
            page_thumb = _absolute_http_url(raw_content, base_url)
        if key in {"og:video:type", "twitter:player:stream:type"} and content and not og_video_mime:
            og_video_mime = content
    if not page_title:
        title_tag = soup.find("title") or soup.find("h1")
        if isinstance(title_tag, Tag):
            page_title = _clean_text(title_tag.get_text(" ", strip=True))

    items_by_url: dict[str, tuple[VideoItem, int]] = {}
    player_links: list[str] = []
    player_link_set: set[str] = set()

    def add_page_link(raw_url: object) -> None:
        absolute = _absolute_http_url(raw_url, base_url)
        if (
            absolute
            and _is_same_origin(absolute, page_url)
            and absolute != page_url
            and _is_crawlable_page_url(absolute)
            and absolute not in player_link_set
        ):
            player_link_set.add(absolute)
            player_links.append(absolute)

    def add_video(
        raw_url: object,
        title: str = "",
        thumb: str = "",
        mime_type: str | None = None,
    ) -> None:
        absolute = _absolute_http_url(raw_url, base_url)
        if not absolute:
            return
        stream_format = _stream_format(absolute, mime_type)
        if not stream_format:
            return
        explicit_title = _clean_text(title)
        chosen_title = explicit_title or page_title or _filename_title(absolute)
        title_rank = 2 if explicit_title else (1 if page_title else 0)
        resolved_thumb = _absolute_http_url(thumb, base_url) if thumb else ""
        if not resolved_thumb:
            resolved_thumb = page_thumb
        existing = items_by_url.get(absolute)
        if existing:
            item, previous_rank = existing
            if title_rank > previous_rank:
                item.title = chosen_title
                previous_rank = title_rank
            if not item.thumb and resolved_thumb:
                item.thumb = resolved_thumb
            items_by_url[absolute] = (item, previous_rank)
            return
        items_by_url[absolute] = (
            VideoItem(
                title=chosen_title,
                thumb=resolved_thumb,
                url=absolute,
                stream_format=stream_format,
            ),
            title_rank,
        )

    # Open Graph, Twitter player metadata, and common HTML metadata.
    for meta in soup.find_all("meta"):
        key = str(meta.get("property") or meta.get("name") or "").lower()
        if key in {"og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"}:
            add_video(
                meta.get("content"),
                title=page_title,
                thumb=page_thumb,
                mime_type=str(meta.get("type") or og_video_mime),
            )

    # JSON-LD is common on publisher pages. embedUrl is crawled only when it is
    # a same-origin page; contentUrl must resolve to an MP4/HLS stream.
    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        stack: list[object] = [payload]
        while stack:
            current = stack.pop()
            if isinstance(current, list):
                stack.extend(current)
                continue
            if not isinstance(current, dict):
                continue
            stack.extend(value for value in current.values() if isinstance(value, (dict, list)))
            raw_type = current.get("@type", [])
            if isinstance(raw_type, str):
                types = [raw_type]
            elif isinstance(raw_type, list):
                types = raw_type
            else:
                types = []
            if not any(str(value).lower() == "videoobject" for value in types):
                continue
            title = _clean_text(current.get("name") or current.get("headline")) or page_title
            thumb = _jsonld_thumbnail(current.get("thumbnailUrl")) or page_thumb
            for media_url in (current.get("contentUrl"), current.get("url")):
                if isinstance(media_url, list):
                    for candidate in media_url:
                        add_video(candidate, title=title, thumb=thumb)
                else:
                    add_video(media_url, title=title, thumb=thumb)
            embed_url = current.get("embedUrl")
            if isinstance(embed_url, list):
                for candidate in embed_url:
                    add_video(candidate, title=title, thumb=thumb)
                    add_page_link(candidate)
            elif embed_url:
                add_video(embed_url, title=title, thumb=thumb)
                add_page_link(embed_url)

    # Native HTML video elements and source children.
    for video in soup.find_all("video"):
        title = _tag_title(video) or page_title
        thumb = _tag_thumbnail(video, base_url) or page_thumb
        add_video(video.get("src"), title=title, thumb=thumb, mime_type=video.get("type"))
        for source in video.find_all("source"):
            add_video(
                source.get("src") or source.get("data-src"),
                title=_tag_title(source) or title,
                thumb=thumb,
                mime_type=source.get("type"),
            )
        for attribute in ("data-video-url", "data-stream-url", "data-hls", "data-src"):
            add_video(video.get(attribute), title=title, thumb=thumb)

    # Also catch source elements outside a <video>, as used by some player shells.
    for source in soup.find_all("source"):
        add_video(
            source.get("src") or source.get("data-src"),
            title=_tag_title(source) or page_title,
            thumb=_tag_thumbnail(source, base_url) or page_thumb,
            mime_type=source.get("type"),
        )

    # Common lazy-player attributes. Only direct HTTP(S) MP4/HLS endpoints pass
    # add_video's format check, so unrelated data-src attributes are ignored.
    for tag in soup.find_all(True):
        if tag.name in {"video", "source"}:
            continue
        title = _tag_title(tag) or page_title
        thumb = _tag_thumbnail(tag, base_url) or page_thumb
        for attribute in ("data-video-url", "data-stream-url", "data-video", "data-hls", "data-src"):
            add_video(tag.get(attribute), title=title, thumb=thumb)

    # Direct media links become items; same-origin HTML links feed the bounded crawl.
    for anchor in soup.find_all("a", href=True):
        absolute = _absolute_http_url(anchor.get("href"), base_url)
        if not absolute:
            continue
        title = _tag_title(anchor) or page_title
        thumb = _tag_thumbnail(anchor, base_url) or page_thumb
        add_video(absolute, title=title, thumb=thumb)
        add_page_link(absolute)

    # Same-origin iframes often host the actual player page. External iframes are
    # deliberately not fetched, to keep the crawler bounded and SSRF surface small.
    for iframe in soup.find_all("iframe", src=True):
        absolute = _absolute_http_url(iframe.get("src"), base_url)
        add_video(absolute, title=_tag_title(iframe) or page_title, thumb=_tag_thumbnail(iframe, base_url) or page_thumb)
        add_page_link(absolute)

    return [item for item, _rank in items_by_url.values()], player_links


def _default_resolver(host: str, port: int) -> list[str]:
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(answer[4][0] for answer in answers))


class SiteScraper:
    """Bounded same-origin crawler for public, server-rendered websites."""

    def __init__(
        self,
        *,
        resolver: Callable[[str, int], Iterable[str]] = _default_resolver,
        session: requests.Session | None = None,
        max_html_bytes: int = MAX_HTML_BYTES,
        allowed_hosts: Iterable[str] | None = None,
    ) -> None:
        self._resolver = resolver
        self._max_html_bytes = max_html_bytes
        if allowed_hosts is None:
            allowed_hosts = os.getenv("TRILLROKU_ALLOWED_HOSTS", "").split(",")
        self._allowed_hosts = {
            str(host).strip().lower().rstrip(".")
            for host in allowed_hosts
            if str(host).strip()
        }
        self.session = session or requests.Session()
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False
        if hasattr(self.session, "headers"):
            self.session.headers.update(
                {
                    "User-Agent": "TrillRokuScraper/1.0 (+public-page video manifest)",
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                }
            )

    def _validate_public_url(self, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_URL_LENGTH:
            raise InvalidTargetError("Provide a valid public HTTP or HTTPS URL.")
        value = value.strip()
        if any(ord(char) < 32 for char in value):
            raise InvalidTargetError("The URL contains unsupported control characters.")
        try:
            parsed = urlsplit(value)
            scheme = parsed.scheme.lower()
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise InvalidTargetError("The URL is malformed.") from exc
        if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            raise InvalidTargetError("Only public HTTP(S) URLs without embedded credentials are allowed.")
        if self._allowed_hosts and host.lower().rstrip(".") not in self._allowed_hosts:
            raise InvalidTargetError("The target host is not in TRILLROKU_ALLOWED_HOSTS.")
        if port is not None and port != (443 if scheme == "https" else 80):
            raise InvalidTargetError("Only the standard HTTP and HTTPS ports are allowed.")

        resolved_port = 443 if scheme == "https" else 80
        try:
            addresses = list(self._resolver(host, resolved_port))
        except (OSError, socket.gaierror, ValueError) as exc:
            raise InvalidTargetError("The target host could not be resolved.") from exc
        if not addresses:
            raise InvalidTargetError("The target host could not be resolved.")
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise InvalidTargetError("The target host resolved to an invalid address.") from exc
            if not ip.is_global:
                raise InvalidTargetError("Private, loopback, and reserved network targets are not allowed.")

        path = parsed.path or "/"
        return urlunsplit((scheme, parsed.netloc, path, parsed.query, ""))

    def _fetch_html(self, value: str) -> tuple[str, str]:
        current_url = value
        seen: set[str] = set()
        for redirect_count in range(MAX_REDIRECTS + 1):
            safe_url = self._validate_public_url(current_url)
            if safe_url in seen:
                raise RemotePageError("The source returned a redirect loop.")
            seen.add(safe_url)
            response = None
            try:
                response = self.session.get(
                    safe_url,
                    timeout=REQUEST_TIMEOUT,
                    stream=True,
                    allow_redirects=False,
                )
                status = int(response.status_code)
                if status in REDIRECT_CODES:
                    location = response.headers.get("Location")
                    if not location:
                        raise RemotePageError("The source returned an invalid redirect.")
                    if redirect_count >= MAX_REDIRECTS:
                        raise RemotePageError("The source exceeded the redirect limit.")
                    current_url = urljoin(safe_url, location)
                    continue
                if status < 200 or status >= 300:
                    raise RemotePageError(f"The source returned HTTP {status}.")

                content_type = str(response.headers.get("Content-Type", "")).lower()
                if content_type and not any(
                    kind in content_type for kind in ("text/html", "application/xhtml+xml", "text/plain")
                ):
                    raise RemotePageError("The URL did not return an HTML page.")
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > self._max_html_bytes:
                            raise RemotePageError("The HTML page is larger than the configured size limit.")
                    except ValueError:
                        pass

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self._max_html_bytes:
                        raise RemotePageError("The HTML page is larger than the configured size limit.")
                    chunks.append(chunk)
                encoding = getattr(response, "encoding", None) or "utf-8"
                try:
                    html = b"".join(chunks).decode(encoding, errors="replace")
                except LookupError:
                    html = b"".join(chunks).decode("utf-8", errors="replace")
                return html, safe_url
            except requests.RequestException as exc:
                raise RemotePageError("The source could not be fetched.") from exc
            finally:
                if response is not None:
                    response.close()
        raise RemotePageError("The source exceeded the redirect limit.")

    def scan(
        self,
        url: str,
        *,
        max_depth: int = DEFAULT_CRAWL_DEPTH,
        max_pages: int = DEFAULT_CRAWL_PAGES,
    ) -> ScanResult:
        """Crawl same-origin links breadth-first and collect direct MP4/HLS items.

        The crawl is deliberately bounded. It explores ordinary same-origin
        anchors and iframes, not just links whose labels look like a video.
        """
        if isinstance(max_depth, bool) or not isinstance(max_depth, int):
            raise InvalidTargetError("maxDepth must be an integer.")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int):
            raise InvalidTargetError("maxPages must be an integer.")
        max_depth = min(max(max_depth, 0), MAX_CRAWL_DEPTH)
        max_pages = min(max(max_pages, 1), MAX_CRAWL_PAGES)

        safe_url = self._validate_public_url(url)
        direct_format = _stream_format(safe_url)
        if direct_format:
            return ScanResult(
                items=[
                    VideoItem(
                        title=_filename_title(safe_url),
                        thumb="",
                        url=safe_url,
                        stream_format=direct_format,
                    )
                ],
                pages_scanned=0,
                truncated=False,
            )

        queue = deque([(safe_url, 0)])
        queued = {safe_url}
        visited: set[str] = set()
        root_origin: str | None = None
        items_by_url: dict[str, VideoItem] = {}
        link_hints: dict[str, tuple[str, str]] = {}
        pages_scanned = 0
        truncated_by_depth = False
        deadline = time.monotonic() + MAX_SCAN_SECONDS

        while queue and pages_scanned < max_pages and time.monotonic() < deadline:
            page_url, depth = queue.popleft()
            if page_url in visited:
                continue
            visited.add(page_url)
            pages_scanned += 1
            try:
                safe_page_url = self._validate_public_url(page_url)
                page_html, resolved_url = self._fetch_html(safe_page_url)
            except ScrapeError:
                if pages_scanned == 1:
                    raise
                continue

            if root_origin is None:
                root_origin = resolved_url
            if not _is_same_origin(resolved_url, root_origin):
                continue
            visited.add(resolved_url)

            page_items, page_links = extract_page(page_html, resolved_url)
            page_hints = _player_link_hints(page_html, resolved_url)
            for link, hint in page_hints.items():
                link_hints.setdefault(link, hint)
            hint_title, hint_thumb = link_hints.get(page_url, ("", ""))
            if not hint_title and not hint_thumb:
                hint_title, hint_thumb = link_hints.get(resolved_url, ("", ""))

            for item in page_items:
                if hint_title and item.title in {"Untitled video", _filename_title(item.url)}:
                    item.title = hint_title
                if hint_thumb and not item.thumb:
                    item.thumb = hint_thumb
                previous = items_by_url.get(item.url)
                if previous is None:
                    items_by_url[item.url] = item
                else:
                    if previous.title in {"Untitled video", _filename_title(previous.url)} and item.title:
                        previous.title = item.title
                    if not previous.thumb and item.thumb:
                        previous.thumb = item.thumb

            if depth >= max_depth:
                if any(
                    _is_same_origin(next_url, root_origin)
                    and next_url not in visited
                    and next_url not in queued
                    for next_url in page_links
                ):
                    truncated_by_depth = True
                continue
            for next_url in page_links:
                if not _is_same_origin(next_url, root_origin):
                    continue
                if next_url not in visited and next_url not in queued:
                    queue.append((next_url, depth + 1))
                    queued.add(next_url)

        return ScanResult(
            items=list(items_by_url.values()),
            pages_scanned=pages_scanned,
            truncated=bool(queue) or truncated_by_depth,
        )

    def scrape(self, url: str) -> list[VideoItem]:
        """Compatibility helper returning only items from the default recursive scan."""
        return self.scan(url).items
