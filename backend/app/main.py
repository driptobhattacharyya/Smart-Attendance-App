import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .db import create_client, ensure_indexes
from .face import OpenCVFaceEngine
from .origin_auth import OriginAuthMiddleware
from .routers import admin, attendance, auth, face, manager, offices, requests

logger = logging.getLogger("attendify")


def create_app(settings: Settings | None = None, *, db=None, face_engine=None) -> FastAPI:
    """Build the app. Tests pass in a mock ``db`` and a fake ``face_engine``."""
    settings = settings or get_settings()

    if settings.environment != "development" and settings.jwt_secret == "dev-only-change-me":
        raise RuntimeError("Set JWT_SECRET to a long random value outside development")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = None
        if db is None:
            client = create_client(settings.mongodb_uri)
            app.state.db = client[settings.mongodb_db]
        else:
            app.state.db = db
        await ensure_indexes(app.state.db)

        if face_engine is not None:
            app.state.face_engine = face_engine
        else:
            try:
                app.state.face_engine = OpenCVFaceEngine(settings.face_models_dir)
            except FileNotFoundError as exc:
                logger.warning("Face recognition disabled: %s", exc)
                app.state.face_engine = None
        yield
        if client is not None:
            await client.close()

    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        description="Smart attendance: face verification + geofencing.",
        lifespan=lifespan,
    )
    # Make every route see the settings this app was built with.
    app.dependency_overrides[get_settings] = lambda: settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if origin_secret := os.environ.get("ORIGIN_SECRET"):
        app.add_middleware(OriginAuthMiddleware, secret=origin_secret)  # outermost

    @app.get("/api/health", tags=["meta"])
    async def health():
        return {"status": "ok", "face_recognition": app.state.face_engine is not None}

    for module in (auth, offices, face, attendance, requests, manager, admin):
        app.include_router(module.router, prefix="/api")
    return app


app = create_app()
