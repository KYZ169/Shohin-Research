import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import pg_enum
from app.domain.enums import PendingNotificationStatus


class PendingNotification(Base):
    """evaluate_notification()がshould_send=Trueと判定したOpportunityの送信キュー
    (2026-08-06追加、CLAUDE.md「収集→通知の自動化」緊急対応)。

    【背景】収集タスク(Celery、同期実行)とDiscord送信(discord.py、非同期ゲートウェイ
    接続)は意図的に分離されている(app/scheduler/tasks.pyモジュールdocstring参照)。
    従来はこの間を橋渡しする永続的なキューが存在せず、should_send=Trueの結果は
    Celeryタスクの戻り値(dict)に含まれるだけで、これを自動的に拾って送信する仕組みが
    存在しなかった(2026-08-06発覚)。本テーブルはそのギャップを埋める。

    書き込み: `_match_and_score_observation()`(app/scheduler/tasks.py)がshould_send=True
    と判定した都度1行作成する(embedは`evaluate_notification()`が組み立てたdict、
    dedupe_keyは監査用、`app/notification/dedupe.py:build_dedupe_key()`が生成したもの)。
    読み出し・送信: `app/notification/dispatcher.py:dispatch_pending_notifications()`が
    常時起動Bot(app/bot/main.py)から定期的(既定15分毎)に呼ばれ、status=pendingの行を
    古い順に送信する。

    dedupe自体(重複「送信すべきか」の判定)はRedis側(check_and_update_dedupe_state()、
    行の作成前)で完結しており、このテーブルは「送信すべきと決まったものを、実際に
    送信し終えるまで確実に配達する」という別の責務(配達の重複防止)を持つ。
    """

    __tablename__ = "pending_notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id"), nullable=False, index=True
    )
    release_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("release_events.id"), nullable=False, index=True
    )
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    embed: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # app/notification/interaction_view.py:OpportunityActionViewへそのまま渡す
    # (Noneなら「照合を確定する」/「別商品として分離」ボタンを追加しない)。
    manual_review_task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String, nullable=True)  # 監査用、送信判定には使わない
    status: Mapped[PendingNotificationStatus] = mapped_column(
        pg_enum(PendingNotificationStatus, "pending_notification_status"),
        nullable=False,
        default=PendingNotificationStatus.PENDING,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discord_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
