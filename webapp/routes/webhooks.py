import hmac
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from webapp import kpay
from webapp.auth import activate_subscription_for_payment, reject_payment_request
from webapp.config import get_settings
from webapp.deps import get_db
from webapp.models import PaymentRequest

router = APIRouter()


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

    # Idempotent: the polling reconciliation in customer.py may have
    # already resolved this payment by the time this arrives, and KPay
    # itself may retry notifications — don't reprocess either way.
    if payment["status"] == "PENDING":
        status = payload.get("status")
        if status == "COMPLETED":
            paid_amount = payload.get("amount")
            try:
                underpaid = paid_amount is not None and float(paid_amount) < payment["amount_fcfa"]
            except (TypeError, ValueError):
                underpaid = False
            # Underpaid relative to the plan we charged for — the amount
            # was set by us (not the user) when we called place_payment, so
            # a lower confirmed amount means something is wrong upstream.
            # Leave it PENDING for manual admin review rather than
            # silently granting the plan.
            if not underpaid:
                await activate_subscription_for_payment(connection, payment)
        elif status in ("FAILED", "CANCELLED"):
            await reject_payment_request(connection, payment_id)
        # Any other status (e.g. KPay also sends an undocumented
        # "payment.initiated" event with status PENDING before the final
        # one) is an intermediate update, not a verdict — leave it PENDING
        # and wait for a terminal COMPLETED/FAILED/CANCELLED notification.

    return PlainTextResponse("received")
