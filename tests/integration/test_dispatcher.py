"""app/notification/dispatcher.py:dispatch_pending_notifications()のテスト(実DB接続)。

should_send=Trueと判定されてもpending_notificationsテーブルへ書き込むだけで誰も
自動的に拾って送信していなかった欠落(2026-08-06発覚、CLAUDE.md参照)への緊急対応。
実際のDiscordゲートウェイ接続は使わず、send_fnを偽関数に差し替えて検証する
(app/notification/interaction_view.pyのhttp_client注入と同じ考え方)。

このファイルの最重要の検証観点は「重複送信防止がこの新しい送信経路でも正しく機能する
か」(ユーザー依頼): status=SENTになった行は次回以降のポーリングで二度と送信されない
ことを直接確認する。
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.time import JST
from app.db.models import Opportunity, PendingNotification, Product, ReleaseEvent, Shop, Source
from app.domain.enums import MatchStatus, PendingNotificationStatus, ShopGranularity, ShopType, SupportedEventType
from app.notification.dispatcher import dispatch_pending_notifications


def _make_pending_notification(
    db_session,
    *,
    status: PendingNotificationStatus = PendingNotificationStatus.PENDING,
    created_at: datetime | None = None,
    attempt_count: int = 0,
    suffix: str = "1",
) -> PendingNotification:
    product = Product(name=f"通知テスト商品{suffix}")
    db_session.add(product)
    db_session.flush()

    source = Source(
        name=f"通知テスト情報源{suffix}", base_url="https://example.com", collector_key=f"test_dispatcher_{suffix}"
    )
    shop = Shop(name=f"通知テスト店舗{suffix}", type=ShopType.ONLINE, granularity=ShopGranularity.NATIONAL_CHAIN)
    db_session.add_all([source, shop])
    db_session.flush()

    event = ReleaseEvent(
        product_id=product.id,
        shop_id=shop.id,
        source_id=source.id,
        event_type=SupportedEventType.LOTTERY,
        product_url=f"https://example.com/{suffix}",
    )
    db_session.add(event)
    db_session.flush()

    opportunity = Opportunity(
        product_id=product.id,
        event_id=event.id,
        best_channel_name="suruga_ya",
        confidence="B",
        score="0.5",
        match_status=MatchStatus.AUTO_MATCH,
    )
    db_session.add(opportunity)
    db_session.flush()

    notification = PendingNotification(
        opportunity_id=opportunity.id,
        release_event_id=event.id,
        channel_id="123456789",
        embed={"title": f"商品{suffix}"},
        manual_review_task_id=None,
        status=status,
        created_at=created_at or datetime.now(tz=JST),
        attempt_count=attempt_count,
    )
    db_session.add(notification)
    db_session.flush()
    return notification


def _fake_message(message_id: int = 999) -> SimpleNamespace:
    return SimpleNamespace(id=message_id, jump_url=f"https://discord.com/channels/x/x/{message_id}")


async def test_dispatch_sends_pending_row_and_marks_sent(db_session):
    notification = _make_pending_notification(db_session)
    calls = []

    async def fake_send_fn(channel_id, embed, view):
        calls.append((channel_id, embed, view))
        return _fake_message(111)

    result = await dispatch_pending_notifications(db_session, send_fn=fake_send_fn)

    assert result == {"checked_count": 1, "sent_count": 1, "failed_count": 0, "errors": []}
    assert len(calls) == 1
    channel_id, embed, view = calls[0]
    assert channel_id == 123456789
    assert embed == {"title": "商品1"}
    assert view.event_id == str(notification.release_event_id)
    assert view.opportunity_id == str(notification.opportunity_id)

    db_session.refresh(notification)
    assert notification.status == PendingNotificationStatus.SENT
    assert notification.sent_at is not None
    assert notification.discord_message_id == "111"


async def test_dispatch_does_not_resend_after_first_successful_dispatch(db_session):
    """重複送信防止(ユーザー依頼)の直接確認: 1回目のポーリングで送信済みになった行は、
    2回目のポーリング(=dispatch_pending_notificationsの再呼び出し)で再送されない。"""
    _make_pending_notification(db_session)
    call_count = 0

    async def fake_send_fn(channel_id, embed, view):
        nonlocal call_count
        call_count += 1
        return _fake_message(222)

    first_result = await dispatch_pending_notifications(db_session, send_fn=fake_send_fn)
    second_result = await dispatch_pending_notifications(db_session, send_fn=fake_send_fn)

    assert first_result["sent_count"] == 1
    assert second_result == {"checked_count": 0, "sent_count": 0, "failed_count": 0, "errors": []}
    assert call_count == 1  # 2回目は一切送信を試みていない


async def test_dispatch_skips_rows_already_sent_or_failed(db_session):
    _make_pending_notification(db_session, status=PendingNotificationStatus.SENT, suffix="sent")
    _make_pending_notification(db_session, status=PendingNotificationStatus.FAILED, suffix="failed")
    calls = []

    async def fake_send_fn(channel_id, embed, view):
        calls.append(embed)
        return _fake_message()

    result = await dispatch_pending_notifications(db_session, send_fn=fake_send_fn)

    assert result["checked_count"] == 0
    assert calls == []


async def test_dispatch_continues_after_one_row_fails(db_session):
    failing = _make_pending_notification(db_session, suffix="failing", created_at=datetime.now(tz=JST))
    succeeding = _make_pending_notification(
        db_session, suffix="succeeding", created_at=datetime.now(tz=JST) + timedelta(seconds=1)
    )

    async def fake_send_fn(channel_id, embed, view):
        if embed["title"] == "商品failing":
            raise RuntimeError("discord API error (test double)")
        return _fake_message(333)

    result = await dispatch_pending_notifications(db_session, send_fn=fake_send_fn)

    assert result["checked_count"] == 2
    assert result["sent_count"] == 1
    assert result["failed_count"] == 0  # まだmax_attempts未満なのでfailed確定はしていない
    assert len(result["errors"]) == 1

    db_session.refresh(failing)
    db_session.refresh(succeeding)
    assert failing.status == PendingNotificationStatus.PENDING
    assert failing.attempt_count == 1
    assert "RuntimeError" in failing.last_error
    assert succeeding.status == PendingNotificationStatus.SENT


async def test_dispatch_marks_row_failed_after_max_attempts(db_session):
    notification = _make_pending_notification(db_session, attempt_count=2)

    async def always_failing_send_fn(channel_id, embed, view):
        raise RuntimeError("permanent failure (test double)")

    result = await dispatch_pending_notifications(db_session, send_fn=always_failing_send_fn, max_attempts=3)

    assert result["failed_count"] == 1
    db_session.refresh(notification)
    assert notification.attempt_count == 3
    assert notification.status == PendingNotificationStatus.FAILED

    # failed確定後はもう二度と拾われない(無限リトライ防止の確認)。
    calls = []

    async def fake_send_fn(channel_id, embed, view):
        calls.append(1)
        return _fake_message()

    second_result = await dispatch_pending_notifications(db_session, send_fn=fake_send_fn)
    assert second_result["checked_count"] == 0
    assert calls == []


async def test_dispatch_processes_rows_oldest_first(db_session):
    older = _make_pending_notification(db_session, suffix="older", created_at=datetime.now(tz=JST) - timedelta(hours=1))
    newer = _make_pending_notification(db_session, suffix="newer", created_at=datetime.now(tz=JST))
    order: list[str] = []

    async def fake_send_fn(channel_id, embed, view):
        order.append(embed["title"])
        return _fake_message()

    await dispatch_pending_notifications(db_session, send_fn=fake_send_fn)

    assert order == ["商品older", "商品newer"]
