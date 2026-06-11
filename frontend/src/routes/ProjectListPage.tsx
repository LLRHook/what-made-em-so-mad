import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiCallError, api } from "../api/client";
import type { VideoListEntry, VideoLatestJob } from "../api/types";

type LoadState = "loading" | "ready" | "error";

function displayName(entry: VideoListEntry): string {
  return (entry.title && entry.title.trim()) || entry.source_uri;
}

function formatDuration(seconds: number | null): string {
  if (seconds == null || Number.isNaN(seconds) || seconds <= 0) return "--:--";
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

function jobLabel(job: VideoLatestJob | null): { text: string; tone: "ok" | "warn" | "muted" | "fail" } {
  if (!job) return { text: "no analysis yet", tone: "muted" };
  switch (job.status) {
    case "complete":
      return { text: "analysis complete", tone: "ok" };
    case "running":
    case "queued":
      return { text: `analysis ${job.status}`, tone: "warn" };
    case "failed":
      return { text: "analysis failed", tone: "fail" };
    case "blocked":
      return { text: "analysis blocked", tone: "warn" };
    case "cancelled":
      return { text: "analysis cancelled", tone: "muted" };
    default:
      return { text: job.status, tone: "muted" };
  }
}

export function ProjectListPage() {
  const [state, setState] = useState<LoadState>("loading");
  const [items, setItems] = useState<VideoListEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState<string>("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setState("loading");
    setError(null);
    try {
      const list = await api.listVideos();
      setItems(list);
      setState("ready");
    } catch (e) {
      setError(e instanceof ApiCallError ? `${e.code}: ${e.message}` : "could not load projects");
      setState("error");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const startRename = useCallback((entry: VideoListEntry) => {
    setRenamingId(entry.id);
    setDraftTitle(entry.title ?? entry.source_uri);
  }, []);

  const cancelRename = useCallback(() => {
    setRenamingId(null);
    setDraftTitle("");
  }, []);

  const submitRename = useCallback(
    async (entry: VideoListEntry) => {
      const trimmed = draftTitle.trim();
      if (!trimmed) {
        setError("title must not be empty");
        return;
      }
      setBusyId(entry.id);
      setError(null);
      try {
        const updated = await api.renameVideo(entry.id, trimmed);
        setItems((prev) => prev.map((e) => (e.id === entry.id ? { ...e, title: updated.title ?? null } : e)));
        setRenamingId(null);
        setDraftTitle("");
      } catch (e) {
        setError(e instanceof ApiCallError ? `${e.code}: ${e.message}` : "rename failed");
      } finally {
        setBusyId(null);
      }
    },
    [draftTitle],
  );

  const clearTitle = useCallback(async (entry: VideoListEntry) => {
    if (entry.title == null) return;
    setBusyId(entry.id);
    setError(null);
    try {
      const updated = await api.renameVideo(entry.id, null);
      setItems((prev) => prev.map((e) => (e.id === entry.id ? { ...e, title: updated.title ?? null } : e)));
    } catch (e) {
      setError(e instanceof ApiCallError ? `${e.code}: ${e.message}` : "rename failed");
    } finally {
      setBusyId(null);
    }
  }, []);

  const removeProject = useCallback(async (entry: VideoListEntry) => {
    const ok = window.confirm(
      `Delete "${displayName(entry)}" and all of its analysis + exported clips? This cannot be undone.`,
    );
    if (!ok) return;
    setBusyId(entry.id);
    setError(null);
    try {
      await api.deleteVideo(entry.id);
      setItems((prev) => prev.filter((e) => e.id !== entry.id));
    } catch (e) {
      setError(e instanceof ApiCallError ? `${e.code}: ${e.message}` : "delete failed");
    } finally {
      setBusyId(null);
    }
  }, []);

  const empty = useMemo(() => state === "ready" && items.length === 0, [state, items]);

  if (state === "loading") {
    return <p>Loading projects...</p>;
  }

  if (state === "error" && items.length === 0) {
    return (
      <section aria-labelledby="error-heading">
        <h1 id="error-heading">Could not load projects</h1>
        <p role="alert" className="error">
          {error}
        </p>
        <button type="button" onClick={() => void refresh()}>
          Retry
        </button>
      </section>
    );
  }

  if (empty) {
    return (
      <section aria-labelledby="empty-heading" className="empty-state">
        <h1 id="empty-heading">No videos yet</h1>
        <p>Import a local MP4 to get started. Uploaded videos appear here on every computer that opens the app.</p>
        <Link to="/import" className="cta">
          Import a video
        </Link>
      </section>
    );
  }

  return (
    <section aria-labelledby="projects-heading">
      <h1 id="projects-heading">Projects</h1>
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      <ul className="project-list" aria-label="Server-side project list">
        {items.map((entry) => {
          const label = jobLabel(entry.latest_job);
          const isRenaming = renamingId === entry.id;
          const isBusy = busyId === entry.id;
          return (
            <li key={entry.id} className="project-row">
              <div className="project-row-main">
                {isRenaming ? (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      void submitRename(entry);
                    }}
                    className="rename-form"
                  >
                    <label className="sr-only" htmlFor={`title-${entry.id}`}>
                      New title for {entry.source_uri}
                    </label>
                    <input
                      id={`title-${entry.id}`}
                      type="text"
                      value={draftTitle}
                      maxLength={120}
                      autoFocus
                      onChange={(e) => setDraftTitle(e.target.value)}
                      disabled={isBusy}
                    />
                    <button type="submit" disabled={isBusy}>
                      Save
                    </button>
                    <button type="button" onClick={cancelRename} disabled={isBusy}>
                      Cancel
                    </button>
                  </form>
                ) : (
                  <Link to={`/videos/${entry.id}`} className="project-name">
                    {displayName(entry)}
                  </Link>
                )}
                <span className={`status-pill status-${label.tone}`} aria-label={`Status: ${label.text}`}>
                  {label.text}
                </span>
                <span className="meta">
                  {formatDuration(entry.duration_seconds)} - {entry.clip_count} clip
                  {entry.clip_count === 1 ? "" : "s"} - added {new Date(entry.created_at).toLocaleString()}
                </span>
              </div>
              <div className="project-row-actions">
                {!isRenaming ? (
                  <button
                    type="button"
                    onClick={() => startRename(entry)}
                    disabled={isBusy}
                    aria-label={`Rename ${displayName(entry)}`}
                  >
                    Rename
                  </button>
                ) : null}
                {entry.title != null && !isRenaming ? (
                  <button
                    type="button"
                    onClick={() => void clearTitle(entry)}
                    disabled={isBusy}
                    aria-label={`Reset title for ${displayName(entry)}`}
                  >
                    Reset title
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => void removeProject(entry)}
                  disabled={isBusy}
                  aria-label={`Delete ${displayName(entry)}`}
                  className="danger"
                >
                  Delete
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      <Link to="/import" className="cta">
        Import another video
      </Link>
    </section>
  );
}
