# Media Atlas

Media Atlas is a local-first media inventory and transcode execution web app. It scans configured media roots with `ffprobe`, stores technical metadata in SQLite, provides a searchable reporting UI, generates transcode plans, and can run staged `ffmpeg` transcode jobs from the web UI without modifying source media.

## MVP Capabilities

- Configure one or more local or mounted media roots.
- Recursively scan media files and skip unchanged files on rescans.
- Store raw `ffprobe` JSON plus normalized codec, container, stream, bitrate, duration, HDR, subtitle, and audio fields.
- Browse, search, filter, report, and export inventory data.
- Classify files as Easy Win, Remux Only, Review, Skip, Already Modern, Error, or Missing.
- Generate staged transcode plans from candidates.
- Start and monitor one server-side transcode run at a time.
- Close and reopen the browser while jobs continue, as long as the backend process remains running.

The MVP never deletes, overwrites, replaces, or automatically mutates source media.

## Requirements

- Docker Compose
- A local or mounted media directory

The published container image supplies Python, Node.js-built frontend assets, `ffmpeg`, and `ffprobe`.

## Docker Compose Install

This is the recommended install path for a seedbox or home server. It uses the published GHCR image and does not require cloning the repository.

Create an install directory:

```bash
mkdir -p ~/media-atlas
cd ~/media-atlas
```

Create `.env` with the host path to your media:

```bash
MEDIA_ATLAS_PORT=8000
MEDIA_ATLAS_MEDIA_ROOT=/mnt/media
MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS=/media
MEDIA_ATLAS_SCAN_CONCURRENCY=2
MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS=60
MEDIA_ATLAS_FFMPEG_TIMEOUT_SECONDS=0
```

Create `docker-compose.yml`:

```yaml
services:
  media-atlas:
    image: ghcr.io/jonhowe/media-atlas:latest
    container_name: media-atlas
    restart: unless-stopped
    ports:
      - "${MEDIA_ATLAS_PORT:-8000}:8000"
    environment:
      MEDIA_ATLAS_HOST: "0.0.0.0"
      MEDIA_ATLAS_PORT: "8000"
      MEDIA_ATLAS_DATA_DIR: "/app/data"
      MEDIA_ATLAS_REPORTS_DIR: "/app/reports"
      MEDIA_ATLAS_LOGS_DIR: "/app/logs"
      MEDIA_ATLAS_TRANSCODE_STAGING_DIR: "/app/transcode-staging"
      MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS: "${MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS:-/media}"
      MEDIA_ATLAS_SCAN_CONCURRENCY: "${MEDIA_ATLAS_SCAN_CONCURRENCY:-2}"
      MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS: "${MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS:-60}"
      MEDIA_ATLAS_FFMPEG_TIMEOUT_SECONDS: "${MEDIA_ATLAS_FFMPEG_TIMEOUT_SECONDS:-0}"
    volumes:
      - ./data:/app/data
      - ./reports:/app/reports
      - ./logs:/app/logs
      - ./transcode-staging:/app/transcode-staging
      - ${MEDIA_ATLAS_MEDIA_ROOT:?Set MEDIA_ATLAS_MEDIA_ROOT in .env}:/media:ro
```

Pull and start the app:

```bash
docker compose pull
docker compose up -d
```

Open the app from another machine on the LAN:

```text
http://SERVER_IP:8000/
```

For the seedbox shown earlier, that would be:

```text
http://192.168.1.106:8000/
```

### Docker Path Mapping

The Compose file mounts the host media root read-only:

```text
host:      MEDIA_ATLAS_MEDIA_ROOT
container: /media
```

Media Atlas runs inside the container, so paths added in the UI must use the container path. Use `/media` or subpaths under `/media`, for example:

```text
/media
/media/Movies
/media/TV
```

Do not add the host path such as `/mnt/media` in the UI unless you also mount that exact path into the container.

Persistent app data is bind-mounted into the install directory:

```text
./data                SQLite database
./reports             CSV/report output
./logs                app and transcode logs
./transcode-staging   staged transcode outputs
```

Source media is mounted read-only. Staged transcode output is written separately and source files are not modified by the MVP.

Useful commands:

```bash
docker compose pull
docker compose up -d
docker compose logs -f media-atlas
docker compose restart media-atlas
docker compose down
```

---

# Transcode Planner

Media Atlas uses a **staging-first transcode planner** designed to safely prepare conversion jobs without modifying your source media.

A transcode plan is a collection of selected media files paired with a transcode profile. When a plan is created, Media Atlas:

- Calculates a staging output path for every file.
- Generates the appropriate `ffmpeg` command for the selected profile.
- Records any warnings associated with the source media.
- Preserves the recommendation that identified the file as a candidate.
- Stores everything for review before any transcode begins.

Running a plan executes the generated commands sequentially.

Source media is **never modified**.

All transcoded files are written into the configured staging directory where they can be reviewed before replacing the originals.

## Planner Safety

The planner is intentionally conservative.

- Source files are never overwritten.
- Output is always written into the staging directory.
- Existing staged files are never overwritten.
- Generated commands can be reviewed before execution.
- Every completed transcode is validated using `ffprobe`.
- Output duration is compared against the source to detect incomplete or corrupt transcodes.
- Failed validation marks the transcode for review rather than success.

This allows an entire library to be processed safely before deciding whether any staged files should replace the originals.

## Recommended Workflow

The intended workflow is:

1. Scan one or more media roots.
2. Review Media Atlas recommendations.
3. Select candidate files.
4. Create a transcode plan using the appropriate profile.
5. Review the generated commands and warnings.
6. Execute the plan.
7. Verify staged output.
8. Replace original media only after confirming the results.

Because all transcoded media remains in a separate staging directory, you can compare quality, playback compatibility, and storage savings before making any permanent changes.

## Transcode Profiles

### Remux to MKV

**Purpose**

Move existing streams into an MKV container without re-encoding.

**Behavior**

- Copies all video streams.
- Copies all audio streams.
- Copies all subtitle streams.
- Changes only the container.

**Best For**

- MOV
- MPEG-TS
- M2TS
- Other modern codecs stored in older containers

**Advantages**

- Extremely fast.
- No quality loss.
- Minimal CPU usage.
- Very low risk.

Choose this profile when the video codec is already modern and you simply want a cleaner or more capable container.

---

### HEVC Archive Balanced

**Purpose**

Reduce storage requirements while preserving nearly everything except the video encoding.

**Behavior**

- Video encoded with **libx265**
- CRF 20
- Medium preset
- Audio copied without re-encoding
- Subtitle streams copied
- Output container: MKV

**Best For**

- Large media libraries
- Legacy MPEG-2
- VC-1
- DivX/Xvid
- Older H.264 encodes
- High bitrate Blu-ray remuxes

**Advantages**

- Significant storage savings.
- Preserves original audio.
- Preserves subtitle streams.
- Excellent long-term archival profile.

For most users, this will become the primary archival profile.

---

### H.264 Compatibility

**Purpose**

Produce files that play on the widest possible range of hardware and software.

**Behavior**

- Video encoded with **libx264**
- CRF 20
- Slow preset
- Audio converted to AAC (192 kbps)
- Text subtitles converted to `mov_text`
- Output container: MP4

**Best For**

- Older televisions
- Mobile devices
- Browsers
- Legacy streaming devices
- Applications with limited HEVC support

**Advantages**

- Maximum compatibility.
- Standard MP4 output.
- Excellent playback support.

**Considerations**

Because MP4 is more restrictive than MKV:

- Image-based subtitles may not survive conversion.
- Lossless audio will be converted.
- Subtitle compatibility should always be verified.

---

### Manual Review Only

**Purpose**

Identify files that deserve human review before transcoding.

This profile intentionally generates **no ffmpeg command**.

Typical candidates include:

- HDR content
- 4K media
- Interlaced video
- Multiple audio tracks
- Multiple subtitle tracks
- Image-based subtitles (PGS/DVD)
- Lossless audio
- Any media where automatic conversion could reduce quality or compatibility

Use this profile whenever you want Media Atlas to help organize work without attempting an automatic conversion.

## Choosing the Right Profile

| Recommendation | Recommended Profile | Why |
| --- | --- | --- |
| Easy Win | HEVC Archive Balanced | Significant storage savings with minimal downside. |
| Remux Only | Remux to MKV | Container cleanup without re-encoding. |
| Review | Manual Review Only | Complex media should be inspected before conversion. |
| Already Modern | Leave unchanged | Additional transcoding usually provides little benefit. |
| Skip | No action recommended | No obvious improvement available. |

## Understanding Recommendations

Media Atlas analyzes every scanned file and assigns one of several recommendation categories.

### Easy Win

Files that are likely to benefit substantially from transcoding.

Typical examples include:

- Legacy codecs
- Legacy containers
- Excessively high bitrates

These are excellent candidates for **HEVC Archive Balanced**.

### Remux Only

The codec is already modern, but the container could be improved.

These files generally benefit from **Remux to MKV**.

### Review

Media that deserves additional attention before transcoding.

Examples include:

- HDR
- 4K
- Interlaced video
- Multiple audio tracks
- Multiple subtitle tracks
- Image-based subtitles
- Lossless audio

These files should generally begin with **Manual Review Only**.

### Already Modern

The media is already encoded efficiently.

Little or no storage benefit is expected from another transcode.

### Skip

No obvious conversion benefit was detected.

Leaving these files unchanged is usually the best choice.

## Verification

After every transcode, Media Atlas automatically validates the staged output.

Verification includes:

- Output file exists.
- Output file is not empty.
- `ffprobe` successfully reads the output.
- Output duration matches the source within configured tolerances.

Only after these checks pass is a transcode marked as successfully completed.

---

## Publishing

GitHub Actions publishes images to GHCR.

- `ghcr.io/jonhowe/media-atlas:latest` tracks the latest published `main` build.
- Release tags publish matching image tags, for example `ghcr.io/jonhowe/media-atlas:v0.1.0`.
- Every published image also gets a commit-pinned `sha-<commit>` tag.
- Builds use GitHub Actions layer caching, Trivy scanning, SBOM attestations, and provenance attestations.

## Development Install

Local development requires Python 3.12+, Node.js 20+, and `ffmpeg`/`ffprobe` on `PATH`.

```bash
git clone git@github.com:jonhowe/Media-Atlas.git
cd Media-Atlas
```

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
cd ../frontend
npm install
npm run dev
```

Open the Vite development server, usually:

```text
http://127.0.0.1:5173
```

## Configuration

Configuration is environment-variable based so the same settings work for both the GHCR container and local development.

```bash
MEDIA_ATLAS_HOST=127.0.0.1
MEDIA_ATLAS_PORT=8000
MEDIA_ATLAS_DATA_DIR=./data
MEDIA_ATLAS_REPORTS_DIR=./reports
MEDIA_ATLAS_LOGS_DIR=./logs
MEDIA_ATLAS_TRANSCODE_STAGING_DIR=./transcode-staging
MEDIA_ATLAS_ALLOWED_BROWSE_ROOTS=/Volumes,/mnt,/media
MEDIA_ATLAS_FFPROBE_PATH=ffprobe
MEDIA_ATLAS_FFMPEG_PATH=ffmpeg
MEDIA_ATLAS_SCAN_CONCURRENCY=2
MEDIA_ATLAS_FFPROBE_TIMEOUT_SECONDS=60
```
