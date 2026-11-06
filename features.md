# What Made 'Em So Mad — Feature Tracker

This file is the working list of features to implement. New features are appended
here as they are scoped. When a feature is shipped, tick its checkbox and migrate
the entry (with the implementation note) to `CHANGELOG.md` so this file stays
focused on outstanding work.

## Conventions

Each entry uses the form:

```
### [<id>] <short title>
- [ ] **Priority:** crit | high | med | low
- **Area:** api | analysis | worker | frontend | db | deploy | tests | tooling | docs
- **File(s):** comma-separated paths the feature will create or modify
- **Why:** product / user motivation, with spec section if relevant
- **Approach:** the design — concrete enough that an implementer doesn't have to ask
- **Library / dependency notes:** evaluation of any third-party deps,
  with the recommendation called out explicitly
- **Acceptance criteria:** bullet checklist of what "done" means
- **Test plan:** unit / integration / E2E coverage to land with the feature
- **Out of scope:** explicit "we are NOT doing X in this ticket"
- **Status:** open | in-progress | shipped-pending-migration
```

Tick `- [x]` once verified shipped. The implementer also adds an
`**Implementation:**` line summarising the diff before migrating the entry to
`CHANGELOG.md`.

Priority guide: crit / high / med / low.

## Lifecycle
1. **File:** new entries under `## Open` with the full template.
2. **Implement:** flip status to `in-progress`, write code + tests.
3. **Verify:** set `shipped-pending-migration`, tick checkbox, add `**Implementation:**` line.
4. **Migrate:** move to `## Shipped`, append a one-liner to `CHANGELOG.md / Unreleased / Added` (or `Changed`).

---

## Open

### [FEAT-001] Worker hardening and stale-job recovery
- [ ] **Priority:** high
- **Area:** worker, db, api
- **File(s):** scripts/run_worker.py, backend/app/services/analysis.py, backend/app/db/schema.sql
- **Why:** SPEC.md F-007 / SRS NFR-R3 — the SQLite-backed worker must survive
  crashes and API restarts for long-running production jobs. Today a worker crash
  strands jobs in `running` forever. Covers PLAN P1-3 (stale-job recovery +
  reaper; worker try/except, exponential backoff, SIGTERM/SIGINT handling).
  Depends on BUG-010 (atomic job-claim CAS) and BUG-014 (WAL) landing first.
- **Approach:** add heartbeat metadata (worker id + last-seen timestamp) to the
  jobs table; on claim, atomically requeue `running` jobs whose heartbeat is
  stale beyond a threshold; support multiple worker processes via the existing
  atomic-claim pattern; extend the worker loop to export and transcription job
  kinds; preserve the stage/percent, cancellation, and partial-diagnostics
  contracts and the synchronous `/analyze` compatibility path.
- **Library / dependency notes:** none expected — stdlib `sqlite3` and the
  existing claim query suffice. Recommendation: no new dependency.
- **Acceptance criteria:**
  - A killed worker's `running` job is requeued and completed by the next worker.
  - Two concurrent workers never double-run the same job.
  - Stage/percent progress, cancel, and partial timeline inspection still work.
- **Test plan:** unit tests for stale-detection and requeue queries; integration
  test simulating a dead worker (stale heartbeat row) then running
  `scripts/run_worker.py --once`; existing analysis-API tests stay green.
- **Out of scope:** distributed queues (Redis/Celery), multi-host workers.
- **Status:** open

### [FEAT-002] Real labeled video evaluation set
- [ ] **Priority:** high
- **Area:** tests, analysis
- **File(s):** backend/app/evaluation/, backend/app/services/evaluation.py, data/ (local fixture media)
- **Why:** SPEC.md E-001 — the packaged nine-scenario JSON baseline cannot
  measure real accuracy. Labeled VOD clips are needed for the spec's fixture
  categories (always visible, leaves-and-returns, occluded facecam, loud game
  audio, clear laughter, yelling, quiet focus, etc.).
- **Approach:** define a local fixture-media layout (gitignored under `data/`),
  with per-clip ground-truth JSON for lifecycle and activity segments; extend the
  evaluation runner to accept fixture media paths and compute the existing
  metric set against them; document how to obtain/prepare clips.
- **Library / dependency notes:** none — reuse the existing evaluation service
  and ffmpeg ingestion. Recommendation: no new dependency.
- **Acceptance criteria:**
  - Each spec fixture category has at least one labeled local clip.
  - `POST /api/evaluation/run` can target real fixture media without touching
    user uploads.
  - Metrics from the spec (E-002 list) are produced per category.
- **Test plan:** unit tests for fixture loading/validation; an evaluation
  integration test against one short bundled-or-synthesised clip.
- **Out of scope:** redistributing copyrighted VOD media in the repo.
- **Status:** open

### [FEAT-003] Real SAM segmentation masks
- [ ] **Priority:** med
- **Area:** analysis
- **File(s):** backend/app/services/analysis.py, backend/app/services/capabilities.py, models/
- **Why:** README "Still planned" / SPEC baseline — lifecycle segments persist
  rectangular mask artifacts as placeholders; real SAM masks would make ROI and
  presence evidence materially more accurate.
- **Approach:** add an optional, capability-gated SAM (e.g. SAM 3) provider that
  consumes the persisted rectangular mask artifact surface; keep the rectangular
  fallback when model assets are absent, mirroring the existing SFace asset
  gating pattern.
- **Library / dependency notes:** model runtime and weights are large and
  license-sensitive (see docs/licenses.md). Evaluate the current SAM release and
  ONNX/torch runtime options at implementation time per the global rule (search
  before adding any dependency). Recommendation: defer the dependency choice to
  the implementation ticket; keep it optional like `faster-whisper`.
- **Acceptance criteria:**
  - With SAM assets installed, mask artifacts contain real segmentation masks.
  - Without assets, behaviour is unchanged (rectangular fallback, no crash).
  - `GET /api/system/capabilities` reports SAM availability.
- **Test plan:** unit tests for the provider gate and fallback; a capability
  test asserting graceful degradation when assets are missing.
- **Out of scope:** GPU provisioning, mask-editing UI.
- **Status:** open

### [FEAT-004] Calibrated face re-identification model assets
- [ ] **Priority:** med
- **Area:** analysis
- **File(s):** backend/app/services/analysis.py, backend/app/services/capabilities.py, models/, docs/setup.md
- **Why:** README "Still planned" / SPEC A-007 — SFace embeddings are asset-gated
  and uncalibrated for this domain; reacquisition confidence thresholds are
  guesses until calibrated against labeled data.
- **Approach:** once FEAT-002 fixtures exist, sweep match thresholds for the
  SFace (and histogram fallback) reacquisition paths against
  leaves-and-returns fixtures; persist the calibrated thresholds as versioned
  config; document the model-asset install path in docs/setup.md.
- **Library / dependency notes:** uses the already-supported OpenCV SFace assets;
  no new package expected. Recommendation: no new dependency.
- **Acceptance criteria:**
  - Reacquisition precision/recall is measured and recorded on fixtures.
  - Calibrated thresholds replace ad-hoc constants, with version metadata.
  - Ambiguous matches still degrade to low confidence (SPEC A-007 criteria).
- **Test plan:** unit tests for threshold config; evaluation-run assertions on
  the leaves-and-returns fixture category.
- **Out of scope:** training custom embedding models.
- **Status:** open

### [FEAT-005] Wire lint, format, and backend type-check tooling
- [ ] **Priority:** med
- **Area:** tooling, tests
- **File(s):** backend/pyproject.toml, frontend/package.json, VERIFICATION.md
- **Why:** discovery (2026-06-11) found no linter or formatter on either tier and
  no backend type-checker; only `tsc --noEmit` is wired. Static review
  (VERIFICATION.md Stage 1) currently has thin coverage, which lets style drift
  and type errors accumulate unreviewed.
- **Approach:** backend — add `ruff` (lint + format) and `mypy` to the `dev`
  extra with project-tuned configs in pyproject.toml; frontend — add ESLint
  (typescript-eslint flat config) and Prettier with scripts `lint` and `format`;
  fix or explicitly suppress the initial finding set; add the commands to
  VERIFICATION.md Stage 1.
- **Library / dependency notes:** ruff, mypy, eslint + typescript-eslint,
  prettier are the standard choices for this stack. Verify latest stable
  versions and config formats at implementation time (search first, per the
  global dependency rule). Recommendation: adopt all four.
- **Acceptance criteria:**
  - `ruff check` / `mypy app` pass in backend; `npm run lint` passes in frontend.
  - VERIFICATION.md Stage 1 lists the commands verbatim.
  - Zero unexplained suppressions.
- **Test plan:** tooling ticket — the gate is the clean lint/type runs plus
  existing suites staying green.
- **Out of scope:** CI setup (no CI provider exists yet), pre-commit hooks.
- **Status:** open

### [FEAT-006] Browser-level E2E smoke layer
- [ ] **Priority:** med
- **Area:** tests, frontend
- **File(s):** frontend/ (new e2e/ dir + config), frontend/package.json, VERIFICATION.md
- **Why:** discovery (2026-06-11) found no browser E2E framework; the four UI
  routes (project list, import, timeline editor, settings) are only covered by
  jsdom component tests, so route wiring, the Vite proxy, and player
  interactions are untested end-to-end. VERIFICATION.md § 2.4 holds a
  placeholder for this ticket.
- **Approach:** add Playwright with a small smoke suite against the dev
  servers: project list renders and navigates, import page error path without
  ffmpeg, timeline editor loads a seeded project, settings shows capabilities;
  wire an `npm run e2e` script and replace VERIFICATION.md § 2.4 with it.
- **Library / dependency notes:** Playwright is the default fit for a
  Vite/React app (built-in server orchestration, trace artifacts). Verify the
  current version and config style at implementation time per the global
  dependency rule. Recommendation: Playwright.
- **Acceptance criteria:**
  - `npm run e2e` runs headless against locally started servers and passes.
  - At least one spec per UI route.
  - VERIFICATION.md § 2.4 lists the verbatim command and spec-count baseline.
- **Test plan:** the E2E suite is the deliverable; existing unit suites stay
  green.
- **Out of scope:** cross-browser matrices, CI wiring, visual regression.
- **Status:** open

### [FEAT-007] Async analysis job progress + cancel UI
- [ ] **Priority:** high
- **Area:** frontend
- **File(s):** frontend/src/routes/TimelineEditorPage.tsx, frontend/src/components/TimelineShell.tsx, frontend/src/api/client.ts
- **Why:** SRS FR-6.4 / PLAN P2-1 + P3-1(1). Re-analysis uses the blocking
  `/analyze` call with no busy guard or abort/epoch guard, so spamming the button
  forks N concurrent full analyses, stale results can render, and setState fires
  after unmount. The async job API (`queueAnalysisJob`/`getAnalysisJob`/cancel,
  `client.ts:80-105`) is already implemented but unused.
- **Approach:** replace the synchronous call with queue + poll + cancel; disable
  the control while a job is in flight; epoch-guard responses; render
  stage/percent progress and a cancel button.
- **Library / dependency notes:** none — client functions exist. No new dependency.
- **Acceptance criteria:**
  - Re-analysis queues a job and shows live stage/percent progress.
  - The control is disabled while busy; double-submit cannot fork two jobs.
  - Cancel stops the job and the UI reflects the cancelled state.
- **Test plan:** component tests for busy-disable, progress render, and cancel,
  with a mocked client.
- **Out of scope:** the backend cancellation correctness (BUG-015) and atomic
  claim (BUG-010) — this is the UI layer over them.
- **Status:** open

### [FEAT-008] Access-token support in the frontend client and Settings
- [ ] **Priority:** high
- **Area:** frontend
- **File(s):** frontend/src/api/client.ts:28-35, frontend/src/routes/SettingsPage.tsx
- **Why:** SRS FR-6.5 / PLAN P2-2. The backend enforces `WMESM_ACCESS_TOKEN` on
  mutations, but the client never sends it, so in hosted mode every mutation
  fails with no recovery path.
- **Approach:** a token field on SettingsPage, persisted (localStorage), attached
  as `x-wmesm-token` on every request in `call()`.
- **Library / dependency notes:** none. No new dependency.
- **Acceptance criteria:**
  - A user can enter/clear the token in Settings; it persists across reloads.
  - Mutations include the header and succeed against a token-protected backend.
- **Test plan:** client test asserting the header is attached; SettingsPage test
  for persistence.
- **Out of scope:** BUG-004 (backend must also guard reads) — separate ticket.
- **Status:** open

### [FEAT-009] Streamer confirmation UI
- [ ] **Priority:** high
- **Area:** frontend
- **File(s):** frontend/src/routes/TimelineEditorPage.tsx, frontend/src/components/, frontend/src/api/client.ts
- **Why:** SRS FR-6.6 / SPEC A-005 / PLAN P3-1(2). The backend exposes ROI
  candidates and accepts a confirmed streamer profile, but the UI offers no way
  to inspect candidates or confirm a region.
- **Approach:** render ROI candidate preview crops on a representative frame; let
  the user accept a candidate (and re-run analysis from it via FEAT-007).
- **Library / dependency notes:** none — endpoints exist
  (`roi-candidates`, `roi-candidates/{id}/preview`, `streamer-profile`).
- **Acceptance criteria:**
  - Candidates with previews are shown; the user can confirm one.
  - A confirmed profile is persisted and used on re-analysis.
- **Test plan:** component test with mocked candidates + confirm call.
- **Out of scope:** click-a-face / draw-a-box (SPEC A-005 stretch) — accept-a-
  candidate only this ticket.
- **Status:** open

### [FEAT-010] Segment editing UI (split / merge / delete / confirm / reject)
- [ ] **Priority:** high
- **Area:** frontend
- **File(s):** frontend/src/routes/TimelineEditorPage.tsx, frontend/src/components/TimelineShell.tsx, frontend/src/api/client.ts
- **Why:** SRS FR-6.6 / SPEC F-005 / PLAN P3-1(3). The backend persists reviewer
  corrections (`activity-events` PATCH/DELETE/split/merge), but none are reachable
  from the UI.
- **Approach:** add edit controls to the selected-segment inspector wired to the
  existing endpoints; reflect updates in the timeline and segment list.
- **Library / dependency notes:** none — endpoints exist.
- **Acceptance criteria:**
  - A segment can be split, merged with a neighbour, deleted, and label-changed
    from the UI, with the timeline updating.
- **Test plan:** component tests per operation with a mocked client.
- **Out of scope:** re-analysis-preserves-edits policy (backend FR-3.2 stretch).
- **Status:** open

### [FEAT-011] Clip management UI (downloads, accept / rename / delete, error display)
- [ ] **Priority:** med
- **Area:** frontend
- **File(s):** frontend/src/routes/TimelineEditorPage.tsx, frontend/src/components/, frontend/src/api/client.ts
- **Why:** SRS FR-6.6 / PLAN P3-1(4). Clip drafts can be accepted/renamed/deleted
  and downloaded via the backend, and clips carry an `error_message`, but the UI
  surfaces none of it.
- **Approach:** a clip list with download links, accept/rename/delete actions, and
  per-clip error display; refresh after each action.
- **Library / dependency notes:** none — endpoints exist.
- **Acceptance criteria:**
  - Clips list with working download links; accept/rename/delete work; failed
    exports show their `error_message`.
- **Test plan:** component tests for list render, actions, and error display.
- **Out of scope:** depends on BUG-016 for the backend recovery behaviour.
- **Status:** open

### [FEAT-012] URL import UI
- [ ] **Priority:** med
- **Area:** frontend
- **File(s):** frontend/src/routes/ImportPage.tsx, frontend/src/api/client.ts
- **Why:** SRS FR-6.6 / PLAN P3-1(5). `POST /api/videos/from-url` exists but the
  import page only offers file upload.
- **Approach:** a URL field on the import page wired to the from-url endpoint, with
  a clear 501 message when yt-dlp is not installed (capability-aware).
- **Library / dependency notes:** none.
- **Acceptance criteria:**
  - A user can paste a URL and start an import; the yt-dlp-missing case shows the
    setup hint, not a raw error.
- **Test plan:** component test for submit + the missing-capability path.
- **Out of scope:** depends on BUG-009 (backend URL hardening) for safe argv.
- **Status:** open

### [FEAT-013] Transcript UI (import / generate / display)
- [ ] **Priority:** med
- **Area:** frontend
- **File(s):** frontend/src/routes/TimelineEditorPage.tsx, frontend/src/api/client.ts
- **Why:** SRS FR-6.6 / SPEC A-002 / PLAN P3-1(6). Transcript import, optional
  generation, and segment cues exist in the backend but are not exposed.
- **Approach:** controls to import a transcript and (capability-permitting)
  generate one via faster-whisper; show matching transcript text in the
  selected-segment inspector.
- **Library / dependency notes:** none.
- **Acceptance criteria:**
  - Clicking a segment shows its transcript text when available; generation is
    offered only when faster-whisper is installed.
- **Test plan:** component tests for display and the capability-gated generate
  control.
- **Out of scope:** none beyond the above.
- **Status:** open

### [FEAT-014] Diagnostics review UI
- [ ] **Priority:** med
- **Area:** frontend
- **File(s):** frontend/src/routes/TimelineEditorPage.tsx, frontend/src/components/, frontend/src/api/client.ts
- **Why:** SRS FR-6.6 / SPEC E-003 / PLAN P3-1(7). Saved diagnostics bundles (ROI
  overlays, crops, VAD/loudness summaries, fusion traces) are persisted and
  exportable but never rendered.
- **Approach:** render ROI candidate overlays on source frames, face crops with
  detection confidence, the VAD/loudness summary, and fusion-rule contributions
  per event; support explicit bundle download.
- **Library / dependency notes:** none — `diagnostics` + `diagnostics/export`
  endpoints exist.
- **Acceptance criteria:**
  - A bad segment can be inspected from the UI without reading server logs;
    a bundle can be downloaded explicitly.
- **Test plan:** component tests with a mocked diagnostics bundle.
- **Out of scope:** real SAM mask rendering (FEAT-003).
- **Status:** open

### [FEAT-015] CI pipeline
- [ ] **Priority:** med
- **Area:** tooling
- **File(s):** .github/workflows/ (new), backend/pyproject.toml, frontend/package.json
- **Why:** SRS NFR-O3 / PLAN P4-2. There is no CI; regressions in either tier are
  caught only by manual runs.
- **Approach:** a CI workflow running backend pytest, frontend vitest, `tsc`,
  and (once FEAT-005 lands) ruff + eslint; optionally pre-commit hooks. Depends on
  BUG-001 (git) and FEAT-005 (lint/format/type wired) so the lint jobs have
  something to invoke.
- **Library / dependency notes:** GitHub Actions is the default; confirm the
  provider with the user (no CI config exists today). Recommendation: GitHub
  Actions unless the repo will live elsewhere.
- **Acceptance criteria:**
  - A push runs both test suites + typecheck (+ lint when available) and fails on
    red.
- **Test plan:** the workflow itself is the deliverable; verify a green run.
- **Out of scope:** deploy/CD, image building.
- **Status:** open

### [FEAT-016] Refactor `analysis.py` god-module into focused modules
- [ ] **Priority:** low
- **Area:** analysis
- **File(s):** backend/app/services/analysis.py (split into detection / vad / expression / fusion / persistence)
- **Why:** SRS hygiene / PLAN P3-7. `analysis.py` is a ~2,487-line module that
  mixes detection, VAD, expression, fusion, and persistence, making every other
  analysis ticket harder and riskier.
- **Approach:** extract cohesive modules behind the current public service
  functions, preserving behaviour; no API or output changes.
- **Library / dependency notes:** none.
- **Acceptance criteria:**
  - The module is split along the named seams; the full backend suite stays green.
  - The nine-scenario evaluation baseline produces byte-identical metrics.
- **Test plan:** existing suite + an evaluation-parity check before/after.
- **Out of scope:** behavioural changes — pure refactor. Best sequenced after the
  Phase 0/1 analysis fixes (BUG-010, BUG-015, BUG-025) to avoid churn collisions.
- **Status:** open

### [FEAT-017] Isolate timeline playhead rendering
- [ ] **Priority:** low
- **Area:** frontend
- **File(s):** frontend/src/components/TimelineShell.tsx, frontend/src/components/Player.tsx
- **Why:** SRS NFR-P4 / PLAN P3-7. The timeline page re-renders fully ~4×/s during
  playback (on every `timeupdate`), wasting work and harming responsiveness.
- **Approach:** isolate the playhead/cursor into its own component/subscription so
  `timeupdate` does not re-render the whole editor.
- **Library / dependency notes:** none.
- **Acceptance criteria:**
  - Playback no longer re-renders the full page each tick (verified via render
    instrumentation or a memoization test).
- **Test plan:** a render-count component test around the playhead.
- **Out of scope:** broader timeline virtualization.
- **Status:** open

### [FEAT-018] Consolidate duplicate `sha256_file` onto `services/hashing.py`
- [ ] **Priority:** low
- **Area:** analysis
- **File(s):** backend/app/services/hashing.py, backend/app/services/analysis.py
- **Why:** SRS hygiene / PLAN P3-7. `sha256_file` is duplicated; there should be a
  single hashing implementation.
- **Approach:** delete the duplicate and route all callers through
  `services/hashing.py`.
- **Library / dependency notes:** none.
- **Acceptance criteria:**
  - One `sha256_file` implementation; all callers use it; suite stays green.
- **Test plan:** existing `test_hashing.py` covers it; add a caller test if a gap
  appears.
- **Out of scope:** none.
- **Status:** open

### [FEAT-019] Extract a shared frontend `useApi` / `formatApiError` helper
- [ ] **Priority:** low
- **Area:** frontend
- **File(s):** frontend/src/api/ (new helper), the 8 call sites flagged in PLAN P3-7
- **Why:** SRS hygiene / PLAN P3-7. Fetch/error boilerplate is duplicated across
  ~8 call sites; a shared hook + error formatter removes the duplication and gives
  BUG-022 (422-array handling) a single place to live.
- **Approach:** extract `useApi` (loading/error/data) and `formatApiError`
  (handles the typed `{error,message}` object and the 422 array shape); migrate
  the call sites.
- **Library / dependency notes:** none.
- **Acceptance criteria:**
  - Call sites use the shared helper; `formatApiError` renders both error shapes.
- **Test plan:** unit tests for `formatApiError`; existing component tests stay
  green.
- **Out of scope:** none — overlaps and should land with or after BUG-022.
- **Status:** open

---

### [FEAT-020] Rust interval matcher for offline evaluation
- [ ] **Priority:** high
- **Area:** analysis, tests, tooling
- **File(s):** backend/app/services/evaluation.py, backend/app/services/interval_matching.py, backend/native/, backend/setup.py, backend/tests/
- **Why:** SPEC E-002 / SRS FR-5.1: preserve the existing evaluation metrics while moving the greedy interval matching processor into an in-process native core.
- **Approach:** batch C ABI over a dependency-free Rust cdylib; keep Python normalization, stable confidence ordering, metric rounding, API and SQLite persistence. Build the library in normal wheel/editable installs and load it only when matching is needed.
- **Library / dependency notes:** Rust standard library and Python ctypes; reuse setuptools. No new runtime package dependency.
- **Acceptance criteria:** exact legacy metric parity including ties, thresholds, empty inputs and non-finite values; actual API/native integration; typed native failure with no partial evaluation row; boot and job claiming remain independent of native availability.
- **Test plan:** independent legacy Python oracle, deterministic synthetic metadata, native ABI error tests, evaluation API/temp SQLite tests, platform wheel and editable-install smoke checks.
- **Out of scope:** media acquisition, model inference, analysis-label changes, job lifecycle fixes, real accuracy datasets or performance claims.
- **Implementation:** Rust IoU/greedy matching now serves the evaluation path through a lazy ctypes batch adapter. Native parity/error, API/temp SQLite and build-adapter tests cover the boundary. Independent review and full wheel/platform acceptance remain pending.
- **Docs:** README.md, backend/README.md, docs/setup.md, docs/architecture.md, CLAUDE.md, VERIFICATION.md
- **Status:** in-progress

## Shipped

_None yet. Tracker starts as of 2026-06-11._
