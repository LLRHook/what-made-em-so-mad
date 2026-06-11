# Plan of Action — Bug Fixes & Improvements

Version: 1.0
Date: 2026-06-11
Basis: Full codebase review (backend, frontend, scripts, deploy, tests, docs), 2026-06-11. Every finding below was verified against source with file:line references. Cross-references: SRS.md requirement IDs.

Severity: **Critical** = exploitable or data-corrupting today · **High** = correctness failure in normal use · **Medium** = degraded behavior / recoverable failure · **Low** = quality/polish.

---

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

---

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

---

## Phase 2 — Frontend correctness & hosted usability

| ID | Sev | Finding | Location | Fix |
|---|---|---|---|---|
| P2-1 | High | Re-analysis uses the blocking `/analyze` call, button never disabled (`busy` not set), no abort/epoch guard — spam forks N concurrent full analyses, stale results can render, setState after unmount | `frontend/src/routes/TimelineEditorPage.tsx:52-60`, `components/TimelineShell.tsx:192` | Switch to the already-implemented job API (`queueAnalysisJob`/`getAnalysisJob` polling + cancel, `client.ts:80-105`); disable while busy |
| P2-2 | High | No access-token support in the client although the backend enforces one — hosted mode: every mutation fails with no recovery path | `frontend/src/api/client.ts:28-35` | Token field on SettingsPage, persisted, attached as `x-wmesm-token` in `call()` |
| P2-3 | Medium | Timeline fetch failure renders a permanent, false "Analyzing timeline..." | `TimelineEditorPage.tsx:36-42`, `TimelineShell.tsx:296-297` | Distinct empty/error/in-progress states + retry button (SRS FR-3.3) |
| P2-4 | Medium | Clip form validation holes: `end > duration` accepted (silently clamped server-side), negative pre/post-roll submitted (cryptic 422), empty input coerces to `start=0`; prefill-from-playhead props are dead (seeded into `useState` once at mount) | `components/ClipRangeForm.tsx:20-33`, `TimelineEditorPage.tsx:186-187` | Extend `validateRange` (end ≤ duration, rolls ≥ 0, reject empty); add "Use playhead" button instead of dead props |
| P2-5 | Low | Batch export hardcodes 0/0 pre/post-roll while single export uses form defaults 2/3 — inconsistent output the user can't see | `TimelineEditorPage.tsx:127-131` | Use shared roll settings for both paths |
| P2-6 | Medium | Drag-drop bypasses the busy guard → concurrent uploads | `routes/ImportPage.tsx:41-45` | `if (busy) return;` in the drop handler |
| P2-7 | Medium | "Delete thumbnails" is destructive with no confirmation and no busy guard; failed export leaves an invisible orphan draft later swept into batch export | `TimelineEditorPage.tsx:82-95, 141-152` | Confirm dialog + busy; refresh clip list in `finally` |
| P2-8 | Medium | FastAPI 422 arrays produce `http_422: ` blank errors (client expects `{error,message}` object) | `frontend/src/api/client.ts:43-49` | Handle `Array.isArray(detail)` → join `msg` fields |
| P2-9 | Low | Dev-script portability: `"${pids[@]}"` under `set -u` breaks macOS bash 3.2; dev.ps1 declares `#requires 5.1` but hardcodes `pwsh`; player shows black box for MKV/MOV (served as `video/mp4`, no `onError`); `check_system.py` probes yt-dlp with `-version` and always exits 0 | `scripts/dev.sh:25,45`, `scripts/dev.ps1:34-37`, `components/Player.tsx:8-19`, `scripts/check_system.py:30-37,121-124` | Guard array expansion; detect pwsh vs powershell; add player `onError` hint; fix `--version` and propagate exit code |

---

## Phase 3 — Product-surface parity & architecture

| ID | Sev | Finding | Fix |
|---|---|---|---|
| P3-1 | High | UI exposes roughly half the backend (SRS FR-6.6): no URL import, transcripts, ROI inspection/streamer confirmation, segment edit/split/merge, clip download links/accept/rename/delete/error display, diagnostics review, feedback export, per-video storage breakdown, opencv_models status. Six client functions are dead code | Prioritize per SPEC implementation order: (1) async job progress UI, (2) streamer confirmation, (3) segment editing, (4) clip management incl. download links + `error_message`, (5) URL import, (6) transcripts, (7) diagnostics review (SPEC E-003) |
| P3-2 | Medium | Hot job queries filter on `json_extract(params_json,'$.kind')` with no index; `list_videos` globs thumbnails per row; `_dir_size` rglobs the data dir per storage call | Materialize a `kind` column + `(video_id, created_at)` index; store thumbnail names in DB; cache storage usage |
| P3-3 | Medium | `_pcm16_rms` is a per-sample Python loop over 16 kHz audio (`analysis.py:1994-2004`); stderr drain pattern can deadlock (`analysis.py:1985`) | `numpy.frombuffer`; `stderr=DEVNULL` |
| P3-4 | Medium | API leaks server-internal absolute paths (`local_path`, embedding paths) in responses (`domain/models.py:62,90`); `_update_job_params` read-modify-write races `_update_job_progress` | Drop paths from responses; single-transaction `json_set` |
| P3-5 | Medium | Migrations are predicate-based with no version tracking; 7 of 8 migrations lack legacy-upgrade tests; `clips.job_id` lacks an FK | `PRAGMA user_version` gating; FK with `ON DELETE SET NULL`; upgrade tests |
| P3-6 | Low | `_now_iso()` truncates to seconds; claim ordering ties are unspecified — `test_worker_runs_oldest_queued_analysis_job` asserts unguaranteed FIFO (flaky) | `ORDER BY created_at, rowid`; sub-second timestamps |
| P3-7 | Low | `analysis.py` is a 2,487-line god-module; full page re-renders ~4×/s during playback; duplicated `sha256_file`; duplicated fetch/error boilerplate in 8 frontend call sites | Split analysis into detection/vad/expression/fusion/persistence modules; isolate playhead rendering; consolidate on `services/hashing.py`; extract `useApi`/`formatApiError` |

---

## Phase 4 — Engineering hygiene & verification

| ID | Sev | Finding | Fix |
|---|---|---|---|
| P4-1 | High | Project is not under version control (no `.git`); `.gitignore` gaps: `tsconfig*.tsbuildinfo` unignored, whitelisted `.gitkeep` files don't exist, `*.sqlite-wal/-shm` unignored | `git init` + initial commit before any Phase 0 work; fix `.gitignore`; add the `.gitkeep` files |
| P4-2 | Medium | No CI, no lint/format config (ruff `noqa` codes present but ruff not configured; no eslint/prettier) | GitHub Actions: pytest, vitest, ruff, tsc, eslint; pre-commit hooks |
| P4-3 | Medium | Test gaps: access-token middleware and `max_upload_bytes` have zero tests; no job-failure/cancel-mid-run/claim-race tests; `TimelineEditorPage`, `ImportPage`, `SettingsPage`, `Player` untested; fusion/tracking internals only exercised via monkeypatched orchestration; `test_analysis_api.py:37-57` patches `_detect_streamer_candidate` (singular) which the pipeline no longer calls directly | Add tests alongside each phase's fixes (see exit criteria); fix the stale monkeypatch |
| P4-4 | Low | Doc drift: README layout omits `deploy/`; `install_remote.sh` referenced by no doc; `httpx` listed as test-only but declared runtime; `opencv-python-headless>=4.13` floor needs verification; `pytest-asyncio` configured with no async tests | Doc pass + dependency cleanup |

---

## Suggested sequencing

1. **P4-1 first** (version control) — nothing else should land without history.
2. **Phase 0** as one reviewed hardening changeset; ship before any further hosted use.
3. **Phase 1** — concurrency fixes are prerequisites for SPEC F-007 (worker hardening) and for trusting the async-job UI built in Phase 2.
4. **Phase 2** (P2-1/P2-2 first — they unblock hosted usability), then **Phase 3** parity work in SPEC's own implementation order.
5. **Phase 4** items P4-2/P4-3 run continuously alongside phases 1–3.

## Verification strategy

Each phase lands with regression tests named in its exit criteria. Add one cross-cutting concurrency suite (worker + API racing the same job; cancel-during-final-insert; crash-recovery reaper) and one hosted-mode security suite (token on reads, traversal probes, oversized upload, `-`-prefixed URL). Re-run the packaged nine-scenario evaluation baseline after Phase 1 and Phase 3 to confirm analysis output is unchanged (byte-identical metrics) by refactors.
