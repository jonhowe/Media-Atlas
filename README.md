# Media Atlas

Media Atlas is a local-first media inventory and transcode execution web app. It scans configured media roots with `ffprobe`, stores technical metadata in SQLite, enriches library rows with optional Plex metadata, provides searchable reporting, generates transcode plans, and can run staged `ffmpeg` jobs without modifying source media.

Media Atlas never deletes, overwrites, replaces, or automatically mutates source media in the current production path. Transcode output is staged separately from originals.

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

For production, prefer a pinned release tag once one is available. `latest` is convenient for first installs and quick updates, but pinned tags make rollback straightforward.

Create an install directory:

```bash
mkdir -p ~/media-atlas
cd ~/media-atlas
```

Create `.env`:

```bash
MEDIA_ATLAS_PORT=8000
MEDIA_ATLAS_IMAGE=ghcr.io/jonhowe/media-atlas:latest
MEDIA_ATLAS_MEDIA_ROOT=/mnt/media
MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS=/media
MEDIA_ATLAS_AUTH_MODE=single_admin
MEDIA_ATLAS_ADMIN_USERNAME=admin
MEDIA_ATLAS_ADMIN_PASSWORD=replace-this-password
MEDIA_ATLAS_SESSION_SECRET=replace-this-with-a-long-random-secret
```

Create `docker-compose.yml`:

```yaml
services:
  media-atlas:
    image: ${MEDIA_ATLAS_IMAGE:-ghcr.io/jonhowe/media-atlas:latest}
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
      MEDIA_ATLAS_TRANSCODE_MIN_FREE_BYTES: "${MEDIA_ATLAS_TRANSCODE_MIN_FREE_BYTES:-1073741824}"
      MEDIA_ATLAS_AUTH_MODE: "${MEDIA_ATLAS_AUTH_MODE:-single_admin}"
      MEDIA_ATLAS_ADMIN_USERNAME: "${MEDIA_ATLAS_ADMIN_USERNAME:-admin}"
      MEDIA_ATLAS_ADMIN_PASSWORD: "${MEDIA_ATLAS_ADMIN_PASSWORD:-}"
      MEDIA_ATLAS_SESSION_SECRET: "${MEDIA_ATLAS_SESSION_SECRET:-}"
      MEDIA_ATLAS_SESSION_COOKIE_SECURE: "${MEDIA_ATLAS_SESSION_COOKIE_SECURE:-false}"
      MEDIA_ATLAS_ALLOWED_ORIGINS: "${MEDIA_ATLAS_ALLOWED_ORIGINS:-http://127.0.0.1:8000,http://localhost:8000}"
      MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN: "${MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN:-false}"
      MEDIA_ATLAS_LOG_RETENTION_DAYS: "${MEDIA_ATLAS_LOG_RETENTION_DAYS:-30}"
      MEDIA_ATLAS_STAGED_OUTPUT_RETENTION_DAYS: "${MEDIA_ATLAS_STAGED_OUTPUT_RETENTION_DAYS:-0}"
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

Generate a stronger session secret with:

```bash
openssl rand -hex 32
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

## Transcode Profiles

Media Atlas generates only app-defined `ffmpeg` commands. The production image includes several staged-output profiles:

| Profile | Encoder | Relative speed | Use case |
| --- | --- | --- | --- |
| Remux to MKV | stream copy | Very fast | Container changes without re-encoding. |
| HEVC Archive Balanced | `libx265 -crf 20 -preset medium` | Slow | Maximum compression and quality. |
| HEVC Archive Fast | `libx265 -crf 21 -preset fast` | Medium | Recommended default for most archival conversions. |
| HEVC Archive Faster | `libx265 -crf 22 -preset faster` | Fast | Bulk conversions where larger outputs are acceptable. |
| HEVC Quick Sync | `hevc_qsv -global_quality 24` | Very fast | Intel iGPU/Quick Sync hosts. |
| HEVC NVENC | `hevc_nvenc -cq 24 -preset p5` | Very fast | NVIDIA GPU hosts. |
| HEVC VAAPI | `hevc_vaapi -qp 24` | Very fast | Linux VAAPI hosts, usually Intel or AMD. |
| H.264 Compatibility | `libx264 -crf 20 -preset slow` | Medium | MP4 compatibility outputs. |

The hardware profiles are optional. They remain visible because the same image can run on different hosts, but they will fail preflight or `ffmpeg` execution if the container does not have access to the required device/driver stack.

For VAAPI or Intel Quick Sync on Linux, add device access to `docker-compose.yml`:

```yaml
services:
  media-atlas:
    devices:
      - /dev/dri:/dev/dri
```

For NVIDIA NVENC, install the NVIDIA Container Toolkit on the host and add:

```yaml
services:
  media-atlas:
    gpus: all
```

Useful CPU checks:

```bash
docker stats media-atlas
docker exec -it media-atlas nproc
docker inspect media-atlas
```

If `libx265` logs messages such as `No thread pool allocated`, check container CPU limits first. If the container sees the expected CPUs and x265 still underutilizes the host, compare the Fast and Faster profiles before adding encoder-specific threading parameters.

## Configuration Reference

Configuration is environment-variable based. In Docker, variables in `.env` are consumed by `docker-compose.yml`; only variables listed under `environment` are passed into the container.

### Compose Variables

| Variable | Belongs in | Required | Default | Purpose |
| --- | --- | --- | --- | --- |
| `MEDIA_ATLAS_IMAGE` | `.env` | No | `ghcr.io/jonhowe/media-atlas:latest` | Image tag to run. Use a release tag for production when available. |
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
| `MEDIA_ATLAS_TRANSCODE_MIN_FREE_BYTES` | `.env` via `docker-compose.yml`; optional local env var | `1073741824` | `1073741824` | Minimum free bytes required on the staging filesystem before starting an item. |
| `MEDIA_ATLAS_AUTH_MODE` | `.env` via `docker-compose.yml`; optional local env var | `single_admin` | `disabled` | Access mode: `disabled`, `single_admin`, or `reverse_proxy_trusted`. |
| `MEDIA_ATLAS_ADMIN_USERNAME` | `.env` via `docker-compose.yml`; optional local env var | `admin` | `admin` | Username for `single_admin` mode. |
| `MEDIA_ATLAS_ADMIN_PASSWORD` | `.env` via `docker-compose.yml`; optional local env var | empty | empty | Password for `single_admin` mode. Required when that mode is enabled. |
| `MEDIA_ATLAS_ADMIN_PASSWORD_HASH` | Optional env var | empty | empty | Optional PBKDF2 password hash in `pbkdf2_sha256$iterations$salt$hash` format. |
| `MEDIA_ATLAS_SESSION_SECRET` | `.env` via `docker-compose.yml`; optional local env var | empty | empty | HMAC secret for signed admin session cookies. Set a long random value in production. |
| `MEDIA_ATLAS_SESSION_TTL_SECONDS` | Optional env var | `43200` | `43200` | Admin session lifetime. Minimum is `300`. |
| `MEDIA_ATLAS_SESSION_COOKIE_SECURE` | `.env` via `docker-compose.yml`; optional local env var | `false` | `false` | Set `true` when serving Media Atlas over HTTPS. |
| `MEDIA_ATLAS_TRUSTED_USER_HEADER` | Optional env var | `X-Forwarded-User` | `X-Forwarded-User` | Header trusted in `reverse_proxy_trusted` auth mode. Only use behind a trusted proxy that strips client-supplied values. |
| `MEDIA_ATLAS_ALLOWED_ORIGINS` | `.env` via `docker-compose.yml`; optional local env var | loopback origins | loopback origins | Comma-separated CORS origins for browser clients. Same-origin production installs usually need no extra origins. |
| `MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN` | `.env` via `docker-compose.yml`; optional local env var | `false` | `false` | Explicit acknowledgement for binding `0.0.0.0` with auth disabled on a trusted LAN/VPN. |
| `MEDIA_ATLAS_FAIL_UNSAFE_BIND` | Optional env var | `false` | `false` | If `true`, startup fails instead of only warning for unsafe all-interface/no-auth config. |
| `MEDIA_ATLAS_READINESS_MIN_FREE_BYTES` | Optional env var | `268435456` | `268435456` | Minimum free bytes required for readiness disk checks. |
| `MEDIA_ATLAS_LOG_RETENTION_DAYS` | `.env` via `docker-compose.yml`; optional local env var | `30` | `30` | Deletes old app/transcode log files during retention cleanup. |
| `MEDIA_ATLAS_STAGED_OUTPUT_RETENTION_DAYS` | `.env` via `docker-compose.yml`; optional local env var | `0` | `0` | Deletes old quarantined partial retry outputs when greater than `0`; completed staged outputs are not removed automatically. |
| `MEDIA_ATLAS_PLEX_URL` | Optional `.env` via `docker-compose.yml`; optional local env var | empty | empty | Optional default Plex server URL. Can also be configured in Settings. |
| `MEDIA_ATLAS_PLEX_TOKEN` | Optional `.env` via `docker-compose.yml`; optional local env var | empty | empty | Optional default Plex token. Can also be configured in Settings; never returned in full by the API. |

Plex path mappings are configured in the Settings page, not by environment variable. In Docker, map Plex paths to container paths, for example `/mnt/media` to `/media`.

## Operations

Health endpoints:

```text
/api/health/live    process liveness
/api/health/ready   database, migrations, writable paths, disk, tools, Plex summary, and config warnings
/api/admin/status   UI-facing admin status payload
/api/admin/stats    lightweight JSON stats for monitoring integrations
```

Readiness returns HTTP `503` when required checks fail. The Docker healthcheck uses liveness so the container can stay up while readiness explains what needs attention.

### Access Modes

- `disabled`: no login. Safe for local loopback development. If bound to `0.0.0.0`, readiness warns unless `MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN=true`.
- `single_admin`: built-in admin login with signed HttpOnly session cookies. Recommended for LAN/VPN Compose installs.
- `reverse_proxy_trusted`: Media Atlas trusts `MEDIA_ATLAS_TRUSTED_USER_HEADER`. Use only behind a reverse proxy that authenticates users and strips client-supplied identity headers.

### Backups

Create a safe SQLite backup from the host:

```bash
docker compose exec media-atlas python - <<'PY'
from app import db
print(db.create_database_backup())
PY
```

You can also download a backup from the Admin Status page. Backups are written under `./data/backups`.

To restore, stop the app, copy the backup over `./data/media_inventory.sqlite`, keep the old file until you have verified startup, then start the app:

```bash
docker compose down
cp ./data/media_inventory.sqlite ./data/media_inventory.sqlite.before-restore
cp ./data/backups/media_inventory-YYYYMMDDTHHMMSSZ.sqlite ./data/media_inventory.sqlite
docker compose up -d
docker compose logs -f media-atlas
```

### Upgrade And Rollback

Upgrade:

```bash
docker compose pull
docker compose up -d
```

If you pin `MEDIA_ATLAS_IMAGE`, change it to the desired release tag before pulling. Database migrations run on startup and are reported in readiness and Admin Status.

Rollback:

```bash
# Edit .env and set:
# MEDIA_ATLAS_IMAGE=ghcr.io/jonhowe/media-atlas:<previous-tag>
docker compose pull
docker compose up -d
```

If the newer version ran migrations, restore the database backup created before the upgrade before rolling back.

### LAN/VPN Reverse Proxy

For a trusted LAN/VPN reverse proxy, terminate HTTPS at the proxy and forward to `http://media-atlas:8000`. Set:

```text
MEDIA_ATLAS_SESSION_COOKIE_SECURE=true
MEDIA_ATLAS_ALLOWED_ORIGINS=https://media-atlas.example.internal
```

If the proxy provides authentication, use `MEDIA_ATLAS_AUTH_MODE=reverse_proxy_trusted` and configure `MEDIA_ATLAS_TRUSTED_USER_HEADER`. The proxy must remove any incoming user identity header from clients before adding its own.

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
