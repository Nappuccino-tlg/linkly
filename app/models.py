import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Referrers are keys in the rollup table, so they are cut to a length a btree can hold.
REFERRER_KEY_MAX = 512


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    links: Mapped[list["Link"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Link(Base):
    __tablename__ = "links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    target_url: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped[User] = relationship(back_populates="links")
    clicks: Mapped[list["Click"]] = relationship(
        back_populates="link", cascade="all, delete-orphan"
    )


class Click(Base):
    """One row per redirect served. Written outside the request path."""

    __tablename__ = "clicks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    link_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("links.id", ondelete="CASCADE"))
    clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    referrer: Mapped[str | None] = mapped_column(Text, default=None)
    user_agent: Mapped[str | None] = mapped_column(Text, default=None)
    # Salted hash, not the raw address -- enough to count uniques, not to identify people.
    ip_hash: Mapped[str | None] = mapped_column(String(64), default=None)

    link: Mapped[Link] = relationship(back_populates="clicks")


class ClickDaily(Base):
    """One row per link per UTC day, folded from raw clicks by app/rollup.py.

    This is what keeps /stats a constant-size read. A link with four years of traffic has
    ~1460 rows here no matter how many millions of clicks produced them, and the raw rows
    behind the closed days can be deleted once they are folded.
    """

    __tablename__ = "click_daily"

    link_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("links.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    clicks: Mapped[int] = mapped_column(BigInteger)
    # Distinct ip_hash values within this one day. Summing the column across days counts a
    # returning visitor once per day -- see LinkStats for why that is the definition.
    unique_visitors: Mapped[int] = mapped_column(Integer)


class ReferrerDaily(Base):
    """Per-link, per-day, per-referrer counts. Empty string means no referrer."""

    __tablename__ = "referrer_daily"

    link_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("links.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    # Part of the primary key, so it is bounded: a btree entry has a hard size limit and a
    # 2048-character referrer would breach it. Truncation merges two referrers that agree
    # for 512 characters, which is not a distinction any report needs.
    referrer: Mapped[str] = mapped_column(String(REFERRER_KEY_MAX), primary_key=True)
    clicks: Mapped[int] = mapped_column(BigInteger)


Index("ix_clicks_link_id_clicked_at", Click.link_id, Click.clicked_at)
# Prune walks the whole table by age, across every link -- a link-first index cannot serve it.
Index("ix_clicks_clicked_at", Click.clicked_at)
