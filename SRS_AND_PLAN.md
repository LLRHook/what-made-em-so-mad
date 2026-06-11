# What Made 'Em So Mad — SRS & Plan of Action

Version: 1.0 (draft)
Date: 2026-06-11
Status: Derived from SPEC.md, README.md, docs/, and a full code review of the implemented system (backend, frontend, scripts, deploy, tests, docs). Every finding was verified against source with file:line references. Part I specifies requirements with conformance status; Part II is the remediation plan. Requirement IDs (FR-x.y / NFR-xx) and plan IDs (Px-y) cross-reference each other.

---

# Part I — Software Requirements Specification

## 1. Introduction

### 1.1 Purpose

This SRS specifies the functional and non-functional requirements of *What Made 'Em So Mad* (WMESM), a local-first video analysis application that helps creators find, review, and export reaction moments from streamer VODs. It serves three purposes: a single source of truth for what the system must do, a conformance baseline that the current implementation can be audited against, and a contract for the remediation work in Part II.

### 1.2 Scope

In scope: video ingestion (local file and URL), automated multimodal timeline analysis (streamer presence, voice activity, expression, activity labels), timeline review and correction, clip drafting and export, fixture-based evaluation, project/storage management, and optional hosted (VPS) deployment with access control.

Out of scope (planned, per SPEC.md): real SAM segmentation masks, calibrated production-grade face re-identification, real labeled-video fixture sets, and multi-tenant accounts.

### 1.3 Definitions

| Term | Meaning |
|---|---|
| Project / Video | One imported VOD and all derived artifacts (thumbnails, jobs, segments, clips, diagnostics). |
| Analysis job | A queued/running/complete/failed/cancelled record in `analysis_jobs` driving the pipeline. |
| Lifecycle segment | Streamer presence state over a time range: `present`, `partial`, `lost`, `AFK`, `returned`. |
| Activity event | Evidence-backed label over a time range: `talking`, `laughing`, `yelling/frustrated`, `focused`. |
| ROI | Candidate facecam region detected in the video frame. |
| VAD | Voice activity detection (Silero when installed, energy fallback otherwise). |
| Clip | A draft or exported sub-range of a source video, manual or suggested. |
| Worker | External process (`scripts/run_worker.py`) that claims queued jobs from SQLite. |
| Hosted mode | Deployment on a VPS behind Caddy/nginx with `WMESM_ACCESS_TOKEN` set. |

### 1.4 References

SPEC.md (improvement backlog and acceptance criteria), docs/architecture.md, docs/setup.md, docs/licenses.md, backend/app (FastAPI implementation), frontend/src (React UI).

## 2. Overall Description

### 2.1 Product perspective

WMESM is a two-tier application: a FastAPI backend (Python 3.12) over a single SQLite database and a filesystem artifact store (`data/`), and a React + Vite + TypeScript frontend. Heavy media work shells out to `ffmpeg`/`ffprobe` (required) and `yt-dlp` (optional). ML capabilities (Silero VAD, faster-whisper, OpenCV SFace/YuNet models) are optional and the pipeline must degrade gracefully without them. Three execution contexts may touch the database concurrently: API request handlers, FastAPI background tasks, and the external worker.

### 2.2 User classes

A single trusted operator (the creator) in local mode; the same operator plus untrusted network traffic in hosted mode. There is no multi-user model; access control is a single shared token.

### 2.3 Constraints

- C-1: SQLite is the only database; all coordination (job claiming, cancellation) must be safe under multi-process access.
- C-2: All analysis must run with zero optional ML packages installed, producing conservative fallback output.
- C-3: App code is Apache-2.0; model assets carry their own licenses (docs/licenses.md).
- C-4: Local-first privacy: media, transcripts, and embeddings never leave the host unless the user explicitly exports them.

### 2.4 Assumptions and dependencies

Python 3.12, Node LTS, ffmpeg/ffprobe on PATH for any real ingestion. Hosted mode assumes a reverse proxy terminates the public interface.

## 3. Functional Requirements

Requirement convention: **FR-x.y**. Each requirement carries a conformance status from the 2026-06-11 code review: ✅ met, ⚠️ partially met / defective, ❌ not met. Defects reference Part II item IDs.

### 3.1 Ingestion

- **FR-1.1** The system shall accept local uploads of `.mp4`, `.mov`, `.m4v`, `.mkv` files, compute a SHA-256, and deduplicate byte-identical imports by returning the existing project. ✅
- **FR-1.2** The system shall enforce the configured `max_upload_bytes` limit *before* the upload consumes disk, rejecting oversized requests. ⚠️ Limit is checked only after the full file is written to disk (P0-2).
- **FR-1.3** The system shall import videos from a URL via `yt-dlp` when installed, treating the URL strictly as data (never as program options) and restricting schemes to http/https. ⚠️ URL is passed as a positional argument without `--`; `cookies_path` is an unvalidated server path (P0-3).
- **FR-1.4** Upload processing shall not block service of concurrent requests. ❌ The upload handler is `async` but performs streaming, hashing, ffprobe, thumbnailing, and full analysis synchronously on the event loop (P0-1).
- **FR-1.5** On ingestion failure (probe error, yt-dlp failure), the system shall remove partially written artifacts and return a typed error. ⚠️ yt-dlp failures clean up; probe failures leak the written file and return a 500 (P1-7).
- **FR-1.6** The system shall extract duration, resolution, codec, and fps metadata via ffprobe and generate a thumbnail strip via ffmpeg (best-effort). ✅
- **FR-1.7** Concurrent duplicate uploads shall resolve to a single project without surfacing an integrity error. ⚠️ TOCTOU between SHA lookup and insert can raise an unhandled `IntegrityError` (P1-6).

### 3.2 Analysis pipeline

- **FR-2.1** The system shall generate an automatic timeline after import: ranked ROI candidates with preview crops, lifecycle segments, VAD intervals, expression samples, activity events, and a diagnostics bundle. ✅
- **FR-2.2** The system shall support analysis profiles `fast` / `balanced` / `best` mapped to sampling windows, recorded in job metadata. ✅
- **FR-2.3** Analysis jobs shall be queueable (HTTP 202), runnable explicitly, cancellable, and inspectable with stage/percent progress. ✅ (API level)
- **FR-2.4** A job shall be executed at most once at a time; starting a job shall be an atomic state transition. ❌ `run_analysis_job` accepts `queued` *and* `running` status with a non-guarded UPDATE; the API background task and the worker can both run the same job, duplicating all derived rows (P0-4).
- **FR-2.5** Cancellation shall be terminal: a job cancelled during execution shall never be reported `complete`, and in-flight subprocesses shall be stopped promptly. ⚠️ Final completion UPDATE has no `AND status='running'` guard; cancel only flips DB state and does not stop OpenCV loops or the ffmpeg audio subprocess (P1-1, P1-2).
- **FR-2.6** Jobs orphaned by a crashed worker or API process shall be detected and requeued or failed; a zombie `running` job shall not permanently block a video. ❌ No stale-job recovery exists, and `running` jobs are treated as reusable by timeline reads (P1-3).
- **FR-2.7** The system shall accept a manually confirmed streamer region, persist it as a stronger-evidence profile, and use it on re-analysis. ✅
- **FR-2.8** Transcripts shall be importable, optionally generatable via faster-whisper, and usable as fusion evidence; transcription shall be user-disableable. ✅ (backend; not exposed in UI — see FR-6.6)
- **FR-2.9** All activity events shall persist evidence JSON and fusion-rule version; confidence shall follow the calibrated bands defined in SPEC.md L-003. ✅ (bands present; calibration itself remains planned work)

### 3.3 Timeline review and correction

- **FR-3.1** The system shall serve the latest timeline with lifecycle segments, activity events, label filters, confidence filtering, and click-to-seek. ✅
- **FR-3.2** Users shall be able to edit, split, merge, delete, confirm, and reject activity events; corrections shall be exportable as feedback JSON. ✅ (backend; UI exposes none of the editing operations — see FR-6.6)
- **FR-3.3** Timeline reads shall distinguish "analysis in progress", "analysis failed", and "no analysis" states. ⚠️ Frontend renders a permanent "Analyzing timeline..." on fetch failure (P2-3).

### 3.4 Clips and export

- **FR-4.1** Users shall create manual clip drafts (validated `end > start`, within video duration) and generate suggested drafts from high-confidence events with recorded reasons. ⚠️ Backend validates; frontend permits `end` past EOF, negative rolls reach the API, and empty inputs coerce to 0 (P2-4).
- **FR-4.2** The system shall export single clips and batches via ffmpeg with pre/post-roll, stream-copy when possible, writing a JSON batch manifest. ✅
- **FR-4.3** A failed or interrupted export shall leave the clip in a recoverable state. ⚠️ Clips committed as `exporting` before ffmpeg runs are unrecoverable after a crash; batch export aborts wholesale on a non-`AppError` (timeout) instead of failing one clip (P1-4, P1-5).
- **FR-4.4** Exported clips shall be downloadable; pre/post-roll behavior shall be consistent between single and batch export. ⚠️ Frontend batch export hardcodes 0/0 roll while single export uses form values (P2-5).

### 3.5 Evaluation and diagnostics

- **FR-5.1** The system shall run JSON fixture evaluations (packaged nine-scenario baseline or user fixtures) producing precision/recall/F1, boundary error, false-positive, and top-K metrics. ✅
- **FR-5.2** Evaluation fixture paths supplied by clients shall be restricted to a designated fixtures directory. ❌ Any server filesystem path is accepted and read (P0-6).
- **FR-5.3** Diagnostics bundles (ROI overlays, identity refs, mask refs, VAD/expression summaries, loudness, fusion traces) shall be persisted per job and exportable. ✅

### 3.6 Frontend (user-facing capability parity)

- **FR-6.1** Project list with rename, delete (confirmed), status, duration, and clip count. ✅
- **FR-6.2** Import page with file picker and drag-drop; busy state shall prevent concurrent uploads through *all* input paths. ⚠️ Drag-drop bypasses the busy guard (P2-6).
- **FR-6.3** Timeline editor with player, thumbnail strip, filters, inspector, and seek. ✅
- **FR-6.4** Re-analysis shall use the asynchronous job API with progress display and cancellation, not the blocking `/analyze` call, and shall be guarded against concurrent invocation. ❌ UI uses the synchronous endpoint, button is not disabled while busy, and no polling/cancel UI exists despite full client support (P2-1).
- **FR-6.5** When `WMESM_ACCESS_TOKEN` is configured, the frontend shall let the user supply the token and attach it to mutating requests. ❌ No token support; every mutation fails irrecoverably in hosted mode (P2-2).
- **FR-6.6** The UI shall expose backend capabilities that define the product per SPEC.md: URL import, async job progress, transcripts, ROI candidate inspection/streamer confirmation, segment editing (split/merge/delete/confirm), per-clip download/accept/rename/delete, diagnostics review, feedback export. ❌ None of these are reachable from the UI today (P3-1).

### 3.7 System, storage, and administration

- **FR-7.1** Capability and setup diagnostics endpoints shall report ffmpeg/ffprobe/yt-dlp/model availability. ✅
- **FR-7.2** Storage usage per project, artifact cleanup (thumbnails/clips/diagnostics, failed-only option), and project deletion shall be available. ✅ (backend; cleanup UI lacks confirmation — P2-7)
- **FR-7.3** Identifier path parameters (`video_id`, `candidate_id`, job/clip IDs) shall be validated against their canonical format before any filesystem access. ❌ `video_id` is interpolated into filesystem paths unvalidated; percent-encoded separators enable path traversal on file-serving routes (P0-5).

## 4. Non-Functional Requirements

### 4.1 Security

- **NFR-S1** With an access token configured, *all* non-public routes — including reads that serve media, diagnostics (which embed server paths), and downloads — shall require the token, compared in constant time (`secrets.compare_digest`). ❌ Guard exempts all GET/HEAD/OPTIONS and uses `!=` (P0-7).
- **NFR-S2** Hosted deployments shall terminate TLS; tokens shall never transit plaintext HTTP. ❌ Shipped Caddy/nginx configs listen on port 80 only (P0-8).
- **NFR-S3** No client-supplied value shall reach a subprocess argument vector or filesystem path without validation (covers FR-1.3, FR-5.2, FR-7.3).
- **NFR-S4** API responses shall not leak server-internal absolute paths (`local_path`, embedding paths) (P3-4).

### 4.2 Reliability and concurrency

- **NFR-R1** SQLite connections shall enable WAL mode and a busy timeout; concurrent API/background/worker writes shall not surface `database is locked` errors under normal load. ❌ Default journal mode, no busy timeout (P1-0).
- **NFR-R2** All job state transitions shall be atomic compare-and-swap operations (covers FR-2.4–2.6).
- **NFR-R3** The worker shall survive transient errors (backoff + retry), handle SIGTERM/SIGINT by requeueing or finishing the in-flight job, and emit heartbeats. ❌ Loop has no exception handling, backoff, or signal handling (P1-3).
- **NFR-R4** Job claiming order shall be deterministic (`ORDER BY created_at, rowid` or sub-second timestamps) (P3-6).

### 4.3 Performance

- **NFR-P1** Long-running media work (upload streaming, hashing, probing, analysis) shall run off the event loop (threadpool, background task, or worker).
- **NFR-P2** Hot queries (latest job per video, claimable jobs) shall be index-backed rather than `json_extract` table scans (P3-2).
- **NFR-P3** Audio RMS computation shall use vectorized operations, not per-sample Python loops (P3-3).
- **NFR-P4** Timeline playback UI shall not re-render the full page on every `timeupdate` (P3-7).

### 4.4 Portability and operability

- **NFR-O1** Dev scripts shall work on stock macOS bash 3.2 and Windows PowerShell 5.1 as documented (P2-9).
- **NFR-O2** The remote install script shall be idempotent, install every unit it enables (including a worker unit), not clobber existing host config, and either deploy or document the expected app/frontend staging (P1-8).
- **NFR-O3** The project shall be under version control with CI running backend tests, frontend tests, lint, and type checks (P4-1, P4-2).
- **NFR-O4** Schema migrations shall be versioned (`PRAGMA user_version`) with legacy-upgrade tests (P3-5).

### 4.5 Privacy

- **NFR-PR1** Transcripts, embeddings, and feedback exports remain local; sharing is explicit (currently met; must be preserved by all remediation work).

## 5. External Interface Summary

REST API under `/api` (see docs/architecture.md for detail): system (`/health`, `/system/capabilities`, `/system/setup`, `/system/storage`), videos (CRUD, `/from-url`, `/source`, `/thumbnails/{f}`, `/cleanup`, `/clips`, `/suggested-clips`), analysis (`/analyze` sync, `/analysis-jobs` async + `/run`, `/cancel`, `/timeline`, `/vad`, `/expression-features`, ROI candidates, streamer profile, transcript import/generate, activity-event PATCH/DELETE/split/merge, feedback export, diagnostics), clips (CRUD, `/export`, `/batch-export`, `/download`), evaluation (`/run`, `/runs/{id}`). Auth: optional `x-wmesm-token` or `Authorization: Bearer` header. Errors: typed `{error, message, setup_hint}` detail objects — except FastAPI 422 validation arrays, which the frontend must also handle (P2-8).

## 6. Conformance Summary

Of the requirements above: the core analysis pipeline, clip export, evaluation, and storage subsystems are functional and well-tested at the API level. The dominant gap clusters are (1) input-trust and authentication boundaries unsuitable for the hosted mode the docs advertise, (2) job/export lifecycle atomicity under concurrent execution, and (3) a frontend that exposes roughly half of the backend's product surface. Part II sequences the remediation.

---

# Part II — Plan of Action

Severity: **Critical** = exploitable or data-corrupting today · **High** = correctness failure in normal use · **Medium** = degraded behavior / recoverable failure · **Low** = quality/polish.

## Phase 0 — Security & input-trust hotfixes (do first, small diffs)

These make hosted mode safe and close exploitable holes. Estimated scope: 1–2 days total.

| ID | Sev | Finding | Location | Fix |
|---|---|---|---|---|
| P0-1 | Critical | `async def import_local_video` does streaming, SHA-256, ffprobe, thumbnails, and full analysis synchronously on the event loop — one upload freezes the entire server | `backend/app/api/videos.py:58-106` | Make handler `def` (threadpool) and move thumbnails/analysis to a background task or the worker |
| P0-2 | Critical | `max_upload_bytes` checked only after the whole file is on disk (default: no limit) — disk-exhaustion DoS | `backend/app/api/videos.py:66-71`, `services/videos.py:116-120` | Enforce cap incrementally during streaming; reject oversized `Content-Length` up front; delete partial file on breach |
| P0-3 | High | yt-dlp argument injection: URL passed positionally with no `--` terminator (a `-`-prefixed "URL" becomes an option, incl. `--exec`); `cookies_path` is an arbitrary server path | `backend/app/api/videos.py:124-134` | Insert `--` before the URL, validate scheme ∈ {http, https}, restrict `cookies_path` to a configured directory |
| P0-4 | Critical | Job start is not atomic: `run_analysis_job` accepts status `queued` **and** `running`, unguarded UPDATE, no rowcount check — API background task and worker can run the same job twice, inserting duplicate track/vad/expression/activity rows | `backend/app/services/analysis.py:154-181`; trigger paths `api/analysis.py:92-93,107-109` | CAS claim: `UPDATE ... SET status='running' WHERE id=? AND status='queued'`; bail if `rowcount != 1`; refuse `running` jobs |
| P0-5 | High | Path traversal: `video_id`/`candidate_id` path params used unvalidated in filesystem paths; Starlette decodes `%2F` after routing, so encoded `../` escapes the videos dir on `GET /source` and friends | `backend/app/api/videos.py:209-225`, `api/analysis.py:137-141` | Validate IDs against `^[0-9a-f]{32}$` (or DB lookup) before touching the filesystem; 404 otherwise |
| P0-6 | High | `POST /api/evaluation/run` reads any caller-supplied server filesystem path | `backend/app/services/evaluation.py:42-47` | Restrict `fixture_set_path` to `data/evaluation/` (resolve + prefix check) |
| P0-7 | High | Access-token guard exempts all GET/HEAD/OPTIONS — with a token set, anyone can enumerate and download every video, clip, and diagnostics bundle (which leaks server paths); comparison uses `!=` (timing side-channel) | `backend/app/main.py:46-58` | Require token on all non-`/api/health` routes (or explicit public allowlist); use `secrets.compare_digest` |
| P0-8 | High | Hosted configs serve plaintext HTTP only (Caddy `:80`, nginx `listen 80`, firewall opens 80) — token transits cleartext | `deploy/Caddyfile`, `deploy/nginx-wmesm.conf`, `deploy/install_remote.sh:40` | Enable Caddy automatic HTTPS with a domain (or document TLS as a hard prerequisite); open 443 |

**Exit criteria:** new tests for token guard (both header forms, read-route coverage), upload-cap streaming rejection, ID-format 404s, yt-dlp arg construction; manual probe of `%2F` traversal returns 404.

## Phase 1 — Concurrency & job/export lifecycle correctness

Makes the queue/worker model trustworthy (SPEC F-007 groundwork). Scope: ~3–5 days.

| ID | Sev | Finding | Location | Fix |
|---|---|---|---|---|
| P1-0 | High | SQLite opened without WAL or `busy_timeout` while three writer classes coexist (API threads, background tasks, worker); worker polls with `BEGIN IMMEDIATE` every 5 s — `database is locked` errors under load | `backend/app/db/database.py:152-166`, `services/analysis.py:287` | `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout` at connect; cheap read before taking the immediate transaction |
| P1-1 | High | Completion UPDATE has no `AND status='running'` guard — a cancellation landing during the final insert window is overwritten to `complete` | `backend/app/services/analysis.py:259-271` | Conditional final update + cancel re-check immediately before it |
| P1-2 | Medium | Cancel only flips DB state; OpenCV loops and the ffmpeg audio subprocess keep running until the next checkpoint; subprocess never killed | `backend/app/services/analysis.py:442-462, 1964` | Thread cancel checks into long loops; terminate the audio subprocess on cancel |
| P1-3 | High | No stale-job recovery: a crashed run leaves `running` forever; `_latest_reusable_job` treats `running` as reusable, so the video is wedged. Worker loop has no exception handling, backoff, or SIGTERM/SIGINT handling (KeyboardInterrupt strands jobs as `running`) | `backend/app/services/analysis.py:284-298, 734-747`; `scripts/run_worker.py:23-36` | Heartbeat in `progress_json`; reaper requeues/fails `running` jobs past threshold; worker: try/except + exponential backoff + signal handlers |
| P1-4 | Medium | Clips committed `exporting` before ffmpeg runs are permanently stuck after a crash (re-export allowed only from `draft`/`failed`) | `backend/app/services/clips.py:233-262` | Timeout-based reset of stale `exporting`, or allow re-export from `exporting` |
| P1-5 | Medium | Batch export catches only `AppError`; `subprocess.TimeoutExpired`/`OSError` aborts the whole batch with a 500 | `backend/app/services/clips.py:197-210`; `services/ffmpeg.py:109-112` | Catch timeout/OSError per clip, mark failed, continue; convert ffmpeg timeout to `FFmpegExecutionError` |
| P1-6 | Medium | Duplicate-upload TOCTOU: concurrent identical uploads race past `find_video_by_sha` into a UNIQUE violation → unhandled 500 | `backend/app/api/videos.py:73-90` | `INSERT ... ON CONFLICT(sha256)` and resolve to existing row |
| P1-7 | Medium | `probe_file` failure leaves orphaned `source.mp4` and returns a bare 500 | `backend/app/api/videos.py:82` | try/except → rmtree + typed `ValidationFailedError` |
| P1-8 | High | `install_remote.sh` enables/starts a systemd unit it never installs (aborts mid-run under `set -e`); assumes app pre-staged; creates empty `frontend/dist`; clobbers `/etc/caddy/Caddyfile`; no root check; no worker unit; doesn't install yt-dlp | `deploy/install_remote.sh:6,14,19-26,33,40` | Install unit + `daemon-reload` before enable; add staging/build steps or document them; write site config without clobbering; add a `wmesm-worker` unit; EUID check |

**Exit criteria:** concurrency test — worker + autostart racing one job runs it exactly once; kill -9 mid-analysis then verify reaper requeues; kill mid-export then verify clip recoverable; batch with one timing-out clip completes the rest.

## Phase 2 — Frontend correctness & hosted usability

| ID | Sev | Finding | Location | Fix |
|---|---|---|---|---|
| P2-1 | High | Re-analysis uses the blocking `/analyze` call, button never disabled (`busy` not set), no abort/epoch guard — spam forks N concurrent full analyses, stale results can render, setState after unmount | `frontend/src/routes/TimelineEditorPage.tsx:52-60`, `components/TimelineShell.tsx:192` | Switch to the already-implemented job API (`queueAnalysisJob`/`getAnalysisJob` polling + cancel, `client.ts:80-105`); disable while busy |
| P2-2 | High | No access-token support in the client although the backend enforces one — hosted mode: every mutation fails with no recovery path | `frontend/src/api/client.ts:28-35` | Token field on SettingsPage, persisted, attached as `x-wmesm-token` in `call()` |
| P2-3 | Medium | Timeline fetch failure renders a permanent, false "Analyzing timeline..." | `TimelineEditorPage.tsx:36-42`, `TimelineShell.tsx:296-297` | Distinct empty/error/in-progress states + retry button (FR-3.3) |
| P2-4 | Medium | Clip form validation holes: `end > duration` accepted (silently clamped server-side), negative pre/post-roll submitted (cryptic 422), empty input coerces to `start=0`; prefill-from-playhead props are dead (seeded into `useState` once at mount) | `components/ClipRangeForm.tsx:20-33`, `TimelineEditorPage.tsx:186-187` | Extend `validateRange` (end ≤ duration, rolls ≥ 0, reject empty); add "Use playhead" button instead of dead props |
| P2-5 | Low | Batch export hardcodes 0/0 pre/post-roll while single export uses form defaults 2/3 — inconsistent output the user can't see | `TimelineEditorPage.tsx:127-131` | Use shared roll settings for both paths |
| P2-6 | Medium | Drag-drop bypasses the busy guard → concurrent uploads | `routes/ImportPage.tsx:41-45` | `if (busy) return;` in the drop handler |
| P2-7 | Medium | "Delete thumbnails" is destructive with no confirmation and no busy guard; failed export leaves an invisible orphan draft later swept into batch export | `TimelineEditorPage.tsx:82-95, 141-152` | Confirm dialog + busy; refresh clip list in `finally` |
| P2-8 | Medium | FastAPI 422 arrays produce `http_422: ` blank errors (client expects `{error,message}` object) | `frontend/src/api/client.ts:43-49` | Handle `Array.isArray(detail)` → join `msg` fields |
| P2-9 | Low | Dev-script portability: `"${pids[@]}"` under `set -u` breaks macOS bash 3.2; dev.ps1 declares `#requires 5.1` but hardcodes `pwsh`; player shows black box for MKV/MOV (served as `video/mp4`, no `onError`); `check_system.py` probes yt-dlp with `-version` and always exits 0 | `scripts/dev.sh:25,45`, `scripts/dev.ps1:34-37`, `components/Player.tsx:8-19`, `scripts/check_system.py:30-37,121-124` | Guard array expansion; detect pwsh vs powershell; add player `onError` hint; fix `--version` and propagate exit code |

## Phase 3 — Product-surface parity & architecture

| ID | Sev | Finding | Fix |
|---|---|---|---|
| P3-1 | High | UI exposes roughly half the backend (FR-6.6): no URL import, transcripts, ROI inspection/streamer confirmation, segment edit/split/merge, clip download links/accept/rename/delete/error display, diagnostics review, feedback export, per-video storage breakdown, opencv_models status. Six client functions are dead code | Prioritize per SPEC implementation order: (1) async job progress UI, (2) streamer confirmation, (3) segment editing, (4) clip management incl. download links + `error_message`, (5) URL import, (6) transcripts, (7) diagnostics review (SPEC E-003) |
| P3-2 | Medium | Hot job queries filter on `json_extract(params_json,'$.kind')` with no index; `list_videos` globs thumbnails per row; `_dir_size` rglobs the data dir per storage call | Materialize a `kind` column + `(video_id, created_at)` index; store thumbnail names in DB; cache storage usage |
| P3-3 | Medium | `_pcm16_rms` is a per-sample Python loop over 16 kHz audio (`analysis.py:1994-2004`); stderr drain pattern can deadlock (`analysis.py:1985`) | `numpy.frombuffer`; `stderr=DEVNULL` |
| P3-4 | Medium | API leaks server-internal absolute paths (`local_path`, embedding paths) in responses (`domain/models.py:62,90`); `_update_job_params` read-modify-write races `_update_job_progress` | Drop paths from responses; single-transaction `json_set` |
| P3-5 | Medium | Migrations are predicate-based with no version tracking; 7 of 8 migrations lack legacy-upgrade tests; `clips.job_id` lacks an FK | `PRAGMA user_version` gating; FK with `ON DELETE SET NULL`; upgrade tests |
| P3-6 | Low | `_now_iso()` truncates to seconds; claim ordering ties are unspecified — `test_worker_runs_oldest_queued_analysis_job` asserts unguaranteed FIFO (flaky) | `ORDER BY created_at, rowid`; sub-second timestamps |
| P3-7 | Low | `analysis.py` is a 2,487-line god-module; full page re-renders ~4×/s during playback; duplicated `sha256_file`; duplicated fetch/error boilerplate in 8 frontend call sites | Split analysis into detection/vad/expression/fusion/persistence modules; isolate playhead rendering; consolidate on `services/hashing.py`; extract `useApi`/`formatApiError` |

## Phase 4 — Engineering hygiene & verification

| ID | Sev | Finding | Fix |
|---|---|---|---|
| P4-1 | High | Project is not under version control (no `.git`); `.gitignore` gaps: `tsconfig*.tsbuildinfo` unignored, whitelisted `.gitkeep` files don't exist, `*.sqlite-wal/-shm` unignored | `git init` + initial commit before any Phase 0 work; fix `.gitignore`; add the `.gitkeep` files |
| P4-2 | Medium | No CI, no lint/format config (ruff `noqa` codes present but ruff not configured; no eslint/prettier) | GitHub Actions: pytest, vitest, ruff, tsc, eslint; pre-commit hooks |
| P4-3 | Medium | Test gaps: access-token middleware and `max_upload_bytes` have zero tests; no job-failure/cancel-mid-run/claim-race tests; `TimelineEditorPage`, `ImportPage`, `SettingsPage`, `Player` untested; fusion/tracking internals only exercised via monkeypatched orchestration; `test_analysis_api.py:37-57` patches `_detect_streamer_candidate` (singular) which the pipeline no longer calls directly | Add tests alongside each phase's fixes (see exit criteria); fix the stale monkeypatch |
| P4-4 | Low | Doc drift: README layout omits `deploy/`; `install_remote.sh` referenced by no doc; `httpx` listed as test-only but declared runtime; `opencv-python-headless>=4.13` floor needs verification; `pytest-asyncio` configured with no async tests | Doc pass + dependency cleanup |

## Suggested sequencing

1. **P4-1 first** (version control) — nothing else should land without history.
2. **Phase 0** as one reviewed hardening changeset; ship before any further hosted use.
3. **Phase 1** — concurrency fixes are prerequisites for SPEC F-007 (worker hardening) and for trusting the async-job UI built in Phase 2.
4. **Phase 2** (P2-1/P2-2 first — they unblock hosted usability), then **Phase 3** parity work in SPEC's own implementation order.
5. **Phase 4** items P4-2/P4-3 run continuously alongside phases 1–3.

## Verification strategy

Each phase lands with regression tests named in its exit criteria. Add one cross-cutting concurrency suite (worker + API racing the same job; cancel-during-final-insert; crash-recovery reaper) and one hosted-mode security suite (token on reads, traversal probes, oversized upload, `-`-prefixed URL). Re-run the packaged nine-scenario evaluation baseline after Phase 1 and Phase 3 to confirm analysis output is unchanged (byte-identical metrics) by refactors.
