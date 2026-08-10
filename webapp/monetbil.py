"""Thin client for Monetbil's Payment API v1 (placePayment / checkPayment)
plus the notification signature scheme, straight from Monetbil's own docs
and PHP SDK:

- placePayment/checkPayment: https://api.monetbil.com/payment/v1/...
- notification signing: sort params by key, concatenate the values (no
  separator), prepend the service secret, MD5 the result. See
  monetbil_sign()/monetbil_check_sign() in Monetbil's "Payment
  Notifications" doc — verify_notification() below is that same algorithm.
"""

import hashlib
import hmac

import httpx

PLACE_PAYMENT_URL = "https://api.monetbil.com/payment/v1/placePayment"
CHECK_PAYMENT_URL = "https://api.monetbil.com/payment/v1/checkPayment"

_TIMEOUT_SECONDS = 30


def sign(service_secret: str, params: dict) -> str:
    ordered_values = [str(params[key]) for key in sorted(params)]
    return hashlib.md5((service_secret + "".join(ordered_values)).encode()).hexdigest()


def verify_notification(service_secret: str, params: dict) -> bool:
    """Verifies a notification's `sign` field against the rest of its
    params. Missing signatures are treated as invalid — Monetbil's doc
    calls signing "optional" (off by default until you turn it on for the
    service), but we require it here since an unsigned notification can't
    be told apart from a forged one."""
    if "sign" not in params:
        return False
    provided = params["sign"]
    rest = {k: v for k, v in params.items() if k != "sign"}
    expected = sign(service_secret, rest)
    return hmac.compare_digest(provided, expected)


async def place_payment(
    *,
    service: str,
    amount: int,
    phonenumber: str,
    operator: str,
    country: str,
    currency: str,
    payment_ref: str,
    item_ref: str,
    user: str,
    first_name: str,
    last_name: str,
    email: str,
    notify_url: str,
) -> dict:
    """Starts a Mobile Money charge. Returns Monetbil's parsed JSON response
    — on success, status == "REQUEST_ACCEPTED" and a paymentId is present
    (see Monetbil Payment API v1 doc, section 1)."""
    payload = {
        "service": service,
        "amount": amount,
        "phonenumber": phonenumber,
        "operator": operator,
        "country": country,
        "currency": currency,
        "payment_ref": payment_ref,
        "item_ref": item_ref,
        "user": user,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "notify_url": notify_url,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(PLACE_PAYMENT_URL, json=payload)
        response.raise_for_status()
        return response.json()


async def check_payment(payment_id: str) -> dict:
    """Polls a payment's current status. Returns the parsed JSON response;
    when payment has resolved, response["transaction"]["status"] is
    1 (success), 0 (failed), -1 (cancelled) or -2 (refunded) — see
    Monetbil Payment API v1 doc, section 2."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(CHECK_PAYMENT_URL, data={"paymentId": payment_id})
        response.raise_for_status()
        return response.json()
