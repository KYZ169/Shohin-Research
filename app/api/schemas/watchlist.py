import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class WatchlistCreate(BaseModel):
    discord_id: str
    product_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def check_exactly_one_target(self) -> "WatchlistCreate":
        if (self.product_id is None) == (self.event_id is None):
            raise ValueError("product_idとevent_idはどちらか一方だけを指定してください")
        return self


class WatchlistRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    product_id: uuid.UUID | None
    event_id: uuid.UUID | None
    created_at: datetime
