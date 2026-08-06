"""pending_notificationsテーブル(app/db/models/pending_notification.py)のstatus=pending
行を実際にDiscordへ送信する(2026-08-06追加、CLAUDE.md「収集→通知の自動化」緊急対応)。

【設計方針】
- session.commit()は呼び出し側の責務にはせず、この関数自身が行単位でcommitする。
  理由: 275件規模の巡回のように複数行をまとめて処理する際、後続の行で例外が起きても
  「Discordへは実際に送信済みだがDBには未送信のまま残る」行を作らないため
  (バッチ末尾で一括commitする設計だと、途中で例外が起きた場合に全体がロールバックされ、
  既に実際に送信済みのメッセージが次回ポーリングで再送されてしまう)。
  app/pipeline/ingest.py等がsession.flush()のみでcommitを呼び出し側に委ねている設計とは
  意図的に異なる(こちらはDiscordという外部への副作用を伴う操作のため、コミット境界を
  1メッセージ単位に細かくする必要がある)。
- 実際のDiscord送信(discord.Client)を直接構築せず、呼び出し側からsend_fn(非同期callable)を
  注入してもらう設計にしている。app/bot/main.pyの常時起動Botからは
  NotificationService(client).send_opportunity_notificationを渡す想定だが、
  テストからは実際のDiscordゲートウェイ接続を持たない偽のsend_fnを渡せる
  (app/notification/interaction_view.pyのhttp_client注入と同じ考え方)。
"""

import logging
from datetime import datetime
from typing import Awaitable, Callable

import discord
from sqlalchemy.orm import Session

from app.core.time import JST
from app.db.models import PendingNotification
from app.domain.enums import PendingNotificationStatus
from app.notification.interaction_view import OpportunityActionView

__all__ = ["dispatch_pending_notifications", "MAX_SEND_ATTEMPTS"]

logger = logging.getLogger(__name__)

# この回数まで失敗したらstatus=failedにしてリトライを止める
# (無限リトライで送信不能な行が永久にポーリングを重くし続けるのを防ぐ)。
MAX_SEND_ATTEMPTS = 5

SendFn = Callable[[int, dict, discord.ui.View | None], Awaitable[discord.Message]]


async def dispatch_pending_notifications(
    session: Session,
    send_fn: SendFn,
    max_attempts: int = MAX_SEND_ATTEMPTS,
) -> dict:
    """status=pendingの行を作成日時の古い順に送信する。

    1件の送信失敗(Discord APIエラー・チャンネル取得失敗等)が残りの行をブロック
    しないよう、try/exceptで個別に捕捉して処理を継続する
    (app/scheduler/tasks.py:run_ichiban_kuji_collector()の各URLループと同じ考え方)。
    """
    rows = (
        session.query(PendingNotification)
        .filter(PendingNotification.status == PendingNotificationStatus.PENDING)
        .order_by(PendingNotification.created_at)
        .all()
    )

    sent_count = 0
    failed_count = 0
    errors: list[str] = []

    for row in rows:
        view = OpportunityActionView(
            event_id=str(row.release_event_id),
            opportunity_id=str(row.opportunity_id),
            # timeout=None: 常駐Botはこの後もずっと動き続けるため、Viewを自動失効させない
            # (app/bot/main.py:_send_test_notification()と同じ理由)。
            timeout=None,
            manual_review_task_id=row.manual_review_task_id,
        )
        try:
            message = await send_fn(int(row.channel_id), row.embed, view)
        except Exception as exc:
            row.attempt_count += 1
            row.last_error = f"{type(exc).__name__}: {exc}"
            if row.attempt_count >= max_attempts:
                row.status = PendingNotificationStatus.FAILED
                failed_count += 1
            errors.append(f"id={row.id}: {row.last_error}")
            logger.exception(
                "[dispatch_pending_notifications] send failed for id=%s (attempt %d/%d)",
                row.id,
                row.attempt_count,
                max_attempts,
            )
            session.commit()  # このモジュールdocstring「設計方針」参照: 行単位でcommitする
            continue

        row.status = PendingNotificationStatus.SENT
        row.sent_at = datetime.now(tz=JST)
        row.discord_message_id = str(message.id)
        sent_count += 1
        session.commit()  # 送信成功の直後に確定させる(モジュールdocstring参照)

    return {
        "checked_count": len(rows),
        "sent_count": sent_count,
        "failed_count": failed_count,
        "errors": errors,
    }
