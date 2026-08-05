import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.enums import ManualReviewTaskStatus


class ManualReviewTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_product_id: uuid.UUID
    matched_product_id: uuid.UUID
    score: int
    status: ManualReviewTaskStatus
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None


class ManualReviewTaskResolve(BaseModel):
    resolution: Literal["confirm", "reject"]
    resolved_by: str
