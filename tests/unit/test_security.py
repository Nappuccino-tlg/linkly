from app.security import (
    create_access_token,
    decode_access_token,
    hash_ip,
    hash_password,
    verify_password,
)


def test_password_round_trip():
    stored = hash_password("supersecret123")
    assert stored != "supersecret123"
    assert verify_password("supersecret123", stored)


def test_wrong_password_is_rejected():
    assert not verify_password("wrongpassword", hash_password("supersecret123"))


def test_same_password_hashes_differently_each_time():
    """bcrypt salts every hash, so two users with one password are not linkable."""
    assert hash_password("supersecret123") != hash_password("supersecret123")


def test_token_round_trip():
    assert decode_access_token(create_access_token("abc-123")) == "abc-123"


def test_tampered_token_is_rejected():
    token = create_access_token("abc-123")
    assert decode_access_token(token[:-2] + "xx") is None


def test_garbage_token_is_rejected():
    assert decode_access_token("not-a-token") is None


def test_ip_hash_is_stable_and_distinguishing():
    assert hash_ip("1.1.1.1") == hash_ip("1.1.1.1")
    assert hash_ip("1.1.1.1") != hash_ip("2.2.2.2")


def test_ip_hash_does_not_contain_the_address():
    assert "1.1.1.1" not in hash_ip("1.1.1.1")


def test_missing_ip_hashes_to_nothing():
    assert hash_ip(None) is None
