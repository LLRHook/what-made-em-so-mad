import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CapabilitiesPanel } from "../components/CapabilitiesPanel";

beforeEach(() => {
  const caps = {
    ffmpeg: { available: true, version: "ffmpeg 7" },
    ffprobe: { available: true, version: "ffprobe 7" },
    yt_dlp: { available: false, reason: "binary_not_on_path" },
    sam3: { available: false, reason: "package_not_installed" },
    cuda: { available: false, reason: "torch_not_installed" },
    mediapipe: { available: false, reason: "package_not_installed" },
    opencv_models: { yunet: false, sface: false },
  };
  const setup = {
    items: [
      { name: "ffmpeg", available: true, required: true },
      { name: "ffprobe", available: true, required: true },
      { name: "yt_dlp", available: false, required: false, hint: "install yt-dlp" },
    ],
    all_required_present: true,
  };
  vi.stubGlobal("fetch", (url: string) => {
    if (url.endsWith("/capabilities"))
      return Promise.resolve(new Response(JSON.stringify(caps), { status: 200 }));
    if (url.endsWith("/setup"))
      return Promise.resolve(new Response(JSON.stringify(setup), { status: 200 }));
    return Promise.reject(new Error(`unexpected ${url}`));
  });
});

afterEach(() => vi.unstubAllGlobals());

describe("CapabilitiesPanel", () => {
  it("renders one row per capability and the overall verdict", async () => {
    render(<CapabilitiesPanel />);
    await waitFor(() => expect(screen.getByRole("status", { name: /ffmpeg available/i })).toBeInTheDocument());
    expect(screen.getByText(/ready to import videos/i)).toBeInTheDocument();
    expect(screen.getByRole("status", { name: /yt_dlp missing/i })).toBeInTheDocument();
  });
});
