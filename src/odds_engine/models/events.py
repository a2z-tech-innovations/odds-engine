import uuid

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from odds_engine.models.database import Base

_uuid_pk = {"primary_key": True, "default": uuid.uuid4}


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    external_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    sport_key: Mapped[str] = mapped_column(String, nullable=False)
    sport_group: Mapped[str] = mapped_column(String, nullable=False)
    home_team: Mapped[str] = mapped_column(String, nullable=False)
    away_team: Mapped[str] = mapped_column(String, nullable=False)
    commence_time: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="upcoming")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_events_external_id", "external_id", unique=True),
        Index("ix_events_sport_key_commence_time", "sport_key", "commence_time"),
        Index("ix_events_status_commence_time", "status", "commence_time"),
        Index("ix_events_sport_group", "sport_group"),
    )
