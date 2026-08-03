import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Region(Base):
    """実装仕様書1.1: 都道府県単位を基本とし、area_groupは地方グループ
    (東海・関東等)のショートカットとして扱う。"""

    __tablename__ = "regions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prefecture: Mapped[str] = mapped_column(String, nullable=False)
    area_group: Mapped[str] = mapped_column(String, nullable=False, index=True)
