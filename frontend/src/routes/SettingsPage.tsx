import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { StorageUsageResponse } from "../api/types";
import { CapabilitiesPanel } from "../components/CapabilitiesPanel";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value.toFixed(1)} ${unit}`;
}

export function SettingsPage() {
  const [storage, setStorage] = useState<StorageUsageResponse | null>(null);

  useEffect(() => {
    let alive = true;
    api.storage().then((value) => {
      if (alive) setStorage(value);
    }).catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section aria-labelledby="settings-heading">
      <h1 id="settings-heading">Settings</h1>
      <CapabilitiesPanel />
      {storage ? (
        <section aria-labelledby="storage-heading">
          <h2 id="storage-heading">Storage</h2>
          <p>
            {formatBytes(storage.total_bytes)} in <code>{storage.data_dir}</code>
          </p>
          <p>
            Videos {formatBytes(storage.videos_bytes)} - clips {formatBytes(storage.clips_bytes)} - database{" "}
            {formatBytes(storage.database_bytes)}
          </p>
        </section>
      ) : null}
    </section>
  );
}
