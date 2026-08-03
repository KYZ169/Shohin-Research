"""実装仕様書14.2「非表示」ボタン。

技術分析レポート15章のAPI一覧には非表示専用エンドポイントの記載が無いため、
既存のopportunities.statusカラムに"hidden"を設定することで実現する
(専用テーブル/カラムを新設しない設計判断)。
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, verify_api_key
from app.api.schemas.opportunity import OpportunityRead, OpportunityStatusUpdate
from app.db.models import Opportunity

router = APIRouter(prefix="/opportunities", tags=["opportunities"], dependencies=[Depends(verify_api_key)])


@router.patch("/{opportunity_id}", response_model=OpportunityRead)
def update_opportunity_status(
    opportunity_id: uuid.UUID, payload: OpportunityStatusUpdate, session: Session = Depends(get_db)
) -> Opportunity:
    opportunity = session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="opportunity not found")

    opportunity.status = payload.status
    session.commit()
    session.refresh(opportunity)
    return opportunity
