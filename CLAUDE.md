# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

*What Made 'Em So Mad* (WMESM) is a local-first video analysis app that finds and
exports reaction moments from streamer VODs. Two tiers plus a sidecar: a
Python 3.12 / FastAPI backend over SQLite, a React 18 / TypeScript / Vite
frontend, and an optional queue worker (`scripts/run_worker.py`) that claims
analysis jobs from the same SQLite database.

[SPEC.md](SPEC.md) is the product/improvement backlog. [SRS.md](SRS.md) holds
numbered FR/NFR requirements with per-requirement conformance status, and
[PLAN_OF_ACTION.md](PLAN_OF_ACTION.md) is the verified remediation backlog (phased,
file:line-referenced). [docs/architecture.md](docs/architecture.md) details the
implemented baseline.

## Commands

Backend (run from `backend/`; venv at `backend/.venv`):

```pwsh
python -m venv .venv ; .venv\Scripts\Activate.ps1   # first time
pip install -e .[dev]                               # first time
python -m pytest                                    # full suite (80 tests)
python -m pytest tests/test_clips_api.py            # one file
python -m pytest tests/test_clips_api.py::test_name # one test
python -m pytest --collect-only -q                  # enumerate without running
python -m uvicorn app.main:app --reload --port 8000 # run the API
```

Frontend (run from `frontend/`):

```pwsh
npm install        # first time
npm run test       # full suite (29 tests, Vitest)
npx vitest run src/__tests__/client.test.ts   # one file
npx vitest list    # enumerate cases
npm run typecheck  # tsc --noEmit
npm run build      # tsc --noEmit && vite build -> dist/
npm run dev        # Vite dev server (proxies /api -> backend)
```

Both tiers at once: `./scripts/dev.ps1` (Windows) or `./scripts/dev.sh` (POSIX).
The launchers pick free localhost ports and print the URLs; set
`WMESM_BACKEND_PORT` / `WMESM_FRONTEND_PORT` for fixed ports.

Other entry points: `python scripts/check_system.py` probes optional components;
`python scripts/run_worker.py [--once]` drains queued analysis jobs.

No linter or formatter is wired on either tier yet (only `tsc`); do not assume a
`lint` script exists.

## Architecture

Three contexts may write the one SQLite DB concurrently — API request handlers,
FastAPI background tasks, and the external worker — so **all job-state
transitions must be atomic compare-and-swap** (`UPDATE ... WHERE id=? AND
status=?` with a rowcount check), not read-then-write. This is the single most
important invariant; getting it wrong duplicates every derived row.

Backend layering (`backend/app/`):
- `api/` — thin HTTP handlers: validate via Pydantic, delegate to services,
  return Pydantic response models. No business logic here.
- `services/` — orchestration and all side effects: capability probing, ffprobe
  parsing, ffmpeg/yt-dlp subprocess execution, thumbnails, timeline analysis,
  clip lifecycle, storage, diagnostics. `analysis.py` is the large timeline
  pipeline (ROI → VAD → expression → multimodal fusion → persistence).
- `db/` — wraps stdlib `sqlite3` with a `Row` factory and `PRAGMA
  foreign_keys=ON`. **No ORM by design** — shapes are small and mostly
  JSON-in-text columns. Schema lives in `db/schema.sql`; migrations are
  predicate-based.
- `core/` — `config.py` (`WMESM_*` settings via pydantic-settings, cached
  singleton; tests reset it via `reset_settings_for_tests`), `errors.py`,
  `paths.py`.
- `domain/models.py` — Pydantic models shared across layers.

Frontend (`frontend/src/`): React Router routes (`routes/`: project list,
import, timeline editor, settings) over a single typed `fetch` client
(`api/client.ts`) — that client is the *only* data layer.

**Capability-gating is a hard rule.** Optional components (ffmpeg, ffprobe,
yt-dlp, faster-whisper, OpenCV SFace/YuNet model assets, SAM) must never be
imported at module top level or required to boot. `services/capabilities.py`
probes each independently and never raises; routes that need a missing tool
return a typed error (503 `ffmpeg_missing`, 501 for missing yt-dlp) with a
`setup_hint`, not a 500. The full backend test suite passes with zero optional
components installed.

Storage: everything lives under `WMESM_DATA_DIR` (default `<repo>/data`) —
`db/wmesm.sqlite`, `videos/{video_id}/` (source, thumbs, roi, identity, masks,
diagnostics), `clips/{clip_id}/`, `manifests/`. DB rows store paths relative to
that root so the data dir can be relocated. `data/` and `models/` are gitignored.

Access control: when `WMESM_ACCESS_TOKEN` is set, a single middleware in
`app/main.py` guards routes. (See BUG-004 / SRS NFR-S1 — the guard currently
exempts reads, which is a tracked critical defect, not the intended contract:
*all* non-public routes should require the token.)

## Testing notes

Backend tests use the `client` / `isolated_settings` fixtures in
`backend/tests/conftest.py`: each test gets a throwaway `tmp_path` data dir and a
fresh app, so tests never touch real `data/`. Frontend tests run under jsdom with
Testing Library (`src/test-setup.ts`). There is no browser E2E layer yet.

## Project-cycle files (`bugs.md` / `features.md` / `CHANGELOG.md` / `VERIFICATION.md`)

The four trackers (the five canonical files minus `README.md`) are
interlocked: defects and features are *filed*, work is
*verified* against `VERIFICATION.md`, and shipped or fixed entries are
*migrated* into `CHANGELOG.md` as a permanent record. `README.md` is the
public face that links them all together.

- When you find a bug, file a `BUG-NNN` entry in `bugs.md / ## Open` in
  the same change. Never silently fix a bug.
- When a fix lands: tick the checkbox, add a `**Fix:**` line, move the
  entry to `## Migrated to changelog`, copy a one-liner into
  `CHANGELOG.md / Unreleased / Fixed`. Do not delete.
- When you scope a feature, file a `FEAT-NNN` in `features.md / ## Open`
  *before* writing code.
- When a feature ships: tick, add `**Implementation:**` line, move to
  `## Shipped`, copy a one-liner into `CHANGELOG.md / Unreleased / Added`
  (or `Changed`).
- When a `VERIFICATION.md` run goes green: append a `Verified` entry to
  `CHANGELOG.md / Unreleased` per `VERIFICATION.md § 6.5`, bump test
  counts in `VERIFICATION.md § 2` if the suite size changed.
- When `README.md` drifts from reality: treat the drift as a `BUG`
  (area `docs`). Do not silently update it.
- Empty `## Open` sections are the desired steady state.

Note: the SRS/PLAN_OF_ACTION backlog is not yet groomed into these trackers
(tracked as BUG-006). Until it is, treat PLAN_OF_ACTION.md as the authoritative
defect list and the cycle files as the lifecycle system.
