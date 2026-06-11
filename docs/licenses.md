# Licenses

App code in this repository is licensed under the [Apache License 2.0](../LICENSE).

External dependencies referenced by the spec ship under their own licenses. The table below tracks what we plan to ship and what license each thing carries. Until each dependency is actually integrated and a model checkpoint is referenced, the entry is informational.

## App-code dependencies (integrated in M0/M1)

| Package              | License        | Role                          |
|----------------------|----------------|-------------------------------|
| fastapi              | MIT            | HTTP framework                |
| uvicorn              | BSD-3-Clause   | ASGI server                   |
| pydantic             | MIT            | Validation                    |
| pydantic-settings    | MIT            | Env config                    |
| python-multipart     | Apache-2.0     | Multipart upload parser       |
| httpx                | BSD-3-Clause   | HTTP client (tests)           |
| pytest               | MIT            | Test runner                   |
| pytest-asyncio       | Apache-2.0     | Async test support            |
| react                | MIT            | UI library                    |
| react-dom            | MIT            | DOM renderer                  |
| react-router-dom     | MIT            | Routing                       |
| vite                 | MIT            | Frontend tooling              |
| typescript           | Apache-2.0     | Compiler                      |
| vitest               | MIT            | Frontend test runner          |
| @testing-library/*   | MIT            | React testing utilities       |

## ML / model dependencies (optional or planned)

| Component                    | License (verify before release)                                    | Notes                                                                                          |
|------------------------------|--------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| SAM 3 / SAM 3.1 code         | SAM License (Meta) — not Apache/MIT                                | External install only. Do not vendor checkpoints. Document expected install path.              |
| SAM 3 checkpoints            | SAM License (Meta)                                                 | Gated. Never auto-download without explicit user action and license acknowledgement.           |
| OpenCV YuNet model           | MIT (per the upstream model card; confirm at integration time)     | Optional face detector asset. Stored under `models/opencv/`.                                   |
| OpenCV SFace model           | Apache-2.0 (per upstream model card; confirm at integration time)  | Optional face re-identification asset. Stored under `models/opencv/`.                          |
| MediaPipe Face Landmarker    | Apache-2.0 (Google MediaPipe Solutions)                            | Landmarks and blendshapes.                                                                     |
| faster-whisper               | MIT (code) — Whisper weights are MIT                               | Transcription.                                                                                  |
| silero-vad                   | MIT                                                                | Voice activity detection.                                                                       |
| ffmpeg / ffprobe             | LGPL-2.1+ / GPL-2.0+ (varies by build)                             | External binary. Treated as a tool the user installs; not bundled.                              |
| yt-dlp                       | Unlicense                                                          | Optional external CLI; user-installed.                                                          |

## Policy

1. Apache-2.0 for app code keeps integration friendly.
2. SAM 3 is treated as an **external dependency** behind a clean interface (see SPEC §12, Stage 4). The repo never vendors SAM 3 source or checkpoints.
3. Models with binary weights live under `models/` (gitignored). Their hashes are recorded in `analysis_jobs.versions_json` per SPEC §18.
4. This document must be refreshed and re-verified before any public release.
