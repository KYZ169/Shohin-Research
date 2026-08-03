"""実装仕様書14.2「応募済みにする」ボタン、技術分析レポート15章
POST /lottery-entries・PATCH /lottery-entries/{id}。
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, verify_api_key
from app.api.schemas.lottery_entry import LotteryEntryCreate, LotteryEntryRead, LotteryEntryUpdate
from app.api.user_service import get_or_create_user
from app.core.time import JST
from app.db.models import LotteryEntry

router = APIRouter(prefix="/lottery-entries", tags=["lottery-entries"], dependencies=[Depends(verify_api_key)])


@router.post("", response_model=LotteryEntryRead, status_code=status.HTTP_201_CREATED)
def create_lottery_entry(payload: LotteryEntryCreate, session: Session = Depends(get_db)) -> LotteryEntry:
    """応募済みにする。既に応募記録がある場合はapplied_atを更新するだけに留め、
    UNIQUE制約(user_id, event_id)違反による二重押下エラーを防ぐ。
    """
    user = get_or_create_user(session, payload.discord_id)
    applied_at = payload.applied_at or datetime.now(tz=JST)

    existing = session.query(LotteryEntry).filter_by(user_id=user.id, event_id=payload.event_id).one_or_none()
    if existing is not None:
        existing.applied_at = applied_at
        session.commit()
        session.refresh(existing)
        return existing

    entry = LotteryEntry(user_id=user.id, event_id=payload.event_id, applied_at=applied_at)
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


@router.patch("/{entry_id}", response_model=LotteryEntryRead)
def update_lottery_entry_result(
    entry_id: uuid.UUID, payload: LotteryEntryUpdate, session: Session = Depends(get_db)
) -> LotteryEntry:
    entry = session.get(LotteryEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lottery entry not found")

    entry.result = payload.result
    session.commit()
    session.refresh(entry)
    return entry
