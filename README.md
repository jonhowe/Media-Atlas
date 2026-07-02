# Media Atlas

Media Atlas is a local-first media inventory and transcode execution web app. It scans configured media roots with `ffprobe`, stores technical metadata in SQLite, provides a searchable reporting UI, generates transcode plans, and can run staged `ffmpeg` transcode jobs from the web UI without modifying source media.

## MVP Capabilities

- Configure one or more local or mounted media roots.
- Recursively scan media files and skip unchanged files on rescans.
- Store raw `ffprobe` JSON plus normalized codec, container, stream, bitrate, duration, HDR, subtitle, and audio fields.
- Browse, search, filter, report, and export inventory data.
- Classify files as Easy Win, Remux Only, Review, Skip, Already Modern, Error, or Missing.
- Generate staged transcode plans from candidates.
- Start and monitor one server-side transcode run at a time.
- Close and reopen the browser while jobs continue, as long as the backend process remains running.

The MVP never deletes, overwrites, replaces, or automatically mutates source media.

## Requirements

- Docker Compose
- A local or mounted media directory

The published container image supplies Python, Node.js-built frontend assets, `ffmpeg`, and `ffprobe`.

## Docker Compose Install

This is the recommended install path for a seedbox or home server. It uses the published GHCR image and does not require cloning the repository.

Create an install directory:

```bash
mkdir -p ~/media-atlas
cd ~/media-atlas
```

Create `.env` with the host path to your media:

```bash
MEDIA_ATLAS_PORT=8000
MEDIA_ATLAS_MEDIA_ROOT=/mnt/media
MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS=/media
MEDIA_ATLAS_SCAN_CONCURRENCY=2
MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS=60
MEDIA_ATLAS_FFMPEG_TIMEOUT_SECONDS=0
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
    volumes:
      - ./data:/app/data
      - ./reports:/app/reports
      - ./logs:/app/logs
      - ./transcode-staging:/app/transcode-staging
      - ${MEDIA_ATLAS_MEDIA_ROOT:?Set MEDIA_ATLAS_MEDIA_ROOT in .env}:/media:ro
```

Pull and start the app:

```bash
docker compose pull
docker compose up -d
```

Open the app from another machine on the LAN:

```text
http://SERVER_IP:8000/
```

For the seedbox shown earlier, that would be:

```text
http://192.168.1.106:8000/
```

### Docker Path Mapping

The Compose file mounts the host media root read-only:

```text
host:      MEDIA_ATLAS_MEDIA_ROOT
container: /media
```

Media Atlas runs inside the container, so paths added in the UI must use the container path. Use `/media` or subpaths under `/media`, for example:

```text
/media
/media/Movies
/media/TV
```

Do not add the host path such as `/mnt/media` in the UI unless you also mount that exact path into the container.

Persistent app data is bind-mounted into the install directory:

```text
./data                SQLite database
./reports             CSV/report output
./logs                app and transcode logs
./transcode-staging   staged transcode outputs
```

Source media is mounted read-only. Staged transcode output is written separately and source files are not modified by the MVP.

Useful commands:

```bash
docker compose pull
docker compose up -d
docker compose logs -f media-atlas
docker compose restart media-atlas
docker compose down
```

## Publishing

GitHub Actions publishes images to GHCR.

- `ghcr.io/jonhowe/media-atlas:latest` tracks the latest published `main` build.
- Release tags publish matching image tags, for example `ghcr.io/jonhowe/media-atlas:v0.1.0`.
- Every published image also gets a commit-pinned `sha-<commit>` tag.
- Builds use GitHub Actions layer caching, Trivy scanning, SBOM attestations, and provenance attestations.

## Development Install

Local development requires Python 3.12+, Node.js 20+, and `ffmpeg`/`ffprobe` on `PATH`.

```bash
git clone git@github.com:jonhowe/Media-Atlas.git
cd Media-Atlas
```

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
cd ../frontend
npm install
npm run dev
```

Open the Vite URL, usually `http://127.0.0.1:5173`.

## Configuration

Configuration is environment-variable based so the same settings work for the GHCR container and local development.

```bash
MEDIA_ATLAS_HOST=127.0.0.1
MEDIA_ATLAS_PORT=8000
MEDIA_ATLAS_DATA_DIR=./data
MEDIA_ATLAS_REPORTS_DIR=./reports
MEDIA_ATLAS_LOGS_DIR=./logs
MEDIA_ATLAS_TRANSCODE_STAGING_DIR=./transcode-staging
MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS=/Volumes,/mnt,/media
MEDIA_ATLAS_FFPROBE_PATH=ffprobe
MEDIA_ATLAS_FFMPEG_PATH=ffmpeg
MEDIA_ATLAS_SCAN_CONCURRENCY=2
MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS=60
```
