import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiCallError, api } from "../api/client";

export function ImportPage() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const navigate = useNavigate();

  async function handleFile(file: File) {
    setBusy(true);
    setError(null);
    setHint(null);
    try {
      const res = await api.uploadVideo(file);
      // The server-side project list is the source of truth (S-001); no
      // localStorage write is needed here.
      navigate(`/videos/${res.video_id}`);
    } catch (e) {
      if (e instanceof ApiCallError) {
        setError(`${e.code}: ${e.message}`);
        if (e.setupHint) setHint(e.setupHint);
      } else {
        setError("Upload failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="import-heading" className="import">
      <h1 id="import-heading">Import a video</h1>
      <p>Drop an MP4 here or use the file picker. The file stays on this machine.</p>
      <label
        className="dropzone"
        onDragOver={(e) => {
          e.preventDefault();
        }}
        onDrop={(e) => {
          e.preventDefault();
          const file = e.dataTransfer.files?.[0];
          if (file) void handleFile(file);
        }}
      >
        <span>{busy ? "Uploading…" : "Drag an MP4 here, or click to choose a file"}</span>
        <input
          type="file"
          accept="video/mp4,video/quicktime,video/x-matroska"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
      </label>
      {error ? (
        <p role="alert" className="error">
          {error}
          {hint ? <> — {hint}</> : null}
        </p>
      ) : null}
    </section>
  );
}
