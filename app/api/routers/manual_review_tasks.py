"""ManualReviewTask(要確認キュー)の一覧・解決API(2026-08-05追加)。

将来Discordボタン("照合を確定する"/"この照合は誤り")を追加する際は、この
エンドポイントを叩く設計にする想定(app/notification/interaction_view.pyの
既存3ボタンと同じ、DiscordボタンはFastAPI経由でDBを操作する既存アーキテクチャに
合わせるため)。マージ処理の実体はapp/pipeline/manual_review.pyにあり、
このルーターはそれを呼ぶだけの薄い層。

【権限チェックについて】既存3ボタン(応募済み/ウォッチ/非表示)と同様、
このエンドポイントもverify_api_key(単一ユーザー前提のAPIキー認証)以外の
権限チェックを持たない。個人利用の現段階では実害は小さいが、複数ユーザー対応
(Phase5)までに必ず対応が必要(CLAUDE.md 3節に記録済み)。
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, verify_api_key
from app.api.schemas.manual_review_task import ManualReviewTaskRead, ManualReviewTaskResolve
from app.db.models import ManualReviewTask
from app.domain.enums import ManualReviewTaskStatus
from app.pipeline.manual_review import ManualReviewTaskNotPendingError, resolve_manual_review_task

router = APIRouter(prefix="/manual-review-tasks", tags=["manual_review_tasks"], dependencies=[Depends(verify_api_key)])


@router.get("", response_model=list[ManualReviewTaskRead])
def list_manual_review_tasks(
    status_filter: ManualReviewTaskStatus = ManualReviewTaskStatus.PENDING,
    session: Session = Depends(get_db),
) -> list[ManualReviewTask]:
    return (
        session.query(ManualReviewTask)
        .filter(ManualReviewTask.status == status_filter)
        .order_by(ManualReviewTask.created_at)
        .all()
    )


@router.post("/{task_id}/resolve", response_model=ManualReviewTaskRead)
def resolve_task(
    task_id: uuid.UUID, payload: ManualReviewTaskResolve, session: Session = Depends(get_db)
) -> ManualReviewTask:
    task = session.get(ManualReviewTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="manual review task not found")

    try:
        resolve_manual_review_task(session, task, resolution=payload.resolution, resolved_by=payload.resolved_by)
    except ManualReviewTaskNotPendingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    session.commit()
    session.refresh(task)
    return task
