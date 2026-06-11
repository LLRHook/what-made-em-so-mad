-- Local-first data model. Tables are created idempotently so new installs and
-- upgraded local databases share the same shape without destructive migrations.

CREATE TABLE IF NOT EXISTS videos (
    id                TEXT    PRIMARY KEY,
    source_type       TEXT    NOT NULL CHECK (source_type IN ('local', 'url')),
    source_uri        TEXT    NOT NULL,
    title             TEXT,
    sha256            TEXT    NOT NULL,
    local_path        TEXT    NOT NULL,
    duration_seconds  REAL,
    fps               REAL,
    width             INTEGER,
    height            INTEGER,
    created_at        TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_videos_sha256 ON videos(sha256);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id              TEXT    PRIMARY KEY,
    video_id        TEXT    NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    status          TEXT    NOT NULL CHECK (status IN ('queued','running','blocked','failed','complete','cancelled')),
    params_json     TEXT    NOT NULL,
    versions_json   TEXT    NOT NULL,
    progress_json   TEXT,
    error_message   TEXT,
    created_at      TEXT    NOT NULL,
    started_at      TEXT,
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_video ON analysis_jobs(video_id);

CREATE TABLE IF NOT EXISTS roi_candidates (
    id                       TEXT    PRIMARY KEY,
    video_id                 TEXT    NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    sample_start_seconds     REAL    NOT NULL,
    sample_end_seconds       REAL    NOT NULL,
    bbox_json                TEXT    NOT NULL,
    score                    REAL    NOT NULL,
    evidence_json            TEXT,
    preview_path             TEXT
);

CREATE INDEX IF NOT EXISTS idx_roi_candidates_video ON roi_candidates(video_id);

CREATE TABLE IF NOT EXISTS streamer_profiles (
    id                       TEXT    PRIMARY KEY,
    video_id                 TEXT    NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    confirmed_by             TEXT    NOT NULL CHECK (confirmed_by IN ('user','auto','user_reacquire')),
    confirmed_time_seconds   REAL    NOT NULL,
    prompt_json              TEXT    NOT NULL,
    roi_bbox_json            TEXT,
    face_crop_path           TEXT,
    embedding_path           TEXT,
    created_at               TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_streamer_profiles_video ON streamer_profiles(video_id);

CREATE TABLE IF NOT EXISTS track_segments (
    id              TEXT    PRIMARY KEY,
    job_id          TEXT    NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    start_seconds   REAL    NOT NULL,
    end_seconds     REAL    NOT NULL,
    state           TEXT    NOT NULL,
    confidence      REAL    NOT NULL,
    bbox_json       TEXT,
    artifact_ref    TEXT,
    evidence_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_track_segments_job ON track_segments(job_id);
CREATE INDEX IF NOT EXISTS idx_track_segments_time ON track_segments(job_id, start_seconds);

CREATE TABLE IF NOT EXISTS activity_events (
    id              TEXT    PRIMARY KEY,
    job_id          TEXT    NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    start_seconds   REAL    NOT NULL,
    end_seconds     REAL    NOT NULL,
    label           TEXT    NOT NULL,
    confidence      REAL    NOT NULL,
    source          TEXT    NOT NULL CHECK (source IN ('fusion','user','imported')),
    evidence_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_activity_events_job ON activity_events(job_id);
CREATE INDEX IF NOT EXISTS idx_activity_events_label ON activity_events(job_id, label);

CREATE TABLE IF NOT EXISTS vad_segments (
    id                  TEXT    PRIMARY KEY,
    job_id              TEXT    NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    start_seconds       REAL    NOT NULL,
    end_seconds         REAL    NOT NULL,
    speech_probability  REAL    NOT NULL,
    source              TEXT    NOT NULL,
    evidence_json       TEXT
);

CREATE INDEX IF NOT EXISTS idx_vad_segments_job ON vad_segments(job_id, start_seconds);

CREATE TABLE IF NOT EXISTS expression_features (
    id              TEXT    PRIMARY KEY,
    job_id          TEXT    NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    time_seconds    REAL    NOT NULL,
    bbox_json       TEXT,
    face_visible    INTEGER NOT NULL,
    mouth_open      REAL,
    smile           REAL,
    brow_raise      REAL,
    squint          REAL,
    head_pose_json  TEXT,
    confidence      REAL    NOT NULL,
    evidence_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_expression_features_job ON expression_features(job_id, time_seconds);

CREATE TABLE IF NOT EXISTS clips (
    id                TEXT    PRIMARY KEY,
    job_id            TEXT,
    video_id          TEXT    NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start_seconds     REAL    NOT NULL,
    end_seconds       REAL    NOT NULL,
    labels_json       TEXT    NOT NULL DEFAULT '[]',
    title             TEXT,
    suggestion_score  REAL,
    suggestion_reason TEXT,
    accepted          INTEGER NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL CHECK (status IN ('draft','exporting','exported','failed')),
    output_path       TEXT,
    error_message     TEXT,
    created_at        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clips_video ON clips(video_id);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id              TEXT    PRIMARY KEY,
    video_id        TEXT    NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start_seconds   REAL    NOT NULL,
    end_seconds     REAL    NOT NULL,
    text            TEXT    NOT NULL,
    source          TEXT    NOT NULL CHECK (source IN ('manual','whisper','imported')),
    confidence      REAL,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transcript_segments_video ON transcript_segments(video_id, start_seconds);

CREATE TABLE IF NOT EXISTS user_annotations (
    id                TEXT    PRIMARY KEY,
    job_id            TEXT    NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    target_type       TEXT    NOT NULL CHECK (target_type IN ('track_segment','activity_event','clip','streamer_profile')),
    target_id         TEXT,
    annotation_type   TEXT    NOT NULL CHECK (annotation_type IN ('confirm','reject','edit','split','merge')),
    payload_json      TEXT    NOT NULL,
    created_at        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_annotations_job ON user_annotations(job_id);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    id                TEXT    PRIMARY KEY,
    fixture_set_path  TEXT    NOT NULL,
    metrics_json      TEXT    NOT NULL,
    created_at        TEXT    NOT NULL
);
