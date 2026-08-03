import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import pg_enum
from app.domain.enums import ProfitScenario


class ProfitSnapshot(Base):
    """技術分析レポート8.1のprofit_snapshots + 実装仕様書1.4の変更を反映。

    技術分析8.1では"event_id or product_id"だが、本実装ではrelease_events基準の
    計算のみ扱うためevent_id必須とする。channel_id(sales_channelsへのFK)は
    sales_channelsテーブルが未実装のため、暫定的にchannel_name(文字列)で保持する。

    TODO(sales_channels実装時に対応): channel_nameをFK(channel_id)に置き換える。
    文字列のまま放置すると表記ゆれ(例: "suruga_ya"と"駿河屋"のような別名)で
    同一チャネルが別チャネル扱いになるリスクがあるため、sales_channels実装と
    同時に必ずFK化すること(2026-08-03 タスク9レビュー時にユーザー指摘)。

    1イベントにつきscenario(pessimistic/standard/optimistic)ごとに最大3レコード。
    再計算のたびに新規追加する(物理削除・直近保持は運用側のクリーンアップジョブで
    別途対応する想定、本モデルではスキーマとして強制しない)。
    """

    __tablename__ = "profit_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("release_events.id"), nullable=False, index=True
    )
    channel_name: Mapped[str | None] = mapped_column(String, nullable=True)
    scenario: Mapped[ProfitScenario] = mapped_column(
        pg_enum(ProfitScenario, "profit_scenario"), nullable=False
    )

    # 実装仕様書1.4
    displayed_profit: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)
    roi: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    excluded_cost_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    has_unconfirmed_cost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
