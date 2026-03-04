import uuid

from sqlalchemy import DateTime, Float, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from odds_engine.models.database import Base

_uuid_pk = {"primary_key": True, "default": uuid.uuid4}


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fetched_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    credits_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_odds_snapshots_event_id_fetched_at", "event_id", "fetched_at"),
        Index("ix_odds_snapshots_fetched_at", "fetched_at"),
    )


class BookmakerOdds(Base):
    __tablename__ = "bookmaker_odds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    bookmaker_key: Mapped[str] = mapped_column(String, nullable=False)
    market_key: Mapped[str] = mapped_column(String, nullable=False)
    outcome_name: Mapped[str] = mapped_column(String, nullable=False)
    outcome_price: Mapped[float] = mapped_column(Float, nullable=False)
    outcome_point: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_update: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_bookmaker_odds_snapshot_id", "snapshot_id"),
        Index("ix_bookmaker_odds_bookmaker_market", "bookmaker_key", "market_key"),
        Index(
            "ix_bookmaker_odds_snapshot_market_bookmaker",
            "snapshot_id",
            "market_key",
            "bookmaker_key",
        ),
    )


class EnrichedSnapshot(Base):
    __tablename__ = "enriched_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    best_line: Mapped[dict] = mapped_column(JSONB, nullable=False)
    consensus_line: Mapped[dict] = mapped_column(JSONB, nullable=False)
    vig_free: Mapped[dict] = mapped_column(JSONB, nullable=False)
    movement: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_enriched_snapshots_event_id_computed_at", "event_id", "computed_at"),
        Index("ix_enriched_snapshots_snapshot_id", "snapshot_id", unique=True),
    )


class ApiUsage(Base):
    __tablename__ = "api_usage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    credits_used: Mapped[int] = mapped_column(Integer, nullable=False)
    credits_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    sport_key: Mapped[str | None] = mapped_column(String, nullable=True)
    endpoint: Mapped[str] = mapped_column(String, nullable=False)
    recorded_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_api_usage_recorded_at", "recorded_at"),)
