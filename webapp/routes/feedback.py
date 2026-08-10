from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp.deps import get_db, require_login
from webapp.models import Feedback, User
from webapp.templates import templates

router = APIRouter()


@router.get("/feedback", response_class=HTMLResponse)
async def feedback_form(request: Request, user: User = Depends(require_login)):
    return templates.TemplateResponse(request, "feedback.html", {"user": user})


@router.post("/feedback")
async def feedback_submit(
    request: Request,
    message: str = Form(...),
    rating: int | None = Form(None),
    user: User = Depends(require_login),
    connection: AsyncConnection = Depends(get_db),
):
    if not message.strip():
        return RedirectResponse("/feedback?error=Tell+us+something+first", status_code=303)
    await connection.execute(
        Feedback.__table__.insert().values(
            id=uuid4(),
            user_id=user.id,
            rating=rating,
            message=message.strip(),
            created_at=datetime.now(UTC),
        )
    )
    return templates.TemplateResponse(request, "feedback.html", {"user": user, "sent": True})
