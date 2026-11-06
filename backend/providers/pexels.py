"""Pexels video-search adapter that returns direct MP4 files for Roku."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_MAX_PER_PAGE = 80


class PexelsProviderError(Exception):
    """An expected upstream API failure."""


class PexelsNotConfiguredError(PexelsProviderError):
    """The server has no Pexels API key configured."""


@dataclass
class PexelsSearchPage:
    items: list[Any]
    total_results: int
    page: int
    per_page: int
    next_page: str


def _https_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return ""
    except ValueError:
        return ""
    return value


def _best_mp4_file(video_files: object) -> str:
    if not isinstance(video_files, list):
        return ""
    candidates: list[tuple[int, str]] = []
    for video_file in video_files:
        if not isinstance(video_file, dict):
            continue
        file_type = str(video_file.get("file_type") or "").lower()
        link = _https_url(video_file.get("link"))
        if not link or ("mp4" not in file_type and not urlsplit(link).path.lower().endswith(".mp4")):
            continue
        try:
            width = int(video_file.get("width") or 0)
            height = int(video_file.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        pixels = width * height
        # Prefer the best file at or below Full HD for Roku; if none exists,
        # choose the smallest larger MP4 to avoid needlessly huge streams.
        candidates.append((pixels, link))
    if not candidates:
        return ""
    under_full_hd = [candidate for candidate in candidates if candidate[0] <= 1920 * 1080]
    if under_full_hd:
        return max(under_full_hd, key=lambda candidate: candidate[0])[1]
    return min(candidates, key=lambda candidate: candidate[0])[1]


class PexelsVideoProvider:
    """Search Pexels and normalize its video files to TrillRoku items."""

    def __init__(self, *, api_key: str | None = None, session: requests.Session | None = None) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("PEXELS_API_KEY", "")).strip()
        self.session = session or requests.Session()
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False

    def search(self, query: str, *, page: int = 1, per_page: int = 50) -> PexelsSearchPage:
        if not self.api_key:
            raise PexelsNotConfiguredError("Pexels is not configured; set PEXELS_API_KEY on the middleware.")
        query = query.strip()
        if not query:
            raise PexelsProviderError("A Pexels search query is required.")
        page = max(1, min(int(page), 1_000_000))
        per_page = max(1, min(int(per_page), PEXELS_MAX_PER_PAGE))
        try:
            response = self.session.get(
                PEXELS_SEARCH_URL,
                headers={"Authorization": self.api_key, "Accept": "application/json"},
                params={"query": query, "page": page, "per_page": per_page},
                timeout=(5, 20),
            )
        except requests.RequestException as exc:
            raise PexelsProviderError("Could not reach the Pexels API.") from exc
        if response.status_code < 200 or response.status_code >= 300:
            if response.status_code == 401 or response.status_code == 403:
                raise PexelsProviderError("Pexels rejected the API key. Check PEXELS_API_KEY.")
            raise PexelsProviderError(f"Pexels API returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise PexelsProviderError("Pexels returned invalid JSON.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("videos"), list):
            raise PexelsProviderError("Pexels returned an unexpected search response.")

        from ..scraper import VideoItem

        items = []
        for video in payload["videos"]:
            if not isinstance(video, dict):
                continue
            direct_url = _best_mp4_file(video.get("video_files"))
            if not direct_url:
                continue
            video_id = str(video.get("id") or "unknown")
            user = video.get("user") if isinstance(video.get("user"), dict) else {}
            contributor = str(user.get("name") or "Pexels contributor")
            items.append(
                VideoItem(
                    title=f"Pexels video {video_id}",
                    thumb=_https_url(video.get("image")),
                    url=direct_url,
                    stream_format="mp4",
                    attribution=f"Pexels — {contributor}",
                    page_url=_https_url(video.get("url")),
                )
            )
        try:
            total_results = max(0, int(payload.get("total_results") or 0))
        except (TypeError, ValueError) as exc:
            raise PexelsProviderError("Pexels returned an invalid result count.") from exc
        return PexelsSearchPage(
            items=items,
            total_results=total_results,
            page=page,
            per_page=per_page,
            next_page=_https_url(payload.get("next_page")),
        )
