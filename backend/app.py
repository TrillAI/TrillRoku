"""Flask API for recursive scans and Roku-friendly video manifests."""

from __future__ import annotations

import hmac
import os
from typing import Callable

from flask import Flask, jsonify, request

from .scan_store import ScanStore
from .scraper import (
    DEFAULT_CRAWL_DEPTH,
    DEFAULT_CRAWL_PAGES,
    MAX_CRAWL_DEPTH,
    MAX_CRAWL_PAGES,
    InvalidTargetError,
    RemotePageError,
    SiteScraper,
    ScrapeError,
)


def _parse_origins(value: str | None) -> list[str]:
    if not value:
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()] or ["*"]


def _bounded_option(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(max(value, minimum), maximum)


def create_app(
    *,
    scraper_factory: Callable[[], SiteScraper] | None = None,
    scan_store: ScanStore | None = None,
    api_key: str | None = None,
    cors_origins: list[str] | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    app.config["TRILLROKU_API_KEY"] = api_key if api_key is not None else os.getenv("TRILLROKU_API_KEY", "")
    app.config["TRILLROKU_CORS_ORIGINS"] = cors_origins or _parse_origins(
        os.getenv("TRILLROKU_CORS_ORIGINS", "*")
    )
    app.config["SCRAPER_FACTORY"] = scraper_factory or SiteScraper
    app.config["SCAN_STORE"] = scan_store or ScanStore()

    @app.before_request
    def verify_api_key():
        expected = app.config["TRILLROKU_API_KEY"]
        if request.path.startswith("/api/") and request.method != "OPTIONS" and expected:
            provided = request.headers.get("X-API-Key", "")
            if not hmac.compare_digest(provided, expected):
                return jsonify({"error": "Unauthorized."}), 401
        return None

    @app.after_request
    def add_cors_headers(response):
        if not request.path.startswith("/api/"):
            return response
        origin = request.headers.get("Origin", "")
        allowed = app.config["TRILLROKU_CORS_ORIGINS"]
        if "*" in allowed:
            response.headers["Access-Control-Allow-Origin"] = "*"
        elif origin and origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
        response.headers["Access-Control-Max-Age"] = "600"
        return response

    def stored_manifest(scan_id: str):
        manifest = app.config["SCAN_STORE"].get(scan_id)
        if manifest is None:
            return jsonify({"error": "Scan code not found or expired."}), 404
        return jsonify(manifest)

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.route("/api/manifest", methods=["GET", "POST", "OPTIONS"])
    def manifest():
        if request.method == "OPTIONS":
            return "", 204
        if request.method == "GET":
            scan_id = request.args.get("scanId", "")
            source_url = request.args.get("url", "")
            payload = None
        else:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({"error": "POST a JSON object containing a 'url' or 'scanId'."}), 400
            scan_id = payload.get("scanId", "")
            source_url = payload.get("url", "")

        if isinstance(scan_id, str) and scan_id.strip():
            return stored_manifest(scan_id)
        if not isinstance(source_url, str) or not source_url.strip():
            return jsonify({"error": "A non-empty 'url' or valid 'scanId' is required."}), 400

        try:
            items = app.config["SCRAPER_FACTORY"]().scrape(source_url)
        except InvalidTargetError as exc:
            return jsonify({"error": str(exc)}), 400
        except RemotePageError as exc:
            return jsonify({"error": str(exc)}), 502
        except ScrapeError as exc:
            return jsonify({"error": str(exc)}), 502

        return jsonify(
            {
                "source": source_url.strip(),
                "count": len(items),
                "items": [item.to_dict() for item in items],
            }
        )

    @app.route("/api/scan", methods=["POST", "OPTIONS"])
    def scan():
        if request.method == "OPTIONS":
            return "", 204
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "POST a JSON object containing a 'url' string."}), 400
        source_url = payload.get("url", "")
        if not isinstance(source_url, str) or not source_url.strip():
            return jsonify({"error": "A non-empty 'url' is required."}), 400

        max_depth = _bounded_option(payload.get("maxDepth"), DEFAULT_CRAWL_DEPTH, 0, MAX_CRAWL_DEPTH)
        max_pages = _bounded_option(payload.get("maxPages"), DEFAULT_CRAWL_PAGES, 1, MAX_CRAWL_PAGES)
        try:
            result = app.config["SCRAPER_FACTORY"]().scan(
                source_url,
                max_depth=max_depth,
                max_pages=max_pages,
            )
        except InvalidTargetError as exc:
            return jsonify({"error": str(exc)}), 400
        except RemotePageError as exc:
            return jsonify({"error": str(exc)}), 502
        except ScrapeError as exc:
            return jsonify({"error": str(exc)}), 502

        manifest_data = {
            "source": source_url.strip(),
            "count": len(result.items),
            "items": [item.to_dict() for item in result.items],
            "pagesScanned": result.pages_scanned,
            "truncated": result.truncated,
            "maxDepth": max_depth,
            "maxPages": max_pages,
        }
        return jsonify(app.config["SCAN_STORE"].save(manifest_data))

    @app.get("/api/scans/<scan_id>")
    def get_scan(scan_id: str):
        return stored_manifest(scan_id)

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify({"error": "Request body is too large."}), 413

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("TRILLROKU_PORT", "8000"))
    # Debug/reloader mode is intentionally off: this endpoint accepts user URLs.
    app.run(host="0.0.0.0", port=port, debug=False)
