# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend-build
WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

ARG MEDIA_ATLAS_VERSION=0.1.0
ARG MEDIA_ATLAS_GIT_SHA=unknown
ARG MEDIA_ATLAS_BUILD_DATE=unknown
ARG MEDIA_ATLAS_IMAGE_TAG=local

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIA_ATLAS_VERSION=${MEDIA_ATLAS_VERSION} \
    MEDIA_ATLAS_GIT_SHA=${MEDIA_ATLAS_GIT_SHA} \
    MEDIA_ATLAS_BUILD_DATE=${MEDIA_ATLAS_BUILD_DATE} \
    MEDIA_ATLAS_IMAGE_TAG=${MEDIA_ATLAS_IMAGE_TAG} \
    MEDIA_ATLAS_HOST=0.0.0.0 \
    MEDIA_ATLAS_PORT=8000 \
    MEDIA_ATLAS_DATA_DIR=/app/data \
    MEDIA_ATLAS_REPORTS_DIR=/app/reports \
    MEDIA_ATLAS_LOGS_DIR=/app/logs \
    MEDIA_ATLAS_TRANSCODE_STAGING_DIR=/app/transcode-staging \
    MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS=/media \
    MEDIA_ATLAS_FFPROBE_PATH=ffprobe \
    MEDIA_ATLAS_FFMPEG_PATH=ffmpeg \
    MEDIA_ATLAS_SCAN_CONCURRENCY=2 \
    MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS=60 \
    MEDIA_ATLAS_MARK_MISSING_FILES=true \
    MEDIA_ATLAS_FFMPEG_TIMEOUT_SECONDS=0 \
    MEDIA_ATLAS_DIRECTORY_BROWSER_ENABLED=true \
    MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_SECONDS=3 \
    MEDIA_ATLAS_TRANSCODE_DURATION_TOLERANCE_PERCENT=0.02 \
    MEDIA_ATLAS_TRANSCODE_MIN_FREE_BYTES=1073741824 \
    MEDIA_ATLAS_AUTH_MODE=single_admin \
    MEDIA_ATLAS_LOG_RETENTION_DAYS=30 \
    MEDIA_ATLAS_STAGED_OUTPUT_RETENTION_DAYS=0 \
    LIBVA_DRIVER_NAME=iHD

RUN set -eux; \
    apt-get update; \
    packages="ffmpeg intel-media-va-driver libva-drm2 libva2 libvpl2 vainfo"; \
    if apt-cache show libmfx1 >/dev/null 2>&1; then \
        packages="$packages libmfx1"; \
    fi; \
    apt-get install -y --no-install-recommends $packages; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

RUN mkdir -p /app/data /app/reports /app/logs /app/transcode-staging /app/transcode-backups /media /mnt

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/live', timeout=3).read()"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
