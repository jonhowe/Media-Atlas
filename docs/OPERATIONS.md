# Operations Runbook

## Backup

Use the Admin Status **Download database backup** button, or run:

```bash
docker compose exec media-atlas python -c "from app import db; print(db.create_database_backup())"
```

Backups are written under `./data/backups` in a standard Compose install. Copy that SQLite file somewhere outside the install directory before upgrades.

## Restore

1. Stop Media Atlas:

   ```bash
   docker compose down
   ```

2. Move the current database aside and copy the backup into place:

   ```bash
   mv ./data/media_inventory.sqlite ./data/media_inventory.sqlite.broken
   cp ./data/backups/media_inventory-YYYYMMDDTHHMMSSZ.sqlite ./data/media_inventory.sqlite
   ```

3. Start Media Atlas:

   ```bash
   docker compose up -d
   ```

4. Open Admin Status and confirm migrations, database, paths, and tools are OK.

## Upgrade

For production, pin `MEDIA_ATLAS_IMAGE` to a release tag instead of `latest`.

```bash
docker compose pull
docker compose up -d
```

Take a database backup before upgrading when release notes mention migrations. After the upgrade, check Admin Status readiness and download diagnostics if anything is degraded.

## Rollback

1. Set `MEDIA_ATLAS_IMAGE` in `.env` to the previous known-good release tag.
2. Restore the pre-upgrade database backup if the newer release ran migrations.
3. Run:

   ```bash
   docker compose pull
   docker compose up -d --force-recreate
   ```

Migration `0008_media_retention_review` only adds retention connection, job, candidate/file, Plex evidence, and remediation-audit tables. It does not rewrite existing media, Plex, scan, or transcode rows. SQLite migrations are forward-only; rolling back to an older image after `0008` should still use the pre-upgrade database backup so schema and application expectations remain aligned. Retention API keys saved through the UI live in SQLite, so protect database backups as secrets.

## Retention Analysis Runbook

Before the first analysis, confirm Plex is enabled and synced, one Seerr connection is enabled, and every Arr instance has the correct Seerr service ID and path mappings. Use the connection Test buttons in Settings.

- Plex or Seerr failure fails the analysis and preserves the previous successful snapshot.
- A single Arr failure completes with warnings and excludes that instance from the new snapshot. Old candidates from that instance do not carry forward.
- Incomplete file mapping appears under mapping diagnostics and cannot be deleted. Correct the Arr-to-container or Plex-to-container mapping, sync Plex, and rerun analysis.
- Scheduled analysis is disabled by default. When enabled, it runs once at the configured server-local time, never overlaps, and does not catch up after downtime.
- A backend restart marks an active analysis interrupted. Retry it from Retention; partial snapshots are not published.

## Retention Deletion Recovery

Deletion always goes through the owning Sonarr/Radarr service after a fresh Plex sync and full source revalidation. Media Atlas does not need or use direct filesystem deletion for this workflow.

- If revalidation reports new play evidence, changed files/sizes, changed eligibility, missing requests, or incomplete mapping, no deletion call is made. Run a new analysis before reconsidering the item.
- If Arr deletion succeeds but Seerr status reconciliation fails, the audit record is `succeeded_with_warning`. Use **Retry Seerr** to repeat only the non-clearing `deleted` status update; do not delete the Arr title again.
- If an Arr delete times out, Media Atlas queries the title. A confirmed missing title is treated as success; a confirmed present title is treated as failure. If the title state cannot be determined, the audit is `unknown` and automatic retry is blocked. Inspect Arr activity/history and the media path before taking any manual action.
- Seerr request history is intentionally preserved. Never use Seerr Clear Data as part of recovery.

## Environment Value Not Reaching Container

If Admin Status does not match `.env`, remember that Compose does not automatically inject every `.env` key. It only passes keys listed under `environment:`.

Check the rendered config:

```bash
docker compose config
docker compose config | grep MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN
```

After changing `.env` or `docker-compose.yml`, run:

```bash
docker compose up -d --force-recreate
```

## Publish Recovery

If a publish is interrupted, do not immediately clean up artifacts.

1. Inspect the source path, staged output path, and recorded backup path in Transcode Runs.
2. Confirm whether the original path contains the old file or the staged output.
3. If needed, restore manually by copying the recorded backup path back to the source path.
4. Retry publish only after source, staged output, and backup state are understood.

After validating the published media in your library, mark the item validated. Cleanup is available only for validated published items.
