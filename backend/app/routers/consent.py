from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..db import Database, get_db
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


_DESK_APP_SECTION = """## The Attendify Desk app

Your company also uses the Attendify Desk app on office computers, which can stand in for the \
desk beacon. The app on your desk computer shares only:

- whether the computer is **locked or unlocked**,
- **how many seconds** it is since the last keyboard or mouse input (not what was typed),
- whether it is **broadcasting** its Bluetooth signal,
- the **app version**, and the computer's **name and operating system**.

It never records keystrokes, screenshots, window titles or which apps you use. If the computer \
is locked or has been idle for a long time, the time is counted as away from your desk.

"""


def render_consent(settings: Settings, desk_app: bool = False) -> str:
    note = _AZURE_NOTE if settings.face_provider == "azure" else _OPENCV_NOTE
    return (
        _template()
        .replace("{desk_app_section}", _DESK_APP_SECTION if desk_app else "")
        .replace("{face_processing}", note)
        .replace("{retention_days}", str(settings.presence_retention_days))
    )


@router.get("/text", response_model=ConsentTextOut)
async def consent_text(settings: Settings = Depends(get_settings), db: Database = Depends(get_db)):
    """Public: the app shows this before the user signs in or registers. Mentions the desk app
    when the organisation has set up any desk computers."""
    desk_app = await db.beacons.find_one({"kind": "desk_app"}) is not None
    return ConsentTextOut(
        version=settings.consent_version,
        title=TITLE,
        body_markdown=render_consent(settings, desk_app),
    )
