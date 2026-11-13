from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...core.db import Database, get_database

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health(db: Annotated[Database, Depends(get_database)]):
    try:
        await db.ping()
    except Exception:
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    return {"status": "ok", "database": "up"}
