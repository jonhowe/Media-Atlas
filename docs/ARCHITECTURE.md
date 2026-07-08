# Architecture

Media Atlas is a local-first FastAPI, SQLite, and React application packaged as a single Docker image.

## Runtime Shape

- FastAPI serves both `/api/*` endpoints and the built frontend assets.
- SQLite stores media roots, scan jobs, normalized `ffprobe` metadata, Plex enrichment, transcode plans, transcode runs, publish state, cleanup state, and settings.
- `ffprobe` is used for scan metadata and output verification.
- `ffmpeg` is used only for app-generated transcode commands.
- One scan, one Plex sync, and one transcode worker are managed in-process.

## Storage

The Docker install bind-mounts persistent directories:

- `/app/data`: SQLite database and backups.
- `/app/reports`: generated reports and exports.
- `/app/logs`: app and transcode logs.
- `/app/transcode-staging`: staged transcode outputs.
- `/app/transcode-backups`: originals moved during manual publish.
- `/media`: source media root, read-only by default.

## Safety Model

Scans and Plex syncs are read-only. Transcodes write staged outputs only. Replacing an original requires the manual Publish action, two confirmations, a verified staged output, and backup storage. Published items must be marked validated before cleanup can delete staged outputs and backups.

## Authentication

Supported modes are `disabled`, `single_admin`, and `reverse_proxy_trusted`. Docker defaults to `single_admin`; disabling auth on `0.0.0.0` requires explicit LAN/VPN acknowledgement. Cookie-authenticated write APIs require CSRF tokens.

## Deployment

GHCR releases publish one image containing Python, the backend, the frontend build, FFmpeg, FFprobe, and Intel iHD VAAPI support. See `docs/DEPLOYMENT.md` and `docs/OPERATIONS.md` for install and runbook details.
