import sqlite3
import sys
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp.config import get_settings
from webapp.deps import get_db, require_login
from webapp.models import UsageLog, User
from webapp.predictions import load_prediction, most_likely_scores
from webapp.templates import templates

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "modeling"))
import simulate  # modeling/simulate.py  # noqa: E402

router = APIRouter()

PAGE_SIZE = 20


def _team_elo(db_path: str, team_id: int | None) -> float:
    if team_id is None:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        rating = simulate.get_latest_rating(conn, team_id)
    finally:
        conn.close()
    return rating["elo"] if rating else 0


def _team_badge(db_path: str, name: str) -> dict[str, str | int | None]:
    from webapp.routes.customer import _team_badge as team_badge

    return team_badge(db_path, name)


def _scoreline_matrix(result: dict) -> dict:
    from webapp.routes.customer import _scoreline_matrix as build_matrix

    return build_matrix(result)


@router.get("/history", response_class=HTMLResponse)
async def history_list(
    request: Request,
    user: User = Depends(require_login),
    connection: AsyncConnection = Depends(get_db),
    page: int = Query(1, ge=1),
):
    offset = (page - 1) * PAGE_SIZE
    result = await connection.execute(
        select(
            UsageLog.id,
            UsageLog.home_team,
            UsageLog.away_team,
            UsageLog.neutral,
            UsageLog.simulated_at,
            UsageLog.prediction_id,
        )
        .where(UsageLog.user_id == user.id)
        .order_by(UsageLog.simulated_at.desc())
        .limit(PAGE_SIZE + 1)
        .offset(offset)
    )
    rows = result.all()
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]

    prediction_ids = [row.prediction_id for row in rows if row.prediction_id is not None]
    scores = await most_likely_scores(connection, prediction_ids)
    entries = [
        {
            "id": row.id,
            "home_team": row.home_team,
            "away_team": row.away_team,
            "neutral": row.neutral,
            "simulated_at": row.simulated_at,
            "prediction_id": row.prediction_id,
            "most_likely_score": scores.get(row.prediction_id) if row.prediction_id else None,
        }
        for row in rows
    ]
    return templates.TemplateResponse(
        request,
        "history_list.html",
        {"user": user, "entries": entries, "page": page, "has_next": has_next},
    )


@router.get("/history/{usage_log_id}", response_class=HTMLResponse)
async def history_detail(
    request: Request,
    usage_log_id: UUID,
    user: User = Depends(require_login),
    connection: AsyncConnection = Depends(get_db),
):
    result = await connection.execute(
        select(UsageLog).where(UsageLog.id == usage_log_id, UsageLog.user_id == user.id)
    )
    usage_row = result.mappings().one_or_none()
    if usage_row is None or usage_row["prediction_id"] is None:
        raise HTTPException(status_code=404)

    result_dict = await load_prediction(connection, usage_row["prediction_id"])
    if result_dict is None:
        raise HTTPException(status_code=404)

    settings = get_settings()
    home_badge = _team_badge(settings.football_db_path, result_dict["home_team"])
    away_badge = _team_badge(settings.football_db_path, result_dict["away_team"])
    result_dict["home_elo"] = _team_elo(settings.football_db_path, home_badge["team_id"])
    result_dict["away_elo"] = _team_elo(settings.football_db_path, away_badge["team_id"])

    return templates.TemplateResponse(
        request,
        "history_detail.html",
        {
            "user": user,
            "simulated_at": usage_row["simulated_at"],
            "home_badge": home_badge,
            "away_badge": away_badge,
            "result": result_dict,
            "scoreline_matrix": _scoreline_matrix(result_dict),
        },
    )
