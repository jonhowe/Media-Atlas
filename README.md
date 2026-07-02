# Media Atlas

Media Atlas is a local-first media inventory and transcode execution web app. It scans configured media roots with `ffprobe`, stores technical metadata in SQLite, enriches library rows with optional Plex metadata, provides searchable reporting, generates transcode plans, and can run staged `ffmpeg` jobs without modifying source media.

The MVP never deletes, overwrites, replaces, or automatically mutates source media.

## Capabilities

- Configure one or more local or mounted media roots.
- Recursively scan media files and skip unchanged files on rescans.
- Store raw `ffprobe` JSON plus normalized codec, container, stream, bitrate, duration, HDR, subtitle, and audio fields.
- Optionally sync Plex libraries for title, year, show/season/episode, collection, genre, label, watched state, and match status.
- Browse, search, filter, report, and export inventory data.
- Classify files as Easy Win, Remux Only, Review, Skip, Already Modern, Error, or Missing.
- Generate staged transcode plans from candidates.
- Start and monitor one server-side transcode run at a time.
- Close and reopen the browser while jobs continue, as long as the backend process remains running.

## Production Install

Recommended install uses the published GHCR image. The host only needs Docker Compose and access to a local or mounted media directory.

Create an install directory:

```bash
mkdir -p ~/media-atlas
cd ~/media-atlas
```

Create `.env`:

```bash
MEDIA_ATLAS_PORT=8000
MEDIA_ATLAS_MEDIA_ROOT=/mnt/media
MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS=/media
```

Create `docker-compose.yml`:

```yaml
services:
  media-atlas:
    image: ghcr.io/jonhowe/media-atlas:latest
    container_name: media-atlas
    restart: unless-stopped
    ports:
      - "${MEDIA_ATLAS_PORT:-8000}:8000"
    environment:
      MEDIA_ATLAS_HOST: "0.0.0.0"
      MEDIA_ATLAS_PORT: "8000"
      MEDIA_ATLAS_DATA_DIR: "/app/data"
      MEDIA_ATLAS_REPORTS_DIR: "/app/reports"
      MEDIA_ATLAS_LOGS_DIR: "/app/logs"
      MEDIA_ATLAS_TRANSCODE_STAGING_DIR: "/app/transcode-staging"
      MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS: "${MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS:-/media}"
      MEDIA_ATLAS_SCAN_CONCURRENCY: "${MEDIA_ATLAS_SCAN_CONCURRENCY:-2}"
      MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS: "${MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS:-60}"
      MEDIA_ATLAS_FFMPEG_TIMEOUT_SECONDS: "${MEDIA_ATLAS_FFMPEG_TIMEOUT_SECONDS:-0}"
      MEDIA_ATLAS_MARK_MISSING_FILES: "${MEDIA_ATLAS_MARK_MISSING_FILES:-true}"
      MEDIA_ATLAS_DIRECTORY_BROWSER_ENABLED: "${MEDIA_ATLAS_DIRECTORY_BROWSER_ENABLED:-true}"
      MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_SECONDS: "${MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_SECONDS:-3}"
      MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_PERCENT: "${MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_PERCENT:-0.02}"
      MEDIA_ATLAS_PLEX_URL: "${MEDIA_ATLAS_PLEX_URL:-}"
      MEDIA_ATLAS_PLEX_TOKEN: "${MEDIA_ATLAS_PLEX_TOKEN:-}"
    volumes:
      - ./data:/app/data
      - ./reports:/app/reports
      - ./logs:/app/logs
      - ./transcode-staging:/app/transcode-staging
      - ${MEDIA_ATLAS_MEDIA_ROOT:?Set MEDIA_ATLAS_MEDIA_ROOT in .env}:/media:ro
```

Start the app:

```bash
docker compose pull
docker compose up -d
```

Open:

```text
http://SERVER_IP:8000/
```

Useful commands:

```bash
docker compose pull
docker compose up -d
docker compose logs -f media-atlas
docker compose restart media-atlas
docker compose down
```

## Paths And Volumes

The container sees your host media root at `/media`.

```text
host:      MEDIA_ATLAS_MEDIA_ROOT
container: /media
```

Add `/media` or subpaths such as `/media/Movies` and `/media/TV` in the Media Atlas UI. Do not add the host path such as `/mnt/media` unless you mount that exact path into the container.

Persistent app data is bind-mounted into the install directory:

```text
./data                SQLite database
./reports             CSV/report output
./logs                app and transcode logs
./transcode-staging   staged transcode outputs
```

Source media is mounted read-only. Staged transcode output is written separately.

## Configuration Reference

Configuration is environment-variable based. In Docker, variables in `.env` are consumed by `docker-compose.yml`; only variables listed under `environment` are passed into the container.

### Compose Variables

| Variable | Belongs in | Required | Default | Purpose |
| --- | --- | --- | --- | --- |
| `MEDIA_ATLAS_PORT` | `.env` | No | `8000` | Host port mapped to container port `8000`. |
| `MEDIA_ATLAS_MEDIA_ROOT` | `.env` | Yes | none | Host media path mounted read-only at `/media`. |
| `MEDIA_ATLAS_TRANSCODE_OUTPUT_ROOT` | `.env` plus a custom Compose volume line | No | none | Optional host path if you choose to mount staged outputs somewhere other than `./transcode-staging`. |

### Application Variables

| Variable | Belongs in | Docker default | Local/default value | Purpose |
| --- | --- | --- | --- | --- |
| `MEDIA_ATLAS_HOST` | `docker-compose.yml`; local shell/env file | `0.0.0.0` | `127.0.0.1` | Backend bind host. Docker should use `0.0.0.0`; local dev should usually use loopback. |
| `MEDIA_ATLAS_PORT` | `docker-compose.yml`; local shell/env file | `8000` | `8000` | Backend port inside the container or local process. |
| `MEDIA_ATLAS_BASE_DIR` | Optional local env var | not used | repository root | Base path used to derive default data/report/log/staging directories. |
| `MEDIA_ATLAS_DATA_DIR` | `docker-compose.yml`; optional local env var | `/app/data` | `./data` | Directory containing SQLite data by default. |
| `MEDIA_ATLAS_DATABASE_PATH` | Optional env var | `/app/data/media_inventory.sqlite` | `DATA_DIR/media_inventory.sqlite` | Full SQLite database path. Usually leave unset. |
| `MEDIA_ATLAS_REPORTS_DIR` | `docker-compose.yml`; optional local env var | `/app/reports` | `./reports` | Report output directory. |
| `MEDIA_ATLAS_LOGS_DIR` | `docker-compose.yml`; optional local env var | `/app/logs` | `./logs` | App and transcode log directory. |
| `MEDIA_ATLAS_TRANSCODE_STAGING_DIR` | `docker-compose.yml`; optional local env var | `/app/transcode-staging` | `./transcode-staging` | Staged transcode output directory. |
| `MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS` | `.env` via `docker-compose.yml`; optional local env var | `/media` | existing paths among home, `/mnt`, `/media`, `/Volumes`, repo root | Comma-separated roots available to the server-side directory browser. Use container paths in Docker. |
| `MEDIA_ATLAS_ALLOW_LAN` | Optional env var | `true` because Docker binds `0.0.0.0` | `false` when bound to loopback | Informational LAN mode flag. |
| `MEDIA_ATLAS_DIRECTORY_BROWSER_ENABLED` | `.env` via `docker-compose.yml`; optional local env var | `true` | `true` | Enables the server-side directory browser. |
| `MEDIA_ATLAS_FFPROBE_PATH` | Docker image default; optional local env var | `ffprobe` | `ffprobe` | Path or command name for `ffprobe`. |
| `MEDIA_ATLAS_FFMPEG_PATH` | Docker image default; optional local env var | `ffmpeg` | `ffmpeg` | Path or command name for `ffmpeg`. |
| `MEDIA_ATLAS_SCAN_CONCURRENCY` | `.env` via `docker-compose.yml`; optional local env var | `2` | `2` | Maximum concurrent `ffprobe` tasks. |
| `MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS` | `.env` via `docker-compose.yml`; optional local env var | `60` | `60` | Per-file `ffprobe` timeout; minimum is `5`. |
| `MEDIA_ATLAS_MARK_MISSING_FILES` | `.env` via `docker-compose.yml`; optional local env var | `true` | `true` | Marks previously scanned files missing when a root is available but a file is gone. |
| `MEDIA_ATLAS_FFMPEG_TIMEOUT_SECONDS` | `.env` via `docker-compose.yml`; optional local env var | `0` | `0` | Global `ffmpeg` timeout. `0` means no timeout. |
| `MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_SECONDS` | `.env` via `docker-compose.yml`; optional local env var | `3` | `3` | Minimum duration tolerance for output verification. |
| `MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_PERCENT` | `.env` via `docker-compose.yml`; optional local env var | `0.02` | `0.02` | Percent duration tolerance for output verification. |
| `MEDIA_ATLAS_PLEX_URL` | Optional `.env` via `docker-compose.yml`; optional local env var | empty | empty | Optional default Plex server URL. Can also be configured in Settings. |
| `MEDIA_ATLAS_PLEX_TOKEN` | Optional `.env` via `docker-compose.yml`; optional local env var | empty | empty | Optional default Plex token. Can also be configured in Settings; never returned in full by the API. |

Plex path mappings are configured in the Settings page, not by environment variable. In Docker, map Plex paths to container paths, for example `/mnt/media` to `/media`.

## Development Install

Local development requires Python 3.12+, Node.js 20+, and `ffmpeg`/`ffprobe` on `PATH`.

```bash
git clone git@github.com:jonhowe/Media-Atlas.git
cd Media-Atlas
cp .env.example .env
```

Backend:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --env-file ../.env
```

Frontend:

```bash
cd ../frontend
npm install
npm run dev
```

Open the Vite URL, usually `http://127.0.0.1:5173`.

## Publishing

GitHub Actions publishes images to GHCR.

- `ghcr.io/jonhowe/media-atlas:latest` tracks the latest published `main` build.
- Release tags publish matching image tags, for example `ghcr.io/jonhowe/media-atlas:v0.1.0`.
- Every published image also gets a commit-pinned `sha-<commit>` tag.
- Builds use GitHub Actions layer caching, Trivy scanning, SBOM attestations, and provenance attestations.
