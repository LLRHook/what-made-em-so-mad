import { Navigate, Route, Routes } from "react-router-dom";
import { Nav } from "./components/Nav";
import { ImportPage } from "./routes/ImportPage";
import { ProjectListPage } from "./routes/ProjectListPage";
import { SettingsPage } from "./routes/SettingsPage";
import { TimelineEditorPage } from "./routes/TimelineEditorPage";

export function App() {
  return (
    <div className="layout">
      <header>
        <h1 className="brand">What Made 'Em So Mad</h1>
        <Nav />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<ProjectListPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/videos/:videoId" element={<TimelineEditorPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
