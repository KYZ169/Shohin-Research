import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Source(Base):
    """技術分析レポート8.1: 仕入れ情報源設定。release_events.source_idの参照先。

    実装仕様書Prompt2の対象テーブル一覧には明示されていないが、
    release_eventsのsource_id FKが参照整合性を持つために必要なため、
    ユーザー確認の上で最小限のカラムのみ本タスクで作成する。
    """

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    collector_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
