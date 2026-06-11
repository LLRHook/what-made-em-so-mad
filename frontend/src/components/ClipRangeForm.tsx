import { useState } from "react";

export type ClipFormValues = {
  start_seconds: number;
  end_seconds: number;
  title: string;
  labels: string[];
  pre_roll_seconds: number;
  post_roll_seconds: number;
};

type Props = {
  initialStart?: number;
  initialEnd?: number;
  videoDuration?: number | null;
  onSubmit: (values: ClipFormValues) => void;
  busy?: boolean;
};

export function validateRange(start: number, end: number, duration: number | null | undefined): string | null {
  if (!Number.isFinite(start) || start < 0) return "start must be ≥ 0";
  if (!Number.isFinite(end) || end <= start) return "end must be greater than start";
  if (duration != null && start > duration) return "start is past end of video";
  return null;
}

export function ClipRangeForm({ initialStart = 0, initialEnd = 5, videoDuration, onSubmit, busy }: Props) {
  const [start, setStart] = useState(initialStart);
  const [end, setEnd] = useState(initialEnd);
  const [title, setTitle] = useState("");
  const [labelsText, setLabelsText] = useState("");
  const [preRoll, setPreRoll] = useState(2);
  const [postRoll, setPostRoll] = useState(3);
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const err = validateRange(start, end, videoDuration);
    if (err) {
      setError(err);
      return;
    }
    setError(null);
    onSubmit({
      start_seconds: start,
      end_seconds: end,
      title,
      labels: labelsText
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      pre_roll_seconds: preRoll,
      post_roll_seconds: postRoll,
    });
  }

  return (
    <form onSubmit={handleSubmit} aria-labelledby="clip-form-heading" className="clip-form">
      <h3 id="clip-form-heading">Export a manual clip</h3>
      <label>
        Start (seconds)
        <input
          type="number"
          step="0.1"
          min={0}
          value={start}
          onChange={(e) => setStart(Number(e.target.value))}
        />
      </label>
      <label>
        End (seconds)
        <input
          type="number"
          step="0.1"
          min={0}
          value={end}
          onChange={(e) => setEnd(Number(e.target.value))}
        />
      </label>
      <label>
        Pre-roll (seconds)
        <input
          type="number"
          step="0.5"
          min={0}
          value={preRoll}
          onChange={(e) => setPreRoll(Number(e.target.value))}
        />
      </label>
      <label>
        Post-roll (seconds)
        <input
          type="number"
          step="0.5"
          min={0}
          value={postRoll}
          onChange={(e) => setPostRoll(Number(e.target.value))}
        />
      </label>
      <label>
        Title (optional)
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label>
        Labels (comma-separated, optional)
        <input value={labelsText} onChange={(e) => setLabelsText(e.target.value)} />
      </label>
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      <button type="submit" disabled={busy}>
        {busy ? "Exporting…" : "Export clip"}
      </button>
    </form>
  );
}
