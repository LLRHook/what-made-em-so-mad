# Architecture

This document describes the implemented local-first baseline. The full target architecture remains in [SPEC.md](../SPEC.md).

## Process model

There is a FastAPI process plus an optional repo-level worker script. The compatibility analysis/export routes still run synchronously, queued analysis jobs can autostart through FastAPI background tasks, and `scripts/run_worker.py` can claim queued analysis jobs from SQLite outside the API request path.

```text
[ React/Vite dev server (selected localhost port) ] --proxy /api/*--> [ FastAPI + BackgroundTasks + SQLite (selected localhost port) ] -- subprocess --> ffmpeg / ffprobe / yt-dlp
                                                                               ^
                                                                               |
                                                                      [ scripts/run_worker.py ]
```

The worker path is intentionally small: it atomically claims the oldest queued automatic analysis job and runs the same service function as the API. The remaining worker work is hardening: stale-running recovery, heartbeat metadata, export/transcription jobs, and production supervision.

## Layers

1. **API layer** (`backend/app/api/`) - thin HTTP handlers. Validate input via Pydantic, delegate to services, return Pydantic response models.
2. **Service layer** (`backend/app/services/`) - orchestration and side effects: capability probing, ffprobe parsing, ffmpeg/yt-dlp command execution, thumbnail generation, queued and synchronous timeline analysis, clip lifecycle, storage cleanup, and diagnostics.
3. **Storage layer** - `backend/app/db/` wraps `sqlite3`. The schema stores videos, jobs, ROI candidates, streamer profiles, lifecycle segments, activity events, VAD intervals, expression samples, clips, transcript segments, user annotations, and evaluation runs.
4. **UI layer** (`frontend/src/`) - React 18 + Vite + TypeScript. Routes: project list, import, timeline editor, settings. A small typed `fetch` client (`src/api/client.ts`) is the only data layer.

## Why no ORM

The data shapes are small, mostly JSON-in-text columns, and the queries are simple. The stdlib `sqlite3` driver with `Row` factory plus `PRAGMA foreign_keys=ON` is enough. Adding SQLAlchemy now would be premature.

## Evaluation interval matching

`POST /api/evaluation/run` calls `services/evaluation.py`, which loads either
fixture predictions or an existing timeline, normalizes intervals, and sorts
predictions stably by confidence. `_match_segments` delegates each whole interval
set to `services/interval_matching.py`. That adapter calls the Rust
`native/intervals` cdylib through ctypes in the API process. No process is spawned
for a segment, frame, or evaluation request.

Rust computes interval IoU and greedy one-to-one matching, retaining strict-best
tie selection, inclusive thresholds, and the existing non-finite comparison
behavior. Python owns labels, normalization, confidence ordering, boundary error
arithmetic, top-five selection, rounding, aggregation and final SQLite persistence.
This is a processor port, not a new accuracy model or a measured speed claim.

The C ABI uses Python-owned `double` interval arrays and `size_t` match-index
arrays; Rust retains no pointers. An ABI version check precedes calls, and native
buffer-shape/panic errors and invalid output indices fail explicitly. Setuptools
builds the dependency-free crate in normal source/editable/wheel builds. The loader
uses the package's absolute path and this interpreter's extension suffix, never
PATH or a fallback glob. Missing/incompatible artifacts produce a typed 503 only
when needed; boot and optional media/model capability gating remain independent.
Native execution errors produce a typed 500. Scoring finishes before the existing
evaluation INSERT, so a failed call persists no partial run.

Analysis job transitions and the worker's SQLite compare-and-swap claim are outside
this boundary. Regression coverage races two claimants against metadata-only
temporary SQLite rows. The frozen pre-native evaluator in `tests/reference/`
supplies independent matching and complete metric parity checks.

## Worker model

The heaviest implemented operation is still an external process (`yt-dlp`, `ffmpeg`, or OpenCV sampling). Analysis can now be queued, run in a FastAPI background task, or claimed by `scripts/run_worker.py`. The worker persists queued jobs in SQLite, but a job that is already marked `running` still needs stale-job recovery if the worker process crashes. Worker hardening should happen before generated transcription, SAM-style tracking, or long batch analysis becomes default.

## Capability probing

`backend/app/services/capabilities.py` performs each check independently and never raises. The result is shaped like this:

```json
{
  "ffmpeg": {"available": true, "version": "..."},
  "ffprobe": {"available": true, "version": "..."},
  "yt_dlp": {"available": false, "reason": "binary_not_on_path"},
  "sam3": {"available": false, "reason": "package_not_installed"},
  "cuda": {"available": false, "reason": "torch_not_installed"},
  "mediapipe": {"available": false, "reason": "package_not_installed"},
  "opencv_models": {"yunet": false, "sface": false}
}
```

This shape is exposed as both an HTTP endpoint (`GET /api/system/capabilities`) and a CLI (`scripts/check_system.py`).
The backend marks YuNet/SFace true only when the matching OpenCV API and ONNX files exist under `models/opencv/`.

## Error contract

When ffmpeg/ffprobe is missing on a route that requires it, the API returns:

```http
HTTP/1.1 503 Service Unavailable
Content-Type: application/json

{"error": "ffmpeg_missing", "setup_hint": "Install ffmpeg and ensure it is on PATH. See docs/setup.md."}
```

When yt-dlp is missing on `/api/videos/from-url`, the API returns `501 Not Implemented` with a similar `setup_hint`.

## Storage layout

```text
data/
  db/wmesm.sqlite
  videos/{video_id}/
    source.mp4
    thumbs/{n}.jpg
    diagnostics/{job_id}-{timestamp}.diagnostics.json
  clips/{clip_id}/
    output.mp4
```

Paths are absolute under `WMESM_DATA_DIR` (default: `<repo>/data`). Database rows store paths relative to that root where practical so the data directory can be relocated.

## Implemented review workflow

- `POST /api/videos` imports a local file.
- `POST /api/videos/from-url` imports through `yt-dlp` when installed.
- `POST /api/videos/{id}/analyze` supports `fast`, `balanced`, and `best` profiles. Current profiles adjust analysis window size and evidence capture; optional model-backed providers are used when installed.
- `POST /api/videos/{id}/analysis-jobs` creates a queued analysis job, optionally autostarts it in-process, and returns immediately with progress metadata.
- `GET /api/analysis-jobs/{id}` reads queued/running/complete/failed/cancelled job state.
- `POST /api/analysis-jobs/{id}/run` runs a queued job synchronously; this is useful for tests and future worker handoff.
- `POST /api/analysis-jobs/{id}/cancel` marks queued/running jobs cancelled, and job timelines remain readable for partial diagnostics.
- `python scripts/run_worker.py` loops over queued automatic analysis jobs; `--once` processes at most one job.
- ROI candidate detection ranks multiple recurring face-like regions and stores preview crops under `data/videos/{video_id}/roi/`.
- `GET /api/analysis-jobs/{id}/vad` returns persisted VAD intervals.
- `GET /api/analysis-jobs/{id}/expression-features` returns persisted expression samples.
- `GET /api/videos/{id}/roi-candidates` exposes automatic streamer candidates.
- `GET /api/videos/{id}/roi-candidates/{candidate_id}/preview` serves a stored ROI preview crop when available.
- `POST /api/videos/{id}/streamer-profile` stores a user-confirmed streamer ROI and analysis prefers it over auto candidates.
- Streamer profiles store face crop and embedding artifacts under `data/videos/{video_id}/identity/` when frames are available. OpenCV SFace is used when `models/opencv/face_recognition_sface*.onnx` and the OpenCV API are available; otherwise the local histogram fallback is used.
- Lifecycle segments write rectangular mask fallback artifacts under `data/videos/{video_id}/masks/{job_id}/`; this is the artifact contract real SAM masks should replace.
- `GET/POST /api/videos/{id}/transcript` lets imported transcript segments strengthen activity labels.
- `POST /api/videos/{id}/transcribe` generates transcript segments when `faster-whisper` is installed locally.
- `PATCH/DELETE /api/activity-events/{id}`, `POST /api/activity-events/{id}/split`, and `POST /api/activity-events/merge` persist reviewer corrections as `user_annotations`.
- `POST /api/videos/{id}/suggested-clips` creates draft clips from high-confidence timeline events.
- `PATCH /api/clips/{id}` accepts or renames a clip draft.
- `DELETE /api/clips/{id}` rejects a clip draft and removes owned clip artifacts.
- `POST /api/clips/batch-export` exports selected draft clips and writes a JSON batch manifest under `data/manifests/`.
- `GET /api/videos/{id}/feedback-export` returns structured review corrections.
- `GET /api/videos/{id}/diagnostics` bundles video, timeline, ROI candidates, transcripts, VAD intervals, expression samples, capabilities, and storage context.
- `POST /api/videos/{id}/diagnostics/export` writes a JSON diagnostics bundle with ROI overlay specs, identity artifact refs, mask refs, VAD/expression summaries, loudness samples, and fusion contribution traces.
- `POST /api/evaluation/run` scores JSON fixture sets against stored jobs or explicit prediction payloads and persists metrics. Without a custom path, it uses the packaged `backend/app/evaluation/fixtures.json` nine-scenario baseline unless `data/evaluation/fixtures.json` exists.
- `GET /api/evaluation/runs/{id}` returns a stored evaluation run.
- `GET /api/system/storage` and `POST /api/videos/{id}/cleanup` expose storage usage and artifact cleanup, including generated diagnostics.

## Server-side project list

The catalogue of uploaded videos lives in SQLite, not in the browser. `GET /api/videos` returns every persisted video newest-first, each enriched with its latest analysis job and clip count in a single round trip, so the project list is identical on any computer that opens the app.

- `GET /api/videos` - list view (`VideoListEntry[]`).
- `PATCH /api/videos/{id}` - set or clear a user-supplied display `title`.
- `DELETE /api/videos/{id}` - remove the row, cascade dependent rows, then delete owned video and clip artifacts best-effort.

## Access control and limits

By default the app remains simple for local use. Hosted deployments can set:

- `WMESM_ACCESS_TOKEN` to require `X-WMESM-Token` or `Authorization: Bearer ...` on state-changing routes.
- `WMESM_MAX_UPLOAD_BYTES` to reject oversized local uploads or URL downloads after staging.

## Remaining architecture work

- Add worker stale-job recovery, heartbeat metadata, and support for export/transcription jobs.
- Integrate real SAM 3 mask generation when the external SAM 3 package/checkpoints/GPU environment are installed and licensed.
- Calibrate SFace or a stronger face re-identification provider against real labeled fixtures.
- Replace the packaged JSON evaluation baseline with representative labeled video fixtures.
- Render diagnostics overlays, crops, loudness graphs, and model contribution traces in the UI.
