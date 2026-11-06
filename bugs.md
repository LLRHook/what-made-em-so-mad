# What Made 'Em So Mad — Bug Tracker

This file is the working list of known issues. New bugs are appended here as they
are found. When a bug is fixed, tick its checkbox and move the entry (with the fix
note) to `CHANGELOG.md` so this file stays focused on outstanding work.

## Conventions

Each entry uses the form:

```
### [<id>] <short title>
- [ ] **Severity:** crit | high | med | low
- **Area:** api | analysis | worker | frontend | db | deploy | tests | tooling | docs
- **File(s):** comma-separated paths (or "n/a")
- **Observation:** what was seen, with line refs where useful.
- **Expected:** what should happen, citing the spec section if relevant.
- **Repro / Notes:** how to confirm, or why it matters.
- **Status:** open | in-progress | fixed-pending-migration
```

Tick `- [x]` once verified fixed. The fixer also adds a `**Fix:**` line summarising
the change before migrating the entry to `CHANGELOG.md`.

Severity guide:
- **crit** — blocks build, run, or a hard product constraint.
- **high** — data loss, crash, or a defining MVP feature is wrong.
- **med** — a documented MVP feature is missing or noticeably broken.
- **low** — UX polish, minor spec drift, or test-quality issue.

Area tags for this project: `api` (FastAPI routes), `analysis` (timeline / ROI /
VAD / fusion services), `worker` (queued-job runner), `frontend` (React app),
`db` (SQLite schema and access), `deploy` (VPS configs under `deploy/`),
`tests`, `tooling` (dev environment, lint, git), `docs`.

---

## Open

### [BUG-001] Repository is not under git version control
- [ ] **Severity:** low
- **Area:** tooling
- **File(s):** n/a (repo root)
- **Observation:** `git rev-parse HEAD` fails with "not a git repository" even
  though a curated `.gitignore` exists at the repo root. There is no `.git/`
  directory.
- **Expected:** the project is versioned so releases can be tagged, V&V runs can
  record a SHA (`VERIFICATION.md § 6.4`), and changes are reviewable. The
  `.gitignore` indicates git was always the intent.
- **Repro / Notes:** run `git status` at the repo root. Fix is `git init` plus an
  initial commit; `.gitignore` already excludes `data/`, `models/`, venvs, and
  `node_modules/`. Until fixed, V&V run metadata records `no-git` instead of a SHA.
  Also fix the `.gitignore` gaps surfaced in the 2026-06-11 review (P4-1): add
  `*.sqlite-wal` / `*.sqlite-shm` (created once WAL mode lands — BUG-014) and
  `*.tsbuildinfo`. (The review's ".gitkeep files don't exist" note is already
  stale — `data/.gitkeep` and `models/.gitkeep` both exist.)
- **Status:** open

### [BUG-002] Frontend build and typecheck fail: `process` is untyped in vite.config.ts
- [x] **Severity:** crit
- **Area:** frontend, tooling
- **File(s):** frontend/vite.config.ts, frontend/tsconfig.json, frontend/package.json
- **Observation:** `npm run build` and `npm run typecheck` exit 2 with
  `vite.config.ts(4,29): error TS2591: Cannot find name 'process'` (and the same
  at 5,28). vite.config.ts reads `process.env.WMESM_FRONTEND_PORT` /
  `WMESM_BACKEND_PORT`, but `tsconfig.json` includes `vite.config.ts` with
  `"types": ["vite/client", "vitest/globals", "@testing-library/jest-dom"]` and
  `@types/node` is not installed, so Node globals are unknown to tsc.
- **Expected:** `npm run build` produces `dist/` and `npm run typecheck` passes.
- **Repro / Notes:** `cd frontend && npm run typecheck` (exit 2, output above,
  2026-06-11). Fix direction: install `@types/node` as a devDependency and add
  `"node"` to the tsconfig `types` array, or type the config via
  `import process from "node:process"`. Tests (`npm run test`) still pass; only
  build/typecheck are blocked.
- **Fix:** resolved by a concurrent edit during the V&V bootstrap (2026-06-11,
  not applied by this run): a `declare const process: { env: ... }` shim was
  added to frontend/vite.config.ts:4. Re-verified: `npm run typecheck` and
  `npm run build` both exit 0.
- **Status:** fixed-pending-migration

### [BUG-003] README and dev scripts claim port 5173, but vite.config.ts now defaults to a random port
- [x] **Severity:** low
- **Area:** docs, tooling
- **File(s):** README.md, scripts/dev.ps1, scripts/dev.sh, frontend/vite.config.ts
- **Observation:** vite.config.ts sets `port: Number(process.env.WMESM_FRONTEND_PORT ?? "0")`,
  so with no env var the dev server binds an OS-assigned random port. README's
  quick start ("the Vite dev server on http://localhost:5173") and
  `scripts/dev.ps1` ("Starting frontend on http://localhost:5173") still
  advertise the old fixed port. Backend CORS defaults (`app/core/config.py`)
  also only allow origin 5173, so a random-port dev server may hit CORS issues
  on credentialed requests.
- **Expected:** docs, launcher scripts, vite config, and CORS defaults agree on
  the default dev port (either restore the 5173 default or document
  `WMESM_FRONTEND_PORT` everywhere it matters).
- **Repro / Notes:** read frontend/vite.config.ts:4-11 against README.md:43 and
  scripts/dev.ps1:36-37. Found during V&V bootstrap, 2026-06-11.
- **Fix:** resolved by a concurrent edit during the V&V bootstrap (2026-06-11,
  not applied by this run): scripts/dev.ps1 and scripts/dev.sh now allocate
  free ports (or honor `WMESM_BACKEND_PORT` / `WMESM_FRONTEND_PORT`) and pass
  them to both processes, and README's quick start documents the
  launcher-chosen-ports behaviour. CORS defaults still list only 5173 origins,
  but dev traffic goes through the Vite proxy (same-origin), so this is not a
  defect in the launcher flow.
- **Status:** fixed-pending-migration

### [BUG-004] Access-token guard exempts all reads and uses a non-constant-time compare
- [ ] **Severity:** crit
- **Area:** api, deploy
- **File(s):** backend/app/main.py:46-58
- **Observation:** with `WMESM_ACCESS_TOKEN` set, the middleware only guards
  non-`GET/HEAD/OPTIONS` methods (`main.py:49`), so every read route is
  unauthenticated. Demonstrated live during V&V run 2026-06-11 (token set,
  no token supplied): `POST /api/videos` → 401 (correct), but
  `GET /api/videos` → 200 and `GET /api/system/storage` → 200. The token
  comparison also uses `!=` (`main.py:53`), a timing side-channel.
- **Expected:** SRS NFR-S1 — with a token configured, all non-public routes
  (including media/diagnostics/download reads) require the token, compared with
  `secrets.compare_digest`. Only an explicit public allowlist (e.g.
  `/api/health`) is exempt.
- **Repro / Notes:** matches PLAN_OF_ACTION.md P0-7. In hosted mode this lets
  anyone enumerate and download every video, clip, and diagnostics bundle.
  Re-verify after fix with the Stage 4.1 probes (reads must 401 without token).
- **Status:** open

### [BUG-005] Unauthenticated read routes leak server-internal absolute paths
- [ ] **Severity:** high
- **Area:** api
- **File(s):** backend/app/api/system.py:22, backend/app/domain/models.py
- **Observation:** `GET /api/system/storage` returns 200 without a token
  (see BUG-004) and, with diagnostics/video responses, exposes server-internal
  absolute paths (`local_path`, embedding paths). Confirmed live 2026-06-11 that
  `/api/system/storage` is reachable unauthenticated.
- **Expected:** SRS NFR-S4 — API responses must not leak server-internal
  absolute paths, and these routes must be behind the access-token guard
  (BUG-004).
- **Repro / Notes:** matches PLAN_OF_ACTION.md P3-4. Blocked-by/related to
  BUG-004; both are part of the hosted-mode hardening gate.
- **Status:** open

### [BUG-006] Verified remediation backlog (SRS.md / PLAN_OF_ACTION.md) lives outside the cycle trackers
- [x] **Severity:** med
- **Area:** docs
- **File(s):** SRS.md, PLAN_OF_ACTION.md, bugs.md, features.md
- **Observation:** a 2026-06-11 full code review produced `SRS.md` (numbered
  FR/NFR requirements with conformance status) and `PLAN_OF_ACTION.md` (30
  file:line-verified findings across Phases 0–4, including 8 Critical/High
  Phase-0 security items: P0-1..P0-8). None of these are filed as `BUG-NNN` /
  `FEAT-NNN`, so the cycle trackers (`bugs.md` / `features.md`) and the review
  backlog have diverged into two parallel systems.
- **Expected:** per CLAUDE.md the four trackers are the source of truth;
  PLAN_OF_ACTION's findings should be groomed into `bugs.md` (defects) and
  `features.md` (FEAT, e.g. the already-filed FEAT-001/005/006 overlap) so each
  has a single resolvable id and a lifecycle.
- **Repro / Notes:** read SRS.md § 6 and PLAN_OF_ACTION.md. Recommended action:
  run `/groom PLAN_OF_ACTION.md` to convert the backlog into cycle tickets, then
  `/implement` Phase 0 first. This V&V run filed BUG-004/005 for the two
  Phase-0 items it demonstrated live; the rest remain tracked only in
  PLAN_OF_ACTION.md until groomed. Until Phase 0 is resolved, hosted mode is
  not release-ready.
- **Fix:** groomed 2026-06-11 from `SRS_AND_PLAN.md`. The 28 plan items were
  decomposed into cycle tickets: BUG-007..030 (defects/perf), FEAT-007..019
  (frontend parity, CI, refactors), with P0-7→BUG-004, P1-3→FEAT-001,
  P4-2(lint)→FEAT-005, P4-1→BUG-001 (extended), P3-4a→BUG-005 reconciled. The
  SRS/PLAN docs remain as reference; the cycle trackers are now the lifecycle
  source of truth.
- **Status:** fixed-pending-migration

### [BUG-007] Upload handler runs streaming, hashing, probing, and analysis on the event loop
- [ ] **Severity:** crit
- **Area:** api
- **File(s):** backend/app/api/videos.py:58-106, backend/app/services/videos.py
- **Observation:** `async def import_local_video` performs streaming, SHA-256,
  ffprobe, thumbnail generation, and full analysis synchronously on the event
  loop — one upload freezes the entire server for all concurrent requests.
- **Expected:** SRS FR-1.4 / NFR-P1 — long-running media work runs off the event
  loop.
- **Repro / Notes:** PLAN P0-1. Fix: make the handler `def` (FastAPI runs it in
  a threadpool) and move thumbnails/analysis to a background task or the worker.
- **Status:** open

### [BUG-008] `max_upload_bytes` enforced only after the whole file is on disk
- [ ] **Severity:** crit
- **Area:** api
- **File(s):** backend/app/api/videos.py:66-71, backend/app/services/videos.py:116-120
- **Observation:** the configured upload cap is checked only after the full file
  has been written to disk (default: no limit) — a disk-exhaustion DoS. The
  middleware/limit also has zero test coverage (PLAN P4-3).
- **Expected:** SRS FR-1.2 — enforce the cap incrementally during streaming;
  reject an oversized `Content-Length` up front; delete the partial file on breach.
- **Repro / Notes:** PLAN P0-2. Land with tests: oversized streaming rejection,
  up-front `Content-Length` rejection, partial-file cleanup.
- **Status:** open

### [BUG-009] yt-dlp argument injection: URL positional with no `--`, unvalidated `cookies_path`
- [ ] **Severity:** high
- **Area:** api
- **File(s):** backend/app/api/videos.py:124-134
- **Observation:** the import URL is passed to `yt-dlp` positionally without a
  `--` terminator, so a `-`-prefixed "URL" becomes an option (including
  `--exec`); `cookies_path` is an arbitrary server filesystem path.
- **Expected:** SRS FR-1.3 / NFR-S3 — the URL is treated strictly as data
  (`--` terminator), scheme restricted to {http, https}, and `cookies_path`
  restricted to a configured directory.
- **Repro / Notes:** PLAN P0-3. Land with a test asserting the constructed
  argv and scheme rejection.
- **Status:** open

### [BUG-010] Analysis job start is not an atomic compare-and-swap
- [ ] **Severity:** crit
- **Area:** analysis
- **File(s):** backend/app/services/analysis.py:154-181, backend/app/api/analysis.py:92-93,107-109
- **Observation:** `run_analysis_job` accepts status `queued` **and** `running`
  with an unguarded UPDATE and no rowcount check, so the API background task and
  the worker can run the same job twice, inserting duplicate
  track/vad/expression/activity rows.
- **Expected:** SRS FR-2.4 / NFR-R2 — CAS claim:
  `UPDATE ... SET status='running' WHERE id=? AND status='queued'`; bail if
  `rowcount != 1`; refuse already-`running` jobs.
- **Repro / Notes:** PLAN P0-4. The single most important concurrency invariant.
  Land with a claim-race test (two claimers, exactly one runs).
- **Status:** open

### [BUG-011] Path traversal via unvalidated `video_id` / `candidate_id` in filesystem paths
- [ ] **Severity:** high
- **Area:** api
- **File(s):** backend/app/api/videos.py:209-225, backend/app/api/analysis.py:137-141
- **Observation:** ID path params are interpolated into filesystem paths without
  validation; Starlette decodes `%2F` after routing, so an encoded `../` escapes
  the videos directory on `GET /source` and similar file-serving routes.
- **Expected:** SRS FR-7.3 / NFR-S3 — validate IDs against their canonical format
  (`^[0-9a-f]{32}$` or a DB lookup) before any filesystem access; 404 otherwise.
- **Repro / Notes:** PLAN P0-5. Land with a `%2f`-traversal test returning 404.
- **Status:** open

### [BUG-012] `POST /api/evaluation/run` reads any caller-supplied server path
- [ ] **Severity:** high
- **Area:** api, analysis
- **File(s):** backend/app/services/evaluation.py:42-47
- **Observation:** the evaluation runner reads any filesystem path supplied by
  the caller as `fixture_set_path`.
- **Expected:** SRS FR-5.2 / NFR-S3 — restrict `fixture_set_path` to
  `data/evaluation/` (resolve + prefix check); reject anything outside it.
- **Repro / Notes:** PLAN P0-6. Land with a path-escape rejection test.
- **Status:** open

### [BUG-013] Hosted reverse-proxy configs serve plaintext HTTP only
- [ ] **Severity:** high
- **Area:** deploy
- **File(s):** deploy/nginx-wmesm.conf, deploy/Caddyfile, deploy/install_remote.sh:40
- **Observation:** the shipped Caddy/nginx configs listen on port 80 only and the
  install script opens only 80, so a configured access token transits cleartext.
- **Expected:** SRS NFR-S2 — hosted deployments terminate TLS; the token never
  transits plaintext HTTP.
- **Approach (per grooming decision):** target **nginx** as the canonical reverse
  proxy (TLS termination + listen 443, HTTP→HTTPS redirect); document the
  certificate source (e.g. certbot/Let's Encrypt for a real domain). The Caddy
  config can be retired or kept as a documented alternative.
- **Repro / Notes:** PLAN P0-8. **Deferred:** there is no domain/public site yet,
  so this is filed but not actionable until one exists — revisit before the first
  public deployment. Not a blocker for local use.
- **Status:** open

### [BUG-014] SQLite opened without WAL or busy_timeout under three concurrent writers
- [ ] **Severity:** high
- **Area:** db
- **File(s):** backend/app/db/database.py:152-166, backend/app/services/analysis.py:287
- **Observation:** connections use the default rollback journal with no
  `busy_timeout` while API threads, background tasks, and the worker all write;
  the worker polls with `BEGIN IMMEDIATE` every 5 s — producing
  `database is locked` errors under load.
- **Expected:** SRS NFR-R1 — `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout` at
  connect; take a cheap read before the immediate transaction.
- **Repro / Notes:** PLAN P1-0. When WAL lands, also add `*.sqlite-wal` /
  `*.sqlite-shm` to `.gitignore` (tracked under BUG-001).
- **Status:** open

### [BUG-015] Cancellation is neither terminal nor prompt
- [ ] **Severity:** high
- **Area:** analysis
- **File(s):** backend/app/services/analysis.py:259-271, 442-462, 1964
- **Observation:** the final completion UPDATE has no `AND status='running'`
  guard, so a cancellation landing during the final insert window is overwritten
  to `complete` (P1-1); and cancel only flips DB state — OpenCV loops and the
  ffmpeg audio subprocess keep running until the next checkpoint, and the
  subprocess is never killed (P1-2).
- **Expected:** SRS FR-2.5 — a cancelled job never reports `complete`, and
  in-flight subprocesses stop promptly.
- **Repro / Notes:** PLAN P1-1, P1-2. Land with a cancel-during-final-insert test
  and a subprocess-termination check.
- **Status:** open

### [BUG-016] Clip export not crash-recoverable; batch export aborts wholesale on timeout
- [ ] **Severity:** med
- **Area:** api
- **File(s):** backend/app/services/clips.py:197-210, 233-262, backend/app/services/ffmpeg.py:109-112
- **Observation:** clips committed as `exporting` before ffmpeg runs are stuck
  permanently after a crash (re-export allowed only from `draft`/`failed`); batch
  export catches only `AppError`, so a `subprocess.TimeoutExpired`/`OSError`
  aborts the whole batch with a 500.
- **Expected:** SRS FR-4.3 — a failed/interrupted export leaves the clip
  recoverable; one timing-out clip fails alone and the batch continues.
- **Repro / Notes:** PLAN P1-4, P1-5. Convert ffmpeg timeout to
  `FFmpegExecutionError`; reset/allow re-export of stale `exporting`; per-clip
  try/except in batch. Land with a "one clip times out, rest complete" test.
- **Status:** open

### [BUG-017] Ingestion failure/race handling: dup-upload TOCTOU and probe-failure file leak
- [ ] **Severity:** med
- **Area:** api
- **File(s):** backend/app/api/videos.py:73-90, 82
- **Observation:** concurrent identical uploads race past `find_video_by_sha`
  into a UNIQUE violation → unhandled 500 (P1-6); a `probe_file` failure leaves
  an orphaned `source.mp4` and returns a bare 500 (P1-7).
- **Expected:** SRS FR-1.5, FR-1.7 — duplicate uploads resolve to one project
  without an integrity error; probe failure removes partial artifacts and returns
  a typed error.
- **Repro / Notes:** PLAN P1-6, P1-7. Use `INSERT ... ON CONFLICT(sha256)` and
  resolve to the existing row; wrap probe in try/except → rmtree +
  `ValidationFailedError`.
- **Status:** open

### [BUG-018] `install_remote.sh` is non-idempotent and installs a unit it never writes
- [ ] **Severity:** high
- **Area:** deploy
- **File(s):** deploy/install_remote.sh:6,14,19-26,33,40
- **Observation:** the script enables/starts a systemd unit it never installs
  (aborts mid-run under `set -e`), assumes the app is pre-staged, creates an empty
  `frontend/dist`, clobbers `/etc/caddy/Caddyfile`, has no root check, ships no
  worker unit, and does not install yt-dlp.
- **Expected:** SRS NFR-O2 — idempotent; installs + `daemon-reload`s every unit it
  enables (including a `wmesm-worker` unit); writes site config without
  clobbering; documents or performs app/frontend staging; EUID check.
- **Repro / Notes:** PLAN P1-8. Coordinate with BUG-013 (nginx vs Caddy) — the
  reverse-proxy install step should match the chosen proxy.
- **Status:** open

### [BUG-019] Timeline fetch failure renders a permanent false "Analyzing timeline…"
- [ ] **Severity:** med
- **Area:** frontend
- **File(s):** frontend/src/routes/TimelineEditorPage.tsx:36-42, frontend/src/components/TimelineShell.tsx:296-297
- **Observation:** a failed timeline fetch renders a permanent, false
  "Analyzing timeline..." with no error or retry.
- **Expected:** SRS FR-3.3 — distinct "in progress", "failed", and "no analysis"
  states, with a retry path on failure.
- **Repro / Notes:** PLAN P2-3. Land with a component test for the error state.
- **Status:** open

### [BUG-020] Clip form validation holes and inconsistent pre/post-roll between single and batch export
- [ ] **Severity:** med
- **Area:** frontend
- **File(s):** frontend/src/components/ClipRangeForm.tsx:20-33, frontend/src/routes/TimelineEditorPage.tsx:127-131,186-187
- **Observation:** the clip form accepts `end > duration` (silently clamped
  server-side), submits negative pre/post-roll (cryptic 422), coerces empty input
  to `start=0`, and the prefill-from-playhead props are dead (seeded into
  `useState` once at mount); batch export hardcodes 0/0 roll while single export
  uses the form defaults (2/3) — inconsistent output the user can't see.
- **Expected:** SRS FR-4.1, FR-4.4 — `validateRange` enforces `end ≤ duration`,
  rolls `≥ 0`, rejects empty; a "Use playhead" button replaces the dead props;
  single and batch export share the same roll settings.
- **Repro / Notes:** PLAN P2-4, P2-5. Extend the existing `validateRange` tests.
- **Status:** open

### [BUG-021] Frontend destructive actions lack confirmation and busy guards
- [ ] **Severity:** med
- **Area:** frontend
- **File(s):** frontend/src/routes/ImportPage.tsx:41-45, frontend/src/routes/TimelineEditorPage.tsx:82-95,141-152
- **Observation:** drag-drop import bypasses the busy guard → concurrent uploads
  (P2-6); "Delete thumbnails" is destructive with no confirmation and no busy
  guard, and a failed export leaves an invisible orphan draft later swept into
  batch export (P2-7).
- **Expected:** SRS FR-6.2 — busy state prevents concurrent uploads through all
  input paths; destructive actions confirm; the clip list refreshes in `finally`.
- **Repro / Notes:** PLAN P2-6, P2-7. `if (busy) return;` in the drop handler;
  confirm dialog + busy on cleanup.
- **Status:** open

### [BUG-022] FastAPI 422 validation arrays render as blank `http_422:` errors
- [ ] **Severity:** med
- **Area:** frontend
- **File(s):** frontend/src/api/client.ts:43-49
- **Observation:** the client expects a `{error, message}` detail object, so
  FastAPI's 422 validation arrays produce a blank `http_422: ` error.
- **Expected:** SRS § 5 — the client handles `Array.isArray(detail)` by joining
  the `msg` fields into a readable message.
- **Repro / Notes:** PLAN P2-8. Overlaps FEAT-019 (shared error formatter); land
  with a client test for the array shape.
- **Status:** open

### [BUG-023] Dev-script portability, player error handling, and check_system exit code
- [ ] **Severity:** low
- **Area:** tooling, frontend
- **File(s):** scripts/dev.sh:25,45, scripts/dev.ps1:34-37, frontend/src/components/Player.tsx:8-19, scripts/check_system.py:30-37,121-124
- **Observation:** `"${pids[@]}"` under `set -u` breaks stock macOS bash 3.2;
  `dev.ps1` declares `#requires 5.1` but hardcodes `pwsh`; the player shows a
  black box for MKV/MOV (served as `video/mp4`, no `onError`); `check_system.py`
  probes yt-dlp with `-version` and always exits 0.
- **Expected:** SRS NFR-O1 — dev scripts work on documented shells; player shows
  an error hint; `check_system.py` uses `--version` and propagates its exit code.
- **Repro / Notes:** PLAN P2-9. Four small independent fixes in one ticket.
- **Status:** open

### [BUG-024] Hot queries unindexed; per-row thumbnail globbing; uncached storage walk
- [ ] **Severity:** med
- **Area:** db, analysis
- **File(s):** backend/app/services/analysis.py, backend/app/services/storage.py, backend/app/db/schema.sql
- **Observation:** job queries filter on `json_extract(params_json,'$.kind')`
  with no index; `list_videos` globs thumbnails per row; `_dir_size` rglobs the
  whole data dir on every storage call.
- **Expected:** SRS NFR-P2 — materialize a `kind` column + `(video_id, created_at)`
  index; store thumbnail names in the DB; cache storage usage.
- **Repro / Notes:** PLAN P3-2. Coordinate the `kind` column with the migration
  work in BUG-027.
- **Status:** open

### [BUG-025] `_pcm16_rms` per-sample Python loop; stderr-drain pattern can deadlock
- [ ] **Severity:** med
- **Area:** analysis
- **File(s):** backend/app/services/analysis.py:1985,1994-2004
- **Observation:** `_pcm16_rms` is a per-sample Python loop over 16 kHz audio; the
  stderr drain pattern around the audio subprocess can deadlock.
- **Expected:** SRS NFR-P3 — vectorize RMS with `numpy.frombuffer`; use
  `stderr=DEVNULL` (or a non-blocking drain).
- **Repro / Notes:** PLAN P3-3. Re-run the nine-scenario evaluation baseline after
  the change to confirm byte-identical metrics.
- **Status:** open

### [BUG-026] `_update_job_params` read-modify-write race; responses leak server-internal paths
- [ ] **Severity:** med
- **Area:** analysis, api
- **File(s):** backend/app/services/analysis.py, backend/app/domain/models.py:62,90
- **Observation:** `_update_job_params` does a read-modify-write that races
  `_update_job_progress`; API responses expose server-internal absolute paths
  (`local_path`, embedding paths).
- **Expected:** SRS NFR-S4, NFR-R2 — single-transaction `json_set` for params;
  drop absolute paths from response models.
- **Repro / Notes:** PLAN P3-4. The path-leak half complements BUG-005 (which
  covers the auth side of the same exposure); this ticket owns the response-shape
  change and the params race.
- **Status:** open

### [BUG-027] Migrations unversioned; missing legacy-upgrade tests; `clips.job_id` lacks an FK
- [ ] **Severity:** med
- **Area:** db
- **File(s):** backend/app/db/database.py, backend/app/db/schema.sql, backend/tests/test_migrations.py
- **Observation:** migrations are predicate-based with no version tracking; 7 of 8
  lack legacy-upgrade tests; `clips.job_id` has no foreign key.
- **Expected:** SRS NFR-O4 — `PRAGMA user_version` gating; `clips.job_id` FK with
  `ON DELETE SET NULL`; per-migration legacy-upgrade tests.
- **Repro / Notes:** PLAN P3-5. Sequence before/with BUG-024's `kind` column so
  both migration changes land under one versioning scheme.
- **Status:** open

### [BUG-028] Second-granularity timestamps make claim ordering ties undefined (flaky FIFO test)
- [ ] **Severity:** low
- **Area:** worker, tests
- **File(s):** backend/app/services/analysis.py, backend/tests/test_*worker*.py
- **Observation:** `_now_iso()` truncates to seconds, so claim-order ties are
  unspecified; `test_worker_runs_oldest_queued_analysis_job` asserts unguaranteed
  FIFO and is flaky.
- **Expected:** SRS NFR-R4 — deterministic `ORDER BY created_at, rowid` and/or
  sub-second timestamps.
- **Repro / Notes:** PLAN P3-6.
- **Status:** open

### [BUG-029] Stale monkeypatch of `_detect_streamer_candidate` in analysis API tests
- [ ] **Severity:** low
- **Area:** tests
- **File(s):** backend/tests/test_analysis_api.py:37-57
- **Observation:** the test patches `_detect_streamer_candidate` (singular), which
  the pipeline no longer calls directly, so the patch is dead and the test no
  longer exercises what it claims.
- **Expected:** the monkeypatch targets the function the pipeline actually calls,
  or the test is rewritten against the current entry point.
- **Repro / Notes:** PLAN P4-3.
- **Status:** open

### [BUG-030] Doc/dependency drift: httpx classification, opencv floor, unused pytest-asyncio
- [ ] **Severity:** low
- **Area:** docs, tooling
- **File(s):** backend/pyproject.toml, docs/
- **Observation:** `httpx` is used as a test dependency but declared a runtime
  dependency; the `opencv-python-headless>=4.13` floor needs verification;
  `pytest-asyncio` is configured (`asyncio_mode=auto`) with no async tests.
- **Expected:** SRS NFR-O3 (hygiene) — dependency classifications match actual
  use and the version floors are justified. (The review's "README omits deploy/"
  note is already stale — the layout lists `deploy/`.)
- **Repro / Notes:** PLAN P4-4.
- **Status:** open

---

### [BUG-031] Single-architecture native wheels advertise universal2 compatibility
- [ ] **Severity:** high
- **Area:** tooling
- **File(s):** backend/setup.py, backend/tests/test_native_build.py
- **Observation:** A universal2 CPython build tags a wheel as universal2 even though
  the Rust interval matcher is built for only the active host architecture.
- **Expected:** Wheel tags advertise only the compiled host architecture and the
  minimum macOS version required by its payload; editable metadata is consistent.
- **Repro / Notes:** Compare bdist_wheel.get_tag() with lipo -archs on the interval
  library. A thin arm64 binary must not advertise x86_64 compatibility.
- **Fix:** Restrict macOS wheel and editable tags to the active Rust host slice,
  retain payload minimum-OS detection, and enforce the arm64 macOS 11 floor.
  Seven tag regressions and real retained-payload metadata pass; final wheel
  build/install validation remains pending. Docs: docs/setup.md, VERIFICATION.md.
- **Status:** in-progress

### [BUG-032] Installed-wheel smoke rejects macOS temporary-directory aliases
- [ ] **Severity:** med
- **Area:** tests, tooling
- **File(s):** backend/scripts/check_native_distribution.py
- **Observation:** The macOS hosted package smoke compares the loader's resolved
  native path with an unresolved temporary install root under /var; /private/var
  aliases make the source-shadowing assertion fail after a successful build.
- **Expected:** Compare canonical paths while still verifying the loaded library
  belongs to the installed wheel rather than the source tree.
- **Repro / Notes:** A synthetic symlink-root check fails before normalization
  and passes with Path.resolve(); hosted final package rerun remains pending.
- **Fix:** Resolve isolated smoke arguments before adding the install root to
  sys.path or checking library ownership. No product matcher or loader change.
- **Status:** in-progress

## Migrated to changelog

Entries below have been ticked off and copied as a one-liner into `CHANGELOG.md`.
They are kept here so each `BUG-NNN` stays resolvable.

_None yet. Tracker starts as of 2026-06-11._
