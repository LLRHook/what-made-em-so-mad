import { useEffect, useMemo, useState } from "react";
import type { ActivityEvent, TimelineResponse, TrackSegment, VideoSummary } from "../api/types";

type Props = {
  video: VideoSummary;
  timeline: TimelineResponse | null;
  currentTime: number;
  onReanalyze: () => void;
  onSeek?: (seconds: number) => void;
};

function formatTime(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "--:--";
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

function formatRange(start: number, end: number): string {
  return `${formatTime(start)}-${formatTime(end)}`;
}

function formatVideoMeta(video: VideoSummary): string {
  const width = video.width ?? "?";
  const height = video.height ?? "?";
  const fps = video.fps == null ? "?" : video.fps.toFixed(2);
  return `${width} x ${height} - ${fps} fps - ${formatTime(video.duration_seconds)} total`;
}

function percent(seconds: number, duration: number): number {
  if (duration <= 0) return 0;
  return Math.max(0, Math.min(100, (seconds / duration) * 100));
}

function segmentWidth(start: number, end: number, duration: number): number {
  return Math.max(0.35, percent(end, duration) - percent(start, duration));
}

function confidence(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function slug(value: string): string {
  return value.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
}

function stringField(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function contextSummary(event: ActivityEvent): string {
  const context = event.evidence.context;
  if (context && typeof context === "object" && "summary" in context) {
    return stringField(context.summary, "No context summary was stored for this event.");
  }
  return "No context summary was stored for this event.";
}

function identitySummary(segment: TrackSegment): string {
  const identity = segment.evidence.identity;
  if (identity && typeof identity === "object" && "method" in identity) {
    return stringField(identity.method, "unknown");
  }
  return "unknown";
}

function activityCounts(events: ActivityEvent[]): { label: string; count: number; averageConfidence: number }[] {
  const counts = new Map<string, { count: number; totalConfidence: number }>();
  for (const event of events) {
    const current = counts.get(event.label) ?? { count: 0, totalConfidence: 0 };
    current.count += 1;
    current.totalConfidence += event.confidence;
    counts.set(event.label, current);
  }
  return Array.from(counts.entries()).map(([label, value]) => ({
    label,
    count: value.count,
    averageConfidence: value.totalConfidence / value.count,
  }));
}

function TrackBar({ segment, duration }: { segment: TrackSegment; duration: number }) {
  return (
    <div
      className="segment segment-lifecycle"
      style={{
        left: `${percent(segment.start_seconds, duration)}%`,
        width: `${segmentWidth(segment.start_seconds, segment.end_seconds, duration)}%`,
      }}
      title={`${segment.state} (${confidence(segment.confidence)}) via ${identitySummary(segment)}`}
    >
      {segment.state}
    </div>
  );
}

function EventBar({
  event,
  duration,
  selected,
  onSelect,
}: {
  event: ActivityEvent;
  duration: number;
  selected: boolean;
  onSelect: (event: ActivityEvent) => void;
}) {
  const width = segmentWidth(event.start_seconds, event.end_seconds, duration);
  const canShowLabel = width >= 7;
  const title = `${formatRange(event.start_seconds, event.end_seconds)} - ${event.label} (${confidence(event.confidence)})`;

  return (
    <button
      type="button"
      className={`segment segment-activity label-${slug(event.label)} ${
        canShowLabel ? "segment-has-label" : "segment-compact"
      } ${selected ? "is-selected" : ""}`}
      style={{
        left: `${percent(event.start_seconds, duration)}%`,
        width: `${width}%`,
      }}
      title={title}
      aria-label={title}
      aria-pressed={selected}
      onClick={() => onSelect(event)}
    >
      {canShowLabel ? (
        <>
          <span className="segment-name">{event.label}</span>
          <span className="segment-confidence">{confidence(event.confidence)}</span>
        </>
      ) : null}
    </button>
  );
}

export function TimelineShell({ video, timeline, currentTime, onReanalyze, onSeek }: Props) {
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [hiddenLabels, setHiddenLabels] = useState<Set<string>>(new Set());
  const [minConfidence, setMinConfidence] = useState(0);
  const [reviewOnly, setReviewOnly] = useState(false);
  const duration = video.duration_seconds ?? 0;
  const cursorPct = percent(currentTime, duration);
  const profileLabel = stringField(timeline?.streamer_profile?.prompt.label, "candidate");
  const allActivityEvents = timeline?.activity_events ?? [];
  const labels = useMemo(() => Array.from(new Set(allActivityEvents.map((event) => event.label))).sort(), [allActivityEvents]);
  const activityEvents = useMemo(
    () =>
      allActivityEvents.filter(
        (event) =>
          !hiddenLabels.has(event.label) &&
          event.confidence >= minConfidence &&
          (!reviewOnly || event.confidence < 0.7),
      ),
    [allActivityEvents, hiddenLabels, minConfidence, reviewOnly],
  );
  const selectedEvent = activityEvents.find((event) => event.id === selectedEventId) ?? activityEvents[0] ?? null;
  const counts = useMemo(() => activityCounts(activityEvents), [activityEvents]);

  function selectAndSeek(event: ActivityEvent) {
    setSelectedEventId(event.id);
    onSeek?.(event.start_seconds);
  }

  function toggleLabel(label: string) {
    setHiddenLabels((current) => {
      const next = new Set(current);
      if (next.has(label)) {
        next.delete(label);
      } else {
        next.add(label);
      }
      return next;
    });
  }

  useEffect(() => {
    if (activityEvents.length === 0) {
      setSelectedEventId(null);
      return;
    }
    if (!selectedEventId || !activityEvents.some((event) => event.id === selectedEventId)) {
      setSelectedEventId(activityEvents[0].id);
    }
  }, [activityEvents, selectedEventId]);

  return (
    <section aria-labelledby="timeline-heading" className="timeline-shell">
      <div className="timeline-head">
        <h2 id="timeline-heading">Timeline</h2>
        <button type="button" onClick={onReanalyze}>
          Re-run analysis
        </button>
      </div>
      <p className="meta">{formatVideoMeta(video)}</p>
      <div className="track" role="presentation" aria-hidden="true">
        <div className="track-fill" style={{ width: `${cursorPct}%` }} data-testid="track-fill" />
      </div>
      {video.thumbnails.length > 0 ? (
        <div className="thumbnail-strip" aria-label="Video thumbnails">
          {video.thumbnails.map((src) => (
            <img key={src} src={src} alt="" loading="lazy" />
          ))}
        </div>
      ) : null}
      {timeline ? (
          <div className="analysis-tracks" aria-label="Automated analysis timeline">
          <div className="timeline-filters" aria-label="Timeline filters">
            {labels.map((label) => (
              <label key={label}>
                <input type="checkbox" checked={!hiddenLabels.has(label)} onChange={() => toggleLabel(label)} /> {label}
              </label>
            ))}
            <label>
              Min confidence {Math.round(minConfidence * 100)}%
              <input
                type="range"
                min="0"
                max="0.95"
                step="0.05"
                value={minConfidence}
                onChange={(event) => setMinConfidence(Number(event.currentTarget.value))}
              />
            </label>
            <label>
              <input type="checkbox" checked={reviewOnly} onChange={(event) => setReviewOnly(event.currentTarget.checked)} /> Review needed
            </label>
          </div>
          <div className="timeline-axis" aria-hidden="true">
            <span>0:00</span>
            <span>{formatTime(duration / 2)}</span>
            <span>{formatTime(duration)}</span>
          </div>
          <div className="analysis-row">
            <div className="analysis-row-label">Streamer</div>
            <div className="analysis-row-track">
              {timeline.lifecycle_segments.map((segment) => (
                <TrackBar key={segment.id} segment={segment} duration={duration} />
              ))}
            </div>
          </div>
          <div className="analysis-row">
            <div className="analysis-row-label">Activity</div>
            <div className="analysis-row-track">
              {activityEvents.map((event) => (
                <EventBar
                  key={event.id}
                  event={event}
                  duration={duration}
                  selected={selectedEvent?.id === event.id}
                  onSelect={selectAndSeek}
                />
              ))}
            </div>
          </div>
          <div className="analysis-legend" aria-label="Activity totals">
            {counts.map((item) => (
              <span key={item.label} className={`legend-pill label-${slug(item.label)}`}>
                {item.label}: {item.count} avg {confidence(item.averageConfidence)}
              </span>
            ))}
          </div>
          {selectedEvent ? (
            <div className="segment-inspector" aria-live="polite">
              <div className="segment-inspector-head">
                <strong>{formatRange(selectedEvent.start_seconds, selectedEvent.end_seconds)}</strong>
                <span className={`segment-badge label-${slug(selectedEvent.label)}`}>{selectedEvent.label}</span>
                <span>{confidence(selectedEvent.confidence)} confidence</span>
              </div>
              <p>{contextSummary(selectedEvent)}</p>
            </div>
          ) : null}
          {activityEvents.length > 0 ? (
            <div className="segment-list" aria-label="Activity segment list">
              {activityEvents.map((event) => (
                <button
                  key={event.id}
                  type="button"
                  className={selectedEvent?.id === event.id ? "segment-list-row is-selected" : "segment-list-row"}
                  onClick={() => selectAndSeek(event)}
                >
                  <span>{formatRange(event.start_seconds, event.end_seconds)}</span>
                  <span className={`segment-dot label-${slug(event.label)}`} aria-hidden="true" />
                  <span>{event.label}</span>
                  <span>{confidence(event.confidence)}</span>
                </button>
              ))}
            </div>
          ) : null}
          <p className="analysis-summary">
            Job {timeline.job.status}. Streamer identity: {timeline.streamer_profile?.confirmed_by ?? "none"} /{" "}
            {profileLabel}.
          </p>
        </div>
      ) : (
        <p className="note">Analyzing timeline...</p>
      )}
      <p className="cursor">
        Cursor: <strong>{formatTime(currentTime)}</strong> / {formatTime(duration)}
      </p>
    </section>
  );
}
