# Architecture

Media Atlas is a local-first FastAPI, SQLite, and React application packaged as a single Docker image.

## Runtime Shape

- FastAPI serves both `/api/*` endpoints and the built frontend assets.
- SQLite stores media roots, scan jobs, normalized `ffprobe` metadata, Plex enrichment, transcode plans/runs, retention connections, analysis snapshots, Plex watch evidence, immutable remediation audits, publish state, cleanup state, and settings.
- `ffprobe` is used for scan metadata and output verification.
- `ffmpeg` is used only for app-generated transcode commands.
- One scan, one Plex sync, one media-retention analysis, and one transcode worker are managed in-process.
- Structured application events are mirrored to stdout and a daily UTC-rotated JSONL file. The UI reads only managed application files under the configured logs directory; it never accepts an arbitrary log path.

## Retention Analysis Domain

Media retention review is independent from the housekeeping service that removes old logs and quarantined partial transcodes. It uses direct REST clients for one Seerr connection and any number of Sonarr/Radarr connections. Environment variables can seed one connection per service; stored UI connections take precedence and all returned credentials are redacted.

An analysis refreshes Plex library items/parts, pages through all Seerr requests, and reads each enabled Arr catalog and its managed files. Plex and Seerr are snapshot-critical: either source failing rejects the run. Each Arr instance is isolated; a failed instance contributes a warning and source-unavailable review results to the atomically published snapshot. Active jobs become `interrupted` after restart because partial snapshots are never published.

The review ledger groups movies as whole copies and TV by requested Seerr season. Sonarr episode-file season numbers associate managed files with those requests; per-file eligibility uses the newer of its season request and file-added timestamps. Each file records a stable ready, waiting, protected, attention, or non-actionable decision. Title, scope, file, requester, routing, mapping, and Plex history evidence are immutable snapshots tied to the analysis job.

Whole-copy deletion candidates remain a parallel safety model grouped by owning Arr movie or whole series. Full path coverage, aggregate Seerr availability, the newest request/file timestamp, and zero qualifying plays are still required. A season-level review result can create a scoped draft transcode plan but can never authorize Sonarr deletion.

The optional scheduler is disabled by default. When enabled it triggers once at the configured server-local time, does not catch up after downtime, and uses the same no-overlap guard as manual analyses.

## Storage

The Docker install bind-mounts persistent directories:

- `/app/data`: SQLite database and backups.
- `/app/reports`: generated reports and exports.
- `/app/logs`: daily application JSONL logs and per-item transcode logs, governed by log retention.
- `/app/transcode-staging`: staged transcode outputs.
- `/app/transcode-backups`: originals moved during manual publish.
- `/media`: source media root, read-only by default.

## Safety Model

Scans, analysis, and Plex syncs are read-only. Transcodes write staged outputs only. Replacing an original requires the manual Publish action, two confirmations, a verified staged output, and backup storage. Published items must be marked validated before cleanup can delete staged outputs and backups.

The primary planner limits selection to one actionable recommendation category at a time. Profile suggestions are advisory, but mismatched overrides are explicit and review-only plans cannot start because they contain no generated commands.

Retention remediation is also manual. Transcode handoff creates an existing-workflow draft plan and never starts it. Deletion accepts one active candidate, requires exact `DELETE <title>` text, refreshes Plex, and revalidates every external fact before calling the owning Arr API. Radarr and Sonarr delete files without adding import exclusions. Media Atlas never deletes local inventory rows, bulk-deletes candidates, clears Seerr data, or deletes Seerr request history. Every attempt is audited; ambiguous Arr timeouts become `unknown` and block retry until inspected.

## Authentication

Supported modes are `disabled`, `single_admin`, and `reverse_proxy_trusted`. Docker defaults to `single_admin`; disabling auth on `0.0.0.0` requires explicit LAN/VPN acknowledgement. Cookie-authenticated write APIs require CSRF tokens.

## Deployment

GHCR releases publish one image containing Python, the backend, the frontend build, FFmpeg, FFprobe, and Intel iHD VAAPI support. See `docs/DEPLOYMENT.md` and `docs/OPERATIONS.md` for install and runbook details.
