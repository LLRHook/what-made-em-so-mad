import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiCallError, api } from "../api/client";
import type { ClipResponse, TimelineResponse, VideoSummary } from "../api/types";
import { ClipRangeForm, type ClipFormValues } from "../components/ClipRangeForm";
import { Player } from "../components/Player";
import { TimelineShell } from "../components/TimelineShell";

export function TimelineEditorPage() {
  const { videoId } = useParams<{ videoId: string }>();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [video, setVideo] = useState<VideoSummary | null>(null);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [clip, setClip] = useState<ClipResponse | null>(null);
  const [clips, setClips] = useState<ClipResponse[]>([]);
  const [clipError, setClipError] = useState<string | null>(null);
  const [clipHint, setClipHint] = useState<string | null>(null);
  const [clipStatus, setClipStatus] = useState<string | null>(null);
  const [analysisProfile, setAnalysisProfile] = useState<"fast" | "balanced" | "best">("balanced");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!videoId) return;
    let alive = true;
    Promise.allSettled([api.getVideo(videoId), api.getVideoTimeline(videoId), api.listVideoClips(videoId)]).then(([videoResult, timelineResult, clipsResult]) => {
      if (!alive) return;
      if (videoResult.status === "fulfilled") {
        setVideo(videoResult.value);
      } else {
        const e = videoResult.reason;
        setLoadError(e instanceof ApiCallError ? `${e.code}: ${e.message}` : "could not load video");
      }
      if (timelineResult.status === "fulfilled") {
        setTimeline(timelineResult.value);
        setTimelineError(null);
      } else {
        const e = timelineResult.reason;
        setTimelineError(e instanceof ApiCallError ? `${e.code}: ${e.message}` : "could not load timeline");
      }
      if (clipsResult.status === "fulfilled") {
        setClips(clipsResult.value);
      }
    });
    return () => {
      alive = false;
    };
  }, [videoId]);

  async function handleReanalyze() {
    if (!videoId) return;
    setTimelineError(null);
    try {
      setTimeline(await api.analyzeVideo(videoId, true, analysisProfile));
    } catch (e) {
      setTimelineError(e instanceof ApiCallError ? `${e.code}: ${e.message}` : "analysis failed");
    }
  }

  function handleSeek(seconds: number) {
    if (!videoRef.current) return;
    videoRef.current.currentTime = seconds;
    videoRef.current.play().catch(() => undefined);
  }

  async function handleClipSubmit(values: ClipFormValues) {
    if (!videoId) return;
    setBusy(true);
    setClip(null);
    setClipError(null);
    setClipHint(null);
    try {
      const draft = await api.createClipDraft({
        video_id: videoId,
        start_seconds: values.start_seconds,
        end_seconds: values.end_seconds,
        labels: values.labels,
        title: values.title || null,
      });
      const exported = await api.exportClip(draft.id, {
        pre_roll_seconds: values.pre_roll_seconds,
        post_roll_seconds: values.post_roll_seconds,
        mode: "stream_copy_when_possible",
      });
      setClip(exported);
      setClips(await api.listVideoClips(videoId));
    } catch (e) {
      if (e instanceof ApiCallError) {
        setClipError(`${e.code}: ${e.message}`);
        if (e.setupHint) setClipHint(e.setupHint);
      } else {
        setClipError("export failed");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleSuggestClips() {
    if (!videoId) return;
    setBusy(true);
    setClipStatus(null);
    setClipError(null);
    try {
      const created = await api.suggestClips(videoId);
      setClips(await api.listVideoClips(videoId));
      setClipStatus(`Created ${created.length} suggested clip draft${created.length === 1 ? "" : "s"}.`);
    } catch (e) {
      setClipError(e instanceof ApiCallError ? `${e.code}: ${e.message}` : "suggested clip generation failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleBatchExport() {
    const draftIds = clips.filter((item) => item.status === "draft").map((item) => item.id);
    if (draftIds.length === 0) {
      setClipStatus("No draft clips to export.");
      return;
    }
    setBusy(true);
    setClipStatus(null);
    setClipError(null);
    try {
      const result = await api.batchExportClips(draftIds, {
        pre_roll_seconds: 0,
        post_roll_seconds: 0,
        mode: "stream_copy_when_possible",
      });
      setClips(result.clips);
      setClipStatus(`Batch export processed ${result.clips.length} clip${result.clips.length === 1 ? "" : "s"}.`);
    } catch (e) {
      setClipError(e instanceof ApiCallError ? `${e.code}: ${e.message}` : "batch export failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleCleanup() {
    if (!videoId) return;
    setClipStatus(null);
    setClipError(null);
    try {
      await api.cleanupVideo(videoId, { thumbnails: true, clips: false });
      setClipStatus("Deleted generated thumbnails for this video.");
      setVideo(await api.getVideo(videoId));
    } catch (e) {
      setClipError(e instanceof ApiCallError ? `${e.code}: ${e.message}` : "cleanup failed");
    }
  }

  if (loadError) {
    return (
      <section>
        <p role="alert">Failed to load video: {loadError}</p>
        <Link to="/">Back to projects</Link>
      </section>
    );
  }
  if (!video) return <p>Loading video...</p>;

  return (
    <section aria-labelledby="editor-heading" className="editor">
      <h1 id="editor-heading">{video.title ?? video.source_uri}</h1>
      <p className="path">
        Local file: <code>{video.local_path}</code>
      </p>
      <Player ref={videoRef} src={video.source_url} onTimeUpdate={setCurrentTime} />
      {timelineError ? (
        <p role="alert" className="error">
          {timelineError}
        </p>
      ) : null}
      <label>
        Analysis profile{" "}
        <select value={analysisProfile} onChange={(event) => setAnalysisProfile(event.currentTarget.value as typeof analysisProfile)}>
          <option value="fast">Fast</option>
          <option value="balanced">Balanced</option>
          <option value="best">Best</option>
        </select>
      </label>
      <TimelineShell video={video} timeline={timeline} currentTime={currentTime} onReanalyze={handleReanalyze} onSeek={handleSeek} />
      <ClipRangeForm
        initialStart={Math.floor(currentTime)}
        initialEnd={Math.floor(currentTime) + 5}
        videoDuration={video.duration_seconds}
        onSubmit={handleClipSubmit}
        busy={busy}
      />
      <div className="clip-actions">
        <button type="button" onClick={handleSuggestClips} disabled={busy}>
          Suggest clips
        </button>
        <button type="button" onClick={handleBatchExport} disabled={busy || clips.length === 0}>
          Export draft clips
        </button>
        <button type="button" onClick={handleCleanup} disabled={busy}>
          Delete thumbnails
        </button>
      </div>
      {clipStatus ? <p className="success">{clipStatus}</p> : null}
      {clipError ? (
        <p role="alert" className="error">
          {clipError}
          {clipHint ? <> - {clipHint}</> : null}
        </p>
      ) : null}
      {clip ? (
        <p className="success">
          Clip <strong>{clip.id}</strong> {clip.status}.{" "}
          {clip.output_url ? (
            <a href={clip.output_url} download>
              Download clip
            </a>
          ) : (
            <>
              Output: <code>{clip.output_path ?? "(pending)"}</code>
            </>
          )}
        </p>
      ) : null}
      {clips.length > 0 ? (
        <div className="segment-list" aria-label="Clip list">
          {clips.map((item) => (
            <button key={item.id} type="button" className="segment-list-row" onClick={() => handleSeek(item.start_seconds)}>
              <span>{item.title ?? item.id}</span>
              <span>{item.labels.join(", ") || "manual"}</span>
              <span>{item.status}</span>
              <span>{item.suggestion_score == null ? "" : `${Math.round(item.suggestion_score * 100)}%`}</span>
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}
