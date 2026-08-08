import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
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
    transaction_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    phone_number: Mapped[str] = mapped_column(String, nullable=False)
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
