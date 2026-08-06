"""`_match_and_score_observation()`(app/scheduler/tasks.py)がshould_send=Trueと
判定した際に、pending_notificationsテーブルへ実際に1行作成することの検証
(実DB・実Redis接続)。

2026-08-06、should_send=Trueの結果がCeleryタスクの戻り値に含まれるだけで誰にも
自動的に拾われず失われていた欠落(収集→通知という当初からの目的そのものに関わる)への
緊急対応。ここでは「書き込み」側(収集タスク)を検証し、「読み出し・送信」側は
tests/integration/test_dispatcher.pyで別途検証している。
"""

import redis as redis_module

import pytest

from app.collectors.markets.base import MarketDataType, MarketObservation
from app.config import settings
from app.core.time import JST
from app.db.models import PendingNotification, Product, ReleaseEvent, Shop, Source
from app.domain.enums import FulfillmentType, RegionSource, ShopGranularity, ShopType, SupportedEventType
from app.scheduler.tasks import _match_and_score_observation
from datetime import datetime
from decimal import Decimal


@pytest.fixture()
def redis_client():
    client = redis_module.Redis.from_url(settings.redis_url, decode_responses=True)
    yield client
    client.close()


def _make_release_event(db_session, product_name: str, price: Decimal, suffix: str) -> ReleaseEvent:
    source = Source(
        name=f"通知作成テスト情報源{suffix}", base_url="https://example.com", collector_key=f"test_pending_creation_{suffix}"
    )
    shop = Shop(name=f"通知作成テスト店舗{suffix}", type=ShopType.ONLINE, granularity=ShopGranularity.NATIONAL_CHAIN)
    db_session.add_all([source, shop])
    db_session.flush()

    product = Product(name=product_name)
    db_session.add(product)
    db_session.flush()

    event = ReleaseEvent(
        product_id=product.id,
        shop_id=shop.id,
        source_id=source.id,
        event_type=SupportedEventType.LOTTERY,
        price=price,
        product_url=f"https://example.com/{suffix}",
        fulfillment_type=FulfillmentType.ONLINE_SHIPPING,
        region_source=RegionSource.UNKNOWN,
    )
    db_session.add(event)
    db_session.flush()
    return event


def test_match_and_score_observation_persists_pending_notification_when_should_send(db_session, redis_client):
    product_name = "通知作成テスト商品X"
    release_event = _make_release_event(db_session, product_name, Decimal("790"), suffix="x")

    observation = MarketObservation(
        product_ref=product_name,
        data_type=MarketDataType.BUYBACK_PRICE,
        amount=Decimal("1200"),
        count=None,
        observed_at=datetime.now(tz=JST),
        source_url="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU999999",
        confidence="B",
        extra={"title": product_name},
    )

    try:
        results = _match_and_score_observation(db_session, redis_client, observation, channel_name="suruga_ya")
        assert len(results) == 1

        rows = db_session.query(PendingNotification).filter_by(release_event_id=release_event.id).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.opportunity_id is not None
        assert row.channel_id == settings.discord_notify_channel_id
        assert row.embed["title"] == product_name
        assert row.dedupe_key is not None
        assert row.status.value == "pending"

        # 同一内容で再評価するとdedupe判定によりUNCHANGEDとなり、should_send=Falseに
        # なるため、pending_notificationsへ2件目が積まれない(重複キューイング防止)。
        second_results = _match_and_score_observation(db_session, redis_client, observation, channel_name="suruga_ya")
        assert second_results == []

        rows_after = db_session.query(PendingNotification).filter_by(release_event_id=release_event.id).all()
        assert len(rows_after) == 1  # 増えていない
    finally:
        redis_client.delete(f"notification:{release_event.id}")
