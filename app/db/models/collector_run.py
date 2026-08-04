import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CollectorRun(Base):
    """Celery Beatで定期実行されるCollectorタスク(app/scheduler/tasks.py)の実行履歴。

    「Collector稼働状況の確認用CLIコマンド」(運用整備タスク)のためだけに存在する、
    ドメインモデルではなく運用ログ用のテーブル。Celeryの結果バックエンド(Redis)は
    task_id単位でしか引けずTTLで消えるため、「タスク名ごとの最終実行結果」を
    確認するにはDBへの永続化が必要と判断した。

    statusはopportunities.confidence等と同様、固定enumではなく素のvarchar
    ("success" | "failure")とする。
    """

    __tablename__ = "collector_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # success | failure
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
