# Deployment Guide

Media Atlas 1.0 targets LAN/VPN self-hosting with Docker Compose and the GHCR image.

## Known-Good Compose Install

Use the repository `docker-compose.yml` and `.env.docker.example` together. Compose reads `.env` for interpolation, but only variables listed under `environment:` are passed into the container.

```bash
mkdir -p ~/media-atlas
cd ~/media-atlas
curl -fsSLo docker-compose.yml https://raw.githubusercontent.com/jonhowe/Media-Atlas/main/docker-compose.yml
curl -fsSLo .env https://raw.githubusercontent.com/jonhowe/Media-Atlas/main/.env.docker.example
curl -fsSLo media-atlas-doctor.sh https://raw.githubusercontent.com/jonhowe/Media-Atlas/main/scripts/media-atlas-doctor.sh
```

Edit `.env`, then verify the rendered container environment:

```bash
bash media-atlas-doctor.sh
docker compose config
docker compose up -d
```

If you copy only parts of the Compose file, make sure `MEDIA_ATLAS_AUTH_MODE`, `MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN`, `MEDIA_ATLAS_TRANSCODE_BACKUP_DIR`, and `MEDIA_ATLAS_ALLOWED_ORIGINS` remain under `environment:`.

## Optional Overrides

Copy one of these files from `docs/compose/` next to `docker-compose.yml`, or reference it with `-f` from a source checkout:

```bash
docker compose -f docker-compose.yml -f intel-vaapi.override.yml up -d
```

- `docs/compose/intel-vaapi.override.yml`: passes `/dev/dri` and defaults `LIBVA_DRIVER_NAME=iHD`.
- `docs/compose/nvidia-nvenc.override.yml`: enables `gpus: all` for NVIDIA Container Toolkit hosts.
- `docs/compose/writable-publish.override.yml`: remounts `/media` read-write for the manual Publish action.
- `docs/compose/reverse-proxy-trusted.override.yml`: enables trusted-header auth behind an authenticating proxy.

Keep the default `/media:ro` mount unless you intentionally want Media Atlas to publish staged outputs back over originals.

## Admin Status And Diagnostics

Admin Status shows the runtime config loaded by the backend, including auth mode, LAN acknowledgement, allowed origins, build metadata, writable path checks, disk checks, tool versions, and recent failures.

Use **Download diagnostics** before filing an issue or debugging a deployment. The diagnostics JSON is redacted and does not include Plex tokens or admin passwords.
