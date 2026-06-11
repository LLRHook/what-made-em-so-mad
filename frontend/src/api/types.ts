export type CapabilityEntry = {
  available: boolean;
  version?: string | null;
  reason?: string | null;
  path?: string | null;
  device?: string | null;
};

export type CapabilitiesResponse = {
  ffmpeg: CapabilityEntry;
  ffprobe: CapabilityEntry;
  yt_dlp: CapabilityEntry;
  sam3: CapabilityEntry;
  cuda: CapabilityEntry;
  mediapipe: CapabilityEntry;
  opencv_models: { yunet: boolean; sface: boolean };
};

export type SetupItem = {
  name: string;
  available: boolean;
  required: boolean;
  hint?: string | null;
};

export type SetupResponse = {
  items: SetupItem[];
  all_required_present: boolean;
};

export type VideoSummary = {
  id: string;
  source_type: "local" | "url";
  source_uri: string;
  title?: string | null;
  sha256: string;
  local_path: string;
  source_url: string;
  duration_seconds: number | null;
  fps: number | null;
  width: number | null;
  height: number | null;
  created_at: string;
  thumbnails: string[];
};

export type VideoLatestJob = {
  id: string;
  status: "queued" | "running" | "blocked" | "failed" | "complete" | "cancelled";
  completed_at: string | null;
  error_message: string | null;
};

export type VideoListEntry = {
  id: string;
  source_type: "local" | "url";
  source_uri: string;
  title: string | null;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  created_at: string;
  source_url: string;
  thumbnails: string[];
  latest_job: VideoLatestJob | null;
  clip_count: number;
};

export type VideoUpdateRequest = {
  title: string | null;
};

export type VideoImportResponse = {
  video_id: string;
  status: "imported";
  duplicate_of: string | null;
};

export type AnalysisJob = {
  id: string;
  video_id: string;
  status: "queued" | "running" | "blocked" | "failed" | "complete" | "cancelled";
  params: Record<string, unknown>;
  versions: Record<string, unknown>;
  progress: Record<string, unknown> | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type StreamerProfile = {
  id: string;
  video_id: string;
  confirmed_by: "user" | "auto" | "user_reacquire";
  confirmed_time_seconds: number;
  prompt: Record<string, unknown>;
  roi_bbox: Record<string, unknown> | null;
  face_crop_path: string | null;
  embedding_path: string | null;
  created_at: string;
};

export type TrackSegment = {
  id: string;
  job_id: string;
  start_seconds: number;
  end_seconds: number;
  state: string;
  confidence: number;
  bbox: Record<string, unknown> | null;
  artifact_ref: string | null;
  evidence: Record<string, unknown>;
};

export type ActivityEvent = {
  id: string;
  job_id: string;
  start_seconds: number;
  end_seconds: number;
  label: string;
  confidence: number;
  source: "fusion" | "user" | "imported";
  evidence: Record<string, unknown>;
};

export type TimelineResponse = {
  job: AnalysisJob;
  streamer_profile: StreamerProfile | null;
  lifecycle_segments: TrackSegment[];
  activity_events: ActivityEvent[];
};

export type RoiCandidate = {
  id: string;
  video_id: string;
  sample_start_seconds: number;
  sample_end_seconds: number;
  bbox: Record<string, unknown>;
  score: number;
  evidence: Record<string, unknown>;
  preview_path: string | null;
};

export type TranscriptSegment = {
  id: string;
  video_id: string;
  start_seconds: number;
  end_seconds: number;
  text: string;
  source: "manual" | "whisper" | "imported";
  confidence: number | null;
  created_at: string;
};

export type VadSegment = {
  id: string;
  job_id: string;
  start_seconds: number;
  end_seconds: number;
  speech_probability: number;
  source: string;
  evidence: Record<string, unknown>;
};

export type ExpressionFeature = {
  id: string;
  job_id: string;
  time_seconds: number;
  bbox: Record<string, unknown> | null;
  face_visible: boolean;
  mouth_open: number | null;
  smile: number | null;
  brow_raise: number | null;
  squint: number | null;
  head_pose: Record<string, unknown> | null;
  confidence: number;
  evidence: Record<string, unknown>;
};

export type StorageUsageResponse = {
  data_dir: string;
  total_bytes: number;
  videos_bytes: number;
  clips_bytes: number;
  database_bytes: number;
  videos: Record<string, unknown>[];
};

export type DiagnosticsResponse = {
  video: VideoSummary;
  timeline: TimelineResponse;
  roi_candidates: RoiCandidate[];
  transcripts: TranscriptSegment[];
  vad_segments: VadSegment[];
  expression_features: ExpressionFeature[];
  capabilities: CapabilitiesResponse;
  storage: Record<string, unknown>;
  artifacts: Record<string, unknown>;
};

export type DiagnosticsExportResponse = {
  video_id: string;
  job_id: string;
  artifact_path: string;
  created_at: string;
  summary: Record<string, unknown>;
};

export type ClipStatus = "draft" | "exporting" | "exported" | "failed";

export type ClipResponse = {
  id: string;
  video_id: string;
  start_seconds: number;
  end_seconds: number;
  labels: string[];
  title: string | null;
  suggestion_score: number | null;
  suggestion_reason: string | null;
  accepted: boolean;
  status: ClipStatus;
  output_path: string | null;
  output_url: string | null;
  error_message: string | null;
  created_at: string;
};

export type ApiError = {
  status: number;
  code: string;
  message: string;
  setupHint?: string;
};
