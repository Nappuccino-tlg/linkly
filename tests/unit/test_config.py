import pytest
from pydantic import ValidationError

from app.config import DEFAULT_IP_HASH_SALT, DEFAULT_JWT_SECRET, Settings

STRONG = {"jwt_secret": "k" * 48, "ip_hash_salt": "s" * 32}


def test_development_runs_on_defaults():
    assert Settings(_env_file=None).environment == "development"


@pytest.mark.parametrize("secret", [DEFAULT_JWT_SECRET, "too-short"])
def test_production_refuses_a_weak_secret(secret):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", **{**STRONG, "jwt_secret": secret})


@pytest.mark.parametrize("salt", [DEFAULT_IP_HASH_SALT, "short"])
def test_production_refuses_a_shipped_or_tiny_ip_salt(salt):
    """A known salt over 2**32 addresses is a lookup table, not anonymisation."""
    with pytest.raises(ValidationError, match="IP_HASH_SALT"):
        Settings(_env_file=None, environment="production", **{**STRONG, "ip_hash_salt": salt})


def test_development_tolerates_the_default_salt():
    """Only production has to be told; a local run is not pretending to anonymise anyone."""
    assert Settings(_env_file=None).ip_hash_salt == DEFAULT_IP_HASH_SALT


def test_production_accepts_strong_values():
    settings = Settings(_env_file=None, environment="production", **STRONG)
    assert settings.jwt_secret == "k" * 48
    assert settings.ip_hash_salt == "s" * 32
