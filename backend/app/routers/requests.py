from fastapi import APIRouter, Depends, status

from ..db import Database, get_db, utcnow
from ..schemas import RequestIn, RequestOut
from ..security import get_current_user
from ..services import request_out

router = APIRouter(prefix="/requests", tags=["requests"])


@router.post("", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
async def submit_request(
    data: RequestIn,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """A *correction* (single day with entry/exit time) adds a session once approved.
    Anything else is *leave*, shown as an on-leave day in reports."""
    doc = {
        "employee_id": user["employee_id"],
        "team_id": user.get("team_id"),
        "reason": data.reason.strip(),
        "start_date": data.start_date.isoformat(),
        "end_date": data.end_date.isoformat(),
        "entry_time": data.entry_time.strftime("%H:%M") if data.entry_time else None,
        "exit_time": data.exit_time.strftime("%H:%M") if data.exit_time else None,
        "kind": "correction" if data.entry_time else "leave",
        "status": "Pending",
        "submitted_at": utcnow(),
    }
    result = await db.attendance_requests.insert_one(doc)
    doc["_id"] = result.inserted_id
    return request_out(doc, user["name"])


@router.get("/me", response_model=list[RequestOut])
async def my_requests(user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    docs = (
        await db.attendance_requests.find({"employee_id": user["employee_id"]})
        .sort("submitted_at", -1)
        .to_list(length=200)
    )
    return [request_out(d, user["name"]) for d in docs]
