import json
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
from webapp.templates import templates

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "modeling"))
import simulate  # modeling/simulate.py  # noqa: E402

sys.path.insert(0, str(BASE_DIR))
from flags import flag_code_for_country  # noqa: E402

router = APIRouter()

PAGE_SIZE = 20


def _most_likely_scores(db_path: str, prediction_ids: list[int]) -> dict[int, str]:
    if not prediction_ids:
        return {}
    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" * len(prediction_ids))
        rows = conn.execute(
            f"SELECT id, most_likely_score FROM predictions WHERE id IN ({placeholders})",
            prediction_ids,
        ).fetchall()
    finally:
        conn.close()
    return {row[0]: row[1] for row in rows}


def _load_prediction_detail(db_path: str, prediction_id: int) -> dict | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT p.lambda_home, p.lambda_away, p.prob_home_win, p.prob_draw, p.prob_away_win,
                   p.most_likely_score, p.n_simulations, p.extra_json,
                   ht.name, ht.country, ht.id, at.name, at.country, at.id
            FROM predictions p
            JOIN teams ht ON ht.id = p.home_team_id
            JOIN teams at ON at.id = p.away_team_id
            WHERE p.id = ?
            """,
            (prediction_id,),
        ).fetchone()
        if row is None:
            return None
        scorelines = conn.execute(
            "SELECT home_goals, away_goals, probability FROM prediction_scorelines "
            "WHERE prediction_id = ?",
            (prediction_id,),
        ).fetchall()
        (
            lambda_home, lambda_away, prob_home_win, prob_draw, prob_away_win,
            most_likely_score, n_sims, extra_json,
            home_name, home_country, home_id, away_name, away_country, away_id,
        ) = row
        home_rating = simulate.get_latest_rating(conn, home_id)
        away_rating = simulate.get_latest_rating(conn, away_id)
    finally:
        conn.close()

    extra = json.loads(extra_json) if extra_json else {}
    scoreline_probs = {f"{h}-{a}": prob for h, a, prob in scorelines}
    result = {
        "home_team": home_name,
        "away_team": away_name,
        "home_elo": home_rating["elo"] if home_rating else 0,
        "away_elo": away_rating["elo"] if away_rating else 0,
        "lambda_home": lambda_home,
        "lambda_away": lambda_away,
        "prob_home_win": prob_home_win,
        "prob_draw": prob_draw,
        "prob_away_win": prob_away_win,
        "most_likely_score": most_likely_score,
        "n_sims": n_sims,
        "scoreline_probs": scoreline_probs,
        "prediction_id": prediction_id,
        "markets": extra.get("markets"),
        "best_picks": extra.get("best_picks"),
    }
    home_badge = {
        "name": home_name, "flag_code": flag_code_for_country(home_country), "team_id": home_id,
    }
    away_badge = {
        "name": away_name, "flag_code": flag_code_for_country(away_country), "team_id": away_id,
    }
    return {
        "result": result,
        "home_badge": home_badge,
        "away_badge": away_badge,
        "scoreline_matrix": _scoreline_matrix(result),
    }


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
    settings = get_settings()
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
    scores = _most_likely_scores(settings.football_db_path, prediction_ids)
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

    settings = get_settings()
    detail = _load_prediction_detail(settings.football_db_path, usage_row["prediction_id"])
    if detail is None:
        raise HTTPException(status_code=404)

    return templates.TemplateResponse(
        request,
        "history_detail.html",
        {
            "user": user,
            "simulated_at": usage_row["simulated_at"],
            "home_badge": detail["home_badge"],
            "away_badge": detail["away_badge"],
            "result": detail["result"],
            "scoreline_matrix": detail["scoreline_matrix"],
        },
    )
