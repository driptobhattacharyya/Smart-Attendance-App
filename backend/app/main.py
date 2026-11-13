import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1 import api_router
from .attendance.trust import build_integrity_verifier
from .core.config import Settings, get_settings
from .core.db import Database
from .core.email import build_email_sender
from .core.errors import install_error_handlers
from .core.security import PasswordHasher
from .origin_auth import OriginAuthMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.assert_production_ready()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.db.dispose()

    app = FastAPI(
        title=f"{settings.app_name} API",
        version="5.0.0",
        description="Multi-tenant attendance platform.",
        lifespan=lifespan,
    )
    # Engines connect lazily, so building them here also works for ASGI test transports that
    # do not run the lifespan.
    app.state.settings = settings
    app.state.db = Database(settings)
    app.state.hasher = PasswordHasher(settings)
    app.state.email = build_email_sender(settings)
    # Pluggable collaborators; tests replace them. The face provider is built lazily on first
    # use (loading the models takes seconds).
    app.state.integrity_verifier = build_integrity_verifier(settings)
    app.state.face_provider = None

    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if origin_secret := os.environ.get("ORIGIN_SECRET"):
        app.add_middleware(OriginAuthMiddleware, secret=origin_secret)  # outermost

    app.include_router(api_router)
    return app


app = create_app()
