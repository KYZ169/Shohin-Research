import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.enums import LotteryEntryResult


class LotteryEntryCreate(BaseModel):
    discord_id: str
    event_id: uuid.UUID
    applied_at: datetime | None = None  # 省略時はサーバー側でnow()を使う


class LotteryEntryUpdate(BaseModel):
    result: LotteryEntryResult


class LotteryEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    event_id: uuid.UUID
    applied_at: datetime
    result: LotteryEntryResult
