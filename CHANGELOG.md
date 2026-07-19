# Changelog

This changelog summarizes released commits on `main` plus current unreleased branch work. New user-facing changes should be added under `Unreleased` before they are released.

## Unreleased

- Expanded retention analysis with an exhaustive decision ledger, requested-season and per-file TV eligibility, partially available season support, scoped transcode planning, comprehensive result filters/CSV, and unchanged whole-copy deletion safeguards.
- Added SQLite migration `0009_retention_evaluation_ledger` with compatible legacy snapshot backfill.
- Added an authenticated version endpoint and a bottom-pinned sidebar release tag with a silent unavailable fallback.
- Fixed release image builds to embed and verify the GitHub release tag as application version metadata.
- Added a reproducible synthetic documentation dataset and a ten-screen Web UI visual tour.
- Added a centralized Logs page for filtered application events, live transcode output, and scan diagnostics. Application logs now persist as daily UTC-rotated JSONL files under the configured logs directory while continuing to stream to stdout.
- Expanded the Transcode Planner to support Easy Win, Remux Only, and Review workflows with search, pagination, page selection, category-specific profile suggestions, override warnings, direct links from Quality Candidates, and clear review-only plan state.
- Fixed Plex TV-library syncs to fetch episode media parts so scanned episode files can be path-matched and enriched.
- Added Media Retention Review, correlating paginated Seerr requests, multiple Sonarr/Radarr instances, Media Atlas inventory, and Plex history into atomic whole-movie/whole-series candidate snapshots.
- Added redacted retention connection management, per-service path mappings and Seerr service IDs, an optional disabled-by-default 03:00 schedule, source warnings, CSV export, responsive Retention UI, dashboard metrics, and analysis/action history.
- Added guarded transcode-plan handoff and single-candidate Arr deletion with typed confirmation, fresh source revalidation, no import exclusions, non-clearing Seerr reconciliation, ambiguous-timeout handling, and immutable remediation audits.
- Added SQLite migration `0008_media_retention_review`; rollback to an older image should restore the pre-upgrade database backup. Added optional Seerr/Sonarr/Radarr environment seeds and deployment/operations guidance.
- Fixed Release Automator backend validation to use an isolated uv environment with the project requirements installed.
- Integrated Release Automator v0.3.0 with frozen planning, protected execution, split read/write credentials, and resumable GitHub publication workflows.
- Added responsive mobile navigation and improved small-screen table layouts.
- Clarified README Docker configuration parameters.
- Hardened GitHub Actions caching with an explicit BuildKit scope, non-fatal cache exports, release cache reuse, and automatic closed-PR cache cleanup.
- Added repository publication instructions for scoped pull request and release automation.
- Added Admin Status version/build metadata and a redacted diagnostics JSON export.
- Added a Compose/env doctor script plus deployment and operations runbooks.
- Changed the Docker image default auth mode to `single_admin` for safer LAN/VPN installs.
- Added CSRF protection for cookie-authenticated write APIs and basic login rate limiting.
- Added explicit published-item validation before staged/backup artifact cleanup.
- Added performance indexes for common library, Plex, scan, and transcode history queries.
- Added Compose override examples for writable publish mode, Intel VAAPI, NVIDIA NVENC, and trusted reverse proxy auth.
- Added Dependabot configuration for npm, pip, Docker, and GitHub Actions updates.
- Added repo hygiene ignores for `.DS_Store` and `transcode-backups`, and archived the original project plan under `docs/archive`.
- Clarified Intel Linux hardware-transcoding guidance to recommend `HEVC VAAPI` first when `/dev/dri` and the Intel iHD driver are available.
- Documented that `HEVC Quick Sync` / `hevc_qsv` may still fail depending on FFmpeg QSV runtime support, even when VAAPI works.

## Main Branch History

### 2026-07-07

#### `8a83c84` (`v0.7`) - Add Intel iHD VAAPI support

- Added Intel iHD VAAPI/media runtime packages, `vainfo`, VA-API libraries, and oneVPL/QSV runtime libraries to the Docker image.
- Set the container default `LIBVA_DRIVER_NAME=iHD` for Intel iGPUs, including Raptor Lake and newer.
- Added image smoke checks for `vainfo`, `iHD_drv_video.so`, FFmpeg/ffprobe, and the iHD driver environment.
- Updated Docker Compose comments, README configuration docs, and transcode profile docs for `/dev/dri`, Intel VAAPI verification, and NVENC requirements.

#### `1e1ab7d` (`v0.6`) - Add transcode savings tracking

- Added source/output byte tracking for transcode run items.
- Added `/api/transcode-runs/stats` with cumulative run count, item count, runtime, before/after size, and total space saved.
- Added Dashboard and Transcode Runs savings panels.
- Added per-run and per-item before/after/saved details.
- Updated tests and README coverage for savings tracking.

#### `66be1e0` (`v0.5`) - Add transcode run cleanup and archiving

- Added cleanup for published transcode items, including staged-output deletion and backup deletion.
- Added per-item cleanup status, cleanup messages, cleanup timestamps, staged deletion timestamps, and backup deletion timestamps.
- Added transcode run archive/unarchive actions and hid archived runs by default.
- Added a guarded cleanup flow that requires confirmation plus typing `DELETE ARTIFACTS`.
- Replaced the light/dark text toggle with icon-only sun/moon controls.

### 2026-07-06

#### `e7e9ba4` - Update changelog structure

- Added the current changelog structure with an `Unreleased` section and backfilled main branch history through `v0.4`.
- Documented the transcode plan archive/unarchive and guarded delete work already present on `main`.

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
