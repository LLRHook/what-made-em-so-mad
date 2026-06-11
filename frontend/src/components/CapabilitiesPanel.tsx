import { useEffect, useState } from "react";
import { ApiCallError, api } from "../api/client";
import type { CapabilitiesResponse, SetupResponse } from "../api/types";

export function CapabilitiesPanel() {
  const [caps, setCaps] = useState<CapabilitiesResponse | null>(null);
  const [setup, setSetup] = useState<SetupResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([api.capabilities(), api.setup()])
      .then(([c, s]) => {
        if (!alive) return;
        setCaps(c);
        setSetup(s);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(e instanceof ApiCallError ? `${e.code}: ${e.message}` : "request failed");
      });
    return () => {
      alive = false;
    };
  }, []);

  if (error) return <p role="alert">Failed to load capabilities: {error}</p>;
  if (!caps || !setup) return <p>Loading capabilities…</p>;

  return (
    <section aria-labelledby="caps-heading" className="panel">
      <h2 id="caps-heading">System capabilities</h2>
      <table className="caps-table">
        <thead>
          <tr>
            <th>Component</th>
            <th>Status</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {(["ffmpeg", "ffprobe", "yt_dlp", "mediapipe", "cuda", "sam3"] as const).map((key) => {
            const entry = caps[key];
            return (
              <tr key={key}>
                <td>{key}</td>
                <td>
                  <span
                    role="status"
                    aria-label={entry.available ? `${key} available` : `${key} missing`}
                    className={entry.available ? "dot dot-ok" : "dot dot-missing"}
                  />
                  {entry.available ? "available" : "missing"}
                </td>
                <td>{entry.available ? entry.version ?? entry.device ?? "ok" : entry.reason ?? "unknown"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <h3>Required for current milestone</h3>
      <ul>
        {setup.items
          .filter((i) => i.required)
          .map((i) => (
            <li key={i.name}>
              <strong>{i.name}</strong>: {i.available ? "ok" : i.hint ?? "missing"}
            </li>
          ))}
      </ul>
      <p>
        Overall:{" "}
        <strong>{setup.all_required_present ? "ready to import videos" : "setup required (see above)"}</strong>
      </p>
    </section>
  );
}
