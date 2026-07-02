# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend-build
WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIA_ATLAS_HOST=0.0.0.0 \
    MEDIA_ATLAS_PORT=8000 \
    MEDIA_ATLAS_DATA_DIR=/app/data \
    MEDIA_ATLAS_REPORTS_DIR=/app/reports \
    MEDIA_ATLAS_LOGS_DIR=/app/logs \
    MEDIA_ATLAS_TRANSCODE_STAGING_DIR=/app/transcode-staging \
    MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS=/media \
    MEDIA_ATLAS_FFPROBE_PATH=ffprobe \
    MEDIA_ATLAS_FFMPEG_PATH=ffmpeg

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/backend/
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

RUN mkdir -p /app/data /app/reports /app/logs /app/transcode-staging /media /mnt

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
