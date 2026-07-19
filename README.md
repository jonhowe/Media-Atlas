![Media Atlas banner](docs/assets/media-atlas-banner.png)

# Media Atlas

Media Atlas is a local-first media inventory, retention-review, and transcode execution web app. It scans configured media roots with `ffprobe`, stores technical metadata in SQLite, enriches library rows with optional Plex metadata, correlates requested-but-unwatched media across Seerr and Arr services, provides searchable reporting, generates transcode plans, and can run staged `ffmpeg` jobs.

Media Atlas stages transcode output separately from originals by default. Replacing a source file is only available through an explicit manual publish action for verified outputs, requires two confirmations, and moves the original into transcode backup storage first.

## Capabilities

- Configure one or more local or mounted media roots.
- Recursively scan media files and skip unchanged files on rescans.
- Store raw `ffprobe` JSON plus normalized codec, container, stream, bitrate, duration, HDR, subtitle, and audio fields.
- Optionally sync Plex libraries for title, year, show/season/episode, collection, genre, label, watched state, and match status.
- Correlate Seerr requests, Sonarr/Radarr-managed files, and Plex history to report whole movies or series that nobody has watched after a configurable waiting period.
- Export retention candidates, create a transcode plan without starting it, or delete one freshly revalidated copy through its owning Arr service with a typed confirmation and immutable audit trail.
- Browse, search, filter, report, and export inventory data.
- Classify files as Easy Win, Remux Only, Review, Skip, Already Modern, Error, or Missing.
- Generate staged transcode plans from candidates.
- Plan Easy Win, Remux Only, and Review files from one searchable, paginated workflow with profile guidance.
- Start and monitor one server-side transcode run at a time.
- Inspect persisted application logs, live transcode output, and scan errors from the Logs page.
- Close and reopen the browser while jobs continue, as long as the backend process remains running.
- Manually publish verified staged outputs back to the original path after two confirmations.

## Web UI Tour

See the [Web UI visual tour](docs/WEB_UI.md) for a task-oriented walkthrough of the Dashboard, Library, retention review, planner, runs, scans, logs, reports, and Admin Status using reproducible synthetic data.

## Production Install

Recommended install uses the published GHCR image. The host only needs Docker Compose and access to a local or mounted media directory.

For production, prefer a pinned release tag once one is available. `latest` is convenient for first installs and quick updates, but pinned tags make rollback straightforward.

Create an install directory:

```bash
mkdir -p ~/media-atlas
cd ~/media-atlas
```

Download the example Compose and environment files:

```bash
curl -fsSLo docker-compose.yml https://raw.githubusercontent.com/jonhowe/Media-Atlas/main/docker-compose.yml
curl -fsSLo .env https://raw.githubusercontent.com/jonhowe/Media-Atlas/main/.env.docker.example
curl -fsSLo media-atlas-doctor.sh https://raw.githubusercontent.com/jonhowe/Media-Atlas/main/scripts/media-atlas-doctor.sh
```

Edit `.env` and set at least:

- `MEDIA_ATLAS_MEDIA_ROOT` to the host path that contains your media.
- `MEDIA_ATLAS_ADMIN_PASSWORD` to a real password.
- `MEDIA_ATLAS_SESSION_SECRET` to a long random value.
- `MEDIA_ATLAS_ALLOWED_ORIGINS` to the browser origin you will use, for example `http://192.168.1.106:8000`.

You can also create the files manually. A minimal `.env` looks like this:

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

A matching `docker-compose.yml` looks like this:

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
      MEDIA_ATLAS_TRANSCODE_BACKUP_DIR: "/app/transcode-backups"
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
      MEDIA_ATLAS_SEERR_URL: "${MEDIA_ATLAS_SEERR_URL:-}"
      MEDIA_ATLAS_SEERR_API_KEY: "${MEDIA_ATLAS_SEERR_API_KEY:-}"
      MEDIA_ATLAS_SONARR_URL: "${MEDIA_ATLAS_SONARR_URL:-}"
      MEDIA_ATLAS_SONARR_API_KEY: "${MEDIA_ATLAS_SONARR_API_KEY:-}"
      MEDIA_ATLAS_SONARR_SEERR_SERVICE_ID: "${MEDIA_ATLAS_SONARR_SEERR_SERVICE_ID:-}"
      MEDIA_ATLAS_SONARR_PATH_MAPPINGS: "${MEDIA_ATLAS_SONARR_PATH_MAPPINGS:-}"
      MEDIA_ATLAS_RADARR_URL: "${MEDIA_ATLAS_RADARR_URL:-}"
      MEDIA_ATLAS_RADARR_API_KEY: "${MEDIA_ATLAS_RADARR_API_KEY:-}"
      MEDIA_ATLAS_RADARR_SEERR_SERVICE_ID: "${MEDIA_ATLAS_RADARR_SEERR_SERVICE_ID:-}"
      MEDIA_ATLAS_RADARR_PATH_MAPPINGS: "${MEDIA_ATLAS_RADARR_PATH_MAPPINGS:-}"
    volumes:
      - ./data:/app/data
      - ./reports:/app/reports
      - ./logs:/app/logs
      - ./transcode-staging:/app/transcode-staging
      - ./transcode-backups:/app/transcode-backups
      - ${MEDIA_ATLAS_MEDIA_ROOT:?Set MEDIA_ATLAS_MEDIA_ROOT in .env}:/media:ro
```

Start the app:

```bash
bash media-atlas-doctor.sh
docker compose pull
docker compose up -d
```

Open:

```text
http://SERVER_IP:8000/
```

Useful commands:

```bash
bash media-atlas-doctor.sh
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

Generate an admin password hash instead of storing a cleartext password:

```bash
docker run --rm -it ghcr.io/jonhowe/media-atlas:latest python /app/scripts/generate-password-hash.py
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
./transcode-backups   originals moved during manual publish
```

Source media is mounted read-only in the default Compose file. Staged transcode output is written separately. The manual publish action requires the media mount to be writable; keep the default read-only mount unless you intentionally want Media Atlas to replace originals.

See [Deployment Guide](docs/DEPLOYMENT.md) for known-good Compose usage and optional overrides for writable publish mode, Intel VAAPI, NVIDIA NVENC, and reverse proxy auth.

## Logs

The Logs page provides three authenticated views:

- **Application** tails structured application events with level, logger-prefix, and text filters. These JSONL events are also written to stdout for `docker compose logs`.
- **Transcodes** follows the latest FFmpeg output for a selected run item. Log actions on Transcode Runs open the corresponding item directly.
- **Scans** shows current scan state plus stored paths, messages, exit codes, and `ffprobe` stderr for failed files.

Application logs are written under `./logs/application` in a standard Compose install and rotate daily at UTC midnight. Rotated application logs and transcode logs are removed by the existing `MEDIA_ATLAS_LOG_RETENTION_DAYS` housekeeping setting; the active application log is never removed while Media Atlas is using it.

## Publishing Transcoded Outputs

Completed transcode items can be published from Transcode Runs when the item succeeded and output verification passed. Publishing copies the staged output over the original source path after moving the original file into transcode backup storage.

By default, the backup directory is a peer of the staging directory: `/app/transcode-backups` beside `/app/transcode-staging` in Docker, bind-mounted to `./transcode-backups` in the install directory. Backups are grouped by run and item, and the exact backup path is recorded on the transcode run item.

The Transcode Runs page shows publish status, current step, percentage, copied/moved bytes, start time, stop time, duration, and final backup path for each item. It also records source size, staged output size, per-item savings, total transcode run count, total transcode runtime, and cumulative storage saved across completed transcodes.

The UI requires two confirmations:

1. Confirm that you want to publish the staged output to the original location.
2. Type `REPLACE` to confirm replacement of the live source file.

The backend also requires the exact source path, staged output path, and confirmation text before it will publish. Publish fails if the staged output is missing, the item is not verified, the item was already published, or the original file/location is not writable.

After you validate a published file in your media library, the Transcode Runs page can clean up artifacts for published items. Cleanup deletes the staged output and the original-file backup from transcode backup storage, records per-item cleanup status, and archives the run when cleanup succeeds. The UI requires confirmation plus typing `DELETE ARTIFACTS` before deleting these files.

Published items must be marked validated before cleanup is available. This keeps cleanup tied to your explicit confirmation that the replacement is working in the live library.

## First Run Checklist

After the container is running:

1. Open the UI and log in with `MEDIA_ATLAS_ADMIN_USERNAME` and `MEDIA_ATLAS_ADMIN_PASSWORD`.
2. Add media roots using container paths such as `/media`, `/media/Movies`, or `/media/TV`.
3. Start a scan from the Scans page and watch progress from the dashboard or scan detail view.
4. Optional: configure Plex in Settings, add path mappings from Plex host paths to container paths, then run a manual Plex sync.
5. Optional: configure Seerr and each Sonarr/Radarr instance in Settings, including the Seerr service ID and any service-to-container path mappings, then run Retention analysis.
6. Review Library, Quality Candidates, Retention, Reports, and Transcode Planner before starting staged transcode runs or taking a deletion action.

## Media Retention Review

Retention analysis is separate from log and staged-output housekeeping. Every evaluated Seerr request remains visible with a reason. Movies are reviewed as whole copies; TV requests are reviewed by requested season and file, including present files from partially available seasons. Each file becomes review-ready after the configured waiting period measured from the newer of its season request or Arr file-added date, exact Plex and Media Atlas mapping, and no Plex play by any account since that date. Recent episodes do not postpone older requested-season files. A Plex item with multiple versions protects every associated copy when it has qualifying play evidence.

Analysis runs only on demand unless the disabled-by-default daily schedule is enabled in Settings. Plex or Seerr failure rejects the new snapshot. A failed Arr instance produces source warnings and visible source-unavailable results. Waiting, watched, unmatched, unrouted, and incomplete-mapping results remain available through filters and the comprehensive CSV export. Review-ready files can be handed to the existing Transcode Planner without starting a run.

Deletion remains a separate whole-copy safeguard and is always one candidate at a time. TV deletion still requires every managed series file to pass the original whole-series age, mapping, availability, and play checks; season-level review never enables season deletion. The UI asks for confirmation and exact `DELETE <title>` text, then the backend refreshes Plex and revalidates Seerr requests, Arr IDs/files/sizes, mappings, dates, and play history. Radarr deletes the movie with files and without adding an import exclusion; Sonarr deletes the whole series with files and without adding an import-list exclusion. Media Atlas never unlinks inventory rows directly, never clears Seerr request data, and never starts a transcode automatically. If Arr deletion succeeds but Seerr reconciliation fails, the successful deletion is audited with a warning and only the non-clearing Seerr status update can be retried.

## Transcode Profiles

Media Atlas includes remux, software HEVC, hardware HEVC, H.264 compatibility, and manual-review profiles. `HEVC Archive Fast` is the recommended default software encode profile; hardware profiles require host/container device support.

The planner supports one recommendation category per plan. It suggests `HEVC Archive Fast` for Easy Win, `Remux to MKV` for Remux Only, and `Manual Review Only` for Review. You can deliberately override a suggestion after reading the inline compatibility warning. Review-only plans contain no runnable commands and remain useful as tracking plans.

See [Transcode Profiles](docs/TRANSCODE_PROFILES.md) for the full profile table, GPU/VAAPI Compose snippets, CPU checks, and x265 tuning notes.

## Configuration Reference

Configuration is environment-variable based. In Docker, variables in `.env` are consumed by `docker-compose.yml`; only variables listed under `environment` are passed into the container.

### Mandatory Fields

Set these intentionally for the recommended GHCR/Compose install. `MEDIA_ATLAS_ADMIN_PASSWORD` can be replaced by `MEDIA_ATLAS_ADMIN_PASSWORD_HASH` if you prefer hashed secret storage.

| Variable | Short description | Set ideally | Details |
| --- | --- | --- | --- |
| `MEDIA_ATLAS_MEDIA_ROOT` | Host media root | `.env` | Compose-only variable for the host path mounted into the container at `/media`. Required for normal Docker installs. |
| `MEDIA_ATLAS_AUTH_MODE` | Access mode | `.env` | Access mode for UI/API. Supported values: `disabled`, `single_admin`, `reverse_proxy_trusted`. Recommended Docker default: `single_admin`. |
| `MEDIA_ATLAS_ADMIN_PASSWORD` | Admin password | `.env` | Password used by `single_admin` auth mode. Required if `MEDIA_ATLAS_AUTH_MODE=single_admin` unless `MEDIA_ATLAS_ADMIN_PASSWORD_HASH` is used. Do not commit real values. |
| `MEDIA_ATLAS_SESSION_SECRET` | Session signing secret | `.env` | HMAC secret for signed admin session cookies. Set a long random value in production. If omitted, sessions use an ephemeral secret and reset on restart. |
| `MEDIA_ATLAS_ALLOWED_ORIGINS` | CORS origins | `.env` | Comma-separated browser origins allowed for API calls. Same-origin Docker installs usually use `http://127.0.0.1:8000,http://localhost:8000`; LAN or reverse proxy installs should include the actual browser origin. |

### Optional Fields

Most installs can leave these at the example defaults. Adjust them when changing ports, paths, auth mode, retention, hardware acceleration, Plex defaults, or local development behavior.

| Variable | Short description | Set ideally | Details |
| --- | --- | --- | --- |
| `MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN` | Acknowledge no-auth LAN bind | `.env` | Set to `true` only when `MEDIA_ATLAS_HOST=0.0.0.0` and `MEDIA_ATLAS_AUTH_MODE=disabled` are intentional for a trusted LAN/VPN. Without it, readiness warns. Default: `false`. |
| `MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS` | Directory browser roots | `.env` | Comma-separated list of server-visible root paths the UI directory browser may browse. In Docker, use container paths such as `/media`, not host paths such as `/mnt/media`. Default in Compose: `/media`. |
| `MEDIA_ATLAS_ALLOW_LAN` | LAN mode hint | advanced env | Informational flag derived from the bind host unless overridden. It is not the main security control; use auth settings for access control. |
| `MEDIA_ATLAS_ADMIN_PASSWORD_HASH` | Admin password hash | advanced env or secret manager | Alternative to `MEDIA_ATLAS_ADMIN_PASSWORD`. Use a PBKDF2 hash in `pbkdf2_sha256$iterations$salt$hash` format if you do not want a cleartext password in `.env`. |
| `MEDIA_ATLAS_ADMIN_USERNAME` | Admin username | `.env` | Username for `single_admin` auth mode. Default: `admin`. |
| `MEDIA_ATLAS_BASE_DIR` | Local default base path | local dev `.env` | Base path used to derive default local `data`, `reports`, `logs`, and `transcode-staging` directories. Usually not used in Docker because explicit container paths are set. |
| `MEDIA_ATLAS_DATABASE_PATH` | SQLite database file | advanced env | Full SQLite path. Usually leave unset so the app uses `MEDIA_ATLAS_DATA_DIR/media_inventory.sqlite`. Useful only for unusual layouts or migration testing. |
| `MEDIA_ATLAS_DATA_DIR` | App data directory | `docker-compose.yml` environment | Directory containing SQLite data and backups. Docker default: `/app/data`, normally bind-mounted to `./data`. |
| `MEDIA_ATLAS_DIRECTORY_BROWSER_ENABLED` | Enable directory browser | `.env` | Enables or disables the server-side directory browser. Set `false` if you want users to type root paths manually. Default: `true`. |
| `MEDIA_ATLAS_FAIL_UNSAFE_BIND` | Fail unsafe no-auth bind | advanced env | If `true`, startup fails when bound to all interfaces with auth disabled and no acknowledgement. If `false`, readiness reports a warning instead. Default: `false`. |
| `MEDIA_ATLAS_FFMPEG_PATH` | `ffmpeg` command path | advanced env | Command or absolute path used for transcode execution. Docker image default: `ffmpeg`. Local installs require it on `PATH` unless overridden. |
| `MEDIA_ATLAS_FFMPEG_TIMEOUT_SECONDS` | Transcode timeout | `.env` | Global `ffmpeg` timeout for each item. `0` means no timeout, which is the normal default for long encodes. |
| `MEDIA_ATLAS_FFPROBE_PATH` | `ffprobe` command path | advanced env | Command or absolute path used for media metadata probing and output verification. Docker image default: `ffprobe`. |
| `MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS` | Probe timeout | `.env` | Per-file `ffprobe` timeout during scans. Minimum enforced value is `5`. Default: `60`. |
| `MEDIA_ATLAS_HOST` | Backend bind host | `docker-compose.yml` environment | Backend bind address. Docker should use `0.0.0.0` so port publishing works. Local development should usually use `127.0.0.1`. |
| `MEDIA_ATLAS_IMAGE` | Container image tag | `.env` | Compose-only variable used by `docker-compose.yml` to choose the GHCR image. Use `latest` for convenience or a pinned release tag for production rollback safety. |
| `LIBVA_DRIVER_NAME` | VAAPI driver name | Docker image default or `.env` override | VAAPI driver selected by libva. The Docker image defaults to `iHD` for Intel iGPUs, including Raptor Lake and newer. Override or unset it only for non-Intel VAAPI hardware. |
| `MEDIA_ATLAS_LOG_RETENTION_DAYS` | Log retention days | `.env` | Number of days to keep old app/transcode log files when retention cleanup runs. Set `0` to disable log deletion. Default: `30`. |
| `MEDIA_ATLAS_LOGS_DIR` | Logs directory | `docker-compose.yml` environment | Directory for app and transcode logs. Docker default: `/app/logs`, normally bind-mounted to `./logs`. |
| `MEDIA_ATLAS_MARK_MISSING_FILES` | Mark missing files | `.env` | If `true`, rescans mark previously seen files as missing when the root is available but the file is gone. Unavailable roots are skipped so whole libraries are not marked missing accidentally. |
| `MEDIA_ATLAS_PLEX_TOKEN` | Plex token default | `.env` or UI Settings | Optional default Plex token. It can also be saved from the Settings page. The API returns only redacted token state. |
| `MEDIA_ATLAS_PLEX_URL` | Plex server URL default | `.env` or UI Settings | Optional default Plex server URL, for example `http://192.168.1.106:32400`. It can also be saved from the Settings page. |
| `MEDIA_ATLAS_SEERR_URL` / `MEDIA_ATLAS_SEERR_API_KEY` | Default Seerr connection | `.env` or UI Settings | Seeds the single Seerr connection when no stored Seerr connection exists. Secrets are write-only in API/UI responses. |
| `MEDIA_ATLAS_SONARR_URL` / `MEDIA_ATLAS_SONARR_API_KEY` | Default Sonarr connection | `.env` or UI Settings | Seeds one Sonarr connection. Add additional standard/4K instances in the UI. |
| `MEDIA_ATLAS_SONARR_SEERR_SERVICE_ID` | Sonarr service ID | `.env` or UI Settings | Matches Seerr requests to the seeded Sonarr instance. Required to disambiguate multiple Sonarr instances. |
| `MEDIA_ATLAS_SONARR_PATH_MAPPINGS` | Sonarr path mappings | `.env` or UI Settings | Semicolon-delimited `source=container` pairs, for example `/tv=/media/TV;/tv4k=/media/TV 4K`. |
| `MEDIA_ATLAS_RADARR_URL` / `MEDIA_ATLAS_RADARR_API_KEY` | Default Radarr connection | `.env` or UI Settings | Seeds one Radarr connection. Add additional standard/4K instances in the UI. |
| `MEDIA_ATLAS_RADARR_SEERR_SERVICE_ID` | Radarr service ID | `.env` or UI Settings | Matches Seerr requests to the seeded Radarr instance. Required to disambiguate multiple Radarr instances. |
| `MEDIA_ATLAS_RADARR_PATH_MAPPINGS` | Radarr path mappings | `.env` or UI Settings | Semicolon-delimited `source=container` pairs, for example `/movies=/media/Movies`. |
| `MEDIA_ATLAS_PORT` | HTTP port | `.env` and `docker-compose.yml` | In `.env`, controls the host port published by Compose. Inside the container, Compose passes `8000` as the application port. Local dev can set this directly. |
| `MEDIA_ATLAS_READINESS_MIN_FREE_BYTES` | Readiness disk threshold | advanced env | Minimum free bytes required for readiness disk checks. Default: `268435456` bytes. This is separate from transcode preflight free-space checks. |
| `MEDIA_ATLAS_REPORTS_DIR` | Reports directory | `docker-compose.yml` environment | Directory for generated reports/exports. Docker default: `/app/reports`, normally bind-mounted to `./reports`. |
| `MEDIA_ATLAS_SCAN_CONCURRENCY` | Scan concurrency | `.env` | Maximum concurrent `ffprobe` tasks during scans. Default: `2`. Increase carefully on slow disks or network mounts. |
| `MEDIA_ATLAS_SESSION_COOKIE_NAME` | Session cookie name | advanced env | Cookie name for signed admin sessions. Default: `media_atlas_session`. Change only if you run multiple Media Atlas instances on the same browser origin. |
| `MEDIA_ATLAS_SESSION_COOKIE_SECURE` | Secure cookie flag | `.env` | Set to `true` when serving Media Atlas over HTTPS. Keep `false` for plain HTTP LAN testing. |
| `MEDIA_ATLAS_SESSION_TTL_SECONDS` | Session lifetime | advanced env | Admin session lifetime in seconds. Minimum enforced value is `300`. Default: `43200`. |
| `MEDIA_ATLAS_STAGED_OUTPUT_RETENTION_DAYS` | Partial output retention | `.env` | Deletes old quarantined partial retry outputs when greater than `0`. Completed staged outputs are not removed automatically. Default: `0`. |
| `MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_PERCENT` | Verification tolerance percent | `.env` | Percent-based duration tolerance for output verification. Default: `0.02`. The app uses the larger of this value and the seconds tolerance. |
| `MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_SECONDS` | Verification tolerance seconds | `.env` | Minimum absolute duration tolerance for output verification. Default: `3`. |
| `MEDIA_ATLAS_TRANSCODE_MIN_FREE_BYTES` | Transcode free-space floor | `.env` | Minimum free bytes required on the staging filesystem before starting a transcode item. Default: `1073741824` bytes. |
| `MEDIA_ATLAS_TRANSCODE_BACKUP_DIR` | Original-file backup directory | `docker-compose.yml` environment | Directory where originals are moved before a verified staged output is manually published over the source path. Defaults to a peer directory beside `MEDIA_ATLAS_TRANSCODE_STAGING_DIR`, usually `/app/transcode-backups` in Docker. The example Compose file bind-mounts this to `./transcode-backups`. |
| `MEDIA_ATLAS_TRANSCODE_OUTPUT_ROOT` | Alternate staging host path | `.env` with Compose edit | Compose-only helper for mounting staged output somewhere other than `./transcode-staging`. Requires uncommenting/adding the matching volume line in `docker-compose.yml`. |
| `MEDIA_ATLAS_TRANSCODE_STAGING_DIR` | Staged output directory | `docker-compose.yml` environment | Container/app path for staged transcode outputs. Docker default: `/app/transcode-staging`, normally bind-mounted to `./transcode-staging`. |
| `MEDIA_ATLAS_TRUSTED_USER_HEADER` | Trusted proxy user header | reverse proxy env | Header trusted in `reverse_proxy_trusted` auth mode. Use only behind a proxy that authenticates users and strips any client-supplied copy. Default: `X-Forwarded-User`. |

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

### Compose Environment Changes

Docker Compose reads `.env` when it creates or updates the container environment. If you change settings such as `MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN`, run:

```bash
docker compose up -d
```

Refreshing the browser does not reload backend configuration, and `docker compose restart` may keep the old container environment. To confirm what Compose will pass into the container, run:

```bash
docker compose config | grep MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN
```

The Admin Status page also shows the parsed runtime configuration loaded by the running backend.

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

## Development

Source-based development setup is documented separately to keep the normal install path focused on GHCR and Docker Compose.

See [Development Install](docs/DEVELOPMENT.md).

## Operations

Upgrade, rollback, backup/restore, environment troubleshooting, and publish recovery are covered in [Operations Runbook](docs/OPERATIONS.md).

For a concise technical map of the app, see [Architecture](docs/ARCHITECTURE.md).

## Publishing

GitHub Actions publishes images to GHCR.

- Repository publication uses the manually approved Release Automator plan and execute workflows before a GitHub Release is created.
- GHCR publishing runs only when a GitHub Release is published.
- Pushing to `main` runs validation workflows but does not publish or retag the container image.
- `ghcr.io/jonhowe/media-atlas:latest` tracks the latest published GitHub Release.
- Release tags publish matching image tags, for example `ghcr.io/jonhowe/media-atlas:v0.1.0`.
- Every published image also gets a commit-pinned `sha-<commit>` tag.
- Builds use GitHub Actions layer caching, Trivy scanning, SBOM attestations, and provenance attestations.

Use the [Release Checklist](docs/RELEASE_CHECKLIST.md) before publishing a GitHub Release.
