import pytest
from pydantic import ValidationError

from app.config import DEFAULT_JWT_SECRET, Settings


def test_development_runs_on_defaults():
    assert Settings(_env_file=None).environment == "development"


@pytest.mark.parametrize("secret", [DEFAULT_JWT_SECRET, "too-short"])
def test_production_refuses_a_weak_secret(secret):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", jwt_secret=secret)


def test_production_accepts_a_strong_secret():
    settings = Settings(_env_file=None, environment="production", jwt_secret="k" * 48)
    assert settings.jwt_secret == "k" * 48
