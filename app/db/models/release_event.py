import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import pg_enum
from app.domain.enums import DeadlineSource, FulfillmentType, RegionSource, SupportedEventType


class ReleaseEvent(Base):
    """技術分析レポート8.1のrelease_events + 実装仕様書1.3の追加カラム。

    deadline_at はPoC-1実測により取得できないサイトが実在するためnullable運用とする
    (実装仕様書1.3参照)。start_at/announce_at/purchase_limit_at/priceも同様の理由で
    nullableとし、取得できた事実のみを保持する設計方針(CLAUDE.md最重要方針2)に合わせる。
    """

    __tablename__ = "release_events"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "shop_id", "event_type", "start_at", name="uq_release_events_product_shop_type_start"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )

    event_type: Mapped[SupportedEventType] = mapped_column(
        pg_enum(SupportedEventType, "release_event_type"), nullable=False
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    announce_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purchase_limit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 0), nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # 実装仕様書1.3 追加カラム
    fulfillment_type: Mapped[FulfillmentType] = mapped_column(
        pg_enum(FulfillmentType, "fulfillment_type"),
        nullable=False,
        default=FulfillmentType.ONLINE_SHIPPING,
        server_default=FulfillmentType.ONLINE_SHIPPING.value,
    )
    region_override: Mapped[str | None] = mapped_column(String, nullable=True)
    region_display_text: Mapped[str | None] = mapped_column(String, nullable=True)
    region_source: Mapped[RegionSource] = mapped_column(
        pg_enum(RegionSource, "region_source"),
        nullable=False,
        default=RegionSource.UNKNOWN,
        server_default=RegionSource.UNKNOWN.value,
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deadline_source: Mapped[DeadlineSource] = mapped_column(
        pg_enum(DeadlineSource, "deadline_source"),
        nullable=False,
        default=DeadlineSource.UNKNOWN,
        server_default=DeadlineSource.UNKNOWN.value,
    )
    store_release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    online_release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
