import uuid
from datetime import date, datetime

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


class LinkCreate(BaseModel):
    target_url: str = Field(max_length=MAX_URL_LENGTH)
    # Optional vanity code, e.g. /my-talk
    custom_code: str | None = Field(default=None, min_length=3, max_length=16)
    expires_at: datetime | None = None

    @field_validator("target_url")
    @classmethod
    def must_be_a_safe_target(cls, value: str) -> str:
        """A shortener is an open redirector, so the target is checked before it is stored."""
        try:
            check(value)
        except UnsafeTargetError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("custom_code")
    @classmethod
    def code_must_be_url_safe(cls, value: str | None) -> str | None:
        if value is not None and not all(c.isalnum() or c in "-_" for c in value):
            raise ValueError("custom_code may only contain letters, digits, '-' and '_'")
        return value


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    target_url: str
    short_url: str
    is_active: bool
    expires_at: datetime | None
    created_at: datetime


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
