import type {
  AnalysisJob,
  ApiError,
  CapabilitiesResponse,
  ClipResponse,
  DiagnosticsExportResponse,
  DiagnosticsResponse,
  SetupResponse,
  StorageUsageResponse,
  TimelineResponse,
  VideoImportResponse,
  VideoListEntry,
  VideoSummary,
} from "./types";

export class ApiCallError extends Error {
  status: number;
  code: string;
  setupHint?: string;
  constructor(error: ApiError) {
    super(error.message);
    this.status = error.status;
    this.code = error.code;
    this.setupHint = error.setupHint;
  }
}

async function call<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* ignore */
    }
    const detail = (body as { detail?: { error?: string; message?: string; setup_hint?: string } } | null)?.detail;
    throw new ApiCallError({
      status: res.status,
      code: detail?.error ?? `http_${res.status}`,
      message: detail?.message ?? res.statusText,
      setupHint: detail?.setup_hint,
    });
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  capabilities: () => call<CapabilitiesResponse>("/api/system/capabilities"),
  setup: () => call<SetupResponse>("/api/system/setup"),
  storage: () => call<StorageUsageResponse>("/api/system/storage"),

  uploadVideo: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return call<VideoImportResponse>("/api/videos", { method: "POST", body: fd });
  },
  listVideos: () => call<VideoListEntry[]>("/api/videos"),
  getVideo: (id: string) => call<VideoSummary>(`/api/videos/${encodeURIComponent(id)}`),
  renameVideo: (id: string, title: string | null) =>
    call<VideoSummary>(`/api/videos/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }),
  deleteVideo: (id: string) =>
    call<void>(`/api/videos/${encodeURIComponent(id)}`, { method: "DELETE" }),
  getVideoTimeline: (id: string) => call<TimelineResponse>(`/api/videos/${encodeURIComponent(id)}/timeline`),
  analyzeVideo: (id: string, force = false, profile: "fast" | "balanced" | "best" = "balanced") =>
    call<TimelineResponse>(`/api/videos/${encodeURIComponent(id)}/analyze?force=${force ? "true" : "false"}&profile=${profile}`, {
      method: "POST",
    }),
  queueAnalysisJob: (
    id: string,
    options: {
      force?: boolean;
      profile?: "fast" | "balanced" | "best";
      transcription_enabled?: boolean;
      autostart?: boolean;
    } = {},
  ) => {
    const params = new URLSearchParams({
      force: options.force ? "true" : "false",
      profile: options.profile ?? "balanced",
      autostart: options.autostart === false ? "false" : "true",
    });
    if (options.transcription_enabled !== undefined) {
      params.set("transcription_enabled", options.transcription_enabled ? "true" : "false");
    }
    return call<AnalysisJob>(`/api/videos/${encodeURIComponent(id)}/analysis-jobs?${params.toString()}`, {
      method: "POST",
    });
  },
  getAnalysisJob: (id: string) => call<AnalysisJob>(`/api/analysis-jobs/${encodeURIComponent(id)}`),
  runAnalysisJob: (id: string) =>
    call<AnalysisJob>(`/api/analysis-jobs/${encodeURIComponent(id)}/run`, { method: "POST" }),
  cancelAnalysisJob: (id: string) =>
    call<AnalysisJob>(`/api/analysis-jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  listVideoClips: (id: string) => call<ClipResponse[]>(`/api/videos/${encodeURIComponent(id)}/clips`),
  suggestClips: (id: string) =>
    call<ClipResponse[]>(`/api/videos/${encodeURIComponent(id)}/suggested-clips`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }),
  diagnostics: (id: string) => call<DiagnosticsResponse>(`/api/videos/${encodeURIComponent(id)}/diagnostics`),
  exportDiagnostics: (id: string) =>
    call<DiagnosticsExportResponse>(`/api/videos/${encodeURIComponent(id)}/diagnostics/export`, { method: "POST" }),
  feedbackExport: (id: string) => call<{ video_id: string; annotations: Record<string, unknown>[] }>(`/api/videos/${encodeURIComponent(id)}/feedback-export`),
  cleanupVideo: (id: string, body: { thumbnails?: boolean; clips?: boolean; diagnostics?: boolean; failed_clips_only?: boolean }) =>
    call<StorageUsageResponse>(`/api/videos/${encodeURIComponent(id)}/cleanup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  createClipDraft: (input: {
    video_id: string;
    start_seconds: number;
    end_seconds: number;
    labels?: string[];
    title?: string | null;
  }) =>
    call<ClipResponse>("/api/clips", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),

  getClip: (id: string) => call<ClipResponse>(`/api/clips/${encodeURIComponent(id)}`),

  updateClip: (id: string, body: { title?: string | null; accepted?: boolean | null }) =>
    call<ClipResponse>(`/api/clips/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  deleteClip: (id: string) =>
    call<void>(`/api/clips/${encodeURIComponent(id)}`, { method: "DELETE" }),

  exportClip: (
    id: string,
    body: { pre_roll_seconds: number; post_roll_seconds: number; mode: "stream_copy_when_possible" | "re_encode" },
  ) =>
    call<ClipResponse>(`/api/clips/${encodeURIComponent(id)}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  batchExportClips: (
    clip_ids: string[],
    body: { pre_roll_seconds: number; post_roll_seconds: number; mode: "stream_copy_when_possible" | "re_encode" },
  ) =>
    call<{ clips: ClipResponse[]; manifest_path: string | null }>("/api/clips/batch-export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clip_ids, ...body }),
    }),
};
