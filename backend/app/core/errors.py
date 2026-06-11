from __future__ import annotations

from fastapi import HTTPException, status


class AppError(HTTPException):
    """Base for typed application errors that the API layer raises."""

    error_code: str = "app_error"

    def __init__(self, *, status_code: int, message: str, setup_hint: str | None = None) -> None:
        detail: dict[str, str] = {"error": self.error_code, "message": message}
        if setup_hint is not None:
            detail["setup_hint"] = setup_hint
        super().__init__(status_code=status_code, detail=detail)


class FFmpegMissingError(AppError):
    error_code = "ffmpeg_missing"

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="ffmpeg is required for this operation but was not found on PATH.",
            setup_hint="Install ffmpeg and ensure 'ffmpeg' and 'ffprobe' are on PATH. See docs/setup.md.",
        )


class FFprobeMissingError(AppError):
    error_code = "ffprobe_missing"

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="ffprobe is required for this operation but was not found on PATH.",
            setup_hint="Install ffmpeg (ffprobe ships with it) and ensure both are on PATH. See docs/setup.md.",
        )


class YtDlpMissingError(AppError):
    error_code = "yt_dlp_missing"

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            message="URL ingestion requires yt-dlp, which was not found on PATH.",
            setup_hint="Install yt-dlp and ensure it is on PATH. See docs/setup.md.",
        )


class NotFoundError(AppError):
    error_code = "not_found"

    def __init__(self, resource: str) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            message=f"{resource} not found.",
        )


class ValidationFailedError(AppError):
    error_code = "validation_failed"

    def __init__(self, message: str) -> None:
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, message=message)


class FFmpegExecutionError(AppError):
    error_code = "ffmpeg_failed"

    def __init__(self, stderr: str) -> None:
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"ffmpeg failed: {stderr.strip()[:500]}",
        )
