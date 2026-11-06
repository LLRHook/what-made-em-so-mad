# What Made 'Em So Mad — End-to-End Verification & Validation Protocol

This document is the canonical checklist for taking What Made 'Em So Mad from
"compiles" to "ship-ready". Run it before any release, after any large
refactor, and any time you suspect a regression. It is **executable from a cold
start** — every command is written verbatim, every expected output is named,
and every "manual" step has unambiguous accept / reject criteria.

The app is two tiers plus a sidecar: a Python 3.12 / FastAPI backend serving
`/api/*` from uvicorn (default port 8000) with SQLite persistence under
`data/db/`, a React 18 / TypeScript / Vite frontend (dev server proxies
`/api` to the backend), and a queue worker (`scripts/run_worker.py`) that
claims analysis jobs from the same SQLite database. Media features depend on
`ffmpeg`/`ffprobe` on PATH; URL import, transcription, and model-backed
analysis depend on optional components reported by
`GET /api/system/capabilities`.

See also: [`bugs.md`](./bugs.md), [`features.md`](./features.md),
[`CHANGELOG.md`](./CHANGELOG.md), [`README.md`](./README.md),
[`SPEC.md`](./SPEC.md), [`SRS.md`](./SRS.md) (numbered FR/NFR requirements),
[`PLAN_OF_ACTION.md`](./PLAN_OF_ACTION.md) (verified remediation backlog).

## How to use this file

1. Work top-to-bottom. Do not skip stages.
2. Tick each step locally as you go.
3. Every failed step files a `BUG-NNN` entry in `bugs.md`.
4. Every gap that is a missing feature, not a defect, files a `FEAT-NNN` in `features.md`.
5. Fill in the summary table (§ 6.3) and either declare the build green or block on open `BUG-NNN`s.

Time estimate: 20–40 minutes without media flows; 45–75 minutes including the
ffmpeg-dependent import/export walkthrough (§ 3.3).

## Roles & abbreviations

- **Host** — the machine running the protocol (Windows/PowerShell or
  Linux/macOS shells; commands below give PowerShell syntax, with POSIX
  equivalents where they differ).
- **Backend venv** — `backend/.venv`. All backend commands assume it exists
  and is used (`backend\.venv\Scripts\python.exe` on Windows,
  `backend/.venv/bin/python` on POSIX).
- **Env knobs** — `WMESM_*` settings from `backend/app/core/config.py`
  (`WMESM_DATA_DIR`, `WMESM_ACCESS_TOKEN`, `WMESM_MAX_UPLOAD_BYTES`, …) and
  `WMESM_FRONTEND_PORT` / `WMESM_BACKEND_PORT` for the Vite dev server.
- **Optional components** — ffmpeg, ffprobe, yt-dlp, faster-whisper,
  mediapipe, SAM, YuNet/SFace model assets. Their absence must degrade
  gracefully, never crash (Stage 5).

---

## Stage 0 — Pre-flight

- [ ] 0.1 Toolchain present:
  ```powershell
  python --version   # expect Python 3.12.x
  node --version     # expect Node LTS (v20+; v24 verified 2026-06-11)
  npm --version
  ```
- [ ] 0.2 Backend venv exists and has dev deps:
  ```powershell
  backend\.venv\Scripts\python.exe -m pytest --version   # expect pytest 8.x
  ```
  If missing: `python -m venv backend\.venv`, activate, then
  `pip install -e backend[dev]`.
- [ ] 0.3 Frontend deps installed: `frontend/node_modules` exists; if not,
  `cd frontend && npm install`.
- [ ] 0.4 Backend port free (default 8000, or your `WMESM_BACKEND_PORT`):
  ```powershell
  Test-NetConnection 127.0.0.1 -Port 8000   # expect TcpTestSucceeded : False
  ```
- [ ] 0.5 Optional-component inventory (record the table in the run notes):
  ```powershell
  python scripts/check_system.py
  ```
  Expected: a status table; `MISSING` rows are acceptable (they scope which
  Stage 3 media steps you can run), a crash is not.

## Stage 1 — Static / spec compliance review

- [ ] 1.1 Frontend type-check:
  ```powershell
  cd frontend; npm run typecheck
  ```
  Expected: exit 0, no output. (Went red mid-bootstrap on 2026-06-11 —
  BUG-002 — and was re-verified green the same day.)
- [ ] 1.2 Backend lint / type-check: **none wired** — placeholder pending
  FEAT-005. When FEAT-005 ships, replace this step with the verbatim `ruff` /
  `mypy` commands.
- [ ] 1.3 Optional dependencies are capability-gated — no top-level imports of
  optional packages in the app package:
  ```powershell
  Select-String -Path backend\app -Pattern "^(import|from) (faster_whisper|mediapipe|torch|yt_dlp)" -Recurse
  ```
  Expected: no matches outside `try`/capability guards (manual eyeball of any
  hit; `backend/app/services/capabilities.py` is the only sanctioned prober).
- [ ] 1.4 Auth guard invariant: the access-token middleware in
  `backend/app/main.py` rejects non-GET/HEAD/OPTIONS requests when
  `WMESM_ACCESS_TOKEN` is set, accepting both `x-wmesm-token` and
  `Authorization: Bearer`. Confirm the middleware is intact and that
  `backend/tests/test_system_api.py` covers it (re-verified live in § 4.1).
- [ ] 1.5 Secrets scan: no `.env` files or token values in tree;
  `.gitignore` still excludes `data/*`, `models/*`, `.env*`, `*.sqlite`.
  ```powershell
  Get-ChildItem -Recurse -Force -Include ".env",".env.*" -File | Where-Object FullName -NotMatch "node_modules|\.venv"
  ```
  Expected: no output.
- [ ] 1.6 Schema integrity: `backend/app/db/schema.sql` parses and migration
  ordering is covered by `backend/tests/test_migrations.py` (runs in Stage 2).

## Stage 2 — Automated builds & tests

Record exact counts; a count drift from the baselines below means tests
appeared or disappeared without a deliberate decision — investigate before
ticking.

- [ ] 2.1 Backend unit + API tests:
  ```powershell
  cd backend; .venv\Scripts\python.exe -m pytest
  ```
  Expected: **133 passed** across **15 test files** (80 original cases plus
  53 native parity/error, evaluation API, packaging and claim-regression cases for FEAT-020).
  Original baseline 2026-06-11:
  analysis, capabilities, clips, database, evaluation, ffmpeg, ffprobe,
  hashing, migrations, system, thumbnails, videos, videos_project_list.
  Enumerate with `.venv\Scripts\python.exe -m pytest --collect-only -q`.
- [ ] 2.2 Frontend unit/component tests:
  ```powershell
  cd frontend; npm run test
  ```
  Expected: **29 passed** across **6 test files** (baseline 2026-06-11:
  CapabilitiesPanel 1, client 10, ClipRangeForm 7, Nav 1, ProjectListPage 8,
  TimelineShell 2). Enumerate with `npx vitest list`.
- [ ] 2.3 Frontend production build:
  ```powershell
  cd frontend; npm run build
  ```
  Expected: exit 0, `frontend/dist/` produced (verified 2026-06-11,
  ~191 kB JS bundle).
- [ ] 2.4 Browser-level E2E: **no framework wired** — placeholder pending
  FEAT-006. Until then, § 3.4's manual UI walkthrough is the only UI gate.
- [ ] 2.5 Evaluation native core (from `backend/`, after source/editable install):
  ```sh
  cargo test --manifest-path native/intervals/Cargo.toml --locked --offline
  cargo clippy --manifest-path native/intervals/Cargo.toml --locked --offline --all-targets -- -D warnings
  cargo fmt --manifest-path native/intervals/Cargo.toml -- --check
  ```
  Expected: **4 native unit tests**, no ignored tests, and clean Clippy/format
  checks. Python tests exercise the installed native artifact, compare all nine
  packaged scenarios to the frozen Python oracle, and verify typed missing-library
  errors without partial SQLite evaluation rows. Build a platform wheel with
  `python -m pip wheel --no-deps .`, install it in a fresh environment, and rerun
  the packaged evaluation API test there. Source/editable builds require Rust;
  wheel installs should run evaluation without Cargo on PATH.

Current suite: **backend 133 tests / 15 files + 4 Rust tests; frontend 29 tests / 6 files.**

## Stage 3 — Functional E2E walkthrough

API smoke first, UI second. Start the backend for this stage:

```powershell
# terminal 1 (from backend/)
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

- [ ] 3.1 Health and system surface:
  ```powershell
  Invoke-WebRequest http://127.0.0.1:8000/api/health | Select-Object -Expand Content
  # expect {"status":"ok","version":"0.1.0"}
  Invoke-WebRequest http://127.0.0.1:8000/api/system/capabilities   # expect 200
  Invoke-WebRequest http://127.0.0.1:8000/api/system/setup          # expect 200
  Invoke-WebRequest http://127.0.0.1:8000/api/system/storage        # expect 200
  ```
- [ ] 3.2 Project list (empty or your existing projects):
  ```powershell
  Invoke-WebRequest http://127.0.0.1:8000/api/videos | Select-Object -Expand Content
  # expect a JSON array
  ```
- [ ] 3.3 Media pipeline (**requires ffmpeg + ffprobe on PATH** — skip with a
  note, not a tick, when `check_system.py` reports them MISSING; not validated
  in the bootstrap sandbox for the same reason):
  - Upload a small MP4 via `POST /api/videos` (multipart `file` field) →
    201 with a video id; duplicate upload of the same file → deduplicated
    response, not a second copy.
  - `GET /api/videos/{id}` → metadata incl. duration; thumbnails generated.
  - Queue analysis: `POST /api/videos/{id}/analysis-jobs` → job id;
    `GET /api/analysis-jobs/{job_id}` shows stage/percent progress.
  - Drain the queue with the worker (terminal 2, repo root):
    ```powershell
    backend\.venv\Scripts\python.exe scripts\run_worker.py --once
    ```
    Expected with an empty queue: `No queued analysis jobs.`, exit 0
    (verified 2026-06-11); with a queued job: it claims and runs it.
  - `GET /api/videos/{id}/timeline` → lifecycle + activity segments with
    confidence values and evidence refs.
  - Create a clip draft (`POST /api/clips`), export it
    (`POST /api/clips/{clip_id}/export`), download it
    (`GET /api/clips/{clip_id}/download`) → playable file, stream-copy where
    possible.
- [ ] 3.4 Evaluation without uploads (works with zero optional components):
  ```powershell
  Invoke-WebRequest -Method Post http://127.0.0.1:8000/api/evaluation/run
  ```
  Expected: 200/201 with a run id; `GET /api/evaluation/runs/{run_id}` returns
  precision/recall/F1, boundary-error, false-positive, and top-K metrics from
  the packaged nine-scenario baseline set.
- [ ] 3.5 Manual UI walkthrough (use `./scripts/dev.ps1` / `./scripts/dev.sh`,
  which allocate ports and print the URLs, or set `WMESM_FRONTEND_PORT` /
  `WMESM_BACKEND_PORT` for fixed ports):
  - Project list page renders; rename and delete work with confirm.
  - Import page uploads (or errors clearly when ffmpeg is missing).
  - Timeline editor: bars render, label filters and confidence slider apply
    to both bars and segment list, clicking a bar/row seeks the player,
    evidence inspector shows the selected segment's signals.
  - Settings/system page shows the capabilities table and storage usage.

## Stage 4 — Adversarial / stress checks

Start the backend with protection enabled for 4.1–4.3:

```powershell
$env:WMESM_ACCESS_TOKEN = "vv-test-token"; cd backend; .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

- [ ] 4.1 No token → request rejected:
  `POST /api/videos` with no auth header → **401** with
  `{"detail":{"error":"unauthorized",...}}` (verified 2026-06-11).
  **Reads must also 401:** `GET /api/videos` and `GET /api/system/storage`
  with no token → **401** per SRS NFR-S1. **Known red as of 2026-06-11: BUG-004**
  — both reads currently return **200** (the guard exempts GET/HEAD/OPTIONS).
  A green run requires BUG-004 closed.
- [ ] 4.2 Tampered token → `Authorization: Bearer wrong-token` and
  `x-wmesm-token: wrong` on any POST → **401**.
- [ ] 4.3 Correct token → `x-wmesm-token: vv-test-token` on a POST → not 401.
- [ ] 4.4 Unknown ids: `GET /api/videos/{bogus}` → 404;
  `DELETE /api/videos/{bogus}` → 404 (delete is not silently idempotent —
  covered by `test_videos_project_list_api.py`).
- [ ] 4.5 Upload limits: restart with `$env:WMESM_MAX_UPLOAD_BYTES = "1024"`,
  upload a >1 KB file → 4xx (not 500), no partial file left in `data/videos/`.
- [ ] 4.6 Non-video upload: `POST /api/videos` with a text file → clean 4xx
  error (never a 500/stack trace), nothing persisted.
- [ ] 4.7 Job lifecycle abuse: cancel a queued job
  (`POST /api/analysis-jobs/{id}/cancel`) → cancelled state, partial timeline
  diagnostics inspectable; cancelling a finished job → clean 4xx/409-style
  error, not a crash.
- [ ] 4.8 Restart preserves data: stop the backend, start it again, confirm
  `GET /api/videos` returns the same list (SQLite at `data/db/wmesm.sqlite`
  survives process restarts).
- [ ] 4.9 Wipe drops data (**destructive — confirm before running**; only on a
  disposable `WMESM_DATA_DIR`): delete the data dir, restart → empty list,
  schema recreated at startup.

## Stage 5 — Hard product-constraint verification

Re-tick the non-negotiable contracts (sources: SPEC.md "Product Direction",
README, docs/licenses.md):

- [ ] 5.1 **Evidence-grounded labels.** Activity labels are limited to the
  spec vocabulary (`present`, `partial`, `lost`, `AFK`, `returned`, `talking`,
  `laughing`, `yelling/frustrated`, `focused`, plus the optional later set);
  the UI never claims psychological certainty, and every segment exposes its
  evidence (inspector shows contributing signals; weak signals → low
  confidence).
- [ ] 5.2 **Graceful degradation with zero optional components.** With ffmpeg,
  yt-dlp, faster-whisper, and all model assets absent, the app boots, the
  full backend suite passes, and capability endpoints report MISSING rather
  than erroring (verified 2026-06-11 on a host with all optional components
  missing).
- [ ] 5.3 **Local-first privacy.** Analysis, transcripts, diagnostics, and
  feedback exports stay on local disk under `WMESM_DATA_DIR`; no third-party
  telemetry or network calls during analysis (the only outbound network is
  user-initiated `yt-dlp` URL import).
- [ ] 5.4 **Access-token protection integrity (SRS NFR-S1 / NFR-S4).** When
  `WMESM_ACCESS_TOKEN` is set, *every* non-public route — including reads that
  serve media, diagnostics (which embed server paths), and downloads — requires
  the token, compared with `secrets.compare_digest`; only an explicit public
  allowlist (`/api/health`) is exempt; and responses never leak server-internal
  absolute paths. **Known red as of 2026-06-11: BUG-004, BUG-005** (reads are
  unauthenticated; compare uses `!=`; `/api/system/storage` leaks paths). Hard
  block.
- [ ] 5.6 **Transport security (SRS NFR-S2).** Hosted deployments terminate TLS;
  the access token never transits plaintext HTTP. Verify the shipped
  `deploy/Caddyfile` / `deploy/nginx-wmesm.conf` enable HTTPS (or that TLS is
  documented as a hard prerequisite). **Tracked red: PLAN_OF_ACTION.md P0-8**
  (configs currently listen on port 80 only). Hard block for hosted mode.
- [ ] 5.5 **Licensing.** LICENSE (Apache-2.0) present; docs/licenses.md still
  enumerates external model/tool licenses; no vendored model weights in tree
  (`models/` contains only `.gitkeep`).

## Stage 6 — Reporting

- [ ] 6.1 Every failed step has a `BUG-NNN` in `bugs.md`.
- [ ] 6.2 Every gap has a `FEAT-NNN` in `features.md`.
- [ ] 6.3 Fill in the summary table:

```
| Stage                    | Pass / Fail | Notes |
|--------------------------|-------------|-------|
| 0 Pre-flight             |             |       |
| 1 Static review          |             |       |
| 2 Automated tests        |             |       |
| 3 Functional E2E         |             |       |
| 4 Adversarial            |             |       |
| 5 Hard constraints       |             |       |
| 6 Reporting hygiene      |             |       |
```

- [ ] 6.4 Record run metadata: git SHA (`git rev-parse --short HEAD`; record
  `no-git` while BUG-001 is open), Python / Node / npm versions,
  `WMESM_*` env knobs in effect, host OS.
- [ ] 6.5 If all green, append a `Verified` entry to
  `CHANGELOG.md / Unreleased / Verified` and migrate any
  `*-pending-migration` tickets.

A build is **release-ready** only if all six stages tick. A failed step in
Stages 1, 2, or 5 is a hard block.

---

## Appendix A — Inspecting persistent state

SQLite (no sqlite3 CLI required — use the venv Python):

```powershell
backend\.venv\Scripts\python.exe -c "import sqlite3; con = sqlite3.connect('data/db/wmesm.sqlite'); print([r[0] for r in con.execute(\"select name from sqlite_master where type='table' order by name\")])"
```

Artifacts on disk: videos under `data/videos/`, clips under `data/clips/`,
DB under `data/db/`. Model assets (optional) under `models/opencv/`.

## Appendix B — Reusable command recipes

Token-protected request:

```powershell
Invoke-WebRequest -Method Post http://127.0.0.1:8000/api/evaluation/run -Headers @{ "x-wmesm-token" = $env:WMESM_ACCESS_TOKEN }
```

POSIX curl equivalent:

```bash
curl -s -X POST -H "x-wmesm-token: $WMESM_ACCESS_TOKEN" http://127.0.0.1:8000/api/evaluation/run
```

Upload a file:

```bash
curl -s -X POST -F "file=@sample.mp4" http://127.0.0.1:8000/api/videos
```

## Appendix C — Common platform commands

Local dev (both tiers): `./scripts/dev.ps1` (Windows) or `./scripts/dev.sh`
(POSIX). Backend alone: from `backend/`,
`.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000`.
Worker: `backend\.venv\Scripts\python.exe scripts\run_worker.py [--once]`.

VPS (deploy/ shape): `systemctl status wmesm`, `journalctl -u wmesm -f`;
reverse proxy via the provided Caddyfile or nginx-wmesm.conf;
provisioning via `deploy/install_remote.sh`.

## Appendix D — Toggling optional providers

| Capability | Enable by | Verify with |
|---|---|---|
| Real ingestion (ffmpeg/ffprobe) | install ffmpeg, on PATH | `python scripts/check_system.py` |
| URL import | install `yt-dlp`, on PATH | capabilities endpoint |
| Generated transcripts | `pip install faster-whisper` into the venv | capabilities endpoint |
| SFace embeddings / YuNet | place ONNX assets under `models/opencv/` | capabilities endpoint |
| Access token | `$env:WMESM_ACCESS_TOKEN = "..."` before starting uvicorn | § 4.1–4.3 |
| Upload cap | `$env:WMESM_MAX_UPLOAD_BYTES = "<bytes>"` | § 4.5 |

## Appendix E — Smoke checklist (sub-15-minute version)

- [ ] `backend: .venv\Scripts\python.exe -m pytest` → 80 passed
- [ ] `frontend: npm run test` → 29 passed
- [ ] `frontend: npm run typecheck` → exit 0
- [ ] backend boots; `GET /api/health` → `{"status":"ok",...}`
- [ ] `GET /api/system/capabilities` → 200 table, no 500s
- [ ] `POST /api/evaluation/run` → metrics from packaged fixtures
- [ ] `scripts/run_worker.py --once` → clean exit
- [ ] with `WMESM_ACCESS_TOKEN` set: tokenless POST → 401
