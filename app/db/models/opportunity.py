import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import pg_enum
from app.domain.enums import MatchStatus


class Opportunity(Base):
    """技術分析レポート8.1のopportunities。

    channel_id(sales_channelsへのFK)はprofit_snapshots.channel_nameと同じ理由
    (sales_channels未実装)でbest_channel_name(文字列)を暫定採用する。
    TODO(sales_channels実装時に対応): best_channel_nameをFK(channel_id)に置き換える。
    profit_snapshots.channel_nameと同じ表記ゆれリスクを抱えているため、
    sales_channels実装時に両方まとめて対応すること。

    statusは技術分析レポート8.1に列挙されているが具体的な値がどのドキュメントにも
    定義されていないため、release_events.statusと同様に素のvarchar(nullable)とする。
    """

    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint("event_id", "best_channel_name", name="uq_opportunities_event_channel"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("release_events.id"), nullable=False, index=True
    )
    best_channel_name: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[str] = mapped_column(String, nullable=False)  # A/B/C/D
    score: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, index=True)
    match_status: Mapped[MatchStatus] = mapped_column(
        pg_enum(MatchStatus, "opportunity_match_status"), nullable=False
    )
    status: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
