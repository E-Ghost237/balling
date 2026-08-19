import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    login: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    first_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Subscription(Base):
    """One row per user, updated in place on each approved payment. Payment
    history/audit trail lives in PaymentRequest, not here."""

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="INACTIVE")
    plan: Mapped[str | None] = mapped_column(String)
    quota_limit: Mapped[int | None] = mapped_column(Integer)
    cycle_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    """One row per user — a new login overwrites the prior row, which is
    what enforces "single active session per subscription": the old
    token_hash simply stops matching anything once overwritten."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, unique=True
    )
    token_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentRequest(Base):
    __tablename__ = "payment_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # KPay's Payment id once place_payment() accepts the request (see
    # webapp/kpay.py) — this row's own `id` is generated up front and sent
    # as KPay's externalId, which the webhook then uses to look it back up.
    transaction_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    phone_number: Mapped[str] = mapped_column(String, nullable=False)
    operator: Mapped[str | None] = mapped_column(String)
    amount_fcfa: Mapped[int] = mapped_column(Integer, nullable=False)
    plan: Mapped[str] = mapped_column(String, nullable=False, default="monthly")
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificationCode(Base):
    """A one-time 6-digit code emailed to the user, for either confirming a
    new account's email address or authorizing a password reset. Codes are
    stored hashed (never plaintext), expire after a short window, and are
    single-use (`used_at` set on successful verification)."""

    __tablename__ = "verification_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Prediction(Base):
    """A saved /simulate result, read back by webapp/routes/history.py.
    Deliberately in Postgres, not data/football.db — that SQLite file also
    holds rebuildable scraped reference data (teams/matches/ratings), and a
    wholesale rebuild/replace of it used to silently orphan every user's
    prediction history (UsageLog.prediction_id pointed at rows that no
    longer existed, with nothing erroring — see git history for the
    "Details unavailable" bug this fixed). Team names are stored directly
    rather than as SQLite team_id FKs for the same reason: resilient to
    that file being rebuilt out from under us. modeling/simulate.py's own
    SQLite-backed predictions table still exists separately, for the
    standalone Streamlit tool (app.py) only."""

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    home_team: Mapped[str] = mapped_column(String, nullable=False)
    away_team: Mapped[str] = mapped_column(String, nullable=False)
    lambda_home: Mapped[float] = mapped_column(Float, nullable=False)
    lambda_away: Mapped[float] = mapped_column(Float, nullable=False)
    n_simulations: Mapped[int] = mapped_column(Integer, nullable=False)
    prob_home_win: Mapped[float] = mapped_column(Float, nullable=False)
    prob_draw: Mapped[float] = mapped_column(Float, nullable=False)
    prob_away_win: Mapped[float] = mapped_column(Float, nullable=False)
    most_likely_score: Mapped[str | None] = mapped_column(String)
    scoreline_probs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    markets: Mapped[dict | None] = mapped_column(JSONB)
    best_picks: Mapped[list | None] = mapped_column(JSONB)


class UsageLog(Base):
    """Append-only: one row per Simulate click, whether or not the
    matchup was a repeat. Quota counts DISTINCT (home, away, neutral)
    within the current cycle window at query time — see quota.py — rather
    than a write-time constraint, since the same matchup must be usable
    again once the subscription renews into a new cycle."""

    __tablename__ = "usage_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    home_team: Mapped[str] = mapped_column(String, nullable=False)
    away_team: Mapped[str] = mapped_column(String, nullable=False)
    neutral: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    simulated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prediction_id: Mapped[int | None] = mapped_column(Integer)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
