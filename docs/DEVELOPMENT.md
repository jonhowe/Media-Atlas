# Development Install

Use this only when working on Media Atlas source code. Normal installs should use the GHCR image described in the main `README.md`.

## Requirements

- Python 3.12+
- Node.js 20+
- `ffmpeg` and `ffprobe` on `PATH`
- Git

## Clone

```bash
git clone git@github.com:jonhowe/Media-Atlas.git
cd Media-Atlas
cp .env.example .env
```

## Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --env-file ../.env
```

The backend serves the API at:

```text
http://127.0.0.1:8000
```

## Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL, usually:

```text
http://127.0.0.1:5173
```

## Useful Checks

```bash
cd backend
python -m unittest discover -s tests
python -m compileall app
```

```bash
cd frontend
npm run build
```

## Local Data

By default local development writes to repository-local directories:

```text
./data
./reports
./logs
./transcode-staging
```

You can override those paths in `.env`.

## Refresh the Web UI Tour

The screenshots in `docs/assets/web-ui/` come from a deterministic fictional dataset. The generator runs current database migrations, performs no external-service calls, and refuses to overwrite a populated output directory.

From the repository root, with the backend virtual environment active, choose a new or empty directory and generate the fixtures:

```bash
DOCS_DEMO_DIR=/private/tmp/media-atlas-docs-demo
python3 scripts/generate_docs_demo.py \
  --output-dir "$DOCS_DEMO_DIR" \
  --version v1.1.0-demo
npm --prefix frontend run build
```

Launch the generated dataset from the repository root:

```bash
PYTHONPATH=backend \
MEDIA_ATLAS_HOST=127.0.0.1 \
MEDIA_ATLAS_PORT=8123 \
MEDIA_ATLAS_DATA_DIR="$DOCS_DEMO_DIR/data" \
MEDIA_ATLAS_REPORTS_DIR="$DOCS_DEMO_DIR/reports" \
MEDIA_ATLAS_LOGS_DIR="$DOCS_DEMO_DIR/logs" \
MEDIA_ATLAS_TRANSCODE_STAGING_DIR="$DOCS_DEMO_DIR/transcode-staging" \
MEDIA_ATLAS_TRANSCODE_BACKUP_DIR="$DOCS_DEMO_DIR/transcode-backups" \
MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS=/demo/media \
MEDIA_ATLAS_AUTH_MODE=disabled \
MEDIA_ATLAS_VERSION=v1.1.0-demo \
MEDIA_ATLAS_GIT_SHA=docs-demo-0123456789abcdef \
MEDIA_ATLAS_BUILD_DATE=2026-07-01T12:00:00Z \
MEDIA_ATLAS_IMAGE_TAG=v1.1.0-demo \
MEDIA_ATLAS_ALLOWED_ORIGINS=http://127.0.0.1:8123 \
MEDIA_ATLAS_READINESS_MIN_FREE_BYTES=0 \
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8123
```

Open `http://127.0.0.1:8123/`, switch to the dark theme, and set the browser viewport to exactly 1440×900. Refresh the ten PNGs with these exact names and states:

| File | UI state |
| --- | --- |
| `dashboard.png` | Dashboard overview |
| `library.png` | Library with the Glass Horizon detail drawer open |
| `retention.png` | Retention with the Aurora Station detail drawer open |
| `quality-candidates.png` | Easy Win category |
| `reports.png` | Reports overview |
| `transcode-planner.png` | Easy Win planner with the visible page selected |
| `runs.png` | Container cleanup run open |
| `scans.png` | Scan history |
| `logs.png` | Transcodes log source, Container cleanup, successful Paper Moons item |
| `admin-status.png` | Admin Status overview |

Save each capture under `docs/assets/web-ui/` as a true 1440×900 PNG. Do not use a real Media Atlas instance or introduce real titles, LAN addresses, media paths, accounts, or secrets. Verify the assets and Markdown references before committing:

```bash
file docs/assets/web-ui/*.png
rg -n "192\\.168\\.|/Users/|/home/" docs/WEB_UI.md scripts/generate_docs_demo.py
git diff --check
```
