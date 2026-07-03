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
5. For migration releases, take a database backup before upgrading a real install.
6. Publish a GitHub release. The GHCR workflow publishes:
   - the release tag,
   - `latest`,
   - `sha-<commit>`.
7. Smoke test a fresh Compose install with empty `data`, `reports`, `logs`, and `transcode-staging` directories.
8. Smoke test rollback instructions with the previously published tag when migrations changed.

