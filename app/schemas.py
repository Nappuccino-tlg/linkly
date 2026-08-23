import uuid
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.urlguard import MAX_URL_LENGTH, UnsafeTargetError, check


class UserCreate(BaseModel):
    email: EmailStr
    # bcrypt only considers the first 72 bytes, so reject longer input rather than
    # silently truncating it.
    password: str = Field(min_length=8, max_length=72)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _validate_target(value: str) -> str:
    """A shortener is an open redirector, so the target is checked before it is stored."""
    try:
        check(value)
    except UnsafeTargetError as exc:
        raise ValueError(str(exc)) from exc
    return value


def _as_utc(value: datetime | None) -> datetime | None:
    """Read a naive timestamp as UTC rather than rejecting it.

    Every timestamp this API hands out is UTC, so that is the only reading of a naive one
    that will not surprise the sender. Leaving it naive is the option with a real cost:
    it reaches a timestamptz column and a comparison against an aware `now()`.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _validate_code(value: str | None) -> str | None:
    if value is not None and not all(c.isalnum() or c in "-_" for c in value):
        raise ValueError("custom_code may only contain letters, digits, '-' and '_'")
    return value


class LinkCreate(BaseModel):
    target_url: str = Field(
        max_length=MAX_URL_LENGTH,
        examples=["https://www.digikala.com/product/dkp-11827364/"],
    )
    # Optional vanity code, e.g. /my-talk
    custom_code: str | None = Field(default=None, min_length=3, max_length=16, examples=["my-talk"])
    expires_at: datetime | None = None

    _check_target = field_validator("target_url")(_validate_target)
    _check_code = field_validator("custom_code")(_validate_code)

    @field_validator("expires_at")
    @classmethod
    def expiry_must_be_ahead(cls, value: datetime | None) -> datetime | None:
        """A link that is born expired is a typo, never an intention.

        PATCH deliberately does allow a past timestamp -- there it means "retire this
        now" -- but at creation the only thing it can produce is a code that answers 410
        to its very first visitor.
        """
        moment = _as_utc(value)
        if moment is not None and moment <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return moment


class LinkUpdate(BaseModel):
    """Every field optional. Only the ones actually sent are applied."""

    target_url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)
    is_active: bool | None = None
    expires_at: datetime | None = None

    @field_validator("target_url")
    @classmethod
    def target_must_be_safe(cls, value: str | None) -> str | None:
        return None if value is None else _validate_target(value)

    # A timestamp in the past is allowed here: it is how a link is retired on the spot
    # while keeping the code reserved.
    _normalise_expiry = field_validator("expires_at")(_as_utc)


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    target_url: str
    short_url: str
    is_active: bool
    expires_at: datetime | None
    created_at: datetime


class LinkPage(BaseModel):
    """A list plus the numbers a client needs to page through it."""

    items: list[LinkOut]
    total: int
    limit: int
    offset: int


class DailyClicks(BaseModel):
    day: date
    count: int


class ReferrerCount(BaseModel):
    referrer: str
    count: int


class LinkStats(BaseModel):
    code: str
    total_clicks: int
    unique_visitors: int
    daily: list[DailyClicks]
    top_referrers: list[ReferrerCount]
