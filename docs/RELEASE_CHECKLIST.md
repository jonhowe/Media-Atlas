# Release Checklist

Use this checklist before publishing a tagged GHCR release.

1. Update `CHANGELOG.md` with user-facing changes, migration notes, and config changes.
2. Confirm backend tests pass:

   ```bash
   cd backend
   python -m unittest discover -s tests
   ```

3. Confirm frontend builds:

   ```bash
   cd frontend
   npm run build
   ```

4. Confirm the container build check passes in GitHub Actions.
5. Run the Compose doctor against a fresh install directory:

   ```bash
   bash media-atlas-doctor.sh
   ```

6. Download Admin Status diagnostics from a local smoke install and confirm secrets are redacted.
7. For migration releases, take a database backup before upgrading a real install.
8. Publish a GitHub release. The GHCR workflow publishes:
   - the release tag,
   - `latest`,
   - `sha-<commit>`.
9. Smoke test a fresh Compose install with empty `data`, `reports`, `logs`, `transcode-staging`, and `transcode-backups` directories.
10. Smoke test rollback instructions with the previously published tag when migrations changed.
