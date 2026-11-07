from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..schemas import ConsentTextOut

router = APIRouter(prefix="/consent", tags=["consent"])

TITLE = "How your employer uses Attendify to record attendance"

_OPENCV_NOTE = (
    "The photo is used to calculate those numbers on our server and is not stored. "
    "Only the numbers are kept."
)
_AZURE_NOTE = (
    "Your photo is sent to Microsoft Azure's face service for processing, which also keeps the "
    "face data it extracts so it can recognise you later. Attendify itself does not store the "
    "photo; it keeps only the numbers and an identifier."
)


@lru_cache
def _template() -> str:
    return Path(__file__).resolve().parent.parent.joinpath("consent.md").read_text("utf-8")


def render_consent(settings: Settings) -> str:
    note = _AZURE_NOTE if settings.face_provider == "azure" else _OPENCV_NOTE
    return (
        _template()
        .replace("{face_processing}", note)
        .replace("{retention_days}", str(settings.presence_retention_days))
    )


@router.get("/text", response_model=ConsentTextOut)
async def consent_text(settings: Settings = Depends(get_settings)):
    """Public: the app shows this before the user signs in or registers."""
    return ConsentTextOut(
        version=settings.consent_version, title=TITLE, body_markdown=render_consent(settings)
    )
