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
