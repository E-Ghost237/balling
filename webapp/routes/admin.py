from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp.admin_registry import ADMIN_JOBS, UNDERSTAT_LEAGUES
from webapp.deps import get_db, require_admin
from webapp.jobs import celery_app, run_script_task
from webapp.models import (
    Feedback,
    PaymentRequest,
    Session,
    Subscription,
    UsageLog,
    User,
    VerificationCode,
)
from webapp.plans import PLANS
from webapp.templates import templates

router = APIRouter(prefix="/admin")


@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: User = Depends(require_admin),
    connection: AsyncConnection = Depends(get_db),
):
    pending_count = (
        await connection.execute(
            select(PaymentRequest).where(PaymentRequest.status == "PENDING")
        )
    ).all()
    user_count = (await connection.execute(select(User.id))).all()
    feedback_count = (await connection.execute(select(Feedback.id))).all()
    total_revenue = (
        await connection.execute(
            select(func.coalesce(func.sum(PaymentRequest.amount_fcfa), 0))
            .where(PaymentRequest.status == "APPROVED")
        )
    ).scalar_one()
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user": user,
            "pending_count": len(pending_count),
            "user_count": len(user_count),
            "feedback_count": len(feedback_count),
            "total_revenue": total_revenue,
        },
    )


@router.get("/payments", response_class=HTMLResponse)
async def payments_queue(
    request: Request,
    user: User = Depends(require_admin),
    connection: AsyncConnection = Depends(get_db),
):
    result = await connection.execute(
        select(
            PaymentRequest.id,
            PaymentRequest.transaction_id,
            PaymentRequest.phone_number,
            PaymentRequest.amount_fcfa,
            PaymentRequest.plan,
            PaymentRequest.submitted_at,
            User.login,
        )
        .join(User, User.id == PaymentRequest.user_id)
        .where(PaymentRequest.status == "PENDING")
        .order_by(PaymentRequest.submitted_at)
    )
    rows = result.all()
    return templates.TemplateResponse(
        request, "admin/payments.html", {"user": user, "rows": rows}
    )


@router.post("/payments/{payment_id}/approve")
async def approve_payment(
    payment_id: UUID,
    user: User = Depends(require_admin),
    connection: AsyncConnection = Depends(get_db),
):
    result = await connection.execute(
        select(PaymentRequest).where(PaymentRequest.id == payment_id)
    )
    payment = result.mappings().one_or_none()
    if payment is None or payment["status"] != "PENDING":
        return RedirectResponse("/admin/payments", status_code=303)

    plan = PLANS[payment["plan"]]
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=plan.duration_days)
    statement = (
        pg_insert(Subscription)
        .values(
            id=uuid4(),
            user_id=payment["user_id"],
            status="ACTIVE",
            plan=payment["plan"],
            quota_limit=plan.quota,
            cycle_started_at=now,
            expires_at=expires_at,
        )
        .on_conflict_do_update(
            index_elements=[Subscription.user_id],
            set_={
                "status": "ACTIVE",
                "plan": payment["plan"],
                "quota_limit": plan.quota,
                "cycle_started_at": now,
                "expires_at": expires_at,
            },
        )
    )
    await connection.execute(statement)
    await connection.execute(
        update(PaymentRequest)
        .where(PaymentRequest.id == payment_id)
        .values(status="APPROVED", reviewed_at=now)
    )
    return RedirectResponse("/admin/payments", status_code=303)


@router.post("/payments/{payment_id}/reject")
async def reject_payment(
    payment_id: UUID,
    user: User = Depends(require_admin),
    connection: AsyncConnection = Depends(get_db),
):
    await connection.execute(
        update(PaymentRequest)
        .where(PaymentRequest.id == payment_id, PaymentRequest.status == "PENDING")
        .values(status="REJECTED", reviewed_at=datetime.now(UTC))
    )
    return RedirectResponse("/admin/payments", status_code=303)


@router.get("/finance", response_class=HTMLResponse)
async def finance_dashboard(
    request: Request,
    user: User = Depends(require_admin),
    connection: AsyncConnection = Depends(get_db),
):
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    approved = PaymentRequest.status == "APPROVED"

    total_revenue, total_count = (
        await connection.execute(
            select(func.coalesce(func.sum(PaymentRequest.amount_fcfa), 0), func.count())
            .where(approved)
        )
    ).one()
    month_revenue, month_count = (
        await connection.execute(
            select(func.coalesce(func.sum(PaymentRequest.amount_fcfa), 0), func.count())
            .where(approved, PaymentRequest.reviewed_at >= month_start)
        )
    ).one()

    by_plan_rows = (
        await connection.execute(
            select(
                PaymentRequest.plan,
                func.count(),
                func.coalesce(func.sum(PaymentRequest.amount_fcfa), 0),
            )
            .where(approved)
            .group_by(PaymentRequest.plan)
            .order_by(func.sum(PaymentRequest.amount_fcfa).desc())
        )
    ).all()
    max_plan_revenue = max((row[2] for row in by_plan_rows), default=0) or 1
    by_plan = [
        {
            "plan": PLANS[plan_key].name if plan_key in PLANS else plan_key,
            "count": count,
            "revenue": revenue,
            "bar_pct": round(revenue / max_plan_revenue * 100, 1),
        }
        for plan_key, count, revenue in by_plan_rows
    ]

    recent = (
        await connection.execute(
            select(
                PaymentRequest.plan,
                PaymentRequest.amount_fcfa,
                PaymentRequest.reviewed_at,
                User.login,
            )
            .join(User, User.id == PaymentRequest.user_id)
            .where(approved)
            .order_by(PaymentRequest.reviewed_at.desc())
            .limit(20)
        )
    ).all()

    return templates.TemplateResponse(
        request,
        "admin/finance.html",
        {
            "user": user,
            "total_revenue": total_revenue,
            "total_count": total_count,
            "month_revenue": month_revenue,
            "month_count": month_count,
            "by_plan": by_plan,
            "recent": recent,
        },
    )


@router.get("/feedback", response_class=HTMLResponse)
async def feedback_list(
    request: Request,
    user: User = Depends(require_admin),
    connection: AsyncConnection = Depends(get_db),
):
    rows = (
        await connection.execute(
            select(Feedback.rating, Feedback.message, Feedback.created_at, User.login)
            .join(User, User.id == Feedback.user_id)
            .order_by(Feedback.created_at.desc())
        )
    ).all()
    return templates.TemplateResponse(request, "admin/feedback.html", {"user": user, "rows": rows})


@router.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    user: User = Depends(require_admin),
    connection: AsyncConnection = Depends(get_db),
):
    # .correlate() must be called on the inner DISTINCT select (the one
    # that actually references User.id / Subscription.cycle_started_at) —
    # calling it on the outer count()-wrapper instead silently produces an
    # UNcorrelated subquery (usage_log CROSS JOIN users CROSS JOIN
    # subscriptions), which computed the same wrong total for every row.
    quota_used_inner = (
        select(UsageLog.home_team, UsageLog.away_team, UsageLog.neutral)
        .where(
            UsageLog.user_id == User.id,
            UsageLog.simulated_at >= Subscription.cycle_started_at,
        )
        .distinct()
        .correlate(User, Subscription)
    )
    quota_used_subq = (
        select(func.count()).select_from(quota_used_inner.subquery()).scalar_subquery()
    )
    result = await connection.execute(
        select(
            User.id,
            User.login,
            User.first_name,
            User.last_name,
            User.is_admin,
            User.is_active,
            User.email_verified,
            Subscription.status,
            Subscription.plan,
            Subscription.quota_limit,
            Subscription.expires_at,
            quota_used_subq.label("quota_used"),
        )
        .select_from(User)
        .join(Subscription, Subscription.user_id == User.id, isouter=True)
        .order_by(User.created_at.desc())
    )
    rows = result.all()
    return templates.TemplateResponse(
        request, "admin/users.html", {"user": user, "rows": rows, "plans": PLANS}
    )


@router.post("/users/{target_id}/deactivate")
async def deactivate_user(
    target_id: UUID,
    user: User = Depends(require_admin),
    connection: AsyncConnection = Depends(get_db),
):
    result = await connection.execute(select(User.is_admin).where(User.id == target_id))
    is_target_admin = result.scalar_one_or_none()
    if is_target_admin is None or is_target_admin or target_id == user.id:
        return RedirectResponse(
            "/admin/users?error=Cannot+deactivate+that+account", status_code=303
        )
    await connection.execute(update(User).where(User.id == target_id).values(is_active=False))
    await connection.execute(delete(Session).where(Session.user_id == target_id))
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{target_id}/reactivate")
async def reactivate_user(
    target_id: UUID,
    user: User = Depends(require_admin),
    connection: AsyncConnection = Depends(get_db),
):
    await connection.execute(update(User).where(User.id == target_id).values(is_active=True))
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{target_id}/reset-quota")
async def reset_quota(
    target_id: UUID,
    user: User = Depends(require_admin),
    connection: AsyncConnection = Depends(get_db),
):
    """Rolls a subscription's cycle forward to now — the same mechanism
    the free plan already uses to auto-renew monthly (see
    auth.get_subscription) — so quota_used reads back as 0 immediately,
    letting the user simulate matchups again without waiting out the
    natural cycle."""
    await connection.execute(
        update(Subscription)
        .where(Subscription.user_id == target_id)
        .values(cycle_started_at=datetime.now(UTC))
    )
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{target_id}/delete")
async def delete_user(
    target_id: UUID,
    user: User = Depends(require_admin),
    connection: AsyncConnection = Depends(get_db),
):
    result = await connection.execute(select(User.is_admin).where(User.id == target_id))
    is_target_admin = result.scalar_one_or_none()
    if is_target_admin is None or is_target_admin or target_id == user.id:
        return RedirectResponse(
            "/admin/users?error=Cannot+delete+that+account", status_code=303
        )
    await connection.execute(delete(UsageLog).where(UsageLog.user_id == target_id))
    await connection.execute(delete(Feedback).where(Feedback.user_id == target_id))
    await connection.execute(delete(PaymentRequest).where(PaymentRequest.user_id == target_id))
    await connection.execute(delete(Session).where(Session.user_id == target_id))
    await connection.execute(delete(VerificationCode).where(VerificationCode.user_id == target_id))
    await connection.execute(delete(Subscription).where(Subscription.user_id == target_id))
    await connection.execute(delete(User).where(User.id == target_id))
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request, user: User = Depends(require_admin)):
    return templates.TemplateResponse(
        request,
        "admin/tools.html",
        {"user": user, "jobs": ADMIN_JOBS.values(), "understat_leagues": UNDERSTAT_LEAGUES},
    )


@router.post("/tools/run/{job_key}")
async def run_job(job_key: str, user: User = Depends(require_admin)):
    job = ADMIN_JOBS.get(job_key)
    if job is None:
        return RedirectResponse("/admin/tools?error=Unknown+job", status_code=303)
    async_result = run_script_task.delay(job.script, list(job.args))
    return RedirectResponse(f"/admin/jobs/{async_result.id}", status_code=303)


@router.post("/tools/run-understat")
async def run_understat(
    user: User = Depends(require_admin),
    league: str = Form(...),
    season: int = Form(...),
):
    if league not in UNDERSTAT_LEAGUES:
        return RedirectResponse("/admin/tools?error=Unknown+league", status_code=303)
    async_result = run_script_task.delay(
        "scrapers/understat_scraper.py", ["--league", league, "--season", str(season)]
    )
    return RedirectResponse(f"/admin/jobs/{async_result.id}", status_code=303)


@router.get("/jobs/{task_id}", response_class=HTMLResponse)
async def job_status(request: Request, task_id: str, user: User = Depends(require_admin)):
    result = AsyncResult(task_id, app=celery_app)
    return templates.TemplateResponse(
        request,
        "admin/job_status.html",
        {
            "user": user,
            "task_id": task_id,
            "state": result.state,
            "ready": result.ready(),
            "value": result.result if result.ready() else None,
        },
    )
