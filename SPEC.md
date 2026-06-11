# What Made 'Em So Mad - Functionality And Accuracy Improvements

Status: Improvement backlog
Date: 2026-05-28

This file now tracks only forward-looking improvements to the functionality and accuracy of the hosted service. It does not track completed deployment work, general setup, packaging, licensing, or broad architecture unless that work directly improves what users can do or how accurate the analysis is.

## Current Baseline

The service can:

- Upload and store a video.
- Import a video from URL when `yt-dlp` is installed.
- Serve the video, thumbnails, and exported clips from the VPS.
- Generate an automatic timeline after upload.
- Detect ranked recurring streamer candidates with OpenCV Haar face detection and save preview crops.
- Persist ROI candidates and accept a manually confirmed streamer region.
- Save per-video face crop and embedding artifacts for confirmed or automatic streamer profiles when frames are available, using optional OpenCV SFace model assets when installed and the histogram fallback otherwise.
- Emit conservative lifecycle segments from OpenCV temporal sampling, with same-kind SFace/histogram reacquisition and fallback full-video presence when tracking evidence is unavailable.
- Persist rectangular mask artifacts for lifecycle segments so future SAM masks have the same artifact surface.
- Store VAD intervals from optional Silero VAD or an energy fallback and use speech likelihood separately from raw loudness.
- Store OpenCV-derived expression samples for face visibility, mouth-open, smile, squint, brow/head-pose proxies where available.
- Emit activity segments from multimodal VAD/audio/expression/transcript fusion: `focused`, `talking`, weak `laughing` candidates, and `yelling/frustrated`.
- Strengthen activity labels with imported transcript cues or locally generated `faster-whisper` transcripts when that optional package is installed.
- Queue in-process analysis jobs, inspect stage/percent progress, run queued jobs explicitly, cancel queued/running jobs, and inspect partial timelines after cancellation or failure.
- Run queued analysis jobs from `scripts/run_worker.py`, which atomically claims the oldest queued job from SQLite and executes the same analysis runner used by the API.
- Save diagnostics bundles with ROI overlay specs, identity artifact refs, mask refs, VAD/expression summaries, loudness samples, and fusion contribution traces.
- Show timeline bars, label filters, confidence filtering, activity totals, a selected-segment evidence inspector, and a scrollable click-to-seek segment list.
- Edit, split, merge, delete, and export feedback for activity events.
- Generate suggested clip drafts from high-confidence events.
- Export manual or suggested clip ranges singly or in batches with pre-roll and post-roll, writing a batch manifest.
- Run JSON fixture evaluations with precision/recall/F1, boundary error, false positive, and top-K reaction metrics, using either user-provided fixtures or the packaged nine-scenario baseline set.
- Show storage usage, clean generated artifacts, and optionally protect hosted state-changing routes with an access token.

The main accuracy limitation is that the service still uses conservative local fallbacks unless optional model packages and model files are installed. Rectangular mask artifacts are not true SAM masks, SFace is asset-gated and uncalibrated for this domain, and the project still needs real labeled video fixtures plus calibrated production-grade accuracy thresholds.

## Product Direction

Improve the service from "rough audio/facecam heuristic timeline" to "evidence-backed reaction finder."

The service should help users answer:

- When is the streamer present, absent, or returning?
- When is the streamer speaking, laughing, yelling, frustrated, surprised, quiet, or focused?
- What evidence supports each label?
- Which moments are worth clipping?
- How confident is the system, and where should the user review manually?

The service must keep labels grounded in observable evidence. It should not claim to know the streamer's internal emotional state.

## Priority 1: Core Accuracy Improvements

### A-001: Add Proper Voice Activity Detection

Replace the current RMS-only speech heuristic with a VAD model or robust VAD pipeline.

Requirements:

- Detect speech intervals separately from generic loud audio.
- Store VAD confidence per interval.
- Distinguish silence, low speech, loud speech, music/game audio bursts, and clipping when possible.
- Use VAD output as evidence for `talking`, `yelling/frustrated`, and `focused`.

Acceptance criteria:

- `talking` segments are based on speech likelihood, not only volume.
- Loud non-speech game audio should not automatically become `talking`.
- Evidence inspector shows VAD confidence and audio-energy context.

### A-002: Add Transcription With Timestamps

Add optional transcript generation for uploaded videos.

Requirements:

- Generate segment-level timestamps.
- Prefer word-level timestamps when available.
- Store transcript text locally.
- Allow users to disable transcription for privacy/performance.
- Expose transcript snippets in the timeline inspector.

Acceptance criteria:

- Clicking an activity segment shows the matching transcript text when available.
- Transcript cues can strengthen labels such as `laughing`, `yelling/frustrated`, and `confused/surprised`.
- Re-analysis can run with transcription enabled or disabled.

### A-003: Add Face/Mouth Expression Features

Use the detected streamer region to extract visual expression evidence.

Requirements:

- Detect face visibility inside the streamer ROI.
- Track mouth openness, smile likelihood, brow movement, squint, and head pose when available.
- Store expression features at a configurable sample rate.
- Degrade gracefully when the face is too small, obscured, or absent.

Acceptance criteria:

- `laughing` requires visual or transcript corroboration before receiving medium/high confidence.
- `focused` is no longer just "quiet audio"; it should incorporate face visibility, speaking absence, and visual stability.
- Evidence inspector shows which visual signals were available or missing.

### A-004: Improve Streamer ROI Detection

Replace single Haar candidate selection with a ranked ROI candidate pipeline.

Requirements:

- Sample frames across the opening portion of the video.
- Score candidates by recurrence, position stability, face size, overlay-like geometry, and motion contrast.
- Support multiple candidate boxes.
- Save representative preview crops for review.

Acceptance criteria:

- The service can show "best candidate" and alternatives.
- False face detections from gameplay/UI are scored lower than stable facecam regions.
- User can inspect why a candidate was chosen.

### A-005: Add Manual Streamer Confirmation

Let users correct the streamer target before or after analysis.

Requirements:

- Show candidate boxes on a representative frame.
- Let the user accept a candidate, click a face, or draw a box.
- Persist the confirmed streamer profile for that video.
- Re-run analysis from the confirmed target.

Acceptance criteria:

- Wrong auto-detection can be fixed without re-uploading the video.
- Confirmed streamer target is used as stronger evidence than automatic detection.
- Re-analysis preserves prior results for comparison.

### A-006: Add Robust Streamer Tracking

Track the confirmed streamer through the video instead of assuming full-video presence.

Requirements:

- Emit lifecycle states: `present`, `partial`, `lost`, `AFK`, and `returned`.
- Store confidence and representative bbox per lifecycle segment.
- Use temporal smoothing to avoid flickering state changes.
- Support reinitialization when the target is lost.

Acceptance criteria:

- If the streamer leaves the camera, the service emits `lost` or `AFK`.
- If the streamer returns, the service emits `returned`.
- Presence confidence is tied to visual evidence, not only the initial candidate.

### A-007: Add Face Re-Identification For Reacquisition

Use per-video face identity evidence to confirm that a returning face is the same streamer.

Requirements:

- Create a per-video face embedding when a confirmed face crop is available.
- Compare candidate faces during lost/AFK periods.
- Keep embeddings scoped to the video by default.
- Ask for user confirmation when the match is ambiguous.

Acceptance criteria:

- The service can distinguish streamer return from another face in the same region.
- Reacquisition evidence includes match confidence.
- Ambiguous reacquisition does not silently create high-confidence labels.

## Priority 2: Label Quality Improvements

### L-001: Replace "Emotion" With Evidence-Based Activity Labels

Use labels that describe observable activity.

Required labels:

- `present`
- `partial`
- `lost`
- `AFK`
- `returned`
- `talking`
- `laughing`
- `yelling/frustrated`
- `focused`

Optional later labels:

- `surprised`
- `confused`
- `celebrating`
- `silent_reaction`

Acceptance criteria:

- UI labels avoid claiming psychological certainty.
- Evidence panel explains each label as a candidate when confidence is weak.

### L-002: Add Multimodal Fusion Rules

Combine lifecycle, VAD, transcript, expression, and audio features into a single event timeline.

Requirements:

- Version all fusion rules.
- Store evidence JSON for every activity segment.
- Merge short adjacent segments when evidence is consistent.
- Avoid generating very short noisy segments unless they are high-confidence reactions.

Acceptance criteria:

- Activity segments have stable start/end boundaries.
- Confidence increases when independent signals agree.
- Confidence decreases when only one weak signal is available.

### L-003: Calibrate Confidence Scores

Make confidence values meaningful and comparable.

Requirements:

- Define confidence bands:
  - `0.00-0.39`: weak candidate
  - `0.40-0.69`: review recommended
  - `0.70-0.89`: likely
  - `0.90-1.00`: strong
- Show confidence band text in the UI.
- Track the source signals that contributed to confidence.

Acceptance criteria:

- A user can tell which segments need review.
- Weak laughter/yelling candidates are not visually presented as equally reliable as strong speech detections.

### L-004: Improve Boundary Detection

Improve the start and end times of generated segments.

Requirements:

- Use VAD boundaries for speech.
- Use transcript segment boundaries where available.
- Use rolling audio/visual features for reactions.
- Apply configurable minimum duration and merge-gap thresholds.

Acceptance criteria:

- Segment boundaries feel clip-ready with minimal manual trimming.
- Short false positives are reduced.

## Priority 3: User Functionality Improvements

### F-001: Add Timeline Filtering

Let users show/hide activity and lifecycle labels.

Requirements:

- Label toggles for each event type.
- Confidence threshold slider.
- "Show review-needed only" filter.
- Filters must apply to bars and the segment list.

Acceptance criteria:

- User can quickly isolate laughter, yelling/frustrated, AFK, or returned moments.
- Dense timelines remain readable.

### F-002: Add Click-To-Seek Timeline Segments

Clicking any bar or segment-list row should seek the video to that segment.

Requirements:

- Segment row click selects the event and moves the player.
- Bar click selects the event and moves the player.
- Add keyboard support for moving between segments.

Acceptance criteria:

- Reviewing a long VOD does not require manually typing timestamps.

### F-003: Add Suggested Clips

Generate clip candidates from high-value moments.

Requirements:

- Suggest clips from high-confidence `laughing`, `yelling/frustrated`, `returned`, and strong reaction clusters.
- Include configurable pre-roll and post-roll.
- Merge nearby high-value events into a single suggested clip.
- Let users accept, trim, reject, or rename suggestions.

Acceptance criteria:

- User sees a clip candidate list without manually creating every range.
- Suggested clips include explanation: "why this was suggested."

### F-004: Add Batch Export

Export multiple selected clips in one operation.

Requirements:

- Multi-select clip candidates.
- Export selected clips with shared settings.
- Show export progress and per-clip errors.
- Write a manifest with source video, ranges, labels, confidence, and settings.

Acceptance criteria:

- A user can export all accepted highlights from one video without repeated manual form submissions.

### F-005: Add Segment Editing

Let users correct the generated timeline.

Requirements:

- Split segment.
- Merge adjacent segments.
- Delete incorrect segment.
- Change label.
- Mark segment as confirmed or rejected.
- Preserve user edits across re-analysis.

Acceptance criteria:

- Corrections become training/evaluation data.
- Re-analysis does not overwrite user decisions without confirmation.

### F-006: Add Analysis Profiles

Offer fast/balanced/best analysis modes.

Requirements:

- `fast`: low sampling, no transcript, quick audio/ROI pass.
- `balanced`: VAD + transcript + visual sampling.
- `best`: highest sample rates and strongest available models.
- Store selected profile in job metadata.

Acceptance criteria:

- Users understand the speed/accuracy tradeoff before starting analysis.

### F-007: Harden Worker Execution

Make the simple SQLite-backed worker safe enough for long-running production jobs.

Requirements:

- Detect and requeue stale `running` jobs after a worker crash.
- Extend the worker to export and transcription jobs, not only analysis.
- Support multiple worker processes without double-running jobs.
- Add operational logging and heartbeat metadata.
- Preserve the existing stage/percent progress, cancellation, and partial diagnostics contracts.
- Keep the synchronous `/analyze` compatibility path or document its replacement.

Acceptance criteria:

- A long analysis can survive API restarts and worker crashes, then still be inspected or cancelled.

## Priority 4: Evaluation And Diagnostics

### E-001: Build A Real Labeled Video Evaluation Set

Replace the packaged JSON baseline with real labeled VOD clips for measurable accuracy.

Required fixture categories:

- Streamer always visible.
- Streamer leaves and returns.
- Facecam partially occluded.
- Facecam disappears or layout changes.
- Loud game audio while streamer is silent.
- Guest/in-game speech while streamer is visible.
- Clear laughter.
- Clear yelling/frustrated speech.
- Quiet focused gameplay.

Acceptance criteria:

- Each fixture has labeled ground truth for lifecycle and activity segments.
- Evaluation can run against real local fixture media without touching user uploads.

### E-002: Add Accuracy Metrics

Track model and heuristic quality over time.

Metrics:

- Presence precision/recall/F1.
- AFK precision/recall.
- Returned detection precision/recall and latency.
- Talking precision/recall.
- Laughing precision at top K.
- Yelling/frustrated precision at top K.
- Mean absolute boundary error.
- False positive segments per hour.
- Analysis speed per video hour.

Acceptance criteria:

- Each accuracy improvement can be validated against the same fixtures.
- Regressions are visible before deployment.

### E-003: Add Rendered Diagnostics Review

Turn saved diagnostics data into an easier review experience.

Requirements:

- Render ROI candidate overlays on source frames.
- Render face crops and detection confidence in the UI.
- Render the VAD timeline and loudness graph from the saved bundle.
- Link transcript snippets, expression feature summaries, and fusion rule contributions to each event.
- Support downloading or sharing a diagnostics bundle explicitly.

Acceptance criteria:

- A bad segment can be debugged from the UI or saved diagnostics without reading server logs.

### E-004: Add Review Feedback Export

Turn user corrections into structured data.

Requirements:

- Export user-confirmed/rejected events as JSON.
- Include source timestamps, original label, corrected label, and evidence snapshot.
- Keep exports local unless the user explicitly downloads/shares them.

Acceptance criteria:

- Manual review can feed future accuracy work.

## Priority 5: Service Usability Improvements

### S-001: Add Project Management

Make uploaded videos manageable from any computer using the hosted service.

Requirements:

- Server-side project list.
- Rename project.
- Delete project and artifacts.
- Show analysis status and latest job.

Acceptance criteria:

- Projects are not dependent on browser localStorage.
- Uploaded videos are discoverable from another computer.

### S-002: Add Basic Access Control

Protect the public VPS instance.

Requirements:

- Add at least a simple password or invite token before public use.
- Prevent anonymous uploads and exports.
- Keep local/admin setup simple.

Acceptance criteria:

- The service is not an open public upload endpoint.

### S-003: Add Storage Controls

Prevent the VPS from filling up.

Requirements:

- Show disk usage per project.
- Delete generated thumbnails, diagnostics, clips, or whole projects.
- Set maximum upload size.
- Set retention options.

Acceptance criteria:

- Users can recover disk space from the UI.

## Implementation Order

1. Server-side project list so uploads are visible from any computer.
2. Click-to-seek timeline segments.
3. Timeline filters and confidence threshold.
4. Proper VAD.
5. Transcript generation and transcript evidence.
6. Manual streamer confirmation.
7. Improved ROI candidate ranking.
8. Suggested clips.
9. Segment editing and user correction persistence.
10. Evaluation fixtures and accuracy metrics.
11. Expression features.
12. Robust tracking/reacquisition.
13. Batch export.
14. Diagnostics mode.
15. Basic access control and storage controls.

## Definition Of Improved Service

The service is meaningfully improved when:

- Uploaded projects are visible from any computer.
- Users can filter and seek through generated segments quickly.
- Talking detection uses VAD, not just volume.
- At least transcript-backed evidence is available for relevant segments.
- Laughter/yelling/frustration labels have explicit evidence and calibrated confidence.
- Users can correct bad streamer targets and bad labels.
- Suggested clips reduce manual timestamp work.
- A labeled evaluation set shows measurable improvement over the current heuristic baseline.
