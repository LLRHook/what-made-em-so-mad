# Setup

## Required

- **Python 3.12** — backend runtime.
- **Node LTS** (≥ 18; 20+ recommended) — frontend tooling.

## Recommended

- **ffmpeg / ffprobe** on PATH — required to import any video or export any clip. Without them, the import and export endpoints return `503 service_unavailable` with a setup hint, and the rest of the app still runs.
- **yt-dlp** on PATH — optional. Enables `POST /api/videos/from-url`. Without it, that endpoint returns `501 not_implemented`.

## Later (not needed for M0/M1)

- NVIDIA CUDA toolchain — for SAM 3 GPU tracking.
- SAM 3 / SAM 3.1 — external install per [Meta's SAM repo](https://github.com/facebookresearch/sam3).
- OpenCV YuNet + SFace model files — under `models/opencv/`.
- MediaPipe Face Landmarker task — under `models/mediapipe/`.
- faster-whisper model files (downloaded on first use).

## Local backend setup

```pwsh
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1     # PowerShell
# .venv/bin/activate           # bash
pip install -e .[dev]
pytest -q
uvicorn app.main:app --reload --port 8000
```

## Local frontend setup

```pwsh
cd frontend
npm install
npm run test
npm run dev
```

## One-shot dev launcher

From the repo root:

```pwsh
./scripts/dev.ps1   # PowerShell
./scripts/dev.sh    # bash
```

This starts uvicorn and Vite on free localhost ports and prints the chosen URLs. The frontend proxies `/api/*` to the selected backend port, so CORS does not need to be configured for the typical dev flow. When running backend/frontend manually, choose explicit ports yourself and set `WMESM_BACKEND_PORT` for Vite.

To force fixed ports, set environment variables before launching:

```pwsh
$env:WMESM_BACKEND_PORT = "8000"
$env:WMESM_FRONTEND_PORT = "5173"
./scripts/dev.ps1
```

```bash
WMESM_BACKEND_PORT=8000 WMESM_FRONTEND_PORT=5173 ./scripts/dev.sh
```

## Capability check (no app boot)

```pwsh
python scripts/check_system.py
```

Prints a table showing which optional tools are detected and what is missing, with installation hints.

## Data and model directories

`data/` and `models/` are gitignored. The backend creates subdirectories under `data/` on demand (`data/videos/{id}/`, `data/clips/{id}/`, `data/db/`).

You can override their location by setting:

- `WMESM_DATA_DIR` — defaults to `<repo>/data`.
- `WMESM_MODELS_DIR` — defaults to `<repo>/models`.
