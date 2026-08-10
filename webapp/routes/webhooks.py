import hmac
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp import kpay, monetbil
from webapp.auth import activate_subscription_for_payment, reject_payment_request
from webapp.config import get_settings
from webapp.deps import get_db
from webapp.models import PaymentRequest

router = APIRouter()


@router.api_route("/webhooks/monetbil/{secret}", methods=["GET", "POST"])
async def monetbil_notify(
    secret: str, request: Request, connection: AsyncConnection = Depends(get_db)
) -> PlainTextResponse:
    settings = get_settings()
    # First layer: an unguessable URL, per Monetbil's own notification doc
    # (section 1.1) — before even looking at the signature.
    if not settings.monetbil_webhook_path or not hmac.compare_digest(
        secret, settings.monetbil_webhook_path
    ):
        raise HTTPException(status_code=404)

    if request.method == "GET":
        params = dict(request.query_params)
    else:
        params = dict(await request.form())

    # Second, real layer: the signed request itself (see monetbil.py).
    if not monetbil.verify_notification(settings.monetbil_service_secret, params):
        raise HTTPException(status_code=403)

    try:
        payment_id = UUID(params.get("payment_ref", ""))
    except ValueError:
        raise HTTPException(status_code=400) from None

    result = await connection.execute(
        select(PaymentRequest).where(PaymentRequest.id == payment_id)
    )
    payment = result.mappings().one_or_none()
    if payment is None:
        raise HTTPException(status_code=404)

    # Idempotent: Monetbil may retry notifications, and the polling
    # reconciliation in customer.py may have already resolved this payment
    # by the time this arrives — don't reprocess either way.
    if payment["status"] == "PENDING":
        if params.get("status") == "success":
            paid_amount = params.get("amount")
            try:
                underpaid = paid_amount is not None and float(paid_amount) < payment["amount_fcfa"]
            except ValueError:
                underpaid = False
            # Underpaid relative to the plan we charged for — this is
            # exactly the "paid for a year, got marked for a month" gap:
            # the amount was set by us (not the user) when we called
            # place_payment, so a mismatch here means something is wrong
            # upstream. Leave it PENDING for manual admin review rather
            # than silently granting the plan.
            if not underpaid:
                await activate_subscription_for_payment(connection, payment)
        else:
            await reject_payment_request(connection, payment_id)

    return PlainTextResponse("received")


@router.post("/webhooks/kpay/{secret}")
async def kpay_notify(
    secret: str, request: Request, connection: AsyncConnection = Depends(get_db)
) -> PlainTextResponse:
    settings = get_settings()
    if not settings.kpay_webhook_path or not hmac.compare_digest(
        secret, settings.kpay_webhook_path
    ):
        raise HTTPException(status_code=404)

    # Must verify against the exact raw bytes KPay sent — read the body
    # before anything parses it, per KPay's webhook security doc.
    raw_body = await request.body()
    signature = request.headers.get("X-KPAY-Signature", "")
    if not kpay.verify_webhook_signature(settings.kpay_webhook_secret, raw_body, signature):
        raise HTTPException(status_code=403)

    try:
        payload = json.loads(raw_body)
    except ValueError:
        raise HTTPException(status_code=400) from None

    try:
        payment_id = UUID(str(payload.get("externalId", "")))
    except ValueError:
        raise HTTPException(status_code=400) from None

    result = await connection.execute(
        select(PaymentRequest).where(PaymentRequest.id == payment_id)
    )
    payment = result.mappings().one_or_none()
    if payment is None:
        raise HTTPException(status_code=404)

    # Idempotent — see monetbil_notify above for the same reasoning.
    if payment["status"] == "PENDING":
        status = payload.get("status")
        if status == "COMPLETED":
            paid_amount = payload.get("amount")
            try:
                underpaid = paid_amount is not None and float(paid_amount) < payment["amount_fcfa"]
            except (TypeError, ValueError):
                underpaid = False
            # Same guard as Monetbil's: the amount was set by us when we
            # called place_payment, so a lower confirmed amount means
            # something upstream is wrong — don't auto-activate.
            if not underpaid:
                await activate_subscription_for_payment(connection, payment)
        elif status in ("FAILED", "CANCELLED"):
            await reject_payment_request(connection, payment_id)
        # Any other status (e.g. KPay also sends an undocumented
        # "payment.initiated" event with status PENDING before the final
        # one) is an intermediate update, not a verdict — leave it PENDING
        # and wait for a terminal COMPLETED/FAILED/CANCELLED notification.

    return PlainTextResponse("received")
