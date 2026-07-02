export type Health = {
  status: string;
  database_available: boolean;
  ffprobe_available: boolean;
  ffmpeg_available: boolean;
  data_dir: string;
  reports_dir: string;
  logs_dir: string;
  transcode_staging_dir: string;
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
  item_count?: number;
  items?: TranscodePlanItem[];
};

export type TranscodePlanItem = {
  id: number;
  file_id: number;
  source_path: string;
  target_path: string;
  action: string;
  reason?: string;
  command_display?: string;
  warnings_json: string;
};

export type TranscodeRun = {
  id: number;
  plan_id: number;
  name: string;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  total_items: number;
  completed_items: number;
  failed_items: number;
  canceled_items: number;
  progress_percent: number;
  message?: string | null;
  items?: TranscodeRunItem[];
};

export type TranscodeRunItem = {
  id: number;
  status: string;
  source_path: string;
  target_path: string;
  command_display: string;
  log_path?: string | null;
  progress_percent: number;
  speed?: string | null;
  exit_code?: number | null;
  verification_status?: string | null;
  verification_message?: string | null;
  warnings_json: string;
};
