import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import pg_enum
from app.domain.enums import LotteryEntryResult


class LotteryEntry(Base):
    """技術分析レポート8.1のlottery_entries。"""

    __tablename__ = "lottery_entries"
    __table_args__ = (UniqueConstraint("user_id", "event_id", name="uq_lottery_entries_user_event"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("release_events.id"), nullable=False
    )
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result: Mapped[LotteryEntryResult] = mapped_column(
        pg_enum(LotteryEntryResult, "lottery_entry_result"),
        nullable=False,
        default=LotteryEntryResult.PENDING,
        server_default=LotteryEntryResult.PENDING.value,
    )

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
