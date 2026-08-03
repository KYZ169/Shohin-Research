"""check_and_update_dedupe_state()の実Redis接続テスト。

実装仕様書4.2: Redisに`notification:{event_id}`としてcontent_hashを保存し、
新規/更新(未確定→確定含む)/変化なしを判定する仕組みを検証する。
テスト用のevent_idを都度uuid4で発行し、テスト後に該当キーを削除して
ローカルRedisに副作用を残さないようにする。
"""

import uuid
from decimal import Decimal

import pytest
import redis

from app.config import settings
from app.notification.dedupe import DedupeDecision, build_dedupe_key, check_and_update_dedupe_state


@pytest.fixture()
def redis_client():
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    yield client
    client.close()


@pytest.fixture()
def event_id():
    key = f"test:{uuid.uuid4()}"
    yield key


def test_first_check_is_new(redis_client, event_id):
    dedupe_key = build_dedupe_key(event_id, "shop-1", Decimal("1000"), deadline_at=None, excluded_cost_items=[])

    try:
        result = check_and_update_dedupe_state(redis_client, event_id, dedupe_key, excluded_cost_items=[])
        assert result.decision == DedupeDecision.NEW
        assert result.newly_resolved_cost_items == []
    finally:
        redis_client.delete(f"notification:{event_id}")


def test_second_check_with_same_key_is_unchanged(redis_client, event_id):
    dedupe_key = build_dedupe_key(event_id, "shop-1", Decimal("1000"), deadline_at=None, excluded_cost_items=[])

    try:
        check_and_update_dedupe_state(redis_client, event_id, dedupe_key, excluded_cost_items=[])
        result = check_and_update_dedupe_state(redis_client, event_id, dedupe_key, excluded_cost_items=[])

        assert result.decision == DedupeDecision.UNCHANGED
    finally:
        redis_client.delete(f"notification:{event_id}")


def test_price_change_is_detected_as_updated(redis_client, event_id):
    first_key = build_dedupe_key(event_id, "shop-1", Decimal("1000"), deadline_at=None, excluded_cost_items=[])
    second_key = build_dedupe_key(event_id, "shop-1", Decimal("1200"), deadline_at=None, excluded_cost_items=[])

    try:
        check_and_update_dedupe_state(redis_client, event_id, first_key, excluded_cost_items=[])
        result = check_and_update_dedupe_state(redis_client, event_id, second_key, excluded_cost_items=[])

        assert result.decision == DedupeDecision.UPDATED
    finally:
        redis_client.delete(f"notification:{event_id}")


def test_unconfirmed_cost_becoming_confirmed_is_detected(redis_client, event_id):
    """実装仕様書4.2: 未確定→確定への変化(dedupe keyにexcluded_cost_itemsを含める)。"""
    first_key = build_dedupe_key(
        event_id, "shop-1", Decimal("1350"), deadline_at=None, excluded_cost_items=["SHIPPING_UNKNOWN"]
    )
    second_key = build_dedupe_key(event_id, "shop-1", Decimal("950"), deadline_at=None, excluded_cost_items=[])

    try:
        check_and_update_dedupe_state(
            redis_client, event_id, first_key, excluded_cost_items=["SHIPPING_UNKNOWN"]
        )
        result = check_and_update_dedupe_state(redis_client, event_id, second_key, excluded_cost_items=[])

        assert result.decision == DedupeDecision.UPDATED
        assert result.newly_resolved_cost_items == ["SHIPPING_UNKNOWN"]
    finally:
        redis_client.delete(f"notification:{event_id}")


def test_previous_displayed_profit_round_trips_through_redis(redis_client, event_id):
    """タスク13で追加: 差分通知(「想定利益: ¥1,350 → ¥950」)の実現に必要な
    前回displayed_profitの記録・取得を検証する。"""
    first_key = build_dedupe_key(
        event_id, "shop-1", Decimal("1350"), deadline_at=None, excluded_cost_items=["SHIPPING_UNKNOWN"]
    )
    second_key = build_dedupe_key(event_id, "shop-1", Decimal("950"), deadline_at=None, excluded_cost_items=[])

    try:
        first_result = check_and_update_dedupe_state(
            redis_client,
            event_id,
            first_key,
            excluded_cost_items=["SHIPPING_UNKNOWN"],
            displayed_profit=Decimal("1350"),
        )
        assert first_result.previous_displayed_profit is None  # 初回は前回値が無い

        second_result = check_and_update_dedupe_state(
            redis_client,
            event_id,
            second_key,
            excluded_cost_items=[],
            displayed_profit=Decimal("950"),
        )

        assert second_result.previous_displayed_profit == Decimal("1350")
    finally:
        redis_client.delete(f"notification:{event_id}")
