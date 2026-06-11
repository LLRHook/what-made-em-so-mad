import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VideoListEntry } from "../api/types";

// Mock the API client module: keep the real ApiCallError class (the component
// does `instanceof` checks against it) but replace the `api` object with spies.
// `vi.hoisted` is required because the `vi.mock` factory is hoisted above
// normal `const` declarations.
const { listVideos, renameVideo, deleteVideo } = vi.hoisted(() => ({
  listVideos: vi.fn(),
  renameVideo: vi.fn(),
  deleteVideo: vi.fn(),
}));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: { listVideos, renameVideo, deleteVideo },
  };
});

// Import after the mock is registered.
import { ProjectListPage } from "../routes/ProjectListPage";

function entry(overrides: Partial<VideoListEntry> = {}): VideoListEntry {
  return {
    id: "abc",
    source_type: "local",
    source_uri: "demo.mp4",
    title: null,
    duration_seconds: 65,
    width: 1280,
    height: 720,
    created_at: "2026-01-01T00:00:00Z",
    source_url: "/api/videos/abc/source",
    thumbnails: [],
    latest_job: null,
    clip_count: 0,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ProjectListPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  listVideos.mockReset();
  renameVideo.mockReset();
  deleteVideo.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ProjectListPage", () => {
  it("shows the empty state when the server returns no videos", async () => {
    listVideos.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByRole("heading", { name: /no videos yet/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /import a video/i })).toBeInTheDocument();
  });

  it("lists server-supplied videos, with status badge, and links to each editor", async () => {
    listVideos.mockResolvedValue([
      entry({ id: "abc", source_uri: "demo.mp4", clip_count: 2, latest_job: { id: "j1", status: "complete", completed_at: "x", error_message: null } }),
    ]);
    renderPage();
    const link = await screen.findByRole("link", { name: /demo\.mp4/i });
    expect(link).toHaveAttribute("href", "/videos/abc");
    expect(screen.getByText(/analysis complete/i)).toBeInTheDocument();
    expect(screen.getByText(/2 clips/i)).toBeInTheDocument();
  });

  it("prefers the title over the source filename when set", async () => {
    listVideos.mockResolvedValue([entry({ id: "abc", source_uri: "raw.mp4", title: "My Stream" })]);
    renderPage();
    expect(await screen.findByRole("link", { name: "My Stream" })).toBeInTheDocument();
  });

  it("renames a video through the API and updates the row", async () => {
    listVideos.mockResolvedValue([entry({ id: "abc", source_uri: "raw.mp4", title: null })]);
    renameVideo.mockResolvedValue({ ...entry(), title: "Renamed" });
    renderPage();

    await screen.findByRole("link", { name: /raw\.mp4/i });
    await userEvent.click(screen.getByRole("button", { name: /rename raw\.mp4/i }));

    const input = screen.getByLabelText(/new title for raw\.mp4/i);
    await userEvent.clear(input);
    await userEvent.type(input, "Renamed");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(renameVideo).toHaveBeenCalledWith("abc", "Renamed"));
    expect(await screen.findByRole("link", { name: "Renamed" })).toBeInTheDocument();
  });

  it("does not submit a rename when the title is blank", async () => {
    listVideos.mockResolvedValue([entry({ id: "abc", source_uri: "raw.mp4" })]);
    renderPage();
    await screen.findByRole("link", { name: /raw\.mp4/i });
    await userEvent.click(screen.getByRole("button", { name: /rename raw\.mp4/i }));

    const input = screen.getByLabelText(/new title for raw\.mp4/i);
    await userEvent.clear(input);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(renameVideo).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/must not be empty/i);
  });

  it("deletes a video after the user confirms", async () => {
    listVideos.mockResolvedValue([entry({ id: "abc", source_uri: "raw.mp4" })]);
    deleteVideo.mockResolvedValue(undefined);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    await screen.findByRole("link", { name: /raw\.mp4/i });
    await userEvent.click(screen.getByRole("button", { name: /delete raw\.mp4/i }));

    await waitFor(() => expect(deleteVideo).toHaveBeenCalledWith("abc"));
    await waitFor(() => expect(screen.queryByRole("link", { name: /raw\.mp4/i })).not.toBeInTheDocument());
    expect(await screen.findByRole("heading", { name: /no videos yet/i })).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("does not delete when the user cancels the confirm", async () => {
    listVideos.mockResolvedValue([entry({ id: "abc", source_uri: "raw.mp4" })]);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();

    await screen.findByRole("link", { name: /raw\.mp4/i });
    await userEvent.click(screen.getByRole("button", { name: /delete raw\.mp4/i }));

    expect(deleteVideo).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /raw\.mp4/i })).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("renders a retry path when the list request fails", async () => {
    listVideos.mockRejectedValueOnce(new Error("boom"));
    renderPage();
    expect(await screen.findByRole("heading", { name: /could not load projects/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
