"""Run queued analysis jobs outside the API request path."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.analysis import run_next_analysis_job  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run queued What Made 'Em So Mad analysis jobs.")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job and exit.")
    parser.add_argument("--idle-seconds", type=float, default=5.0, help="Sleep interval when no jobs are queued.")
    args = parser.parse_args()

    while True:
        job = run_next_analysis_job()
        if job is None:
            if args.once:
                print("No queued analysis jobs.")
                return 0
            time.sleep(max(0.1, args.idle_seconds))
            continue

        print(f"Analysis job {job.id} for video {job.video_id}: {job.status}")
        if job.status == "failed" and job.error_message:
            print(job.error_message, file=sys.stderr)
        if args.once:
            return 1 if job.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
