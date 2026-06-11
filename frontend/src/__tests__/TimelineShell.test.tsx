import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { TimelineResponse, VideoSummary } from "../api/types";
import { TimelineShell } from "../components/TimelineShell";

const baseVideo: VideoSummary = {
  id: "v1",
  source_type: "local",
  source_uri: "demo.mp4",
  sha256: "x",
  local_path: "/data/v1/source.mp4",
  source_url: "/api/videos/v1/source",
  duration_seconds: 60,
  fps: 30,
  width: 1280,
  height: 720,
  created_at: "2026-01-01T00:00:00Z",
  thumbnails: [],
};

const baseTimeline: TimelineResponse = {
  job: {
    id: "j1",
    video_id: "v1",
    status: "complete",
    params: {},
    versions: {},
    progress: null,
    error_message: null,
    created_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:00:01Z",
  },
  streamer_profile: {
    id: "sp1",
    video_id: "v1",
    confirmed_by: "auto",
    confirmed_time_seconds: 0,
    prompt: { label: "primary_streamer_candidate" },
    roi_bbox: null,
    face_crop_path: null,
    embedding_path: null,
    created_at: "2026-01-01T00:00:00Z",
  },
  lifecycle_segments: [
    {
      id: "t1",
      job_id: "j1",
      start_seconds: 0,
      end_seconds: 60,
      state: "present",
      confidence: 0.35,
      bbox: null,
      artifact_ref: null,
      evidence: { identity: { method: "auto_full_frame_fallback" } },
    },
  ],
  activity_events: [
    {
      id: "a1",
      job_id: "j1",
      start_seconds: 10,
      end_seconds: 20,
      label: "talking",
      confidence: 0.56,
      source: "fusion",
      evidence: { context: { summary: "Likely speech context." } },
    },
  ],
};

describe("TimelineShell", () => {
  it("renders metadata, automated segments, and a play cursor", () => {
    render(<TimelineShell video={baseVideo} timeline={baseTimeline} currentTime={15} onReanalyze={vi.fn()} />);
    expect(screen.getByRole("heading", { name: /timeline/i })).toBeInTheDocument();
    expect(screen.getByText("1280 x 720 - 30.00 fps - 1:00 total")).toBeInTheDocument();
    expect(screen.getByText(/0:15/)).toBeInTheDocument();
    expect(screen.getByText("present")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "0:10-0:20 - talking (56%)" })).toBeInTheDocument();
    expect(screen.getByLabelText("Activity segment list")).toBeInTheDocument();
  });

  it("clamps the fill width to 100% when currentTime exceeds duration", () => {
    render(<TimelineShell video={baseVideo} timeline={baseTimeline} currentTime={9999} onReanalyze={vi.fn()} />);
    const fill = screen.getByTestId("track-fill") as HTMLElement;
    expect(fill.style.width).toBe("100%");
  });
});
