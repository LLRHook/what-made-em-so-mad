# What Made 'Em So Mad

A local-first video analysis app that helps creators find and export moments from streamer VODs.

See [SPEC.md](SPEC.md) for the full product specification.

## Status

The app is now a usable local-first baseline for VOD review and clip export. The heavy model-backed accuracy work is still intentionally optional/planned, but the main product workflows have backend support and minimal UI access.

What works today:

- Local MP4 import with ffprobe metadata extraction and SHA-256 deduplication.
- URL import through an optional user-installed `yt-dlp`.
- Thumbnail strip generation via ffmpeg.
- Automatic timeline generation with ranked OpenCV streamer candidates, ROI preview crops, conservative lifecycle sampling, persisted VAD intervals, OpenCV expression samples, optional imported transcript cues, and optional generated transcription when `faster-whisper` is installed locally.
- ROI candidate inspection, manual streamer-region confirmation, scoped face-crop/embedding artifacts, optional OpenCV SFace embeddings when model assets are installed, histogram-based reacquisition fallback, and rectangular mask artifacts.
- Queued in-process analysis jobs with stage/percent progress, job inspection, explicit run, cancellation, and partial timeline diagnostics for cancelled or failed jobs.
- A simple `scripts/run_worker.py` worker that claims queued analysis jobs from SQLite and runs them outside the API request path.
- Saved diagnostics bundles with ROI overlay specs, identity artifact refs, mask refs, VAD/expression summaries, loudness samples, and fusion contribution traces.
- Timeline filtering, click-to-seek review, segment edit/split/merge/delete APIs, and feedback export.
- Manual and suggested clip drafts, single export, and batch export via ffmpeg (stream-copy when possible), with JSON batch manifests.
- JSON fixture evaluation runs with precision/recall/F1, boundary error, false-positive, and top-K metrics, including a packaged nine-scenario baseline set that runs without user uploads.
- Server-side project list, rename/delete, storage usage, thumbnail/clip cleanup, optional access token protection, and optional upload/download size limits.
- React + Vite frontend with project list, import, timeline review, basic clip controls, and system/storage diagnostics.
- `GET /api/system/capabilities` and `GET /api/system/setup` for environment diagnostics.

Still planned or model-dependent (tracked in [features.md](features.md)):

- Worker hardening/stale-job recovery (FEAT-001), real labeled-video fixture sets (FEAT-002), real SAM segmentation masks (FEAT-003), stronger/calibrated face re-identification model assets (FEAT-004), and calibrated production-grade accuracy metrics.

## Architecture

```
 Browser
   |
   v
 Vite dev server (launcher-chosen port)               scripts/run_worker.py
   |  proxies /api/* ----------------------+            (claims queued jobs)
   v                                       v                    |
 React 18 + TypeScript SPA          FastAPI backend (uvicorn)   |
                                           |                    |
                                           +--------+-----------+
                                                    v
                                       SQLite (data/db/wmesm.sqlite)
                                       media + artifacts (data/videos, data/clips)
                                       optional model assets (models/)
                                       ffmpeg / ffprobe / yt-dlp via subprocess
```

## Tech stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.12, FastAPI, uvicorn, Pydantic v2 |
| Evaluation interval matching | Rust cdylib, called in-process through Python ctypes |
| Persistence | SQLite (schema in `backend/app/db/schema.sql`) |
| Analysis | OpenCV (headless), optional faster-whisper / SFace / YuNet assets |
| Media tooling | ffmpeg + ffprobe (subprocess), optional yt-dlp |
| Worker | `scripts/run_worker.py` (same venv, SQLite job claim) |
| Frontend | React 18, TypeScript 5, Vite 5, react-router 6 |
| Tests | pytest + pytest-asyncio (backend), Vitest + Testing Library (frontend) |
| Deploy | systemd + Caddy or nginx on a VPS (`deploy/`) |

## Repository layout

```
backend/   FastAPI app, SQLite schema, services, tests
frontend/  React + Vite + TypeScript app
scripts/   dev launchers, check_system.py, run_worker.py
deploy/    systemd unit, Caddy/nginx configs, VPS install script
docs/      setup, licenses, architecture
data/      runtime artifacts (gitignored)
models/    model checkpoints (gitignored)
```

## Prerequisites

- Python 3.12+
- Rust 1.85+ and Cargo for backend source/editable installs; platform wheels include the evaluation library.
- Node LTS (v20+) and npm
- `ffmpeg` + `ffprobe` on PATH — optional for boot, required for any real ingestion
- Optional: `yt-dlp` (URL import), `faster-whisper` (transcription), OpenCV YuNet/SFace ONNX assets under `models/opencv/` (face re-identification)

## Quick start

```pwsh
# from the repo root
./scripts/dev.ps1    # Windows / PowerShell
# or
./scripts/dev.sh     # macOS / Linux
```

The dev launcher chooses free localhost ports for both processes and prints the URLs. Set `WMESM_BACKEND_PORT` or `WMESM_FRONTEND_PORT` before launching if you need fixed ports. The frontend proxies `/api/*` to the selected backend port.

To probe your machine without starting the app:

```pwsh
python scripts/check_system.py
```

To run queued analysis jobs outside the API process:

```pwsh
python scripts/run_worker.py        # loop forever
python scripts/run_worker.py --once # process at most one job
```

## Build & test

Backend (from `backend/`, venv at `backend/.venv`):

```pwsh
python -m venv .venv ; .venv\Scripts\Activate.ps1   # first time
pip install -e .[dev]                               # first time
python -m pytest
```

The backend install builds the Rust interval matcher offline from its committed
Cargo lockfile. Evaluation uses this native core for greedy interval matching;
Python retains normalization, metric rounding and SQLite persistence. See
[setup](docs/setup.md) for native build and missing-library errors, and
[architecture](docs/architecture.md#evaluation-interval-matching) for the boundary.

Frontend (from `frontend/`):

```pwsh
npm install        # first time
npm run test       # 29 tests (Vitest)
npm run typecheck  # tsc --noEmit
npm run build      # production bundle into dist/
```

## Configuration

Backend settings come from `WMESM_*` environment variables (or `backend/.env`),
defined in `backend/app/core/config.py`:

| Variable | Default | Purpose |
|---|---|---|
| `WMESM_DATA_DIR` | `<repo>/data` | DB, videos, clips, artifacts |
| `WMESM_MODELS_DIR` | `<repo>/models` | optional model assets |
| `WMESM_CORS_ORIGINS` | `http://localhost:5173`, `http://127.0.0.1:5173` | allowed browser origins for direct API calls; the dev launcher normally uses Vite's proxy on a selected port |
| `WMESM_ACCESS_TOKEN` | unset | when set, non-GET requests require the token (`x-wmesm-token` or `Authorization: Bearer`) |
| `WMESM_MAX_UPLOAD_BYTES` | unset | reject uploads larger than this |
| `WMESM_THUMBNAIL_INTERVAL_SECONDS` | `5.0` | thumbnail strip sampling |
| `WMESM_THUMBNAIL_MAX_HEIGHT` | `180` | thumbnail size |
| `WMESM_DEFAULT_PRE_ROLL_SECONDS` / `WMESM_DEFAULT_POST_ROLL_SECONDS` | `2.0` / `3.0` | clip export padding |

Dev-server knobs: `WMESM_FRONTEND_PORT`, `WMESM_BACKEND_PORT` (read by
`frontend/vite.config.ts` and the `scripts/dev.*` launchers).

## API overview

All routes live under `/api`. Route groups (see `backend/app/api/`):

- `GET /api/health` — liveness + version.
- `/api/system/*` — `capabilities`, `setup`, `storage` diagnostics.
- `/api/videos` — list/upload, `from-url` import, per-video metadata, rename, delete, clips, suggested clips, cleanup, source stream, thumbnails.
- `/api/videos/{id}/analysis-jobs`, `/api/analysis-jobs/{id}/*` — queue, inspect, run, cancel analysis; per-job timeline/VAD/expression features.
- `/api/videos/{id}/roi-candidates`, `streamer-profile`, `transcript`, `transcribe`, `feedback-export`, `diagnostics` — ROI review, manual confirmation, transcripts, diagnostics bundles.
- `/api/activity-events/*` — edit, split, merge, delete timeline segments.
- `/api/clips/*` — drafts, export, batch export, download.
- `/api/evaluation/run`, `/api/evaluation/runs/{id}` — fixture evaluation.

## Testing & verification

[`VERIFICATION.md`](VERIFICATION.md) is the canonical end-to-end V&V protocol —
run it before any release. The sub-15-minute version lives in its Appendix E
smoke checklist.

## Contributing

Work is tracked in the cycle files: defects in [bugs.md](bugs.md) (`BUG-NNN`),
scoped work in [features.md](features.md) (`FEAT-NNN`), and verified/shipped
entries migrate to [CHANGELOG.md](CHANGELOG.md). File a ticket before coding;
tick and migrate it when the work is verified. See `CLAUDE.md` for the full
lifecycle rules.

## License

App code is Apache-2.0 (see [LICENSE](LICENSE)). External model dependencies (SAM 3, MediaPipe, OpenCV, etc.) ship under their own licenses; see [docs/licenses.md](docs/licenses.md).
