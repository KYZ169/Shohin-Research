import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import pg_enum
from app.domain.enums import ManualReviewTaskStatus


class ManualReviewTask(Base):
    """MatchStatus.NEEDS_REVIEW(要確認)になった商品照合を人間が確定/棄却するための
    キュー(2026-08-05追加)。app/pipeline/ingest.py:match_or_create_product()が
    NEEDS_REVIEWで新規Productを作成する際に、実際に候補として比較した既存Productが
    あれば1行作成する。

    candidate_product_id: NEEDS_REVIEW時にmatch_or_create_product()が作成した
      新規Product(要確認の対象そのもの)。
    matched_product_id: 照合スコア計算で最良候補だった既存Product。
      「確定(confirmed)」時はcandidate_product側の各種レコードをこちらへ
      付け替えるマージ処理を行う(app/pipeline/manual_review.py参照)。
    """

    __tablename__ = "manual_review_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True
    )
    matched_product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ManualReviewTaskStatus] = mapped_column(
        pg_enum(ManualReviewTaskStatus, "manual_review_task_status"),
        nullable=False,
        default=ManualReviewTaskStatus.PENDING,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
