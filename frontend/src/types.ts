export type Health = {
  status: string;
  database_available: boolean;
  ffprobe_available: boolean;
  ffmpeg_available: boolean;
  data_dir: string;
  reports_dir: string;
  logs_dir: string;
  transcode_staging_dir: string;
  transcode_backup_dir: string;
  readiness?: ReadinessStatus;
};

export type ReadinessStatus = {
  status: string;
  ok: boolean;
  database: { ok: boolean; path?: string; error?: string };
  migrations: {
    ok: boolean;
    applied: string[];
    pending: string[];
    error?: string | null;
    last_run_at?: string | null;
  };
  paths: Record<string, { path: string; writable: boolean; error?: string }>;
  disk: Record<string, { path: string; ok: boolean; free_bytes?: number; min_free_bytes?: number; error?: string }>;
  tools: Record<string, { available: boolean; version?: string | null; command?: string; path?: string }>;
  config_warnings: string[];
  jobs: Record<string, Record<string, number>>;
};

export type AuthStatus = {
  mode: "disabled" | "single_admin" | "reverse_proxy_trusted";
  authenticated: boolean;
  username?: string | null;
  configured: boolean;
  csrf_token?: string | null;
  trusted_user_header?: string | null;
};

export type AdminStatus = {
  version: {
    version: string;
    git_sha: string;
    build_date: string;
    image_tag: string;
  };
  readiness: ReadinessStatus;
  auth: Record<string, unknown>;
  runtime_config: {
    host: string;
    port: number;
    allow_lan: boolean;
    auth: {
      mode: "disabled" | "single_admin" | "reverse_proxy_trusted";
    };
    operations: {
      acknowledge_auth_disabled_lan: boolean;
      fail_unsafe_bind: boolean;
      allowed_origins: string[];
    };
  };
  storage: Record<string, { path: string; ok: boolean; free_bytes?: number; total_bytes?: number; used_bytes?: number; error?: string }>;
  recent_failures: {
    scans: ScanJob[];
    transcodes: TranscodeRun[];
    plex_syncs: PlexSyncJob[];
  };
  retention: {
    log_retention_days: number;
    staged_output_retention_days: number;
  };
};

export type MediaRoot = {
  id: number;
  name: string;
  path: string;
  enabled: boolean;
  include_extensions: string[];
  exclude_patterns: string[];
  last_scanned_at?: string | null;
};

export type ScanJob = {
  id: number;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  total_files_discovered: number;
  files_skipped: number;
  files_probed: number;
  files_failed: number;
  current_path?: string | null;
  message?: string | null;
};

export type MediaFile = {
  id: number;
  root_name?: string;
  path: string;
  filename: string;
  extension: string;
  size_bytes: number;
  duration_seconds?: number | null;
  container?: string | null;
  primary_video_codec?: string | null;
  primary_audio_codec?: string | null;
  resolution_bucket?: string | null;
  bitrate_mbps?: number | null;
  audio_stream_count: number;
  subtitle_stream_count: number;
  is_hdr: boolean;
  is_missing: boolean;
  recommendation_category?: string | null;
  recommendation_summary?: string | null;
  recommendation_reasons?: string[];
  recommendation_warnings?: string[];
  streams?: Array<Record<string, unknown>>;
  chapters?: Array<Record<string, unknown>>;
  raw_probe_json?: string | null;
  plex?: PlexMetadata | null;
};

export type PlexMetadata = {
  match_status: string;
  match_method?: string | null;
  path_match_detail?: string | null;
  rating_key?: string | null;
  guid?: string | null;
  library_section_key?: string | null;
  library_section_title?: string | null;
  library_section_type?: string | null;
  type?: string | null;
  title?: string | null;
  sort_title?: string | null;
  year?: number | null;
  show_title?: string | null;
  season_number?: number | null;
  episode_number?: number | null;
  summary?: string | null;
  content_rating?: string | null;
  audience_rating?: number | null;
  user_rating?: number | null;
  originally_available_at?: string | null;
  added_at?: string | null;
  updated_at?: string | null;
  last_viewed_at?: string | null;
  view_count?: number | null;
  watched?: boolean;
  thumb?: string | null;
  art?: string | null;
  collections?: string[];
  genres?: string[];
  labels?: string[];
  file_path?: string | null;
  normalized_path?: string | null;
  raw_json?: string | null;
};

export type PlexPathMapping = {
  plex_path_prefix: string;
  media_atlas_path_prefix: string;
};

export type PlexSettings = {
  enabled: boolean;
  server_url: string;
  token_configured: boolean;
  token_hint: string;
  selected_library_keys: string[];
  timeout_seconds: number;
  path_mappings: PlexPathMapping[];
};

export type PlexLibrary = {
  section_key: string;
  title: string;
  type?: string | null;
  agent?: string | null;
  scanner?: string | null;
  language?: string | null;
  uuid?: string | null;
  updated_at?: string | null;
};

export type PlexSyncJob = {
  id: number;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  total_items: number;
  processed_items: number;
  matched_files: number;
  unmatched_files: number;
  unmatched_parts: number;
  message?: string | null;
  error_message?: string | null;
};

export type PlexStatus = {
  configured: boolean;
  enabled: boolean;
  server_url?: string | null;
  last_sync?: PlexSyncJob | null;
  matched_count: number;
  unmatched_file_count: number;
  unmatched_part_count: number;
  latest_error?: string | null;
};

export type Summary = {
  total_files: number;
  total_size_bytes: number;
  total_duration_seconds: number;
  by_video_codec: ReportRow[];
  by_container: ReportRow[];
  by_resolution: ReportRow[];
  by_audio_codec: ReportRow[];
  by_recommendation: ReportRow[];
  plex?: PlexStatus;
  largest_files: MediaFile[];
  recent_errors: Array<Record<string, unknown>>;
};

export type ReportRow = {
  label: string;
  file_count: number;
  total_size_bytes: number;
  total_duration_seconds: number;
};

export type TranscodeProfile = {
  id: number;
  name: string;
  description: string;
  container: string;
  command_template: string;
};

export type TranscodePlan = {
  id: number;
  name: string;
  profile_id: number;
  profile_name?: string;
  status: string;
  created_at: string;
  archived_at?: string | null;
  item_count?: number;
  items?: TranscodePlanItem[];
  sample_items?: TranscodePlanItem[];
  run_count?: number;
  latest_run?: TranscodeRunSummary | null;
};

export type TranscodePlanItem = {
  id: number;
  file_id: number;
  source_path: string;
  target_path: string;
  action: string;
  filename?: string | null;
  reason?: string;
  command_display?: string;
  warnings_json?: string;
};

export type TranscodeRunSummary = {
  id: number;
  name: string;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  archived_at?: string | null;
  total_items: number;
  completed_items: number;
  failed_items: number;
  canceled_items: number;
  progress_percent: number;
};

export type TranscodeRun = {
  id: number;
  plan_id: number;
  name: string;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  current_item_id?: number | null;
  total_items: number;
  completed_items: number;
  failed_items: number;
  canceled_items: number;
  progress_percent: number;
  archived_at?: string | null;
  message?: string | null;
  items?: TranscodeRunItem[];
  cleanup_summary?: {
    run_archived?: boolean;
    errors?: Array<{ item_id: number; message: string }>;
  };
};

export type TranscodeSavingsStats = {
  runs_total: number;
  runs_started: number;
  runs_succeeded: number;
  runs_archived: number;
  items_total: number;
  items_succeeded: number;
  items_published: number;
  items_validated: number;
  items_cleaned: number;
  items_with_size_comparison: number;
  total_runtime_seconds: number;
  total_source_size_bytes: number;
  total_output_size_bytes: number;
  total_space_saved_bytes: number;
  savings_percent: number;
};

export type TranscodeRunItem = {
  id: number;
  status: string;
  source_path: string;
  target_path: string;
  command_display: string;
  log_path?: string | null;
  progress_percent: number;
  source_size_bytes?: number | null;
  output_size_bytes?: number | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  time_seconds?: number | null;
  speed?: string | null;
  exit_code?: number | null;
  verification_status?: string | null;
  verification_message?: string | null;
  published_at?: string | null;
  validated_at?: string | null;
  validation_message?: string | null;
  publish_status?: string | null;
  publish_message?: string | null;
  published_backup_path?: string | null;
  publish_started_at?: string | null;
  publish_finished_at?: string | null;
  publish_step?: string | null;
  publish_progress_percent?: number | null;
  publish_bytes_done?: number | null;
  publish_bytes_total?: number | null;
  cleanup_status?: string | null;
  cleanup_message?: string | null;
  cleanup_started_at?: string | null;
  cleanup_finished_at?: string | null;
  staged_deleted_at?: string | null;
  backup_deleted_at?: string | null;
  warnings_json: string;
};
