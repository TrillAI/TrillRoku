from __future__ import annotations

import pytest

from backend.app import create_app
from backend.providers.pexels import PexelsProviderError, PexelsSearchPage, PexelsVideoProvider
from backend.scan_store import ScanStore
from backend.scraper import VideoItem


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.trust_env = True

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_pexels_provider_selects_direct_full_hd_mp4_and_preserves_credit():
    response = FakeResponse(
        {
            "page": 1,
            "per_page": 10,
            "total_results": 1,
            "next_page": "https://api.pexels.com/videos/search?page=2",
            "videos": [
                {
                    "id": 123,
                    "url": "https://www.pexels.com/video/example-123/",
                    "image": "https://images.pexels.com/photos/123/cover.jpeg",
                    "user": {"name": "Contributor"},
                    "video_files": [
                        {
                            "file_type": "video/mp4",
                            "width": 640,
                            "height": 360,
                            "link": "https://videos.pexels.com/video-files/123/sd.mp4",
                        },
                        {
                            "file_type": "video/mp4",
                            "width": 1920,
                            "height": 1080,
                            "link": "https://videos.pexels.com/video-files/123/hd.mp4",
                        },
                        {
                            "file_type": "video/mp4",
                            "width": 3840,
                            "height": 2160,
                            "link": "https://videos.pexels.com/video-files/123/4k.mp4",
                        },
                        {
                            "file_type": "video/webm",
                            "width": 1920,
                            "height": 1080,
                            "link": "https://videos.pexels.com/video-files/123/hd.webm",
                        },
                    ],
                }
            ],
        }
    )
    session = FakeSession(response)
    provider = PexelsVideoProvider(api_key="test-key", session=session)

    page = provider.search("nature", page=1, per_page=10)

    assert len(page.items) == 1
    item = page.items[0].to_dict()
    assert item["url"] == "https://videos.pexels.com/video-files/123/hd.mp4"
    assert item["streamFormat"] == "mp4"
    assert item["thumb"] == "https://images.pexels.com/photos/123/cover.jpeg"
    assert item["attribution"] == "Pexels — Contributor"
    assert item["pageUrl"] == "https://www.pexels.com/video/example-123/"
    request_url, kwargs = session.calls[0]
    assert request_url == "https://api.pexels.com/videos/search"
    assert kwargs["headers"]["Authorization"] == "test-key"
    assert session.trust_env is False


def test_pexels_provider_requires_server_key():
    provider = PexelsVideoProvider(api_key="", session=FakeSession(FakeResponse({"videos": []})))

    with pytest.raises(PexelsProviderError, match="PEXELS_API_KEY"):
        provider.search("nature")


def test_pexels_scan_reports_missing_server_key(tmp_path):
    app = create_app(
        pexels_provider_factory=lambda: PexelsVideoProvider(
            api_key="",
            session=FakeSession(FakeResponse({"videos": []})),
        ),
        scan_store=ScanStore(str(tmp_path / "unconfigured-pexels.sqlite3")),
    )

    response = app.test_client().post(
        "/api/providers/pexels/scan",
        json={"query": "nature"},
    )

    assert response.status_code == 503
    assert "PEXELS_API_KEY" in response.get_json()["error"]


def test_pexels_scan_is_saved_as_a_roku_manifest(tmp_path):
    class FakeProvider:
        def search(self, query: str, *, page: int, per_page: int) -> PexelsSearchPage:
            assert query == "clouds"
            assert page == 1
            assert per_page == 50
            return PexelsSearchPage(
                items=[
                    VideoItem(
                        "Pexels video 45",
                        "https://images.pexels.com/45.jpg",
                        "https://videos.pexels.com/45.mp4",
                        "mp4",
                        attribution="Pexels — Contributor",
                        page_url="https://www.pexels.com/video/45/",
                    )
                ],
                total_results=800,
                page=1,
                per_page=50,
                next_page="https://api.pexels.com/videos/search?page=2",
            )

    app = create_app(
        pexels_provider_factory=FakeProvider,
        scan_store=ScanStore(str(tmp_path / "pexels-scans.sqlite3")),
    )
    client = app.test_client()
    response = client.post(
        "/api/providers/pexels/scan",
        json={"query": "clouds", "page": 1, "perPage": 50},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["provider"] == "pexels"
    assert payload["totalResults"] == 800
    assert payload["items"][0]["streamFormat"] == "mp4"
    assert payload["items"][0]["attribution"] == "Pexels — Contributor"

    roku_manifest = client.post("/api/manifest", json={"scanId": payload["scanId"]})
    assert roku_manifest.status_code == 200
    assert roku_manifest.get_json()["items"][0]["url"] == "https://videos.pexels.com/45.mp4"
