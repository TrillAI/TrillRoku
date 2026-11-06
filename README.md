# TrillRoku

A Roku SceneGraph video picker, a native Android companion scanner, and a Python middleware API. The Android app contains **no WebView**: it submits a URL to the API and renders the returned stream list using native Android views.

## How the companion and Roku work together

```text
Android Companion (native form + results list)
       │ POST /api/scan { url, maxDepth, maxPages }
       ▼
Flask API ──► bounded same-origin recursive crawler ──► SQLite scan record
       │                                                 │
       └── videos + 10-character Roku scan code          │ POST /api/manifest { scanId }
                                                         ▼
Roku ManifestTask ◄────────────────────────────── same stored list
       │
       ▼
PosterGrid ── remote selects a poster ──► Video plays and advances on state="finished"
                                             └── loops until Back/Stop
```

The companion displays the scan results and a short-lived code. Enter that code in the Roku app's source prompt (or use **Change Source / Scan Code**); Roku then loads the exact same stored list, so the viewer can choose with the remote without retyping a long URL. Scan codes expire after 24 hours. The Roku can also scan a website URL itself.

## Permission notice

The Android app shows a persistent disclaimer and requires the user to check an acknowledgement before scanning: **only scan websites and video streams you own or have permission to access and stream**. The middleware does not log in, bypass paywalls/DRM, or defeat other access controls. Users are responsible for rights and site terms.

## Middleware: Python / Flask

### Install and run locally

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt
python -m pip install pytest  # only needed to run tests
python -m backend.app
```

The development server listens on `0.0.0.0:8000`; `/healthz` is a health check. For the current Cloudflare connection, clients use `https://origins-monitored-accomplish-constraints.trycloudflare.com`. You reported that its `/healthz` endpoint returned `{"status":"ok"}`. The tunnel forwards HTTPS traffic to the local Flask API over `http://127.0.0.1:8000`.

### Cloudflare Tunnel and editable API host

Start Flask in one terminal, then run the Cloudflare Quick Tunnel in another:

```bash
python -m backend.app
# another terminal
scripts/start-cloudflare-quick-tunnel.sh
```

The script runs `cloudflared tunnel --url http://127.0.0.1:8000`; install `cloudflared` on the API host first. Quick Tunnel hostnames can change when the tunnel is restarted. For a stable long-term host, use a named Cloudflare Tunnel and a hostname you control; for Quick Tunnel changes, update the shared origin in `config/api-base-url.txt` as described below.

To change it, run:

```bash
python3 scripts/sync_api_base_url.py https://new-host.trycloudflare.com
```

This updates the shared config and Roku XML. The Android Gradle build reads the same config as its packaged default; rebuild the APK to update that default. The API host is also editable directly in the Android app, and Roku uses the synchronized XML value (repackage the Roku channel after changing it). To verify the currently configured host, run `curl -fsS "$(cat config/api-base-url.txt)/healthz"`; it should return `{"status":"ok"}`.

Optional environment settings (see `backend/.env.example`):

- `TRILLROKU_API_KEY`: optional shared key; API requests must send `X-API-Key` when set.
- `TRILLROKU_ALLOWED_HOSTS`: optional comma-separated exact hostname allowlist for source pages and redirects.
- `TRILLROKU_CORS_ORIGINS`: browser origins or `*`; native apps do not use browser CORS.
- `TRILLROKU_DB_PATH`: SQLite file for scan records; use persistent storage in production.
- `TRILLROKU_PORT`: local development port, default `8000`.

### Recursive scan API

`POST /api/scan` crawls same-origin links breadth-first. `maxDepth` and `maxPages` are bounded by the server (depth 0–5, pages 1–500); defaults are depth 3 and 100 pages.

```bash
curl -X POST https://origins-monitored-accomplish-constraints.trycloudflare.com/api/scan \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.org/videos","maxDepth":3,"maxPages":100}'
```

The response includes the discovered list, scan statistics, and a `scanId`:

```json
{
  "source": "https://example.org/videos",
  "count": 1,
  "pagesScanned": 12,
  "truncated": false,
  "scanId": "7M4FQ9W2DK",
  "expiresAt": 1794000000,
  "items": [
    {
      "title": "Episode One",
      "thumb": "https://example.org/posters/one.jpg",
      "url": "https://cdn.example.org/streams/one.m3u8?token=…",
      "streamFormat": "hls"
    }
  ]
}
```

`POST /api/manifest` accepts either `{"url":"https://..."}` (a default recursive scan) or `{"scanId":"7M4FQ9W2DK"}` (load the companion's saved list). `GET /api/scans/<scanId>` also retrieves a saved scan. A direct MP4 or HLS URL can be scanned as a one-item list.

The scraper recognizes HTML `<video>` / `<source>`, Open Graph/Twitter metadata, `VideoObject` JSON-LD, common lazy-source attributes, and direct `.mp4` / `.m3u8` links. It crawls ordinary same-origin links and iframes up to the selected limits; direct streams can live on a CDN.

### Crawl limits and security

“Any and every video” cannot be guaranteed across arbitrary websites. This implementation is intentionally bounded and best-effort: it scans same-origin server-rendered pages up to the requested page/depth caps. It will miss URLs created only by client-side JavaScript, unsupported external player pages, login-required media, DRM/encrypted streams, or videos hidden behind access controls. If `truncated` is true, increase the scan limits or scan a narrower section of the site.

The backend accepts public HTTP(S) targets on standard ports, checks DNS addresses against private/loopback/reserved ranges, revalidates redirects, limits each response size, individual request time, and each crawl to about two minutes, and can enforce an exact host allowlist. For production, also restrict outbound network access at the host/firewall layer; DNS pre-validation alone is not a complete defense against DNS rebinding. Keep the SQLite file on persistent storage so Android scan codes remain usable across restarts. An API key embedded in a mobile/Roku package is extractable and should not be treated as a durable secret.

## Android companion: native UI, no WebView

Project files are in `android/`. The screen includes the permission notice/required acknowledgement, API origin, optional API key, source URL, recursive depth/page controls, scan progress, discovered stream list, and a **Copy Roku scan code** button. It uses Android SDK views, `HttpURLConnection`, and `org.json`; no WebView or third-party UI/network library is included.

Open the `android/` directory in Android Studio and build the `app` APK (min SDK 26, target SDK 35). The API URL field defaults to the Cloudflare host in `config/api-base-url.txt`; it is editable in-app and has a reset-to-packaged-value button. Enter an optional API key and permitted site URL, acknowledge the disclaimer, and scan. Copy the returned code and type it into the Roku source prompt.

## Roku SceneGraph client

The channel package is in `roku/`:

- `manifest` and `source/main.brs`: channel entry point.
- `components/MainScene.xml` / `MainScene.brs`: URL or scan-code prompt, `PosterGrid`, player, and autoplay logic.
- `components/ManifestTask.xml` / `ManifestTask.brs`: off-render-thread HTTP request, JSON parsing, and `ContentNode` creation.

`roku/components/MainScene.xml` is synchronized from `config/api-base-url.txt` by `scripts/sync_api_base_url.py`; the current default is the live Cloudflare origin above. If you update the origin, run the sync command and repackage the Roku channel. If enabled on the server, set `apiKey` in the XML to the same key. Optionally set `initialSourceUrl` to a source URL or companion scan code; leave it blank to prompt.

The Roku grid is a single `PosterGrid` row so `itemSelected` indexes line up with the manifest. Selecting a poster assigns a fresh content node; `onContentChange()` starts playback. On `Video.state == "finished"`, the next entry starts and the sequence wraps. Press **Back** to stop playback and return to the grid.

```brightscript
sub onContentChange(event as Object)
    if event.getData() = invalid then return
    m.video.control = "play"
end sub

sub onVideoStateChanged(event as Object)
    if LCase(event.getData()) = "finished" then
        nextIndex = m.currentIndex + 1
        if nextIndex >= m.entries.Count() then nextIndex = 0
        playEntryAt(nextIndex)
    end if
end sub
```

SceneGraph reports completion using the `Video.state` string `"finished"`, not an `isFinished` boolean. Package from the Roku directory and sideload through Roku developer mode:

```bash
cd roku
zip -r ../trillroku.zip manifest source components
```

## Tests

The Python tests use local HTML fixtures and a temporary SQLite database; they do not scrape external websites:

```bash
.venv/bin/python -m pytest -q backend/tests
```
