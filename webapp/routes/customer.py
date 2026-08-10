import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp.auth import (
    authenticate,
    create_session,
    get_subscription,
    subscription_is_active,
    transaction_id_already_used,
)
from webapp.config import get_settings
from webapp.deps import get_db, get_optional_user, require_login
from webapp.email import send_email
from webapp.models import PaymentRequest, Subscription, User
from webapp.otp import (
    PURPOSE_EMAIL_VERIFY,
    PURPOSE_PASSWORD_RESET,
    create_verification_code,
    seconds_until_resend_allowed,
    verify_code,
)
from webapp.plans import PAID_PLANS, PLANS
from webapp.quota import check_quota, distinct_matchup_count, record_usage
from webapp.security import generate_session_token, hash_password
from webapp.templates import templates

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def _send_verification_email(connection: AsyncConnection, user_id, login: str) -> bool:
    code = await create_verification_code(connection, user_id, PURPOSE_EMAIL_VERIFY)
    return send_email(
        login,
        "Confirm your Balling Predictions account",
        f"Enter this code to verify your account. It expires in "
        f"{get_settings().verification_code_ttl_minutes} minutes.",
        code=code,
    )

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "modeling"))
import simulate  # modeling/simulate.py  # noqa: E402

sys.path.insert(0, str(BASE_DIR))
from flags import flag_code_for_country  # noqa: E402

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, user: User | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse("/login")
    return RedirectResponse("/simulate")


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/simulate"):
    return templates.TemplateResponse(request, "login.html", {"next": next, "user": None})


@router.post("/login")
async def login_submit(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    next: str = Form("/simulate"),
    connection: AsyncConnection = Depends(get_db),
):
    login = login.strip().lower()
    user = await authenticate(connection, login, password)
    if user is None:
        return RedirectResponse(
            f"/login?error=Invalid+login+or+password&next={next}", status_code=303
        )
    if not user.is_admin and not user.email_verified:
        sent = await _send_verification_email(connection, user.id, user.login)
        suffix = "" if sent else "&error=Could+not+send+the+code+email+%E2%80%94+try+Resend"
        return RedirectResponse(f"/verify-email?login={user.login}{suffix}", status_code=303)
    token = generate_session_token()
    await create_session(connection, user.id, token)
    response = RedirectResponse(next or "/simulate", status_code=303)
    response.set_cookie(
        get_settings().session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        max_age=get_settings().session_ttl_days * 86400,
    )
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    return templates.TemplateResponse(request, "register.html", {"user": None})


@router.post("/register")
async def register_submit(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    login: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    connection: AsyncConnection = Depends(get_db),
):
    login = login.strip().lower()
    if not first_name.strip() or not last_name.strip():
        return RedirectResponse(
            "/register?error=Enter+your+first+and+last+name", status_code=303
        )
    if not _EMAIL_RE.match(login):
        return RedirectResponse("/register?error=Enter+a+valid+email+address", status_code=303)
    if password != confirm_password:
        return RedirectResponse("/register?error=Passwords+do+not+match", status_code=303)
    existing = await connection.execute(select(User.id).where(User.login == login))
    if existing.first() is not None:
        return RedirectResponse("/register?error=That+login+is+already+registered", status_code=303)
    user_id = uuid4()
    await connection.execute(
        User.__table__.insert().values(
            id=user_id,
            login=login,
            password_hash=hash_password(password),
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            is_admin=False,
            email_verified=False,
            created_at=datetime.now(UTC),
        )
    )
    sent = await _send_verification_email(connection, user_id, login)
    suffix = "" if sent else "&error=Could+not+send+the+code+email+%E2%80%94+try+Resend"
    return RedirectResponse(f"/verify-email?login={login}{suffix}", status_code=303)


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email_form(
    request: Request,
    login: str,
    connection: AsyncConnection = Depends(get_db),
):
    login = login.strip().lower()
    user_result = await connection.execute(select(User.id).where(User.login == login))
    user_id = user_result.scalar_one_or_none()
    resend_wait_seconds = 0
    if user_id is not None:
        resend_wait_seconds = await seconds_until_resend_allowed(
            connection, user_id, PURPOSE_EMAIL_VERIFY
        )
    return templates.TemplateResponse(
        request,
        "verify_email.html",
        {"user": None, "login": login, "resend_wait_seconds": resend_wait_seconds},
    )


@router.post("/verify-email")
async def verify_email_submit(
    request: Request,
    login: str = Form(...),
    code: str = Form(...),
    connection: AsyncConnection = Depends(get_db),
):
    login = login.strip().lower()
    user_result = await connection.execute(select(User).where(User.login == login))
    row = user_result.mappings().one_or_none()
    if row is None:
        return RedirectResponse("/register", status_code=303)
    ok = await verify_code(connection, row["id"], PURPOSE_EMAIL_VERIFY, code)
    if not ok:
        return RedirectResponse(
            f"/verify-email?login={login}&error=Invalid+or+expired+code", status_code=303
        )
    await connection.execute(
        User.__table__.update().where(User.id == row["id"]).values(email_verified=True)
    )
    now = datetime.now(UTC)
    free_plan = PLANS["free"]
    await connection.execute(
        pg_insert(Subscription)
        .values(
            id=uuid4(),
            user_id=row["id"],
            status="ACTIVE",
            plan="free",
            quota_limit=free_plan.quota,
            cycle_started_at=now,
            expires_at=now + timedelta(days=365 * 100),
        )
        .on_conflict_do_nothing(index_elements=[Subscription.user_id])
    )
    token = generate_session_token()
    await create_session(connection, row["id"], token)
    response = RedirectResponse("/simulate", status_code=303)
    response.set_cookie(
        get_settings().session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        max_age=get_settings().session_ttl_days * 86400,
    )
    return response


@router.post("/verify-email/resend")
async def verify_email_resend(
    request: Request,
    login: str = Form(...),
    connection: AsyncConnection = Depends(get_db),
):
    login = login.strip().lower()
    user_result = await connection.execute(select(User.id).where(User.login == login))
    user_id = user_result.scalar_one_or_none()
    suffix = ""
    if user_id is not None:
        wait = await seconds_until_resend_allowed(connection, user_id, PURPOSE_EMAIL_VERIFY)
        if wait <= 0:
            sent = await _send_verification_email(connection, user_id, login)
            if not sent:
                suffix = "&error=Could+not+send+the+code+email+%E2%80%94+please+try+again+shortly"
    return RedirectResponse(f"/verify-email?login={login}{suffix}", status_code=303)


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_form(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {"user": None})


@router.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    login: str = Form(...),
    connection: AsyncConnection = Depends(get_db),
):
    login = login.strip().lower()
    user_result = await connection.execute(select(User.id).where(User.login == login))
    user_id = user_result.scalar_one_or_none()
    if user_id is not None:
        code = await create_verification_code(connection, user_id, PURPOSE_PASSWORD_RESET)
        send_email(
            login,
            "Reset your Balling Predictions password",
            f"Enter this code to reset your password. It expires in "
            f"{get_settings().verification_code_ttl_minutes} minutes. "
            "If you didn't request this, you can ignore this email.",
            code=code,
        )
    # Same redirect whether or not the account exists, so this can't be used
    # to probe which emails are registered.
    return RedirectResponse(f"/reset-password?login={login}", status_code=303)


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_form(request: Request, login: str):
    return templates.TemplateResponse(
        request, "reset_password.html", {"user": None, "login": login.strip().lower()}
    )


@router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    login: str = Form(...),
    code: str = Form(...),
    password: str = Form(...),
    connection: AsyncConnection = Depends(get_db),
):
    login = login.strip().lower()
    user_result = await connection.execute(select(User.id).where(User.login == login))
    user_id = user_result.scalar_one_or_none()
    if user_id is None:
        return RedirectResponse(
            f"/reset-password?login={login}&error=Invalid+or+expired+code", status_code=303
        )
    ok = await verify_code(connection, user_id, PURPOSE_PASSWORD_RESET, code)
    if not ok:
        return RedirectResponse(
            f"/reset-password?login={login}&error=Invalid+or+expired+code", status_code=303
        )
    await connection.execute(
        User.__table__.update()
        .where(User.id == user_id)
        .values(password_hash=hash_password(password))
    )
    return RedirectResponse("/login?error=Password+updated,+log+in+below", status_code=303)


@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(get_settings().session_cookie_name)
    return response


@router.get("/forbidden", response_class=HTMLResponse)
async def forbidden(request: Request, user: User | None = Depends(get_optional_user)):
    return templates.TemplateResponse(request, "forbidden.html", {"user": user}, status_code=403)


def _load_leagues(db_path: str) -> list[tuple[str, list[dict]]]:
    """Leagues grouped for the <optgroup> dropdown: a "Friendly" group with
    one synthetic entry (any two teams, unconstrained by league — the
    simulation engine itself has no notion of league anyway), then one
    group per country for domestic club leagues, plus "International"."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, name, country, kind FROM leagues ORDER BY country IS NULL, country, name"
        ).fetchall()
    finally:
        conn.close()
    friendly_entry = {
        "id": FRIENDLY_LEAGUE_ID, "name": "Friendly match (any two teams)", "kind": "friendly"
    }
    groups: dict[str, list[dict]] = {"Friendly": [friendly_entry]}
    for league_id, name, country, kind in rows:
        group_label = country if country else "International"
        groups.setdefault(group_label, []).append({"id": league_id, "name": name, "kind": kind})
    return list(groups.items())


FRIENDLY_LEAGUE_ID = 0  # sentinel: "any two teams", not a real leagues.id


def _load_teams_for_league(db_path: str, league_id: int) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        if league_id == FRIENDLY_LEAGUE_ID:
            rows = conn.execute("SELECT DISTINCT name FROM teams ORDER BY name").fetchall()
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT t.name FROM teams t
                JOIN matches m ON t.id IN (m.home_team_id, m.away_team_id)
                WHERE m.league_id = ?
                ORDER BY t.name
                """,
                (league_id,),
            ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def _team_badge(db_path: str, name: str) -> dict[str, str | int | None]:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT country, id FROM teams WHERE name = ?", (name,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"name": name, "flag_code": None, "team_id": None}
    country, team_id = row
    return {"name": name, "flag_code": flag_code_for_country(country), "team_id": team_id}


# Same blue ramp as the original Streamlit app's scoreline heatmap.
_SEQUENTIAL_BLUE = ["cde2fb", "9ec5f4", "6da7ec", "3987e5", "256abf", "184f95", "0d366b"]
_MAX_DISPLAY_GOALS = 6


def _interpolate_color(t: float) -> str:
    t = max(0.0, min(1.0, t))
    steps = len(_SEQUENTIAL_BLUE) - 1
    scaled = t * steps
    i = min(int(scaled), steps - 1)
    frac = scaled - i
    r1, g1, b1 = (int(_SEQUENTIAL_BLUE[i][j : j + 2], 16) for j in (0, 2, 4))
    r2, g2, b2 = (int(_SEQUENTIAL_BLUE[i + 1][j : j + 2], 16) for j in (0, 2, 4))
    r = round(r1 + (r2 - r1) * frac)
    g = round(g1 + (g2 - g1) * frac)
    b = round(b1 + (b2 - b1) * frac)
    return f"#{r:02x}{g:02x}{b:02x}"


def _scoreline_matrix(result: dict) -> dict:
    max_g = _MAX_DISPLAY_GOALS
    labels = [str(i) if i < max_g else f"{i}+" for i in range(max_g + 1)]
    matrix = [[0.0] * (max_g + 1) for _ in range(max_g + 1)]
    for scoreline, prob in result["scoreline_probs"].items():
        h, a = scoreline.replace("+", "").split("-")
        matrix[int(h)][int(a)] = prob * 100
    max_val = max(max(row) for row in matrix) or 1.0
    grid = []
    for row in matrix:
        rendered_row = []
        for value in row:
            t = value / max_val
            rendered_row.append(
                {
                    "value": round(value, 1),
                    "color": _interpolate_color(t),
                    "text_color": "#f8fafc" if t >= 0.5 else "#0f172a",
                }
            )
        grid.append(rendered_row)
    return {"labels": labels, "grid": grid}


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _render_simulate(request: Request, context: dict) -> HTMLResponse:
    template_name = "_simulate_content.html" if _is_htmx(request) else "simulate.html"
    return templates.TemplateResponse(request, template_name, context)


@router.get("/simulate", response_class=HTMLResponse)
async def simulate_page(
    request: Request,
    user: User = Depends(require_login),
    connection: AsyncConnection = Depends(get_db),
):
    settings = get_settings()
    subscription = await get_subscription(connection, user.id)
    active = user.is_admin or subscription_is_active(subscription, datetime.now(UTC))
    leagues = _load_leagues(settings.football_db_path) if active else []
    quota_used = 0
    quota_limit = subscription.quota_limit or 0 if subscription else 0
    if active and not user.is_admin and subscription is not None:
        quota_used = await distinct_matchup_count(
            connection, user.id, subscription.cycle_started_at
        )
    return _render_simulate(
        request,
        {
            "user": user,
            "active": active,
            "leagues": leagues,
            "teams": [],
            "quota_used": quota_used,
            "quota_limit": quota_limit,
            "result": None,
        },
    )


@router.get("/simulate/teams-for-league", response_class=HTMLResponse)
async def teams_for_league(
    request: Request,
    league_id: int = Query(...),
    user: User = Depends(require_login),
):
    settings = get_settings()
    teams = _load_teams_for_league(settings.football_db_path, league_id)
    return templates.TemplateResponse(
        request, "_team_selects_oob.html", {"teams": teams}
    )


@router.post("/simulate", response_class=HTMLResponse)
async def simulate_submit(
    request: Request,
    league_id: int = Form(...),
    home: str = Form(...),
    away: str = Form(...),
    neutral: bool = Form(False),
    user: User = Depends(require_login),
    connection: AsyncConnection = Depends(get_db),
):
    settings = get_settings()
    subscription = await get_subscription(connection, user.id)
    if not user.is_admin and not subscription_is_active(subscription, datetime.now(UTC)):
        return RedirectResponse("/payment", status_code=303)

    leagues = _load_leagues(settings.football_db_path)
    teams = _load_teams_for_league(settings.football_db_path, league_id)
    quota_limit = subscription.quota_limit or 0 if subscription else 0

    def base_context(**extra: object) -> dict:
        return {
            "user": user,
            "active": True,
            "leagues": leagues,
            "teams": teams,
            "selected_league_id": league_id,
            "selected_home": home,
            "selected_away": away,
            "neutral": neutral,
            "quota_limit": quota_limit,
            "result": None,
            **extra,
        }

    if home == away:
        quota_used = 0
        if not user.is_admin and subscription is not None:
            quota_used = await distinct_matchup_count(
                connection, user.id, subscription.cycle_started_at
            )
        return _render_simulate(
            request,
            base_context(
                quota_used=quota_used,
                form_error="Home and away team must be different.",
            ),
        )

    quota_state = None
    if not user.is_admin:
        quota_state = await check_quota(
            connection, user.id, home, away, neutral, subscription.cycle_started_at, quota_limit
        )
        if not quota_state.allowed:
            return _render_simulate(
                request,
                base_context(quota_used=quota_state.distinct_used, quota_exceeded=True),
            )

    conn = sqlite3.connect(settings.football_db_path)
    try:
        result = simulate.predict_match(
            conn, settings.football_db_path, home, away, save=True, neutral=neutral
        )
    except ValueError as exc:
        conn.close()
        return _render_simulate(
            request,
            base_context(
                quota_used=quota_state.distinct_used if quota_state else 0,
                form_error=str(exc),
            ),
        )
    conn.close()

    if not user.is_admin:
        await record_usage(
            connection, user.id, home, away, neutral, prediction_id=result["prediction_id"]
        )

    quota_used_after = 0
    if quota_state:
        quota_used_after = quota_state.distinct_used + (1 if quota_state.is_new_matchup else 0)
    return _render_simulate(
        request,
        base_context(
            quota_used=quota_used_after,
            result=result,
            quota_warning=quota_state.warn if quota_state else False,
            home_badge=_team_badge(settings.football_db_path, home),
            away_badge=_team_badge(settings.football_db_path, away),
            scoreline_matrix=_scoreline_matrix(result),
        ),
    )


@router.get("/payment", response_class=HTMLResponse)
async def payment_form(request: Request, user: User = Depends(require_login)):
    return templates.TemplateResponse(
        request, "payment.html", {"user": user, "plans": PAID_PLANS}
    )


@router.post("/payment")
async def payment_submit(
    request: Request,
    plan: str = Form(...),
    transaction_id: str = Form(...),
    phone_number: str = Form(...),
    amount_fcfa: int = Form(...),
    user: User = Depends(require_login),
    connection: AsyncConnection = Depends(get_db),
):
    plans_by_key = {p.key: p for p in PAID_PLANS}
    if plan not in plans_by_key:
        return RedirectResponse("/payment?error=Choose+a+plan", status_code=303)
    if amount_fcfa != plans_by_key[plan].price_fcfa:
        return RedirectResponse(
            "/payment?error=That+amount+doesn%27t+match+the+selected+plan%27s+price+"
            "%E2%80%94+please+try+again",
            status_code=303,
        )
    if await transaction_id_already_used(connection, transaction_id):
        return RedirectResponse(
            "/payment?error=That+transaction+ID+has+already+been+used", status_code=303
        )
    await connection.execute(
        PaymentRequest.__table__.insert().values(
            id=uuid4(),
            user_id=user.id,
            transaction_id=transaction_id,
            phone_number=phone_number,
            amount_fcfa=amount_fcfa,
            plan=plan,
            status="PENDING",
            submitted_at=datetime.now(UTC),
        )
    )
    return templates.TemplateResponse(request, "payment_submitted.html", {"user": user})


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request,
    user: User = Depends(require_login),
    connection: AsyncConnection = Depends(get_db),
):
    subscription = await get_subscription(connection, user.id)
    quota_used = 0
    if subscription is not None and subscription.cycle_started_at is not None:
        quota_used = await distinct_matchup_count(
            connection, user.id, subscription.cycle_started_at
        )
    plan = PLANS.get(subscription.plan) if subscription and subscription.plan else None
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "user": user,
            "subscription": subscription,
            "plan": plan,
            "quota_used": quota_used,
            "quota_limit": subscription.quota_limit or 0 if subscription else 0,
        },
    )
