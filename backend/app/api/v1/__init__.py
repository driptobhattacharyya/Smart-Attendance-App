from fastapi import APIRouter

from . import (
    attendance,
    audit,
    auth,
    desk,
    face,
    health,
    internal,
    orgs,
    people,
    places,
    reports,
    requests,
)

api_router = APIRouter(prefix="/api/v1")
for module in (
    health,
    auth,
    orgs,
    people,
    places,
    desk,
    audit,
    attendance,
    face,
    requests,
    reports,
    internal,
):
    api_router.include_router(module.router)
