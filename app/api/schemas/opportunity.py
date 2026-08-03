import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.domain.enums import MatchStatus


class OpportunityStatusUpdate(BaseModel):
    status: str  # 例: "hidden" (実装仕様書14.2の「非表示」ボタン用)


class OpportunityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    event_id: uuid.UUID
    best_channel_name: str | None
    confidence: str
    score: Decimal
    match_status: MatchStatus
    status: str | None
