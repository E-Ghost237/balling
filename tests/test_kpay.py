import hashlib
import hmac

from webapp.kpay import verify_webhook_signature


def _sign(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def test_verify_webhook_signature_accepts_correct_signature() -> None:
    raw = b'{"status":"COMPLETED","amount":10000,"externalId":"abc"}'
    signature = _sign("secret", raw)
    assert verify_webhook_signature("secret", raw, signature) is True


def test_verify_webhook_signature_rejects_tampered_body() -> None:
    raw = b'{"status":"COMPLETED","amount":10000,"externalId":"abc"}'
    signature = _sign("secret", raw)
    tampered = b'{"status":"COMPLETED","amount":1,"externalId":"abc"}'
    assert verify_webhook_signature("secret", tampered, signature) is False


def test_verify_webhook_signature_rejects_wrong_secret() -> None:
    raw = b'{"status":"COMPLETED","amount":10000,"externalId":"abc"}'
    signature = _sign("secret-one", raw)
    assert verify_webhook_signature("secret-two", raw, signature) is False


def test_verify_webhook_signature_rejects_missing_signature() -> None:
    raw = b'{"status":"COMPLETED"}'
    assert verify_webhook_signature("secret", raw, "") is False


def test_verify_webhook_signature_requires_the_exact_raw_bytes() -> None:
    """A re-serialized (e.g. key-reordered) body must NOT verify — this is
    the whole reason the webhook route hashes request.body() before any
    JSON parsing touches it."""
    import json

    body = {"status": "COMPLETED", "amount": 10000, "externalId": "abc"}
    raw = json.dumps(body).encode()
    signature = _sign("secret", raw)
    reserialized = json.dumps(body, sort_keys=True).encode()
    assert reserialized != raw
    assert verify_webhook_signature("secret", reserialized, signature) is False
