# Changelog

All notable Media Atlas changes should be recorded here.

## Unreleased

- Added single-admin and trusted reverse proxy auth modes.
- Added signed HttpOnly session cookies and security headers.
- Added structured request logging with request IDs.
- Added ordered SQLite migration tracking through `schema_migrations`.
- Added liveness, readiness, admin status, metrics, and database backup endpoints.
- Added startup recovery for queued scans, Plex syncs, and transcode runs.
- Added interrupted-job retry actions and transcode preflight checks.
- Added quarantine-on-retry for partial staged transcode outputs.
- Added conservative retention controls for logs and quarantined partial outputs.
- Added backend migration smoke tests and CI gates before container publish.

