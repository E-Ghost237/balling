"""Thin client for KPay's Payment API (USSD deposit mode) plus webhook
signature verification, per KPay's own integration doc:

- Base URL: https://admin.kpay.site, auth via X-API-Key/X-Secret-Key headers.
- POST /api/v1/payments/init starts a push-to-phone Mobile Money charge.
- GET /api/v1/payments/{id} polls status (fallback if the webhook is late).
- Webhook notifications are signed via HMAC-SHA256 over the RAW request
  body (X-KPAY-Signature header) — distinct from the GATEWAY return-URL
  signature scheme, which this integration doesn't use (we're on USSD
  mode, not GATEWAY, so there's no browser redirect to verify).
"""

import hashlib
import hmac

import httpx

BASE_URL = "https://admin.kpay.site"
INIT_PAYMENT_URL = f"{BASE_URL}/api/v1/payments/init"

_TIMEOUT_SECONDS = 30


def _auth_headers(api_key: str, secret_key: str) -> dict:
    return {"X-API-Key": api_key, "X-Secret-Key": secret_key}


async def place_payment(
    *,
    api_key: str,
    secret_key: str,
    amount: int,
    provider: str,
    phone_number: str,
    external_id: str,
    description: str | None = None,
    customer_name: str | None = None,
    customer_email: str | None = None,
) -> dict:
    """Starts a USSD push-to-phone Mobile Money charge. Returns KPay's
    parsed JSON body either way: on success (201) the Payment object,
    keyed by "id"; on a rejected request (4xx) their error envelope
    `{statusCode, message, error}` instead — deliberately NOT raised here,
    so the caller can surface the real `message` (e.g. "invalid
    phonenumber") instead of a generic failure. Network-level failures
    (no response at all) still raise httpx.HTTPError normally."""
    payload = {
        "amount": amount,
        "provider": provider,
        "phoneNumber": phone_number,
        "externalId": external_id,
    }
    if description:
        payload["description"] = description
    if customer_name:
        payload["customerName"] = customer_name
    if customer_email:
        payload["customerEmail"] = customer_email

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(
            INIT_PAYMENT_URL, headers=_auth_headers(api_key, secret_key), json=payload
        )
        return response.json()


async def check_payment(*, api_key: str, secret_key: str, payment_id: str) -> dict:
    """Polls a payment's current status. Returns the parsed Payment object
    — status is one of PENDING/PROCESSING/COMPLETED/FAILED/CANCELLED."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"{BASE_URL}/api/v1/payments/{payment_id}",
            headers=_auth_headers(api_key, secret_key),
        )
        response.raise_for_status()
        return response.json()


def verify_webhook_signature(secret_key: str, raw_body: bytes, signature: str) -> bool:
    """Recomputes the HMAC-SHA256 (hex) over the exact raw bytes KPay sent
    and compares it against the X-KPAY-Signature header, in constant time.
    Must be called with the untouched request body — re-serializing the
    parsed JSON before hashing would produce a different signature."""
    if not signature:
        return False
    expected = hmac.new(secret_key.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)
