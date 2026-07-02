# Media Library Inventory and Transcode Planning Web App

Date: 2026-07-02
Status: Project plan / build specification
Primary user: A home media-library owner with roughly 10 TB of movies and TV files
Primary goal: Build a simple, local-first web UI for scanning one or more media directories, analyzing codecs/containers/bitrates/streams, searching and reporting on the library, and planning safe transcoding work.

---

## 1. Executive Summary

Build a local web application that scans media files directly from the filesystem, uses `ffprobe` to extract technical metadata, stores normalized results in SQLite, and presents a modern web UI for browsing, searching, reporting, and generating transcode recommendations.

The application should not start with Plex as the source of truth. Plex integration can be added later as an optional enrichment layer for title/year/watch-state/library metadata. The technical source of truth should be the actual files on disk.

The app should initially generate reports and transcode plans only. It should not automatically overwrite or replace source files. Actual transcoding can be added later as a controlled workflow with staging, verification, and rollback safeguards.

---

## 2. Product Goals

### 2.1 Core Goals

1. Allow the user to configure one or more media root directories.
2. Recursively scan those directories for movie and TV files.
3. Extract technical media metadata using `ffprobe`.
4. Store scan results in a local SQLite database.
5. Provide a modern web UI to browse, search, filter, sort, and inspect media files.
6. Provide dashboards and reports showing library composition by codec, container, resolution, audio format, bitrate, file size, and transcode suitability.
7. Identify likely transcode candidates using configurable rules.
8. Export reports to CSV and, later, Excel/JSON.
9. Generate a transcode plan without immediately executing it.
10. Keep development simple and avoid unnecessary distributed systems.

### 2.2 Non-Goals for the First Version

1. Do not build a full Plex replacement.
2. Do not scrape movie metadata from IMDb, TMDb, TVDB, or similar services in version 1.
3. Do not require Plex.
4. Do not require Docker for local development, although Docker can be supported.
5. Do not require authentication if the app is bound to localhost only.
6. Do not automatically transcode or delete source files in the MVP.
7. Do not optimize for multi-user cloud hosting in the MVP.
8. Do not build a complex job system such as Celery/Redis unless the simple local job system becomes insufficient.

---

## 3. Recommended Technology Stack

### 3.1 Backend

- Python 3.12 or newer, using the current stable version available on the target machine.
- FastAPI for the HTTP API.
- Uvicorn as the ASGI server.
- SQLite as the local database.
- Python `sqlite3`, SQLAlchemy, or SQLModel for persistence. For simplicity, either direct SQL with `sqlite3` or SQLAlchemy Core is acceptable.
- `ffprobe` from FFmpeg as the media metadata extraction tool.
- Optional: Pydantic models for request/response validation.

### 3.2 Frontend

- React with TypeScript.
- Vite for frontend dev/build tooling.
- Tailwind CSS for styling.
- shadcn/ui-style components for modern, accessible, copy-owned UI building blocks.
- A data table implementation that supports server-side pagination, sorting, and filters.

### 3.3 Storage

- SQLite database file stored under an application data directory, for example:

```text
./data/media_inventory.sqlite
```

- Exported reports stored under:

```text
./reports/
```

- Application logs stored under:

```text
./logs/
```

---

## 4. Guiding Design Decisions

### 4.1 Scan Files Directly, Not Plex First

The direct filesystem scan should be the canonical technical inventory. Plex can be wrong, stale, missing items, or normalized around entertainment metadata rather than stream metadata. For codec, bitrate, container, subtitles, audio streams, HDR, and transcode suitability, inspect the files directly.

### 4.2 Use SQLite First

SQLite is enough for a local 10 TB library inventory. The database will contain metadata, not video content, so size should remain manageable. SQLite also keeps deployment simple: one database file, no database server.

### 4.3 Use Background Jobs, But Keep Them Simple

Scanning thousands of files can take a while. The API should start scan jobs and return immediately. A simple background worker inside the backend process is enough for version 1.

### 4.4 Never Overwrite Source Media Automatically

All transcode workflows should initially generate recommendations, scripts, and staged output paths only. Replacing originals should require explicit manual confirmation outside the MVP.

### 4.5 Make the UI Data-Dense but Calm

The user is working with a large technical inventory. The UI should feel like a clean admin console: searchable tables, badges, filters, summaries, and detail panes. Avoid flashy entertainment-library UI patterns.

---

## 5. System Architecture

```text
Browser
  |
  | HTTP / JSON / Server-Sent Events or WebSocket
  v
React + TypeScript frontend
  |
  v
FastAPI backend
  |
  |-- Directory configuration service
  |-- File discovery service
  |-- ffprobe execution service
  |-- Metadata normalization service
  |-- Rules/recommendation engine
  |-- Reports/export service
  |-- Optional Plex enrichment service, later
  |
  v
SQLite database
  |
  |-- media roots
  |-- files
  |-- streams
  |-- scan jobs
  |-- scan errors
  |-- rules
  |-- transcode plans
  |
  v
Local filesystem media directories
```

---

## 6. Application Modules

### 6.1 Backend Modules

```text
backend/
  app/
    main.py
    config.py
    db.py
    models.py
    schemas.py
    api/
      health.py
      roots.py
      scans.py
      media.py
      reports.py
      exports.py
      rules.py
      transcode_plans.py
      directory_browser.py
    services/
      file_discovery.py
      ffprobe.py
      metadata_parser.py
      scanner.py
      recommendation_engine.py
      report_builder.py
      export_builder.py
      transcode_planner.py
      plex_client.py          # later/optional
    workers/
      scan_worker.py
    utils/
      paths.py
      time.py
      hashing.py
      logging.py
  tests/
    test_ffprobe_parser.py
    test_recommendations.py
    test_file_discovery.py
```

### 6.2 Frontend Modules

```text
frontend/
  src/
    main.tsx
    App.tsx
    api/
      client.ts
      roots.ts
      scans.ts
      media.ts
      reports.ts
      rules.ts
      exports.ts
    components/
      AppShell.tsx
      Sidebar.tsx
      Topbar.tsx
      StatCard.tsx
      DataTable.tsx
      FilterBar.tsx
      DirectoryPicker.tsx
      ScanProgress.tsx
      CodecBadge.tsx
      ResolutionBadge.tsx
      RecommendationBadge.tsx
      StreamList.tsx
      FileSize.tsx
      Duration.tsx
      EmptyState.tsx
      ErrorPanel.tsx
    pages/
      DashboardPage.tsx
      DirectoriesPage.tsx
      ScansPage.tsx
      LibraryPage.tsx
      MediaDetailPage.tsx
      ReportsPage.tsx
      CandidatesPage.tsx
      TranscodePlannerPage.tsx
      SettingsPage.tsx
    hooks/
      useScanEvents.ts
      useMediaFilters.ts
      useDebouncedValue.ts
    types/
      api.ts
    styles/
      globals.css
```

---

## 7. User Workflows

### 7.1 First-Run Setup

1. User opens the web UI.
2. App shows an empty-state setup screen.
3. User adds one or more media root directories.
4. User can either type paths manually or use a server-side directory browser.
5. App validates that each path exists and is readable.
6. User starts the first scan.

### 7.2 Directory Management

The Directories page should allow the user to:

1. Add a root directory.
2. Name the root directory, for example Movies, TV, Anime, Kids, Concerts.
3. Enable or disable a root without deleting it.
4. Set include/exclude patterns.
5. See last scanned time.
6. See number of files discovered under that root.
7. See total size under that root.
8. Remove a root from the app without deleting any files.

Example root configuration:

```json
{
  "name": "Movies",
  "path": "/mnt/media/Movies",
  "enabled": true,
  "include_extensions": ["mkv", "mp4", "m4v", "avi", "mov", "wmv", "mpg", "mpeg", "ts", "m2ts", "webm"],
  "exclude_patterns": ["*/sample/*", "*.part", "*.tmp"]
}
```

### 7.3 Scan Workflow

1. User clicks `Start Scan`.
2. Backend creates a scan job.
3. File discovery walks all enabled roots.
4. Each media file is compared to the database using path, file size, and modified time.
5. Unchanged files are skipped.
6. New or changed files are queued for probing.
7. Backend runs `ffprobe` with limited concurrency.
8. Results are normalized and saved.
9. Scan progress is streamed to the UI.
10. Errors are saved and shown in the Errors tab.
11. Deleted/missing files are marked as missing rather than immediately removed.

### 7.4 Browse/Search Workflow

The Library page should support:

1. Text search across path, filename, folder, guessed title, and optional Plex title later.
2. Filters for container, video codec, audio codec, resolution, HDR, subtitle count, file size, duration, bitrate, root directory, and recommendation category.
3. Sort by size, duration, bitrate, date modified, codec, resolution, and last scanned.
4. Server-side pagination.
5. A detail drawer or detail page for any selected item.
6. Saved filters, later.

### 7.5 Media Detail Workflow

For a selected file, show:

1. Full path.
2. File size.
3. Last modified time.
4. Container and format metadata.
5. Duration.
6. Overall bitrate.
7. Primary video stream.
8. All video streams.
9. All audio streams.
10. All subtitle streams.
11. Chapters, if available.
12. HDR/color metadata, if available.
13. Raw `ffprobe` JSON, hidden behind an advanced disclosure panel.
14. Recommendation result and reasons.
15. Candidate transcode profile, if applicable.

### 7.6 Reports Workflow

Reports page should provide:

1. Summary dashboard.
2. Storage by video codec.
3. Storage by container.
4. Storage by resolution.
5. Storage by audio codec.
6. Largest files.
7. Oldest/legacy formats.
8. High-bitrate files by resolution.
9. Files with scan errors.
10. Likely transcode candidates.
11. Likely remux-only candidates.
12. Files that should be manually reviewed.
13. Export buttons.

### 7.7 Transcode Planning Workflow

1. User filters to likely candidates.
2. User selects files individually or in bulk.
3. User chooses a transcode profile.
4. App generates estimated target paths and command templates.
5. App displays warnings for HDR, multiple audio tracks, image subtitles, lossless audio, or unusual streams.
6. User exports a transcode plan CSV or shell script.
7. Actual execution remains manual in version 1.

---

## 8. Directory Selection Design

A normal browser cannot freely inspect the server's filesystem unless the backend provides that capability. Since this app is local-first, implement a server-side directory browser endpoint.

### 8.1 Directory Browser Features

1. Start from safe base locations, such as `/mnt`, `/media`, `/Volumes`, user home, or configured allow-list paths.
2. Show subdirectories only.
3. Do not show file contents.
4. Validate paths server-side.
5. Prevent path traversal problems.
6. Allow manual path entry for advanced users.

### 8.2 API Sketch

```http
GET /api/directory-browser?path=/mnt/media
```

Example response:

```json
{
  "path": "/mnt/media",
  "parent": "/mnt",
  "directories": [
    { "name": "Movies", "path": "/mnt/media/Movies", "readable": true },
    { "name": "TV", "path": "/mnt/media/TV", "readable": true }
  ]
}
```

---

## 9. File Discovery

### 9.1 Default Media Extensions

```text
.mkv
.mp4
.m4v
.avi
.mov
.wmv
.mpg
.mpeg
.ts
.m2ts
.flv
.webm
.ogm
.iso    # optional; inspect later, not first-class MVP
.vob    # optional; useful for old DVD structures
```

### 9.2 Exclusions

Default exclude patterns:

```text
*.part
*.partial
*.tmp
*.download
*/sample/*
*/samples/*
@eaDir/*
.DS_Store
```

### 9.3 Change Detection

For each discovered file, store:

```text
path
size_bytes
modified_time_ns
```

A file should be re-probed only when one of those values changes or the previous probe failed.

### 9.4 Missing Files

If a previously scanned file is no longer found, mark it as missing:

```text
is_missing = true
missing_since = current timestamp
```

Do not delete rows automatically. This preserves history and avoids confusion when network mounts are temporarily unavailable.

---

## 10. ffprobe Execution

### 10.1 Command Template

Use `ffprobe` in JSON mode:

```bash
ffprobe -v error -print_format json -show_format -show_streams -show_chapters "/path/to/media.mkv"
```

### 10.2 Execution Rules

1. Run subprocess commands with argument arrays, not shell-concatenated strings.
2. Set a timeout per file, for example 60 seconds by default.
3. Capture stdout and stderr.
4. Store raw JSON for successful probes.
5. Store stderr and exit code for failures.
6. Limit concurrency to avoid hammering disks or network storage.
7. Default concurrency should be configurable, with a conservative default such as 2.

### 10.3 Normalization

The raw `ffprobe` JSON should be preserved, but the app should also extract normalized fields for fast filtering and reporting.

---

## 11. Data Model

### 11.1 Tables Overview

```text
media_roots
files
streams
chapters
scan_jobs
scan_errors
recommendations
transcode_profiles
transcode_plans
transcode_plan_items
app_settings
```

### 11.2 SQL Schema Draft

```sql
CREATE TABLE media_roots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1,
  include_extensions_json TEXT NOT NULL,
  exclude_patterns_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_scanned_at TEXT
);

CREATE TABLE files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  root_id INTEGER NOT NULL REFERENCES media_roots(id),
  path TEXT NOT NULL UNIQUE,
  directory TEXT NOT NULL,
  filename TEXT NOT NULL,
  extension TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  modified_time_ns INTEGER NOT NULL,
  created_time_ns INTEGER,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_scanned_at TEXT,
  is_missing INTEGER NOT NULL DEFAULT 0,
  missing_since TEXT,

  format_name TEXT,
  format_long_name TEXT,
  container TEXT,
  duration_seconds REAL,
  overall_bitrate INTEGER,

  primary_video_codec TEXT,
  primary_video_codec_long TEXT,
  primary_video_profile TEXT,
  width INTEGER,
  height INTEGER,
  resolution_bucket TEXT,
  frame_rate REAL,
  video_bitrate INTEGER,
  pixel_format TEXT,
  bit_depth INTEGER,
  color_space TEXT,
  color_transfer TEXT,
  color_primaries TEXT,
  hdr_format TEXT,
  is_hdr INTEGER NOT NULL DEFAULT 0,
  is_interlaced INTEGER,

  primary_audio_codec TEXT,
  primary_audio_codec_long TEXT,
  primary_audio_channels INTEGER,
  primary_audio_channel_layout TEXT,
  primary_audio_language TEXT,
  audio_stream_count INTEGER NOT NULL DEFAULT 0,
  subtitle_stream_count INTEGER NOT NULL DEFAULT 0,
  video_stream_count INTEGER NOT NULL DEFAULT 0,

  size_per_hour_gb REAL,
  bitrate_mbps REAL,

  recommendation_category TEXT,
  recommendation_summary TEXT,
  recommendation_reasons_json TEXT,

  raw_probe_json TEXT,
  probe_error TEXT,
  probe_exit_code INTEGER,

  updated_at TEXT NOT NULL
);

CREATE INDEX idx_files_root_id ON files(root_id);
CREATE INDEX idx_files_extension ON files(extension);
CREATE INDEX idx_files_size ON files(size_bytes);
CREATE INDEX idx_files_video_codec ON files(primary_video_codec);
CREATE INDEX idx_files_audio_codec ON files(primary_audio_codec);
CREATE INDEX idx_files_container ON files(container);
CREATE INDEX idx_files_resolution ON files(resolution_bucket);
CREATE INDEX idx_files_recommendation ON files(recommendation_category);
CREATE INDEX idx_files_missing ON files(is_missing);

CREATE TABLE streams (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  stream_index INTEGER NOT NULL,
  stream_type TEXT NOT NULL,
  codec_name TEXT,
  codec_long_name TEXT,
  profile TEXT,
  language TEXT,
  title TEXT,
  disposition_default INTEGER,
  disposition_forced INTEGER,
  width INTEGER,
  height INTEGER,
  frame_rate REAL,
  channels INTEGER,
  channel_layout TEXT,
  sample_rate INTEGER,
  bit_rate INTEGER,
  bits_per_raw_sample INTEGER,
  pixel_format TEXT,
  color_space TEXT,
  color_transfer TEXT,
  color_primaries TEXT,
  duration_seconds REAL,
  raw_stream_json TEXT NOT NULL
);

CREATE INDEX idx_streams_file_id ON streams(file_id);
CREATE INDEX idx_streams_type ON streams(stream_type);
CREATE INDEX idx_streams_codec ON streams(codec_name);

CREATE TABLE chapters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  chapter_index INTEGER NOT NULL,
  start_seconds REAL,
  end_seconds REAL,
  title TEXT,
  raw_chapter_json TEXT NOT NULL
);

CREATE TABLE scan_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL,
  requested_by TEXT,
  total_files_discovered INTEGER NOT NULL DEFAULT 0,
  files_skipped INTEGER NOT NULL DEFAULT 0,
  files_probed INTEGER NOT NULL DEFAULT 0,
  files_failed INTEGER NOT NULL DEFAULT 0,
  current_path TEXT,
  message TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE scan_errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_job_id INTEGER REFERENCES scan_jobs(id),
  path TEXT NOT NULL,
  error_type TEXT NOT NULL,
  error_message TEXT NOT NULL,
  ffprobe_exit_code INTEGER,
  stderr TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE transcode_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  container TEXT NOT NULL,
  video_codec TEXT NOT NULL,
  audio_policy TEXT NOT NULL,
  subtitle_policy TEXT NOT NULL,
  command_template TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE transcode_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  profile_id INTEGER REFERENCES transcode_profiles(id),
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE transcode_plan_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id INTEGER NOT NULL REFERENCES transcode_plans(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id),
  source_path TEXT NOT NULL,
  target_path TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT,
  command TEXT,
  warnings_json TEXT NOT NULL
);

CREATE TABLE app_settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### 11.3 Optional Full-Text Search

Add SQLite FTS later if normal `LIKE` search is too slow:

```sql
CREATE VIRTUAL TABLE files_fts USING fts5(
  path,
  filename,
  directory,
  content='files',
  content_rowid='id'
);
```

---

## 12. Normalized Metadata Fields

### 12.1 File-Level Fields

```text
path
filename
extension
size_bytes
size_gb
modified_time
root_name
is_missing
last_scanned_at
```

### 12.2 Format Fields

```text
format_name
format_long_name
container
duration_seconds
overall_bitrate
bitrate_mbps
size_per_hour_gb
```

### 12.3 Primary Video Fields

```text
video_codec
video_codec_long
video_profile
width
height
resolution_bucket
frame_rate
video_bitrate
pixel_format
bit_depth
color_space
color_transfer
color_primaries
hdr_format
is_hdr
is_interlaced
```

### 12.4 Audio Summary Fields

```text
audio_stream_count
primary_audio_codec
primary_audio_channels
primary_audio_channel_layout
primary_audio_language
audio_summary
```

Example audio summary:

```text
DTS-HD MA 7.1 eng; AC3 5.1 eng; AAC 2.0 eng commentary
```

### 12.5 Subtitle Summary Fields

```text
subtitle_stream_count
subtitle_codecs
subtitle_languages
has_forced_subtitles
has_image_subtitles
```

---

## 13. Recommendation Engine

### 13.1 Recommendation Categories

Use these categories in the UI:

```text
Easy Win
Remux Only
Review
Skip
Already Modern
Error
Missing
```

### 13.2 Easy Win Rules

A file is likely an Easy Win if it matches one or more:

1. Container is AVI, WMV, MPG, MPEG, FLV, or OGM.
2. Video codec is MPEG-2, MPEG-4 ASP/DivX/XviD, MSMPEG4, WMV3, VC-1, Theora, or similar legacy codec.
3. File has unusually high bitrate for its resolution.
4. File is standard-definition or 720p with a very large file size.
5. File has compatibility-problem audio and no compatible secondary track.

### 13.3 Remux Only Rules

A file is likely Remux Only if:

1. The video codec is already acceptable, such as H.264 or HEVC.
2. The audio streams are acceptable or can be copied.
3. The main problem is only the container, such as MOV or TS.
4. Re-encoding the video would be unnecessary quality loss.

### 13.4 Review Rules

A file should be marked Review if it has:

1. 4K video.
2. HDR or Dolby Vision metadata.
3. TrueHD, DTS-HD MA, Atmos, or other high-value audio.
4. Multiple audio tracks.
5. Multiple subtitle tracks.
6. Image-based subtitles such as PGS.
7. Interlaced video.
8. Unusually low bitrate, where further transcoding may visibly damage quality.
9. Any ffprobe ambiguity or missing fields.

### 13.5 Skip / Already Modern Rules

A file can usually be skipped if:

1. It is already HEVC/H.265 or AV1 and has reasonable size.
2. It is H.264 with reasonable bitrate and broad compatibility.
3. It is 4K HDR and the user has not explicitly chosen an HDR-safe workflow.
4. It has already been processed and verified.

### 13.6 Default Bitrate Thresholds

These are starting points only and should be configurable:

```text
480p / SD:   flag if greater than 4 Mbps
720p:        flag if greater than 8 Mbps
1080p:       flag if greater than 15 Mbps
4K:          flag if greater than 50 Mbps, but mark Review rather than Easy Win
```

### 13.7 Recommendation Output Shape

```json
{
  "category": "Easy Win",
  "summary": "Legacy AVI / MPEG-4 ASP file with high bitrate for SD content",
  "reasons": [
    "Container is AVI",
    "Video codec is mpeg4",
    "Resolution is 480p",
    "Bitrate is above configured SD threshold"
  ],
  "warnings": []
}
```

---

## 14. Transcode Profiles

### 14.1 Profiles to Define Initially

#### Profile A: Remux to MKV

Use when video/audio can be copied safely.

```text
Container: MKV
Video: copy
Audio: copy
Subtitles: copy
Chapters: copy
Use case: Fix awkward containers without re-encoding.
```

#### Profile B: HEVC Archive Balanced

```text
Container: MKV
Video: HEVC/H.265
Mode: CRF-based constant-quality encode
Audio: copy original audio by default; optionally add AAC compatibility track later
Subtitles: copy
Use case: Reduce size of legacy encodes while preserving flexible streams.
```

#### Profile C: H.264 Compatibility

```text
Container: MP4 or MKV
Video: H.264
Audio: AAC or AC3 compatible track
Subtitles: compatible text subtitles where possible
Use case: Maximum playback compatibility.
```

#### Profile D: HEVC Hardware Fast

```text
Container: MKV
Video: HEVC via available hardware encoder
Audio: copy
Subtitles: copy
Use case: Fast conversion when minor quality tradeoffs are acceptable.
```

#### Profile E: Manual Review Only

```text
No command generated.
Use case: 4K HDR, Dolby Vision, complex audio/subtitle layouts, or unusual files.
```

### 14.2 Command Generation Rules

1. Never overwrite input files.
2. Always write output to a staging directory.
3. Preserve chapters when possible.
4. Copy subtitles by default unless the selected container cannot support them.
5. Copy all audio by default for MKV profiles.
6. Include warnings when MP4 output may drop unsupported streams.
7. Generate commands as text artifacts first.
8. Store every generated command in the database.

### 14.3 Example Command Templates

#### Remux to MKV

```bash
ffmpeg -i "SOURCE" -map 0 -c copy "TARGET.mkv"
```

#### HEVC Software Balanced

```bash
ffmpeg -i "SOURCE" -map 0 -c:v libx265 -crf 20 -preset medium -c:a copy -c:s copy "TARGET.mkv"
```

#### H.264 Compatibility

```bash
ffmpeg -i "SOURCE" -map 0 -c:v libx264 -crf 20 -preset slow -c:a aac -b:a 192k -c:s mov_text "TARGET.mp4"
```

Important: These are templates. The actual app must generate commands based on stream details and selected profile.

---

## 15. API Design

### 15.1 Health

```http
GET /api/health
```

Response:

```json
{
  "status": "ok",
  "ffprobe_available": true,
  "database_available": true
}
```

### 15.2 Media Roots

```http
GET /api/roots
POST /api/roots
PATCH /api/roots/{root_id}
DELETE /api/roots/{root_id}
```

### 15.3 Directory Browser

```http
GET /api/directory-browser?path=/mnt/media
```

### 15.4 Scans

```http
POST /api/scans
GET /api/scans
GET /api/scans/{scan_id}
POST /api/scans/{scan_id}/cancel
POST /api/scans/{scan_id}/retry-errors
GET /api/scans/{scan_id}/events
```

Use Server-Sent Events or WebSockets for progress updates.

Example scan job response:

```json
{
  "id": 42,
  "status": "running",
  "total_files_discovered": 13250,
  "files_skipped": 12040,
  "files_probed": 983,
  "files_failed": 3,
  "current_path": "/mnt/media/Movies/example.mkv"
}
```

### 15.5 Media Search

```http
GET /api/media?query=alien&video_codec=h264&resolution=1080p&page=1&page_size=50&sort=size_bytes&direction=desc
```

Response:

```json
{
  "items": [],
  "page": 1,
  "page_size": 50,
  "total": 1234
}
```

### 15.6 Media Detail

```http
GET /api/media/{file_id}
```

### 15.7 Reports

```http
GET /api/reports/summary
GET /api/reports/video-codecs
GET /api/reports/containers
GET /api/reports/resolutions
GET /api/reports/audio-codecs
GET /api/reports/largest-files
GET /api/reports/candidates
GET /api/reports/errors
```

### 15.8 Exports

```http
GET /api/exports/all-files.csv
GET /api/exports/candidates.csv
GET /api/exports/errors.csv
GET /api/exports/summary.json
GET /api/exports/workbook.xlsx     # later
```

### 15.9 Rules

```http
GET /api/rules
PUT /api/rules
POST /api/rules/recompute
```

### 15.10 Transcode Plans

```http
POST /api/transcode-plans
GET /api/transcode-plans
GET /api/transcode-plans/{plan_id}
GET /api/transcode-plans/{plan_id}/download.csv
GET /api/transcode-plans/{plan_id}/download.sh
```

---

## 16. UI Design Plan

### 16.1 Visual Style

Use a modern admin-console style:

1. Left sidebar navigation.
2. Top bar with scan status, global search, and settings.
3. Card-based dashboard summaries.
4. Dense but readable data tables.
5. Badges for codec, resolution, recommendation, HDR, and errors.
6. Dark mode and light mode.
7. Clear empty states.
8. Progressive disclosure for advanced metadata.
9. Sticky table headers.
10. Resizable detail drawer, if practical.

### 16.2 Main Navigation

```text
Dashboard
Directories
Scans
Library
Candidates
Reports
Transcode Planner
Settings
```

### 16.3 Dashboard Page

Show:

1. Total files.
2. Total size.
3. Total duration.
4. Number of roots.
5. Last scan status.
6. Files by recommendation category.
7. Storage by codec.
8. Storage by resolution.
9. Top 10 largest files.
10. Recent scan errors.

### 16.4 Directories Page

Show:

1. Configured roots table.
2. Add directory button.
3. Directory browser modal.
4. Include/exclude pattern editor.
5. Per-root scan button.
6. Enable/disable toggle.

### 16.5 Scans Page

Show:

1. Current scan progress.
2. Historical scans.
3. Progress bars.
4. Files discovered/skipped/probed/failed.
5. Current file path.
6. Cancel button.
7. Retry failed button.

### 16.6 Library Page

The most important page.

Features:

1. Search box.
2. Filter drawer.
3. Sortable table.
4. Column picker.
5. Pagination.
6. Bulk select.
7. Export current view.
8. Open detail drawer.

Default columns:

```text
Filename
Root
Size
Duration
Resolution
Video Codec
Container
Audio
Subtitles
Bitrate
Recommendation
Last Scanned
```

### 16.7 Media Detail Page / Drawer

Sections:

1. Overview.
2. File information.
3. Format/container.
4. Video streams.
5. Audio streams.
6. Subtitle streams.
7. Chapters.
8. Recommendation reasons.
9. Raw ffprobe JSON.

### 16.8 Candidates Page

Purpose: decide what to convert.

Show grouped tabs:

```text
Easy Wins
Remux Only
Needs Review
Already Modern
Errors
```

Each row should explain why it was grouped that way.

### 16.9 Reports Page

Reports should be visual and exportable:

1. Storage by video codec.
2. Count by video codec.
3. Storage by container.
4. Count by container.
5. Storage by resolution.
6. Largest files.
7. High-bitrate files.
8. Legacy formats.
9. Audio codec breakdown.
10. Subtitle breakdown.

### 16.10 Transcode Planner Page

Features:

1. Select candidate set.
2. Choose profile.
3. Preview output paths.
4. Preview command lines.
5. Show warnings.
6. Export CSV.
7. Export shell script.
8. Mark as planned.

---

## 17. Search and Filtering

### 17.1 Searchable Fields

```text
path
filename
directory
root name
container
video codec
audio codec
resolution bucket
recommendation summary
optional Plex title later
```

### 17.2 Filters

```text
Root
Extension
Container
Video codec
Audio codec
Resolution
HDR yes/no
Interlaced yes/no
Subtitle count
Audio stream count
File size range
Duration range
Bitrate range
Size per hour range
Recommendation category
Missing yes/no
Scan error yes/no
Modified date range
Last scanned range
```

### 17.3 Sorting

```text
Filename
Size
Duration
Bitrate
Size per hour
Resolution
Video codec
Container
Modified time
Last scanned
Recommendation
```

---

## 18. Reporting and Export Requirements

### 18.1 CSV Exports

Implement first:

```text
all_files.csv
transcode_candidates.csv
scan_errors.csv
summary_by_codec.csv
summary_by_container.csv
summary_by_resolution.csv
largest_files.csv
```

### 18.2 Excel Workbook Export Later

Workbook tabs:

```text
Summary
All Files
Candidates
Largest Files
By Video Codec
By Container
By Resolution
By Audio Codec
Errors
Rules
```

### 18.3 JSON Export Later

Useful for feeding another AI or script:

```text
library_summary.json
candidate_summary.json
```

---

## 19. Optional Plex Integration

Add only after the direct scanner is working.

### 19.1 Purpose

Use Plex to enrich technical inventory with:

```text
Plex title
year
library section
show name
season number
episode number
collections
watched/unwatched
date added
Plex rating key
```

### 19.2 Join Strategy

Match Plex items to scanned files by absolute file path.

```text
files.path == plex_media_part.file
```

### 19.3 Plex Should Not Override Technical Fields

Plex enrichment should not replace ffprobe-derived codec/container/stream data. Keep the technical inventory independent.

---

## 20. Performance Plan

### 20.1 Expected Scale

A 10 TB library may contain hundreds, thousands, or tens of thousands of files depending on resolution and file sizes. The app should assume large enough scale that server-side pagination and filtering are required.

### 20.2 Scan Performance

1. Use incremental rescans.
2. Skip unchanged files.
3. Limit ffprobe concurrency.
4. Batch database writes.
5. Avoid hashing full video files in the MVP.
6. Use indexes on common filters.
7. Stream progress to the UI.

### 20.3 Database Performance

Indexes should cover:

```text
path
root_id
extension
container
primary_video_codec
primary_audio_codec
resolution_bucket
size_bytes
recommendation_category
is_missing
```

### 20.4 Network Storage Safety

If scanning NAS-mounted media:

1. Keep concurrency conservative.
2. Time out stuck probes.
3. Mark mount unavailable clearly.
4. Do not mark all files missing unless the root path itself is confirmed available.

---

## 21. Safety and Security

### 21.1 Local-Only Default

Bind the backend to localhost by default:

```text
127.0.0.1:8000
```

Only allow LAN access if explicitly configured.

### 21.2 Filesystem Safety

1. Validate all paths.
2. Do not expose arbitrary file contents through the API.
3. Do not delete media files.
4. Do not overwrite media files.
5. Prevent path traversal in directory browsing and export downloads.
6. Store generated reports in a known reports directory.

### 21.3 Subprocess Safety

1. Use subprocess argument arrays.
2. Never build shell command strings from unescaped paths for execution.
3. Shell scripts may be generated for user review, but execution should be manual.
4. Quote paths safely in generated scripts.

### 21.4 Transcode Safety

1. Write output to staging directories.
2. Verify output exists and can be probed.
3. Compare duration before/after.
4. Keep source files untouched.
5. Require explicit manual action for replacement.

---

## 22. Logging and Error Handling

### 22.1 Log Categories

```text
app startup
configuration changes
scan lifecycle
ffprobe command failures
parse errors
database errors
export generation
transcode plan generation
```

### 22.2 Error Types

```text
File not readable
ffprobe timeout
ffprobe non-zero exit
Invalid JSON
No video stream
Unsupported file type
Database write failure
Root path unavailable
Permission denied
```

### 22.3 UI Error Display

The Errors page should show:

```text
Path
Error type
Error message
Scan job
Timestamp
Retry button
```

---

## 23. Testing Plan

### 23.1 Backend Unit Tests

1. Parse sample ffprobe JSON for MKV/H.264/AAC.
2. Parse sample ffprobe JSON for MKV/HEVC/DTS/subtitles.
3. Parse sample ffprobe JSON for AVI/DivX.
4. Parse sample ffprobe JSON for MPEG-2.
5. Parse weird files with missing bitrate.
6. Test recommendation rules.
7. Test path validation.
8. Test incremental scan skip logic.

### 23.2 Backend Integration Tests

1. Create a temporary media directory.
2. Add small sample media files.
3. Run scan.
4. Verify database rows.
5. Verify reports.
6. Verify failed file handling.

### 23.3 Frontend Tests

1. Dashboard renders summary.
2. Directory add flow works.
3. Scan progress updates render.
4. Library filters update query parameters.
5. Media detail drawer renders streams.
6. Candidate page groups files correctly.

### 23.4 End-to-End Tests Later

Use browser automation later for:

1. Add root.
2. Start scan.
3. Browse library.
4. Export candidates.
5. Generate transcode plan.

---

## 24. Development Milestones

### Milestone 0: Repo and Skeleton

Deliverables:

1. Backend FastAPI skeleton.
2. Frontend React/Vite skeleton.
3. SQLite initialization.
4. Health endpoint.
5. Basic app shell UI.

Acceptance criteria:

1. Backend starts locally.
2. Frontend starts locally.
3. UI can call `/api/health`.

### Milestone 1: Directory Configuration

Deliverables:

1. Add/list/update/delete media roots.
2. Validate paths.
3. Directory browser endpoint.
4. Directories UI page.

Acceptance criteria:

1. User can add Movies and TV roots.
2. Roots persist after restart.
3. Invalid paths show clear errors.

### Milestone 2: File Discovery

Deliverables:

1. Recursive file discovery.
2. Extension filtering.
3. Exclude patterns.
4. Basic scan job table.
5. Scan progress without ffprobe yet.

Acceptance criteria:

1. Scan discovers media files.
2. Skips excluded files.
3. Shows progress in UI.
4. Stores file path, size, modified time.

### Milestone 3: ffprobe Integration

Deliverables:

1. ffprobe command runner.
2. JSON parsing.
3. Store raw JSON.
4. Store normalized fields.
5. Store streams.
6. Store scan errors.

Acceptance criteria:

1. Scanned files show codec/container/resolution/audio metadata.
2. Bad files show errors.
3. Rescans skip unchanged files.

### Milestone 4: Library UI

Deliverables:

1. Searchable media table.
2. Filters.
3. Sorting.
4. Pagination.
5. Media detail drawer/page.

Acceptance criteria:

1. User can find files by name.
2. User can filter by codec, container, resolution, and size.
3. Detail view shows streams and raw JSON.

### Milestone 5: Dashboard and Reports

Deliverables:

1. Dashboard stats.
2. Codec/container/resolution summaries.
3. Largest files report.
4. Error report.
5. CSV exports.

Acceptance criteria:

1. User can answer: How many files are H.264, HEVC, AVI, MPEG-2, 1080p, 4K?
2. User can export all files and candidates to CSV.

### Milestone 6: Recommendation Engine

Deliverables:

1. Rule engine.
2. Recommendation category per file.
3. Recommendation reasons.
4. Candidates page.
5. Recompute recommendations endpoint.

Acceptance criteria:

1. Legacy files are flagged as Easy Win.
2. 4K/HDR and complex stream files are marked Review.
3. Already-modern files are marked Skip or Already Modern.

### Milestone 7: Transcode Planner

Deliverables:

1. Transcode profiles.
2. Bulk select candidates.
3. Generate plan.
4. Preview output paths and commands.
5. Export plan CSV and shell script.

Acceptance criteria:

1. User can generate a safe transcode plan without modifying originals.
2. Complex files include warnings.
3. Commands are generated with safe quoting and staging output paths.

### Milestone 8: Optional Plex Enrichment

Deliverables:

1. Plex token/settings.
2. Plex library scan.
3. Match by file path.
4. Display Plex title/year/show/season/episode.

Acceptance criteria:

1. Plex metadata appears when available.
2. Technical metadata remains ffprobe-derived.

---

## 25. Suggested MVP Cut Line

The first truly useful version should include:

1. Directory setup.
2. Recursive scan.
3. ffprobe metadata extraction.
4. SQLite storage.
5. Library table.
6. Detail view.
7. Dashboard summary.
8. CSV export.
9. Basic recommendation categories.

Defer these until after the MVP:

1. Plex integration.
2. Excel workbook export.
3. Full-text search.
4. Actual transcoding execution.
5. Duplicate detection.
6. Multi-user auth.
7. Docker packaging.
8. Native desktop wrapper.

---

## 26. Configuration

Use a simple config file or environment variables.

Example `config.yaml`:

```yaml
app:
  host: "127.0.0.1"
  port: 8000
  data_dir: "./data"
  reports_dir: "./reports"
  logs_dir: "./logs"

scanner:
  ffprobe_path: "ffprobe"
  concurrency: 2
  timeout_seconds: 60
  mark_missing_files: true

security:
  allow_lan: false
  directory_browser_enabled: true
  allowed_browse_roots:
    - "/mnt"
    - "/media"
    - "/Volumes"

recommendations:
  bitrate_thresholds_mbps:
    sd: 4
    p720: 8
    p1080: 15
    p4k: 50
```

---

## 27. Development Commands

### 27.1 Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 27.2 Frontend

```bash
cd frontend
npm install
npm run dev
```

### 27.3 Build Frontend

```bash
cd frontend
npm run build
```

### 27.4 Run Tests

```bash
cd backend
pytest

cd ../frontend
npm test
```

---

## 28. Example Backend Implementation Notes

### 28.1 ffprobe Runner Pseudocode

```python
async def probe_file(path: Path, ffprobe_path: str, timeout_seconds: int) -> dict:
    args = [
        ffprobe_path,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        raise ProbeTimeoutError(str(path))

    if proc.returncode != 0:
        raise ProbeFailedError(path=str(path), exit_code=proc.returncode, stderr=stderr.decode("utf-8", "replace"))

    return json.loads(stdout.decode("utf-8"))
```

### 28.2 Scanner Pseudocode

```python
async def run_scan(scan_job_id: int):
    roots = db.get_enabled_roots()
    files = discover_files(roots)
    db.update_scan_total(scan_job_id, len(files))

    semaphore = asyncio.Semaphore(config.scanner.concurrency)

    for file_path in files:
        if db.file_is_unchanged(file_path):
            db.increment_skipped(scan_job_id)
            continue

        async with semaphore:
            try:
                raw = await probe_file(file_path)
                normalized = normalize_probe_result(file_path, raw)
                db.upsert_file_with_streams(normalized, raw)
                db.increment_probed(scan_job_id)
            except Exception as exc:
                db.record_scan_error(scan_job_id, file_path, exc)
                db.increment_failed(scan_job_id)

        if db.cancel_requested(scan_job_id):
            break

    db.finish_scan(scan_job_id)
```

---

## 29. UX Details That Matter

1. Every recommendation must show reasons, not just a label.
2. The app should make it easy to answer: "What should I transcode first?"
3. The app should distinguish transcode from remux.
4. The app should warn loudly about 4K HDR and complex audio/subtitle files.
5. The app should support large tables without freezing the browser.
6. Filters should be shareable via URL query parameters.
7. The user should be able to export the current filtered view.
8. Scan progress should be visible but not require staying on the scan page.
9. Failed files should not poison the whole scan.
10. Unavailable NAS roots should be treated differently from deleted files.

---

## 30. Key Risks and Mitigations

### 30.1 Risk: ffprobe Metadata Is Inconsistent

Mitigation:

1. Store raw JSON.
2. Normalize defensively.
3. Show unknown values as Unknown, not errors.
4. Add parser tests from real samples.

### 30.2 Risk: Network Storage Is Slow

Mitigation:

1. Conservative concurrency.
2. Incremental scans.
3. Timeouts.
4. Clear root-unavailable state.

### 30.3 Risk: Bad Transcode Recommendations

Mitigation:

1. Recommendations are advisory.
2. Show reasons and warnings.
3. Default complex files to Review.
4. Generate plans, not automatic execution.

### 30.4 Risk: UI Table Gets Too Large

Mitigation:

1. Server-side pagination.
2. Server-side filtering.
3. Index common columns.
4. Consider virtualized rows later.

### 30.5 Risk: Accidentally Damaging Source Library

Mitigation:

1. Read-only scanning.
2. No deletes.
3. No overwrites.
4. Staging output only.
5. Manual replacement only.

---

## 31. Handoff Prompt for Another AI System

Use this prompt to start implementation with another AI system:

```text
Build a local-first media library inventory and transcode planning web application.

Primary goal:
Create a web UI that lets me configure one or more media directories, scan them recursively, extract technical metadata from movie and TV files using ffprobe, store the results in SQLite, search/filter/browse the library, view detailed stream metadata, generate reports, identify transcode candidates, and export CSV/transcode-plan artifacts.

Important product decisions:
- Scan the filesystem directly. Do not use Plex as the primary source of truth.
- Plex integration is optional and should come later only as metadata enrichment.
- Use Python FastAPI for the backend.
- Use SQLite for local storage.
- Use React + TypeScript + Vite for the frontend.
- Use Tailwind CSS and modern component patterns for the UI.
- Use ffprobe JSON output for metadata extraction.
- Keep the MVP simple and local-first.
- Do not automatically transcode, delete, overwrite, or replace source files.
- Generate recommendations and transcode plans first.

Core pages:
1. Dashboard
2. Directories
3. Scans
4. Library
5. Media Detail
6. Candidates
7. Reports
8. Transcode Planner
9. Settings

Core backend modules:
1. Directory roots CRUD
2. Server-side directory browser
3. Recursive file discovery
4. ffprobe runner
5. Metadata parser/normalizer
6. SQLite persistence
7. Background scan jobs
8. Scan progress events
9. Recommendation engine
10. Reports and CSV exports
11. Transcode plan generation

MVP acceptance criteria:
- I can add a Movies directory and a TV directory.
- I can start a scan.
- The app skips unchanged files on rescan.
- The app extracts container, duration, bitrate, video codec, resolution, frame rate, audio streams, subtitle streams, HDR/color metadata when available, and raw ffprobe JSON.
- I can search and filter the library by filename, codec, container, resolution, audio codec, file size, bitrate, and recommendation category.
- I can open a media detail page and see all streams.
- I can view dashboard summaries by codec/container/resolution.
- I can export all files and transcode candidates to CSV.
- I can generate a transcode plan without modifying source files.

Start by creating the repository structure, backend health endpoint, frontend app shell, SQLite initialization, and media roots CRUD. Then implement scanning and ffprobe integration.
```

---

## 32. Final Build Order

Recommended order:

1. Backend skeleton.
2. SQLite schema.
3. Health endpoint.
4. Frontend shell.
5. Media roots CRUD.
6. Directory browser.
7. File discovery.
8. Scan jobs.
9. ffprobe runner.
10. Metadata parser.
11. Library table.
12. Media detail view.
13. Reports.
14. Recommendation engine.
15. Candidate page.
16. CSV exports.
17. Transcode planner.
18. Optional Plex enrichment.

This order gives usable results early and avoids getting stuck on complex transcoding or Plex integration before the inventory system is solid.
