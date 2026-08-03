"""実装仕様書14.2「ウォッチリスト登録」ボタン、技術分析レポート15章
POST /watchlists・DELETE /watchlists/{id}。
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, verify_api_key
from app.api.schemas.watchlist import WatchlistCreate, WatchlistRead
from app.api.user_service import get_or_create_user
from app.core.time import JST
from app.db.models import Watchlist

router = APIRouter(prefix="/watchlists", tags=["watchlists"], dependencies=[Depends(verify_api_key)])


@router.post("", response_model=WatchlistRead, status_code=status.HTTP_201_CREATED)
def create_watchlist(payload: WatchlistCreate, session: Session = Depends(get_db)) -> Watchlist:
    """ウォッチ登録。同じ組み合わせで既に登録済みの場合は既存のものをそのまま返す
    (技術分析15章のIdempotency-Keyヘッダ対応の趣旨に沿い、ボタンの二重押下を
    エラーにしない)。
    """
    user = get_or_create_user(session, payload.discord_id)

    existing = (
        session.query(Watchlist)
        .filter_by(user_id=user.id, product_id=payload.product_id, event_id=payload.event_id)
        .one_or_none()
    )
    if existing is not None:
        return existing

    watchlist = Watchlist(
        user_id=user.id,
        product_id=payload.product_id,
        event_id=payload.event_id,
        created_at=datetime.now(tz=JST),
    )
    session.add(watchlist)
    session.commit()
    session.refresh(watchlist)
    return watchlist


@router.delete("/{watchlist_id}", status_code=status.HTTP_200_OK)
def delete_watchlist(watchlist_id: uuid.UUID, session: Session = Depends(get_db)) -> dict:
    """ウォッチ解除。技術分析15章: 冪等(存在しなくても200)。"""
    watchlist = session.get(Watchlist, watchlist_id)
    if watchlist is not None:
        session.delete(watchlist)
        session.commit()
    return {"status": "ok"}
