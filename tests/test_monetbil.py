from webapp.monetbil import sign, verify_notification


def test_sign_is_order_independent() -> None:
    params_a = {"status": "success", "amount": "10000", "payment_ref": "abc"}
    params_b = {"amount": "10000", "payment_ref": "abc", "status": "success"}
    assert sign("secret", params_a) == sign("secret", params_b)


def test_sign_changes_with_different_secret() -> None:
    params = {"status": "success", "amount": "10000"}
    assert sign("secret-one", params) != sign("secret-two", params)


def test_sign_changes_if_any_value_changes() -> None:
    base = {"status": "success", "amount": "10000"}
    tampered = {"status": "success", "amount": "1"}
    assert sign("secret", base) != sign("secret", tampered)


def test_verify_notification_accepts_correctly_signed_params() -> None:
    params = {"status": "success", "amount": "10000", "payment_ref": "abc"}
    params["sign"] = sign("secret", params)
    assert verify_notification("secret", params) is True


def test_verify_notification_rejects_tampered_amount() -> None:
    params = {"status": "success", "amount": "10000", "payment_ref": "abc"}
    params["sign"] = sign("secret", params)
    params["amount"] = "1"  # attacker lowers the paid amount after signing
    assert verify_notification("secret", params) is False


def test_verify_notification_rejects_missing_sign() -> None:
    params = {"status": "success", "amount": "10000"}
    assert verify_notification("secret", params) is False


def test_verify_notification_rejects_wrong_secret() -> None:
    params = {"status": "success", "amount": "10000"}
    params["sign"] = sign("secret-one", params)
    assert verify_notification("secret-two", params) is False
