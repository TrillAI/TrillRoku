#!/usr/bin/env python3
"""Set the shared API origin and sync the Roku package default.

Android reads config/api-base-url.txt during Gradle configuration. This script
also writes that value into MainScene.xml for Roku packaging.
"""

from __future__ import annotations

import argparse
import re
import sys
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "config" / "api-base-url.txt"
ROKU_SCENE = ROOT / "roku" / "components" / "MainScene.xml"


def normalize_origin(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("The API origin is not a valid URL.") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Use a public HTTPS origin, for example https://api.example.com.")
    if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Enter only the API origin (no credentials, path, query, or fragment).")
    if port not in (None, 443):
        raise ValueError("The API origin must use the standard HTTPS port.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the shared TrillRoku API base URL.")
    parser.add_argument("url", nargs="?", help="new HTTPS origin; omit to re-sync the saved value")
    args = parser.parse_args()

    try:
        if args.url:
            origin = normalize_origin(args.url)
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(origin + "\n", encoding="utf-8")
        else:
            origin = normalize_origin(CONFIG_FILE.read_text(encoding="utf-8"))

        xml = ROKU_SCENE.read_text(encoding="utf-8")
        pattern = r'(<field\s+id="apiBaseUrl"\s+type="string"\s+value=")[^"]*(")'
        updated, count = re.subn(
            pattern,
            lambda match: match.group(1) + escape(origin, quote=True) + match.group(2),
            xml,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"Could not find the apiBaseUrl field in {ROKU_SCENE}.")
        ROKU_SCENE.write_text(updated, encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"API origin set to: {origin}")
    print("Roku XML synchronized. Android Gradle will use the saved config on the next build.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
