from __future__ import annotations

import pytest

from backend.app import create_app
from backend.scan_store import ScanStore
from backend.scraper import InvalidTargetError, ScanResult, SiteScraper, VideoItem, extract_page


PUBLIC_RESOLVER = lambda _host, _port: ["93.184.216.34"]


def test_extracts_video_sources_direct_links_and_metadata():
    html = """
    <html>
      <head><title>Catalog Page</title><meta property="og:image" content="/images/site.jpg"></head>
      <body>
        <video title="Finale" poster="/images/finale.jpg">
          <source src="/streams/finale/master.m3u8?token=abc" type="application/vnd.apple.mpegurl">
          <source src="/streams/finale.mp4" type="video/mp4">
        </video>
        <a href="/clips/preview.mp4"><img src="/images/preview.jpg" alt="Preview clip"></a>
      </body>
    </html>
    """

    items, player_links = extract_page(html, "https://media.example.test/catalog")

    assert player_links == []
    assert [item.url for item in items] == [
        "https://media.example.test/streams/finale/master.m3u8?token=abc",
        "https://media.example.test/streams/finale.mp4",
        "https://media.example.test/clips/preview.mp4",
    ]
    assert [item.stream_format for item in items] == ["hls", "mp4", "mp4"]
    assert items[0].title == "Finale"
    assert items[0].thumb == "https://media.example.test/images/finale.jpg"
    assert items[2].title == "Preview clip"
    assert items[2].thumb == "https://media.example.test/images/preview.jpg"


def test_uses_open_graph_mime_for_extensionless_streams():
    html = """
    <meta property="og:video" content="/media/play?id=42">
    <meta property="og:video:type" content="video/mp4">
    <meta property="og:title" content="Mime-tagged clip">
    """

    items, _links = extract_page(html, "https://media.example.test/watch")

    assert len(items) == 1
    assert items[0].url == "https://media.example.test/media/play?id=42"
    assert items[0].stream_format == "mp4"
    assert items[0].title == "Mime-tagged clip"


def test_extracts_json_ld_video_and_same_origin_player_links():
    html = """
    <html><head><title>Document title</title></head><body>
      <script type="application/ld+json">
        {"@type":"VideoObject","name":"Documentary","contentUrl":"/media/doc.mp4",
         "thumbnailUrl":"/img/doc.jpg","embedUrl":"/player/17"}
      </script>
      <a class="video-card" href="/watch/18">Watch episode</a>
      <iframe src="https://player.other.test/embed/19"></iframe>
    </body></html>
    """

    items, player_links = extract_page(html, "https://media.example.test/shows")

    assert len(items) == 1
    assert items[0].title == "Documentary"
    assert items[0].url == "https://media.example.test/media/doc.mp4"
    assert items[0].thumb == "https://media.example.test/img/doc.jpg"
    assert player_links == [
        "https://media.example.test/player/17",
        "https://media.example.test/watch/18",
    ]
    # External iframe pages are not crawled.


def test_scrape_follows_limited_same_origin_player_page(monkeypatch):
    source = "https://media.example.test/library"
    player = "https://media.example.test/watch/episode-1"
    pages = {
        source: "<a class='video-card' href='/watch/episode-1'>Episode One</a>",
        player: "<video poster='/covers/one.jpg'><source src='https://cdn.example.test/one.m3u8' type='application/x-mpegURL'></video>",
    }
    scraper = SiteScraper(resolver=PUBLIC_RESOLVER)
    monkeypatch.setattr(scraper, "_fetch_html", lambda url: (pages[url], url))

    items = scraper.scrape(source)

    assert len(items) == 1
    assert items[0].title == "Episode One"
    assert items[0].thumb == "https://media.example.test/covers/one.jpg"
    assert items[0].url == "https://cdn.example.test/one.m3u8"
    assert items[0].stream_format == "hls"


def test_recursively_finds_videos_on_unlabeled_same_origin_pages(monkeypatch):
    root = "https://media.example.test/library"
    category = "https://media.example.test/category"
    deep_page = "https://media.example.test/series/one"
    pages = {
        root: "<a href='/category'>Browse catalog</a>",
        category: "<a href='/series/one'>Series details</a>",
        deep_page: "<video src='/assets/episode.mp4' poster='/covers/episode.jpg'></video>",
    }
    scraper = SiteScraper(resolver=PUBLIC_RESOLVER)
    monkeypatch.setattr(scraper, "_fetch_html", lambda url: (pages[url], url))

    result = scraper.scan(root, max_depth=2, max_pages=3)

    assert result.pages_scanned == 3
    assert result.truncated is False
    assert len(result.items) == 1
    assert result.items[0].url == "https://media.example.test/assets/episode.mp4"
    assert result.items[0].thumb == "https://media.example.test/covers/episode.jpg"


def test_recursive_scan_reports_when_depth_limit_truncates(monkeypatch):
    root = "https://media.example.test/library"
    pages = {root: "<a href='/child'>More videos</a>"}
    scraper = SiteScraper(resolver=PUBLIC_RESOLVER)
    monkeypatch.setattr(scraper, "_fetch_html", lambda url: (pages[url], url))

    result = scraper.scan(root, max_depth=0, max_pages=10)

    assert result.pages_scanned == 1
    assert result.truncated is True


def test_recursive_scan_reports_when_page_limit_truncates(monkeypatch):
    root = "https://media.example.test/library"
    category = "https://media.example.test/category"
    deep_page = "https://media.example.test/series/one"
    pages = {
        root: "<a href='/category'>Browse catalog</a>",
        category: "<a href='/series/one'>Series details</a>",
        deep_page: "<video src='/assets/episode.mp4'></video>",
    }
    scraper = SiteScraper(resolver=PUBLIC_RESOLVER)
    monkeypatch.setattr(scraper, "_fetch_html", lambda url: (pages[url], url))

    result = scraper.scan(root, max_depth=4, max_pages=2)

    assert result.pages_scanned == 2
    assert result.truncated is True
    assert result.items == []


def test_direct_stream_source_is_returned_without_fetching_page_html():
    scraper = SiteScraper(resolver=PUBLIC_RESOLVER)

    items = scraper.scrape("https://cdn.example.test/movie.mp4?token=abc")

    assert len(items) == 1
    assert items[0].url == "https://cdn.example.test/movie.mp4?token=abc"
    assert items[0].stream_format == "mp4"


def test_redirect_to_private_address_is_rejected_before_second_request():
    class FakeResponse:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/private"}
        closed = False

        def close(self):
            self.closed = True

    class FakeSession:
        def __init__(self):
            self.calls = []
            self.response = FakeResponse()

        def get(self, url, **_kwargs):
            self.calls.append(url)
            return self.response

    def resolver(host, _port):
        if host == "127.0.0.1":
            return ["127.0.0.1"]
        return ["93.184.216.34"]

    session = FakeSession()
    scraper = SiteScraper(resolver=resolver, session=session)

    with pytest.raises(InvalidTargetError, match="Private"):
        scraper._fetch_html("https://public.example.test/start")
    assert session.calls == ["https://public.example.test/start"]
    assert session.response.closed


def test_rejects_non_public_targets_before_fetching():
    scraper = SiteScraper(resolver=lambda _host, _port: ["127.0.0.1"])

    with pytest.raises(InvalidTargetError, match="Private"):
        scraper.scrape("http://127.0.0.1/admin")


def test_optional_exact_host_allowlist_is_enforced():
    scraper = SiteScraper(resolver=PUBLIC_RESOLVER, allowed_hosts=["allowed.example.test"])

    with pytest.raises(InvalidTargetError, match="TRILLROKU_ALLOWED_HOSTS"):
        scraper.scrape("https://other.example.test/catalog")


def test_android_scan_code_resolves_to_saved_roku_manifest(tmp_path):
    class FakeScraper:
        def scan(self, url: str, *, max_depth: int, max_pages: int) -> ScanResult:
            assert url == "https://media.example.test/catalog"
            assert max_depth == 2
            assert max_pages == 25
            return ScanResult(
                items=[VideoItem("Clip", "", "https://cdn.example.test/clip.mp4", "mp4")],
                pages_scanned=7,
                truncated=False,
            )

    store = ScanStore(str(tmp_path / "scans.sqlite3"))
    client = create_app(scraper_factory=FakeScraper, scan_store=store).test_client()
    scanned = client.post(
        "/api/scan",
        json={"url": "https://media.example.test/catalog", "maxDepth": 2, "maxPages": 25},
    )
    assert scanned.status_code == 200
    scan_id = scanned.get_json()["scanId"]
    assert len(scan_id) == 10
    assert scanned.get_json()["pagesScanned"] == 7

    fetched = client.post("/api/manifest", json={"scanId": scan_id})
    assert fetched.status_code == 200
    assert fetched.get_json()["items"][0]["url"] == "https://cdn.example.test/clip.mp4"


def test_manifest_api_returns_roku_friendly_json_and_checks_optional_key():
    class FakeScraper:
        def scrape(self, url: str) -> list[VideoItem]:
            assert url == "https://media.example.test/catalog"
            return [VideoItem("Clip", "https://media.example.test/thumb.jpg", "https://cdn.example.test/clip.mp4", "mp4")]

    client = create_app(scraper_factory=FakeScraper, api_key="test-secret").test_client()

    rejected = client.post("/api/manifest", json={"url": "https://media.example.test/catalog"})
    assert rejected.status_code == 401

    response = client.post(
        "/api/manifest",
        json={"url": "https://media.example.test/catalog"},
        headers={"X-API-Key": "test-secret"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    assert payload["items"][0] == {
        "title": "Clip",
        "thumb": "https://media.example.test/thumb.jpg",
        "url": "https://cdn.example.test/clip.mp4",
        "streamFormat": "mp4",
    }
