"""Persistence for saved /simulate results — see models.Prediction for why
this lives in Postgres rather than data/football.db."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp.models import Prediction


async def save_prediction(connection: AsyncConnection, result: dict) -> int:
    inserted = await connection.execute(
        Prediction.__table__.insert()
        .values(
            generated_at=datetime.now(UTC),
            home_team=result["home_team"],
            away_team=result["away_team"],
            lambda_home=result["lambda_home"],
            lambda_away=result["lambda_away"],
            n_simulations=result["n_sims"],
            prob_home_win=result["prob_home_win"],
            prob_draw=result["prob_draw"],
            prob_away_win=result["prob_away_win"],
            most_likely_score=result["most_likely_score"],
            scoreline_probs=result["scoreline_probs"],
            markets=result["markets"],
            best_picks=result["best_picks"],
        )
        .returning(Prediction.id)
    )
    return inserted.scalar_one()


async def most_likely_scores(
    connection: AsyncConnection, prediction_ids: list[int]
) -> dict[int, str]:
    if not prediction_ids:
        return {}
    result = await connection.execute(
        select(Prediction.id, Prediction.most_likely_score).where(
            Prediction.id.in_(prediction_ids)
        )
    )
    return dict(result.all())


async def load_prediction(connection: AsyncConnection, prediction_id: int) -> dict | None:
    result = await connection.execute(select(Prediction).where(Prediction.id == prediction_id))
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return {
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "lambda_home": row["lambda_home"],
        "lambda_away": row["lambda_away"],
        "n_sims": row["n_simulations"],
        "prob_home_win": row["prob_home_win"],
        "prob_draw": row["prob_draw"],
        "prob_away_win": row["prob_away_win"],
        "most_likely_score": row["most_likely_score"],
        "scoreline_probs": row["scoreline_probs"],
        "markets": row["markets"],
        "best_picks": row["best_picks"],
        "prediction_id": row["id"],
    }
