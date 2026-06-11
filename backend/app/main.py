from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import analysis as analysis_routes
from app.api import clips as clips_routes
from app.api import evaluation as evaluation_routes
from app.api import system as system_routes
from app.api import videos as videos_routes
from app.core.config import get_settings
from app.db.database import connection


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Touch the DB once at startup so the schema is created before the first request.
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.videos_dir.mkdir(parents=True, exist_ok=True)
    settings.clips_dir.mkdir(parents=True, exist_ok=True)
    with connection(settings):
        pass
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="What Made 'Em So Mad — backend",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def optional_access_token_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        token = settings.access_token
        if token and request.method not in ("GET", "HEAD", "OPTIONS"):
            supplied = request.headers.get("x-wmesm-token")
            auth = request.headers.get("authorization", "")
            bearer = auth.removeprefix("Bearer ").strip() if auth.lower().startswith("bearer ") else None
            if supplied != token and bearer != token:
                return JSONResponse(
                    status_code=401,
                    content={"detail": {"error": "unauthorized", "message": "valid access token required"}},
                )
        return await call_next(request)

    app.include_router(system_routes.router)
    app.include_router(videos_routes.router)
    app.include_router(analysis_routes.router)
    app.include_router(clips_routes.router)
    app.include_router(evaluation_routes.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
