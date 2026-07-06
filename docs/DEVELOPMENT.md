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
