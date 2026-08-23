"""Validation that happens before anything reaches the database."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas import LinkCreate, LinkUpdate


def test_a_future_expiry_is_accepted():
    moment = datetime.now(UTC) + timedelta(days=1)
    assert LinkCreate(target_url="https://example.com", expires_at=moment).expires_at == moment


def test_creating_a_link_that_has_already_expired_is_refused():
    past = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ValidationError, match="must be in the future"):
        LinkCreate(target_url="https://example.com", expires_at=past)


def test_no_expiry_means_no_expiry():
    assert LinkCreate(target_url="https://example.com").expires_at is None


def test_a_naive_timestamp_is_read_as_utc():
    naive = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)
    parsed = LinkCreate(target_url="https://example.com", expires_at=naive).expires_at
    assert parsed.tzinfo is UTC
    assert parsed == naive.replace(tzinfo=UTC)


def test_a_naive_past_timestamp_is_still_caught():
    """The comparison happens after normalisation, so a naive value cannot slip past it."""
    naive_past = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None)
    with pytest.raises(ValidationError, match="must be in the future"):
        LinkCreate(target_url="https://example.com", expires_at=naive_past)


def test_patching_an_expiry_into_the_past_is_allowed():
    """That is how a link is retired on the spot while keeping its code reserved."""
    past = datetime.now(UTC) - timedelta(days=1)
    assert LinkUpdate(expires_at=past).expires_at == past


def test_patch_also_normalises_a_naive_timestamp():
    naive = datetime(2030, 1, 1, 12, 0)
    assert LinkUpdate(expires_at=naive).expires_at == datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


def test_an_unsafe_target_is_refused_on_create():
    with pytest.raises(ValidationError):
        LinkCreate(target_url="http://127.0.0.1/admin")


def test_an_unsafe_target_is_refused_on_patch():
    with pytest.raises(ValidationError):
        LinkUpdate(target_url="http://169.254.169.254/latest/meta-data/")


def test_a_custom_code_may_not_carry_path_characters():
    with pytest.raises(ValidationError, match="custom_code"):
        LinkCreate(target_url="https://example.com", custom_code="a/b")
