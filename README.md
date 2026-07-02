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

- Python 3.12+
- Node.js 20+
- `ffmpeg` and `ffprobe` available on `PATH`

For Docker installs, only Docker Compose is required on the host. The image build supplies Node.js, Python, `ffmpeg`, and `ffprobe`.

## Docker Compose Install

This is the recommended install path for a seedbox or home server.

```bash
git clone git@github.com:jonhowe/Media-Atlas.git
cd Media-Atlas

cp .env.docker.example .env
```

Edit `.env` so `MEDIA_ATLAS_MEDIA_ROOT` points at the media directory on the Docker host:

```bash
MEDIA_ATLAS_PORT=8000
MEDIA_ATLAS_MEDIA_ROOT=/mnt/media
MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS=/media
```

Start the app:

```bash
docker compose up -d --build
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

That means the path you add in the Media Atlas UI should be the in-container path, for example:

```text
/media
/media/Movies
/media/TV
```

not the host path such as `/mnt/media`, unless you also mount that exact path into the container.

Persistent app data is bind-mounted into the project directory:

```text
./data                SQLite database
./reports             CSV/report output
./logs                app and transcode logs
./transcode-staging   staged transcode outputs
```

Source media is mounted read-only. Staged transcode output is written separately and source files are not modified by the MVP.

Useful commands:

```bash
docker compose logs -f media-atlas
docker compose restart media-atlas
docker compose down
docker compose pull
docker compose up -d --build
```

## Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL, usually `http://127.0.0.1:5173`.

## Configuration

Configuration is environment-variable based so the app can move into a container later without changing code.

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

## Future Container Work

The MVP runs directly on the host. Future Docker support should bind mount media roots, `data`, `reports`, `logs`, and `transcode-staging`; package `ffmpeg`/`ffprobe`; add a healthcheck; and document host-path to container-path mapping.
