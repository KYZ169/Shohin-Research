import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Watchlist(Base):
    """技術分析レポート8.1のwatchlists。product_id/event_idのどちらか一方を指す
    (「product_id or event_id」という設計)。どちらも指定/どちらも未指定は
    アプリケーション層(APIのpydanticバリデーション)で防ぐ想定だが、
    最低限の保護としてCHECK制約も入れておく。
    """

    __tablename__ = "watchlists"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_watchlists_user_product"),
        UniqueConstraint("user_id", "event_id", name="uq_watchlists_user_event"),
        CheckConstraint(
            "(product_id IS NOT NULL AND event_id IS NULL) OR (product_id IS NULL AND event_id IS NOT NULL)",
            name="ck_watchlists_exactly_one_target",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=True
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("release_events.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
