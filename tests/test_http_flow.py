from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from httpx import ASGITransport
from sqlalchemy import select, update

from webapp.db import get_engine
from webapp.main import app
from webapp.models import PaymentRequest, Subscription, User
from webapp.otp import PURPOSE_EMAIL_VERIFY, PURPOSE_PASSWORD_RESET, create_verification_code
from webapp.plans import PLANS
from webapp.security import hash_password


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _create_user(login: str, password: str = "hunter22", **overrides: object) -> object:
    user_id = uuid4()
    values = {
        "id": user_id,
        "login": login,
        "password_hash": hash_password(password),
        "is_admin": False,
        "email_verified": True,
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    async with get_engine().begin() as connection:
        await connection.execute(User.__table__.insert().values(**values))
    return user_id


async def test_register_requires_email_verification_before_reaching_simulate() -> None:
    async with await _client() as client:
        response = await client.post(
            "/register",
            data={
                "first_name": "New",
                "last_name": "User",
                "login": "newuser@example.com",
                "password": "hunter22",
                "confirm_password": "hunter22",
            },
            follow_redirects=True,
        )
        assert "Check your inbox" in response.text

    async with get_engine().begin() as connection:
        result = await connection.execute(
            select(User.id).where(User.login == "newuser@example.com")
        )
        user_id = result.scalar_one()
        code = await create_verification_code(connection, user_id, PURPOSE_EMAIL_VERIFY)

    async with await _client() as client:
        response = await client.post(
            "/verify-email",
            data={"login": "newuser@example.com", "code": code},
            follow_redirects=True,
        )
    assert response.status_code == 200
    assert "Pick a matchup" in response.text

    async with get_engine().begin() as connection:
        result = await connection.execute(
            select(Subscription.plan, Subscription.quota_limit, Subscription.status)
            .where(Subscription.user_id == user_id)
        )
        row = result.one()
    assert row.plan == "free"
    assert row.quota_limit == 20
    assert row.status == "ACTIVE"


async def test_unverified_user_cannot_log_in_directly() -> None:
    await _create_user("unverified@example.com", email_verified=False)
    async with await _client() as client:
        response = await client.post(
            "/login",
            data={"login": "unverified@example.com", "password": "hunter22"},
            follow_redirects=True,
        )
    assert "Check your inbox" in response.text


async def test_simulate_blocked_without_active_subscription() -> None:
    await _create_user("nosub@example.com")
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "nosub@example.com", "password": "hunter22"}
        )
        response = await client.get("/simulate", follow_redirects=True)
    assert "Subscribe to start predicting" in response.text


async def test_simulate_runs_a_real_matchup_for_active_subscriber() -> None:
    user_id = await _create_user("subscriber@example.com")
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        await connection.execute(
            Subscription.__table__.insert().values(
                id=uuid4(), user_id=user_id, status="ACTIVE", plan="monthly", quota_limit=150,
                cycle_started_at=now, expires_at=now + timedelta(days=30),
            )
        )

    async with await _client() as client:
        await client.post(
            "/login", data={"login": "subscriber@example.com", "password": "hunter22"}
        )
        response = await client.post(
            "/simulate",
            data={"league_id": 1, "home": "Arsenal", "away": "Chelsea"},
            follow_redirects=True,
        )
    assert response.status_code == 200
    assert "Best picks" in response.text
    assert "Arsenal" in response.text and "Chelsea" in response.text


async def test_non_admin_cannot_reach_admin_pages() -> None:
    await _create_user("regular@example.com")
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "regular@example.com", "password": "hunter22"}
        )
        response = await client.get("/admin", follow_redirects=True)
    assert "Not available" in response.text


async def test_admin_can_reach_admin_dashboard() -> None:
    user_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=user_id, login="boss", password_hash=hash_password("hunter22"),
                is_admin=True, created_at=datetime.now(UTC),
            )
        )
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        response = await client.get("/admin", follow_redirects=True)
    assert response.status_code == 200
    assert "Pending payments" in response.text


async def _seed_admin() -> None:
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=uuid4(), login="boss", password_hash=hash_password("hunter22"),
                is_admin=True, created_at=datetime.now(UTC),
            )
        )


async def test_admin_users_page_renders_without_error() -> None:
    await _seed_admin()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=uuid4(), login="plainuser", password_hash=hash_password("hunter22"),
                is_admin=False, created_at=datetime.now(UTC),
            )
        )
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        response = await client.get("/admin/users")
    assert response.status_code == 200
    assert "plainuser" in response.text


async def test_admin_payments_page_renders_and_shows_pending_payment() -> None:
    from webapp.models import PaymentRequest

    user_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=user_id, login="payer", password_hash=hash_password("hunter22"),
                is_admin=False, created_at=datetime.now(UTC),
            )
        )
        await connection.execute(
            PaymentRequest.__table__.insert().values(
                id=uuid4(), user_id=user_id, transaction_id="TXN-TEST-1",
                phone_number="670000000", amount_fcfa=5000, plan="monthly",
                status="PENDING", submitted_at=datetime.now(UTC),
            )
        )
    await _seed_admin()
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        response = await client.get("/admin/payments")
    assert response.status_code == 200
    assert "TXN-TEST-1" in response.text
    assert "payer" in response.text


async def test_admin_can_approve_a_pending_payment() -> None:
    from webapp.models import PaymentRequest

    user_id = uuid4()
    payment_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=user_id, login="payer2", password_hash=hash_password("hunter22"),
                is_admin=False, created_at=datetime.now(UTC),
            )
        )
        await connection.execute(
            PaymentRequest.__table__.insert().values(
                id=payment_id, user_id=user_id, transaction_id="TXN-TEST-2",
                phone_number="670000000", amount_fcfa=5000, plan="monthly",
                status="PENDING", submitted_at=datetime.now(UTC),
            )
        )
    await _seed_admin()
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        response = await client.post(
            f"/admin/payments/{payment_id}/approve", follow_redirects=True
        )
    assert response.status_code == 200
    assert "TXN-TEST-2" not in response.text  # no longer pending

    async with get_engine().begin() as connection:
        result = await connection.execute(
            select(Subscription.status).where(Subscription.user_id == user_id)
        )
        assert result.scalar_one() == "ACTIVE"


async def test_password_reset_full_round_trip() -> None:
    await _create_user("resetme@example.com", password="oldpassword")

    async with await _client() as client:
        await client.post("/forgot-password", data={"login": "resetme@example.com"})

    async with get_engine().begin() as connection:
        result = await connection.execute(
            select(User.id).where(User.login == "resetme@example.com")
        )
        user_id = result.scalar_one()
        code = await create_verification_code(connection, user_id, PURPOSE_PASSWORD_RESET)

    async with await _client() as client:
        reset_response = await client.post(
            "/reset-password",
            data={"login": "resetme@example.com", "code": code, "password": "newpassword"},
            follow_redirects=True,
        )
        assert reset_response.status_code == 200

        old_login = await client.post(
            "/login",
            data={"login": "resetme@example.com", "password": "oldpassword"},
            follow_redirects=True,
        )
        assert "Welcome back" in old_login.text  # rejected, still on login page

        new_login = await client.post(
            "/login",
            data={"login": "resetme@example.com", "password": "newpassword"},
            follow_redirects=True,
        )
    assert "Subscribe to start predicting" in new_login.text


async def test_forgot_password_does_not_reveal_whether_email_exists() -> None:
    async with await _client() as client:
        response = await client.post(
            "/forgot-password", data={"login": "doesnotexist@example.com"}, follow_redirects=True
        )
    assert response.status_code == 200
    assert "reset-password" in str(response.url)


async def test_register_rejects_mismatched_confirm_password() -> None:
    async with await _client() as client:
        response = await client.post(
            "/register",
            data={
                "first_name": "New",
                "last_name": "User",
                "login": "mismatch@example.com",
                "password": "hunter22",
                "confirm_password": "somethingelse",
            },
            follow_redirects=True,
        )
    assert "Passwords do not match" in response.text

    async with get_engine().begin() as connection:
        result = await connection.execute(
            select(User.id).where(User.login == "mismatch@example.com")
        )
        assert result.first() is None


async def test_approving_annual_payment_grants_that_plans_quota_and_expiry() -> None:
    from webapp.models import PaymentRequest
    from webapp.plans import PLANS

    user_id = uuid4()
    payment_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=user_id, login="annual-payer", password_hash=hash_password("hunter22"),
                is_admin=False, created_at=datetime.now(UTC),
            )
        )
        await connection.execute(
            PaymentRequest.__table__.insert().values(
                id=payment_id, user_id=user_id, transaction_id="TXN-ANNUAL",
                phone_number="670000000", amount_fcfa=84000, plan="annual",
                status="PENDING", submitted_at=datetime.now(UTC),
            )
        )
    await _seed_admin()
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        response = await client.post(
            f"/admin/payments/{payment_id}/approve", follow_redirects=True
        )
    assert response.status_code == 200

    plan = PLANS["annual"]
    async with get_engine().begin() as connection:
        result = await connection.execute(
            select(Subscription.plan, Subscription.quota_limit, Subscription.expires_at)
            .where(Subscription.user_id == user_id)
        )
        row = result.one()
    assert row.plan == "annual"
    assert row.quota_limit == plan.quota
    assert (row.expires_at - datetime.now(UTC)).days >= plan.duration_days - 1


async def test_user_cannot_view_another_users_history_detail() -> None:
    owner_id = await _create_user("owner@example.com")
    await _create_user("intruder@example.com")
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        await connection.execute(
            Subscription.__table__.insert().values(
                id=uuid4(), user_id=owner_id, status="ACTIVE", plan="monthly", quota_limit=150,
                cycle_started_at=now, expires_at=now + timedelta(days=30),
            )
        )

    async with await _client() as owner_client:
        await owner_client.post(
            "/login", data={"login": "owner@example.com", "password": "hunter22"}
        )
        await owner_client.post(
            "/simulate",
            data={"league_id": 1, "home": "Arsenal", "away": "Chelsea"},
        )

    from webapp.models import UsageLog

    async with get_engine().begin() as connection:
        result = await connection.execute(
            select(UsageLog.id).where(UsageLog.user_id == owner_id)
        )
        usage_log_id = result.scalar_one()

    async with await _client() as intruder_client:
        await intruder_client.post(
            "/login", data={"login": "intruder@example.com", "password": "hunter22"}
        )
        response = await intruder_client.get(f"/history/{usage_log_id}")
    assert response.status_code == 404


async def test_owner_can_view_their_own_history_detail() -> None:
    owner_id = await _create_user("historyowner@example.com")
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        await connection.execute(
            Subscription.__table__.insert().values(
                id=uuid4(), user_id=owner_id, status="ACTIVE", plan="monthly", quota_limit=150,
                cycle_started_at=now, expires_at=now + timedelta(days=30),
            )
        )

    async with await _client() as client:
        await client.post(
            "/login", data={"login": "historyowner@example.com", "password": "hunter22"}
        )
        sim_response = await client.post(
            "/simulate",
            data={"league_id": 1, "home": "Arsenal", "away": "Chelsea"},
        )
        assert "Most likely score" in sim_response.text

        from webapp.models import UsageLog

        async with get_engine().begin() as connection:
            result = await connection.execute(
                select(UsageLog.id).where(UsageLog.user_id == owner_id)
            )
            usage_log_id = result.scalar_one()

        list_response = await client.get("/history")
        assert "Arsenal" in list_response.text and "Chelsea" in list_response.text

        detail_response = await client.get(f"/history/{usage_log_id}")
    assert detail_response.status_code == 200
    assert "Best picks" in detail_response.text
    assert "Arsenal" in detail_response.text and "Chelsea" in detail_response.text


async def test_payment_page_only_lists_paid_plans() -> None:
    await _create_user("browsing@example.com")
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "browsing@example.com", "password": "hunter22"}
        )
        response = await client.get("/payment")
    assert response.status_code == 200
    assert "Monthly" in response.text
    assert "6 Months" in response.text
    assert "Yearly" in response.text
    assert 'value="free"' not in response.text


async def test_payment_rejects_the_free_plan_key() -> None:
    await _create_user("triesfree@example.com")
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "triesfree@example.com", "password": "hunter22"}
        )
        response = await client.post(
            "/payment",
            data={
                "plan": "free",
                "operator": "CM_MTNMOBILEMONEY",
                "phone_number": "670000000",
            },
            follow_redirects=True,
        )
    assert "Choose a plan" in response.text


async def test_payment_rejects_unknown_operator() -> None:
    await _create_user("badoperator@example.com")
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "badoperator@example.com", "password": "hunter22"}
        )
        response = await client.post(
            "/payment",
            data={
                "plan": "monthly",
                "operator": "US_VENMO",
                "phone_number": "670000000",
            },
            follow_redirects=True,
        )
    assert "Choose your Mobile Money operator" in response.text


async def test_payment_submit_starts_a_monetbil_charge_for_the_exact_plan_price(
    monkeypatch,
) -> None:
    """The amount charged comes from the PLANS registry (server-side), not
    anything the user submits — this is what prevents someone picking the
    yearly plan while only actually being charged the monthly price."""
    from webapp import monetbil

    captured = {}

    async def fake_place_payment(**kwargs):
        captured.update(kwargs)
        return {"status": "REQUEST_ACCEPTED", "paymentId": "MB-PID-1"}

    monkeypatch.setattr(monetbil, "place_payment", fake_place_payment)

    await _create_user("payer-annual@example.com")
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "payer-annual@example.com", "password": "hunter22"}
        )
        response = await client.post(
            "/payment",
            data={"plan": "annual", "operator": "CM_MTNMOBILEMONEY", "phone_number": "670000001"},
        )
    assert response.status_code == 303
    assert "/payment/pending/" in response.headers["location"]
    assert captured["amount"] == PLANS["annual"].price_fcfa

    async with get_engine().begin() as connection:
        result = await connection.execute(
            select(PaymentRequest.transaction_id, PaymentRequest.amount_fcfa, PaymentRequest.status)
            .where(PaymentRequest.transaction_id == "MB-PID-1")
        )
        row = result.one()
    assert row.amount_fcfa == PLANS["annual"].price_fcfa
    assert row.status == "PENDING"


async def test_payment_submit_surfaces_monetbil_rejection_without_creating_a_payment_row(
    monkeypatch,
) -> None:
    from webapp import monetbil

    async def fake_place_payment(**kwargs):
        return {"status": "INVALID_MSISDN", "message": "invalid phonenumber"}

    monkeypatch.setattr(monetbil, "place_payment", fake_place_payment)

    await _create_user("badphone@example.com")
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "badphone@example.com", "password": "hunter22"}
        )
        response = await client.post(
            "/payment",
            data={"plan": "monthly", "operator": "CM_MTNMOBILEMONEY", "phone_number": "1"},
            follow_redirects=True,
        )
    assert "invalid phonenumber" in response.text

    async with get_engine().begin() as connection:
        result = await connection.execute(select(PaymentRequest.id))
        assert result.first() is None


async def test_monetbil_webhook_activates_subscription_on_success() -> None:
    from webapp.monetbil import sign

    user_id = await _create_user("webhook-user@example.com")
    payment_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            PaymentRequest.__table__.insert().values(
                id=payment_id, user_id=user_id, transaction_id="MB-PID-WEBHOOK",
                phone_number="670000000", operator="CM_MTNMOBILEMONEY",
                amount_fcfa=PLANS["monthly"].price_fcfa, plan="monthly",
                status="PENDING", submitted_at=datetime.now(UTC),
            )
        )

    params = {
        "status": "success",
        "amount": str(PLANS["monthly"].price_fcfa),
        "payment_ref": str(payment_id),
        "transaction_id": "op-txn-1",
    }
    params["sign"] = sign("test-service-secret", params)

    async with await _client() as client:
        response = await client.post(
            "/webhooks/monetbil/test-webhook-secret", data=params
        )
    assert response.status_code == 200
    assert response.text == "received"

    async with get_engine().begin() as connection:
        payment_row = (
            await connection.execute(
                select(PaymentRequest.status).where(PaymentRequest.id == payment_id)
            )
        ).scalar_one()
        sub_row = (
            await connection.execute(
                select(Subscription.status, Subscription.plan, Subscription.quota_limit)
                .where(Subscription.user_id == user_id)
            )
        ).one()
    assert payment_row == "APPROVED"
    assert sub_row.status == "ACTIVE"
    assert sub_row.plan == "monthly"
    assert sub_row.quota_limit == PLANS["monthly"].quota


async def test_monetbil_webhook_rejects_wrong_secret_path() -> None:
    async with await _client() as client:
        response = await client.post("/webhooks/monetbil/not-the-real-secret", data={})
    assert response.status_code == 404


async def test_monetbil_webhook_rejects_bad_signature() -> None:
    user_id = await _create_user("webhook-badsign@example.com")
    payment_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            PaymentRequest.__table__.insert().values(
                id=payment_id, user_id=user_id, transaction_id="MB-PID-BADSIGN",
                phone_number="670000000", amount_fcfa=PLANS["monthly"].price_fcfa,
                plan="monthly", status="PENDING", submitted_at=datetime.now(UTC),
            )
        )
    params = {
        "status": "success", "amount": str(PLANS["monthly"].price_fcfa),
        "payment_ref": str(payment_id), "sign": "not-a-real-signature",
    }
    async with await _client() as client:
        response = await client.post("/webhooks/monetbil/test-webhook-secret", data=params)
    assert response.status_code == 403

    async with get_engine().begin() as connection:
        status = (
            await connection.execute(
                select(PaymentRequest.status).where(PaymentRequest.id == payment_id)
            )
        ).scalar_one()
    assert status == "PENDING"


async def test_monetbil_webhook_does_not_activate_an_underpaid_notification() -> None:
    """If the paid amount comes back lower than what we charged for the
    plan, don't grant the plan — leave it PENDING for manual review."""
    from webapp.monetbil import sign

    user_id = await _create_user("underpaid@example.com")
    payment_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            PaymentRequest.__table__.insert().values(
                id=payment_id, user_id=user_id, transaction_id="MB-PID-UNDERPAID",
                phone_number="670000000", amount_fcfa=PLANS["annual"].price_fcfa,
                plan="annual", status="PENDING", submitted_at=datetime.now(UTC),
            )
        )
    params = {
        "status": "success",
        "amount": str(PLANS["monthly"].price_fcfa),  # much less than the annual price charged
        "payment_ref": str(payment_id),
    }
    params["sign"] = sign("test-service-secret", params)
    async with await _client() as client:
        response = await client.post("/webhooks/monetbil/test-webhook-secret", data=params)
    assert response.status_code == 200

    async with get_engine().begin() as connection:
        payment_status_, sub_row = (
            await connection.execute(
                select(PaymentRequest.status, Subscription.status)
                .select_from(PaymentRequest)
                .join(Subscription, Subscription.user_id == PaymentRequest.user_id, isouter=True)
                .where(PaymentRequest.id == payment_id)
            )
        ).one()
    assert payment_status_ == "PENDING"
    assert sub_row is None


async def test_payment_status_polling_reconciles_and_redirects_when_monetbil_confirms(
    monkeypatch,
) -> None:
    from webapp import monetbil

    async def fake_check_payment(payment_id: str):
        return {"transaction": {"status": 1, "amount": PLANS["monthly"].price_fcfa}}

    monkeypatch.setattr(monetbil, "check_payment", fake_check_payment)

    user_id = await _create_user("polling-user@example.com")
    payment_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            PaymentRequest.__table__.insert().values(
                id=payment_id, user_id=user_id, transaction_id="MB-PID-POLL",
                phone_number="670000000", operator="CM_MTNMOBILEMONEY",
                amount_fcfa=PLANS["monthly"].price_fcfa, plan="monthly",
                status="PENDING", submitted_at=datetime.now(UTC),
            )
        )

    async with await _client() as client:
        await client.post(
            "/login", data={"login": "polling-user@example.com", "password": "hunter22"}
        )
        response = await client.get(f"/payment/status/{payment_id}")
    assert response.status_code == 200
    assert "Payment confirmed" in response.text
    assert response.headers.get("hx-redirect") is not None


async def test_payment_pending_page_is_not_visible_to_another_user() -> None:
    owner_id = await _create_user("pending-owner@example.com")
    await _create_user("pending-intruder@example.com")
    payment_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            PaymentRequest.__table__.insert().values(
                id=payment_id, user_id=owner_id, transaction_id="MB-PID-OWNERSHIP",
                phone_number="670000000", amount_fcfa=PLANS["monthly"].price_fcfa,
                plan="monthly", status="PENDING", submitted_at=datetime.now(UTC),
            )
        )
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "pending-intruder@example.com", "password": "hunter22"}
        )
        response = await client.get(f"/payment/pending/{payment_id}")
    assert response.status_code == 404


async def test_teams_for_league_returns_only_teams_in_that_league() -> None:
    await _create_user("league-tester@example.com")
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "league-tester@example.com", "password": "hunter22"}
        )
        response = await client.get("/simulate/teams-for-league", params={"league_id": 1})
    assert response.status_code == 200
    assert 'id="home-select"' in response.text
    assert 'id="away-select"' in response.text
    assert "Arsenal" in response.text
    assert "hx-swap-oob" in response.text


async def test_friendly_match_offers_teams_from_every_league() -> None:
    """league_id=0 is the "friendly match" sentinel — it should return
    teams pulled from across every league, not scoped to a single one,
    since the simulation engine itself has no notion of league."""
    await _create_user("friendly-tester@example.com")
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "friendly-tester@example.com", "password": "hunter22"}
        )
        response = await client.get("/simulate/teams-for-league", params={"league_id": 0})
    assert response.status_code == 200
    assert "Bournemouth" in response.text  # Premier League
    assert "Malaga" in response.text  # La Liga


async def test_simulate_page_lists_friendly_match_option() -> None:
    user_id = await _create_user("friendly-list@example.com")
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        await connection.execute(
            Subscription.__table__.insert().values(
                id=uuid4(), user_id=user_id, status="ACTIVE", plan="monthly", quota_limit=150,
                cycle_started_at=now, expires_at=now + timedelta(days=30),
            )
        )
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "friendly-list@example.com", "password": "hunter22"}
        )
        response = await client.get("/simulate")
    assert response.status_code == 200
    assert "Friendly match" in response.text
    assert 'value="0"' in response.text


async def test_feedback_submission_and_admin_can_view_it() -> None:
    await _create_user("feedback-user@example.com")
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "feedback-user@example.com", "password": "hunter22"}
        )
        response = await client.post(
            "/feedback",
            data={"message": "Loving the scoreline heatmap!", "rating": "5"},
            follow_redirects=True,
        )
    assert response.status_code == 200
    assert "Thanks" in response.text

    await _seed_admin()
    async with await _client() as admin_client:
        await admin_client.post("/login", data={"login": "boss", "password": "hunter22"})
        admin_response = await admin_client.get("/admin/feedback")
    assert admin_response.status_code == 200
    assert "Loving the scoreline heatmap!" in admin_response.text
    assert "feedback-user@example.com" in admin_response.text
    assert "5/5" in admin_response.text


async def test_feedback_rejects_empty_message() -> None:
    await _create_user("empty-feedback@example.com")
    async with await _client() as client:
        await client.post(
            "/login", data={"login": "empty-feedback@example.com", "password": "hunter22"}
        )
        response = await client.post(
            "/feedback", data={"message": "   "}, follow_redirects=True
        )
    assert "Tell us something first" in response.text


async def test_finance_dashboard_sums_only_approved_payments() -> None:
    from webapp.models import PaymentRequest

    payer_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=payer_id, login="finance-payer", password_hash=hash_password("hunter22"),
                is_admin=False, created_at=datetime.now(UTC),
            )
        )
        await connection.execute(
            PaymentRequest.__table__.insert().values(
                id=uuid4(), user_id=payer_id, transaction_id="TXN-FIN-APPROVED",
                phone_number="670000000", amount_fcfa=10000, plan="monthly",
                status="APPROVED", submitted_at=datetime.now(UTC), reviewed_at=datetime.now(UTC),
            )
        )
        await connection.execute(
            PaymentRequest.__table__.insert().values(
                id=uuid4(), user_id=payer_id, transaction_id="TXN-FIN-PENDING",
                phone_number="670000000", amount_fcfa=84000, plan="annual",
                status="PENDING", submitted_at=datetime.now(UTC),
            )
        )
    await _seed_admin()
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        response = await client.get("/admin/finance")
    assert response.status_code == 200
    assert "10,000" in response.text
    assert "84,000" not in response.text  # pending payment must not count as revenue


async def test_deactivated_user_cannot_log_in() -> None:
    await _create_user("deactivated-login@example.com")
    async with get_engine().begin() as connection:
        result = await connection.execute(
            select(User.id).where(User.login == "deactivated-login@example.com")
        )
        user_id = result.scalar_one()
        await connection.execute(
            update(User).where(User.id == user_id).values(is_active=False)
        )
    async with await _client() as client:
        response = await client.post(
            "/login",
            data={"login": "deactivated-login@example.com", "password": "hunter22"},
            follow_redirects=True,
        )
    assert "Invalid login or password" in response.text


async def test_admin_deactivating_user_kills_their_active_session() -> None:
    from webapp.models import Session

    user_id = await _create_user("kicked@example.com")
    async with await _client() as user_client:
        await user_client.post(
            "/login", data={"login": "kicked@example.com", "password": "hunter22"}
        )

        await _seed_admin()
        async with await _client() as admin_client:
            await admin_client.post("/login", data={"login": "boss", "password": "hunter22"})
            await admin_client.post(f"/admin/users/{user_id}/deactivate")

        blocked_response = await user_client.get("/account", follow_redirects=True)
    assert "Welcome back" in blocked_response.text  # bounced to the login page

    async with get_engine().begin() as connection:
        remaining_sessions = (
            await connection.execute(select(Session.id).where(Session.user_id == user_id))
        ).all()
    assert remaining_sessions == []


async def test_admin_can_reactivate_a_deactivated_user() -> None:
    user_id = await _create_user("comeback@example.com")
    async with get_engine().begin() as connection:
        await connection.execute(update(User).where(User.id == user_id).values(is_active=False))
    await _seed_admin()
    async with await _client() as admin_client:
        await admin_client.post("/login", data={"login": "boss", "password": "hunter22"})
        await admin_client.post(f"/admin/users/{user_id}/reactivate")

    async with await _client() as user_client:
        response = await user_client.post(
            "/login",
            data={"login": "comeback@example.com", "password": "hunter22"},
            follow_redirects=True,
        )
    assert "Invalid login or password" not in response.text


async def test_admin_cannot_deactivate_or_delete_another_admin_or_self() -> None:
    await _seed_admin()
    other_admin_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=other_admin_id, login="other-admin", password_hash=hash_password("hunter22"),
                is_admin=True, created_at=datetime.now(UTC),
            )
        )
        boss_id = (
            await connection.execute(select(User.id).where(User.login == "boss"))
        ).scalar_one()

    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        other_admin_response = await client.post(
            f"/admin/users/{other_admin_id}/deactivate", follow_redirects=True
        )
        self_response = await client.post(f"/admin/users/{boss_id}/delete", follow_redirects=True)
    assert "Cannot deactivate that account" in other_admin_response.text
    assert "Cannot delete that account" in self_response.text

    async with get_engine().begin() as connection:
        still_admin = (
            await connection.execute(select(User.is_active).where(User.id == other_admin_id))
        ).scalar_one()
        boss_still_exists = (
            await connection.execute(select(User.id).where(User.id == boss_id))
        ).scalar_one_or_none()
    assert still_admin is True
    assert boss_still_exists is not None


async def test_admin_can_delete_a_user_and_cascades_their_data() -> None:
    from webapp.models import PaymentRequest

    user_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=user_id, login="deleteme@example.com", password_hash=hash_password("hunter22"),
                is_admin=False, created_at=datetime.now(UTC),
            )
        )
        await connection.execute(
            PaymentRequest.__table__.insert().values(
                id=uuid4(), user_id=user_id, transaction_id="TXN-DELETE-ME",
                phone_number="670000000", amount_fcfa=10000, plan="monthly",
                status="PENDING", submitted_at=datetime.now(UTC),
            )
        )
    await _seed_admin()
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        response = await client.post(f"/admin/users/{user_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert "deleteme@example.com" not in response.text

    async with get_engine().begin() as connection:
        assert (
            await connection.execute(select(User.id).where(User.id == user_id))
        ).first() is None
        assert (
            await connection.execute(
                select(PaymentRequest.id).where(PaymentRequest.user_id == user_id)
            )
        ).first() is None


async def test_users_list_shows_plan_and_quota_used() -> None:
    user_id = await _create_user("quota-display@example.com")
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        await connection.execute(
            Subscription.__table__.insert().values(
                id=uuid4(), user_id=user_id, status="ACTIVE", plan="monthly", quota_limit=150,
                cycle_started_at=now, expires_at=now + timedelta(days=30),
            )
        )
    await _seed_admin()
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        response = await client.get("/admin/users")
    assert response.status_code == 200
    assert "Monthly" in response.text
    assert "0 / 150" in response.text


async def test_users_list_quota_used_is_scoped_per_user_not_shared() -> None:
    """Regression test: a broken correlated subquery once computed the same
    (wrong) quota_used for every row instead of scoping it to each user's
    own usage_log rows — this needs at least two users with different
    usage counts to catch, which the single-user test above cannot."""
    from webapp.models import UsageLog

    now = datetime.now(UTC)
    heavy_user_id = await _create_user("heavy-user@example.com")
    light_user_id = await _create_user("light-user@example.com")
    async with get_engine().begin() as connection:
        for user_id, quota_limit in ((heavy_user_id, 150), (light_user_id, 150)):
            await connection.execute(
                Subscription.__table__.insert().values(
                    id=uuid4(), user_id=user_id, status="ACTIVE", plan="monthly",
                    quota_limit=quota_limit, cycle_started_at=now,
                    expires_at=now + timedelta(days=30),
                )
            )
        for i in range(3):
            await connection.execute(
                UsageLog.__table__.insert().values(
                    id=uuid4(), user_id=heavy_user_id, home_team=f"H{i}", away_team=f"A{i}",
                    neutral=False, simulated_at=now,
                )
            )
        await connection.execute(
            UsageLog.__table__.insert().values(
                id=uuid4(), user_id=light_user_id, home_team="X", away_team="Y",
                neutral=False, simulated_at=now,
            )
        )
    await _seed_admin()
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        response = await client.get("/admin/users")
    assert response.status_code == 200
    assert "3 / 150" in response.text
    assert "1 / 150" in response.text


async def test_register_normalizes_whitespace_and_casing_in_email() -> None:
    async with await _client() as client:
        await client.post(
            "/register",
            data={
                "first_name": "New", "last_name": "User",
                "login": "  MixedCase@Example.com  ",
                "password": "hunter22", "confirm_password": "hunter22",
            },
            follow_redirects=True,
        )
    async with get_engine().begin() as connection:
        result = await connection.execute(
            select(User.login).where(User.login == "mixedcase@example.com")
        )
        stored_login = result.scalar_one_or_none()
    assert stored_login == "mixedcase@example.com"


async def test_login_is_case_and_whitespace_insensitive() -> None:
    await _create_user("caseinsensitive@example.com")
    async with await _client() as client:
        response = await client.post(
            "/login",
            data={"login": "  CaseInsensitive@Example.com  ", "password": "hunter22"},
            follow_redirects=True,
        )
    assert "Subscribe to start predicting" in response.text  # reached the app, not bounced


async def test_failed_verification_email_surfaces_error_instead_of_silence(monkeypatch) -> None:
    import webapp.routes.customer as customer_module

    monkeypatch.setattr(customer_module, "send_email", lambda *a, **k: False)
    async with await _client() as client:
        response = await client.post(
            "/register",
            data={
                "first_name": "New", "last_name": "User",
                "login": "emailfails@example.com",
                "password": "hunter22", "confirm_password": "hunter22",
            },
            follow_redirects=True,
        )
    assert "Could not send the code email" in response.text


async def test_resend_reports_failure_when_smtp_fails(monkeypatch) -> None:
    import webapp.routes.customer as customer_module

    # No prior verification code exists for this user, so the 60s resend
    # cooldown (otp.seconds_until_resend_allowed) is not in play here —
    # isolates the SMTP-failure path from the cooldown path.
    await _create_user("resendfails@example.com", email_verified=False)
    monkeypatch.setattr(customer_module, "send_email", lambda *a, **k: False)
    async with await _client() as client:
        response = await client.post(
            "/verify-email/resend",
            data={"login": "resendfails@example.com"},
            follow_redirects=True,
        )
    assert "Could not send the code email" in response.text


async def test_users_list_shows_name_below_email() -> None:
    user_id = uuid4()
    async with get_engine().begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=user_id, login="named-user@example.com", password_hash=hash_password("hunter22"),
                first_name="Ada", last_name="Lovelace", is_admin=False, email_verified=True,
                created_at=datetime.now(UTC),
            )
        )
    await _seed_admin()
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        response = await client.get("/admin/users")
    assert response.status_code == 200
    assert "Ada Lovelace" in response.text
    assert "named-user@example.com" in response.text


async def test_admin_can_reset_a_users_quota() -> None:
    from webapp.models import UsageLog

    user_id = await _create_user("quota-reset@example.com")
    old_cycle_start = datetime.now(UTC) - timedelta(days=10)
    async with get_engine().begin() as connection:
        await connection.execute(
            Subscription.__table__.insert().values(
                id=uuid4(), user_id=user_id, status="ACTIVE", plan="monthly", quota_limit=150,
                cycle_started_at=old_cycle_start, expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await connection.execute(
            UsageLog.__table__.insert().values(
                id=uuid4(), user_id=user_id, home_team="A", away_team="B", neutral=False,
                simulated_at=old_cycle_start + timedelta(hours=1),
            )
        )
    await _seed_admin()
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        before = await client.get("/admin/users")
        assert "1 / 150" in before.text

        response = await client.post(f"/admin/users/{user_id}/reset-quota", follow_redirects=True)
    assert response.status_code == 200
    assert "0 / 150" in response.text

    async with get_engine().begin() as connection:
        new_cycle_start = (
            await connection.execute(
                select(Subscription.cycle_started_at).where(Subscription.user_id == user_id)
            )
        ).scalar_one()
    assert new_cycle_start > old_cycle_start


async def test_users_list_shows_never_for_free_plan_instead_of_the_100_year_date() -> None:
    """The free plan's expires_at is deliberately set ~100 years out (it
    renews itself — see auth.get_subscription) so it never actually lapses.
    Showing that raw date in the admin table reads as a data bug, not as
    "never expires" — this locks in the friendlier label."""
    user_id = await _create_user("free-forever@example.com")
    now = datetime.now(UTC)
    async with get_engine().begin() as connection:
        await connection.execute(
            Subscription.__table__.insert().values(
                id=uuid4(), user_id=user_id, status="ACTIVE", plan="free", quota_limit=20,
                cycle_started_at=now, expires_at=now + timedelta(days=365 * 100),
            )
        )
    await _seed_admin()
    async with await _client() as client:
        await client.post("/login", data={"login": "boss", "password": "hunter22"})
        response = await client.get("/admin/users")
    assert response.status_code == 200
    assert "Never" in response.text
    assert "2126" not in response.text


async def test_french_login_page_renders_in_french() -> None:
    async with await _client() as client:
        response = await client.get("/fr/login")
    assert response.status_code == 200
    assert "Content de vous revoir" in response.text
    assert "Connexion" in response.text
    assert 'lang="fr"' in response.text


async def test_french_register_page_renders_in_french() -> None:
    async with await _client() as client:
        response = await client.get("/fr/register")
    assert response.status_code == 200
    assert "Créez votre compte" in response.text


async def test_english_login_page_unaffected_by_french_route() -> None:
    async with await _client() as client:
        response = await client.get("/login")
    assert response.status_code == 200
    assert "Welcome back" in response.text
    assert 'lang="en"' in response.text


async def test_failed_french_login_redirects_within_french_section() -> None:
    async with await _client() as client:
        response = await client.post(
            "/fr/login",
            data={"login": "nobody@example.com", "password": "wrong"},
            follow_redirects=True,
        )
    assert response.status_code == 200
    assert str(response.url).startswith("http://test/fr/login")
    # still on the French page, not bounced to English
    assert "Content de vous revoir" in response.text


async def test_hreflang_alternate_links_present() -> None:
    async with await _client() as client:
        response = await client.get("/login")
    assert 'hreflang="en"' in response.text
    assert 'hreflang="fr"' in response.text
    assert 'href="http://test/fr/login"' in response.text
