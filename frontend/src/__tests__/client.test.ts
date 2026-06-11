import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiCallError, api } from "../api/client";

type FetchArgs = [RequestInfo | URL, RequestInit | undefined];

let calls: FetchArgs[] = [];
let response: Response;

beforeEach(() => {
  calls = [];
  response = new Response(JSON.stringify({ status: "ok" }), { status: 200, headers: { "Content-Type": "application/json" } });
  vi.stubGlobal("fetch", (...args: FetchArgs) => {
    calls.push(args);
    return Promise.resolve(response.clone());
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("requests the capabilities endpoint", async () => {
    response = new Response(
      JSON.stringify({
        ffmpeg: { available: true },
        ffprobe: { available: true },
        yt_dlp: { available: false },
        sam3: { available: false },
        cuda: { available: false },
        mediapipe: { available: false },
        opencv_models: { yunet: false, sface: false },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
    const caps = await api.capabilities();
    expect(calls[0][0]).toBe("/api/system/capabilities");
    expect(caps.ffmpeg.available).toBe(true);
  });

  it("posts JSON to createClipDraft", async () => {
    response = new Response(
      JSON.stringify({
        id: "c1", video_id: "v1", start_seconds: 1, end_seconds: 2,
        labels: [], title: null, status: "draft", output_path: null, output_url: null, error_message: null, created_at: "",
      }),
      { status: 201, headers: { "Content-Type": "application/json" } },
    );
    await api.createClipDraft({ video_id: "v1", start_seconds: 1, end_seconds: 2 });
    const [url, init] = calls[0];
    expect(url).toBe("/api/clips");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ video_id: "v1", start_seconds: 1, end_seconds: 2 });
  });

  it("throws ApiCallError with code + setupHint on error responses", async () => {
    response = new Response(
      JSON.stringify({ detail: { error: "ffmpeg_missing", message: "nope", setup_hint: "install ffmpeg" } }),
      { status: 503, headers: { "Content-Type": "application/json" } },
    );
    await expect(api.capabilities()).rejects.toBeInstanceOf(ApiCallError);
    try {
      await api.capabilities();
    } catch (e) {
      const err = e as ApiCallError;
      expect(err.code).toBe("ffmpeg_missing");
      expect(err.status).toBe(503);
      expect(err.setupHint).toBe("install ffmpeg");
    }
  });

  it("uses FormData for file uploads", async () => {
    response = new Response(
      JSON.stringify({ video_id: "v1", status: "imported", duplicate_of: null }),
      { status: 201, headers: { "Content-Type": "application/json" } },
    );
    const file = new File([new Uint8Array([1, 2, 3])], "x.mp4", { type: "video/mp4" });
    await api.uploadVideo(file);
    const [, init] = calls[0];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeInstanceOf(FormData);
  });

  it("requests the server-side project list", async () => {
    response = new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
    const list = await api.listVideos();
    expect(calls[0][0]).toBe("/api/videos");
    expect(calls[0][1]?.method ?? "GET").toBe("GET");
    expect(list).toEqual([]);
  });

  it("PATCHes a rename with a JSON title body", async () => {
    response = new Response(
      JSON.stringify({
        id: "v1", source_type: "local", source_uri: "raw.mp4", title: "Renamed",
        sha256: "x", local_path: "/p", source_url: "/api/videos/v1/source",
        duration_seconds: 1, fps: 30, width: 1, height: 1, created_at: "", thumbnails: [],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
    await api.renameVideo("v1", "Renamed");
    const [url, init] = calls[0];
    expect(url).toBe("/api/videos/v1");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(init?.body as string)).toEqual({ title: "Renamed" });
  });

  it("sends a null title to clear the rename", async () => {
    response = new Response(
      JSON.stringify({
        id: "v1", source_type: "local", source_uri: "raw.mp4", title: null,
        sha256: "x", local_path: "/p", source_url: "/api/videos/v1/source",
        duration_seconds: 1, fps: 30, width: 1, height: 1, created_at: "", thumbnails: [],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
    await api.renameVideo("v1", null);
    expect(JSON.parse(calls[0][1]?.body as string)).toEqual({ title: null });
  });

  it("issues a DELETE for a project", async () => {
    response = new Response(null, { status: 204 });
    await api.deleteVideo("v1");
    const [url, init] = calls[0];
    expect(url).toBe("/api/videos/v1");
    expect(init?.method).toBe("DELETE");
  });

  it("queues analysis jobs with query options", async () => {
    response = new Response(
      JSON.stringify({
        id: "j1", video_id: "v1", status: "queued", params: {}, versions: {},
        progress: { stage: "queued" }, error_message: null, created_at: "", started_at: null, completed_at: null,
      }),
      { status: 202, headers: { "Content-Type": "application/json" } },
    );
    await api.queueAnalysisJob("v1", { force: true, profile: "best", transcription_enabled: true, autostart: false });
    const [url, init] = calls[0];
    expect(url).toBe("/api/videos/v1/analysis-jobs?force=true&profile=best&autostart=false&transcription_enabled=true");
    expect(init?.method).toBe("POST");
  });

  it("exports diagnostics bundles", async () => {
    response = new Response(
      JSON.stringify({
        video_id: "v1", job_id: "j1", artifact_path: "/tmp/diagnostics.json", created_at: "", summary: {},
      }),
      { status: 201, headers: { "Content-Type": "application/json" } },
    );
    await api.exportDiagnostics("v1");
    const [url, init] = calls[0];
    expect(url).toBe("/api/videos/v1/diagnostics/export");
    expect(init?.method).toBe("POST");
  });
});
