# Changelog

This changelog summarizes the commits currently on `main`. New user-facing changes should be added under `Unreleased` before they are released.

## Unreleased

- Added transcode run cleanup for published items, including staged-output deletion, backup deletion, per-item cleanup status, and archive-on-success behavior.
- Added transcode run archive/unarchive controls and hid archived runs by default.
- Added transcode savings tracking with before/after file sizes, cumulative runtime, run/item counts, and total space saved.
- Added Intel iHD VAAPI runtime packages, `vainfo`, hardware-transcode verification docs, and image smoke checks for Linux Intel VAAPI / Quick Sync hosts.
- Replaced the light/dark text toggle with icon-only sun/moon controls.

## Main Branch History

### 2026-07-06

#### `63bdf7d` (`v0.4`) - Add publish progress details and theme toggle

- Added publish status, step, percent, byte, timing, and backup-path details to transcode run items.
- Added interrupted publish recovery so active publishes do not appear stuck after backend restart.
- Added a persisted light/dark theme toggle for the web UI.
- Converted the web UI styling to theme variables for light and dark palettes.

#### `21fef3c` (`v0.3`) - Add transcode backup directory support

- Added configurable transcode backup storage with `MEDIA_ATLAS_TRANSCODE_BACKUP_DIR`.
- Added `/app/transcode-backups` and `./transcode-backups` Compose defaults.
- Moved manual publish backups out of source media directories and into backup storage grouped by run/item.
- Added backup directory health/readiness reporting and settings exposure.
- Updated publish prompts and documentation to describe dedicated backup storage.

#### `f113c0a` (`v0.2`) - Add manual transcode publish action

- Added a two-confirmation publish action for verified staged transcode outputs.
- Added backend publish validation for exact source path, staged target path, verification status, duplicate publish state, and confirmation text.
- Added original-file backup recording on transcode run items.
- Added Transcode Runs UI actions and status display for published outputs.
- Updated README safety guidance for manual source replacement.

#### `94b5912` - Use release-only GHCR publishing

- Changed GHCR publishing to run only when a GitHub Release is published.
- Removed push and manual dispatch publishing paths from the GHCR workflow.
- Kept Docker build checks for pushes and PRs without pushing images.
- Updated README publishing guidance so `latest` tracks the latest published GitHub Release.

#### `d2b6150` - Add transcode plan archive and delete actions

- Added transcode plan archive and unarchive API actions.
- Added guarded transcode plan deletion for plans that have never been run.
- Hid archived transcode plans from the default planner view.
- Added a planner toggle to show archived plans and manage archived state.

#### `79b1907` - Enhance transcode plans API and app branding

- Expanded `/api/transcode-plans` with sample plan items, run counts, and latest run details.
- Added an authenticated first-run Plex setup flow for Plex URL, token, timeout, enabled state, and path mappings.
- Moved Existing Plans higher on the Transcode Planner page and added created time, involved files, and run history details.
- Added a Transcode Profiles documentation link from the planner.
- Added SVG favicon and app logo assets.
- Made the left sidebar sticky for long pages.

#### `cf2ddcf` - Add README banner and skip GHCR publish for docs-only pushes

- Added a generated Media Atlas banner image under `docs/assets`.
- Inserted the banner at the top of the README.
- Updated the GHCR publish workflow so docs-only pushes do not publish a container image.

#### `456f234` - Improve README structure and development docs

- Reworked the README around GHCR-first installation.
- Added a complete environment variable reference table.
- Moved development setup into `docs/DEVELOPMENT.md`.
- Moved transcode profile details into `docs/TRANSCODE_PROFILES.md`.
- Added first-run checklist and setup improvement suggestions.

#### `4610b26` - Add transcode run timing details

- Added created, started, stopped, and duration fields to transcode run views.
- Added per-item timing details for transcode run items.
- Improved run progress display for active and completed work.

### 2026-07-03

#### `478eafb` - Add hardware transcode profiles

- Added seeded HEVC profiles for Intel Quick Sync, NVIDIA NVENC, and VAAPI.
- Added preflight checks for required hardware device availability.
- Updated Docker and README guidance for hardware transcoding.
- Added smoke test coverage for profile availability.

#### `53d0948` - Add production-readiness foundations

- Added optional auth modes: disabled, single-admin, and trusted reverse proxy.
- Added signed HttpOnly session cookies, security headers, and CORS configuration.
- Added structured request logging with request IDs.
- Added migration tracking with `schema_migrations`.
- Added liveness, readiness, admin status, stats, and database backup endpoints.
- Added job recovery handling for scans, Plex syncs, and transcodes after restart.
- Added retry support for interrupted jobs and quarantine handling for partial staged outputs.
- Added retention controls for logs and quarantined staged outputs.
- Added backend smoke tests and CI gates before container publishing.

### 2026-07-02

#### `0a554d0` - Implement Plex integration

- Added optional read-only Plex integration using server URL and token settings.
- Added Plex sync jobs, library/item/part tables, and file match storage.
- Added Plex settings, connection test, library refresh, sync, sync job, cancel, retry, and unmatched APIs.
- Added exact normalized path matching with configurable Plex-to-Media Atlas path mappings.
- Added Plex metadata to library list and detail responses.
- Added Plex settings UI, sync job UI, dashboard status, library filters, and metadata display.
- Added transcode run progress refresh behavior.

#### `5be2543` - Expand Transcode Planner README details

- Expanded README documentation for transcode planning and staged execution.
- Added additional operational detail around profiles, plans, runs, and safety expectations.

#### `b32653b` - Clarify Docker installation and configuration docs

- Reworked Docker install documentation.
- Clarified Compose configuration, path mapping, persistent volumes, and useful commands.
- Improved production-oriented GHCR install instructions.

#### `d54e104` - Add Docker CI and GHCR publishing workflows

- Added Docker build check workflow.
- Added GHCR publish workflow with image tagging.
- Added Trivy scanning, SBOM, provenance, and GitHub Actions cache support.
- Added frontend UI refinements related to scan progress and recent scan detail.

#### `c415024` - Add Docker support

- Added `Dockerfile`, `docker-compose.yml`, `.dockerignore`, and `.env.docker.example`.
- Added Docker-oriented runtime defaults and volume paths.
- Updated README with container installation guidance.
- Tagged as `v0.1`.

#### `fa5a3fe` - Build the initial Media Atlas application

- Added backend FastAPI app, config loading, SQLite initialization, and API routes.
- Added scanning, file discovery, `ffprobe` metadata extraction, recommendations, reports, and staged transcode planning/execution services.
- Added frontend Vite/React app with dashboard, directories, scans, library, candidates, reports, planner, runs, and settings views.
- Added API client, frontend types, and base application styling.
- Added the formal project plan document.

#### `dce732b` - Initial commit

- Added repository baseline with `.gitignore`, `LICENSE`, and initial README.
