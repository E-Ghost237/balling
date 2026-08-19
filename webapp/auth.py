import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp.models import PaymentRequest, Session, Subscription, User
from webapp.plans import PLANS
from webapp.security import hash_token, verify_password

# The free plan has no payment to trigger a renewal, so there's no billing
# job to reset its quota window. Instead, each time it's read (below), a
# cycle older than this just gets rolled forward to now — same effect as a
# monthly renewal, with no scheduler required.
FREE_PLAN_CYCLE_DAYS = 30


async def authenticate(connection: AsyncConnection, login: str, password: str) -> User | None:
    result = await connection.execute(select(User).where(User.login == login))
    row = result.mappings().one_or_none()
    if row is None:
        return None
    if not row["is_active"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return User(**row)


async def create_session(connection: AsyncConnection, user_id: UUID, token: str) -> None:
    """Upserts the one session row for this user. Because `user_id` is
    unique on `sessions`, this overwrites any prior row's token_hash —
    which is the entire single-active-session mechanism: an old raw
    cookie's hash simply stops matching any row in the table once a new
    login happens here. No separate "kick" step is needed."""
    now = datetime.now(UTC)
    statement = (
        pg_insert(Session)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            token_hash=hash_token(token),
            created_at=now,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=[Session.user_id],
            set_={"token_hash": hash_token(token), "created_at": now, "last_seen_at": now},
        )
    )
    await connection.execute(statement)


async def get_session_user(connection: AsyncConnection, token: str) -> User | None:
    result = await connection.execute(
        select(User)
        .join(Session, Session.user_id == User.id)
        .where(Session.token_hash == hash_token(token))
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    if not row["is_active"]:
        # Deactivation must take effect immediately, not just block future
        # logins — this is what actually revokes an already-open session.
        return None
    await connection.execute(
        update(Session)
        .where(Session.user_id == row["id"])
        .values(last_seen_at=datetime.now(UTC))
    )
    return User(**row)


async def get_subscription(connection: AsyncConnection, user_id: UUID) -> Subscription | None:
    result = await connection.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    subscription = Subscription(**row)
    if subscription.plan == "free" and subscription.cycle_started_at is not None:
        now = datetime.now(UTC)
        if now - subscription.cycle_started_at >= timedelta(days=FREE_PLAN_CYCLE_DAYS):
            await connection.execute(
                update(Subscription)
                .where(Subscription.id == subscription.id)
                .values(cycle_started_at=now)
            )
            subscription.cycle_started_at = now
    return subscription


def subscription_is_active(subscription: Subscription | None, now: datetime) -> bool:
    if subscription is None or subscription.status != "ACTIVE":
        return False
    if subscription.expires_at is None or subscription.expires_at <= now:
        return False
    return True


async def transaction_id_already_used(connection: AsyncConnection, transaction_id: str) -> bool:
    result = await connection.execute(
        select(PaymentRequest.id).where(PaymentRequest.transaction_id == transaction_id)
    )
    return result.scalar_one_or_none() is not None


async def activate_subscription_for_payment(connection: AsyncConnection, payment: Mapping) -> None:
    """Grants the plan on an approved PaymentRequest — shared by the admin's
    manual approve button, the KPay webhook, and the /payment/status
    reconciliation poll, so all three paths compute expiry/quota identically
    from the same PLANS registry."""
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
        .where(PaymentRequest.id == payment["id"])
        .values(status="APPROVED", reviewed_at=now)
    )


async def reject_payment_request(connection: AsyncConnection, payment_id: UUID) -> None:
    await connection.execute(
        update(PaymentRequest)
        .where(PaymentRequest.id == payment_id, PaymentRequest.status == "PENDING")
        .values(status="REJECTED", reviewed_at=datetime.now(UTC))
    )
