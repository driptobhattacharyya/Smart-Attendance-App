from fastapi import APIRouter

from . import audit, auth, desk, health, orgs, people, places

api_router = APIRouter(prefix="/api/v1")
for module in (health, auth, orgs, people, places, desk, audit):
    api_router.include_router(module.router)
