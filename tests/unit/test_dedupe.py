"""dedupeロジック(app/notification/dedupe.py)の純粋関数テスト。
Redis接続を要する部分はtests/integration/test_dedupe_redis.pyで検証する。
"""

from datetime import datetime
from decimal import Decimal

from app.core.time import JST
from app.notification.dedupe import (
    DedupeDecision,
    build_dedupe_key,
    build_diff_notification_text,
    classify_dedupe,
    resolved_cost_items,
)

DEADLINE = datetime(2026, 8, 10, 23, 59, tzinfo=JST)


def test_build_dedupe_key_is_deterministic():
    key1 = build_dedupe_key("event-1", "shop-1", Decimal("1000"), DEADLINE, ["SHIPPING_UNKNOWN"])
    key2 = build_dedupe_key("event-1", "shop-1", Decimal("1000"), DEADLINE, ["SHIPPING_UNKNOWN"])

    assert key1 == key2


def test_build_dedupe_key_sorts_excluded_cost_items_so_order_does_not_matter():
    key1 = build_dedupe_key("event-1", "shop-1", Decimal("1000"), DEADLINE, ["A", "B"])
    key2 = build_dedupe_key("event-1", "shop-1", Decimal("1000"), DEADLINE, ["B", "A"])

    assert key1 == key2


def test_build_dedupe_key_changes_when_price_changes():
    key1 = build_dedupe_key("event-1", "shop-1", Decimal("1000"), DEADLINE, [])
    key2 = build_dedupe_key("event-1", "shop-1", Decimal("1200"), DEADLINE, [])

    assert key1 != key2


def test_build_dedupe_key_handles_none_deadline_without_raising():
    """実装仕様書1.3のnullable運用: deadline_at=Noneでも.isoformat()で例外にならない。"""
    key = build_dedupe_key("event-1", None, Decimal("1000"), None, [])

    assert isinstance(key, str) and len(key) == 64


def test_build_dedupe_key_uses_national_placeholder_when_shop_id_is_none():
    key_with_national = build_dedupe_key("event-1", None, Decimal("1000"), DEADLINE, [])
    key_with_literal_national = build_dedupe_key("event-1", "national", Decimal("1000"), DEADLINE, [])

    assert key_with_national == key_with_literal_national


def test_classify_dedupe_new_when_no_previous_hash():
    assert classify_dedupe(None, "abc") == DedupeDecision.NEW


def test_classify_dedupe_unchanged_when_hash_matches():
    assert classify_dedupe("abc", "abc") == DedupeDecision.UNCHANGED


def test_classify_dedupe_updated_when_hash_differs():
    assert classify_dedupe("abc", "def") == DedupeDecision.UPDATED


def test_resolved_cost_items_detects_items_no_longer_excluded():
    resolved = resolved_cost_items(
        previous_excluded=["SHIPPING_UNKNOWN", "TRAVEL_UNKNOWN"], current_excluded=["TRAVEL_UNKNOWN"]
    )

    assert resolved == ["SHIPPING_UNKNOWN"]


def test_resolved_cost_items_empty_when_nothing_changed():
    resolved = resolved_cost_items(previous_excluded=["SHIPPING_UNKNOWN"], current_excluded=["SHIPPING_UNKNOWN"])

    assert resolved == []


def test_build_diff_notification_text_returns_none_when_nothing_resolved():
    text = build_diff_notification_text(
        Decimal("1350"), Decimal("950"), newly_resolved_cost_items=[], excluded_cost_item_labels={}
    )

    assert text is None


def test_build_diff_notification_text_matches_spec_example_shape():
    """実装仕様書4.2の例: 「送料が判明しました。想定利益: ¥1,350 → ¥950...」"""
    text = build_diff_notification_text(
        Decimal("1350"),
        Decimal("950"),
        newly_resolved_cost_items=["SHIPPING_UNKNOWN"],
        excluded_cost_item_labels={"SHIPPING_UNKNOWN": "送料不明"},
    )

    assert text == "送料不明が判明しました。想定利益: ¥1,350 → ¥950"


def test_build_diff_notification_text_joins_multiple_resolved_items():
    text = build_diff_notification_text(
        Decimal("1350"),
        Decimal("950"),
        newly_resolved_cost_items=["SHIPPING_UNKNOWN", "TRAVEL_UNKNOWN"],
        excluded_cost_item_labels={"SHIPPING_UNKNOWN": "送料不明", "TRAVEL_UNKNOWN": "交通費不明"},
    )

    assert text == "送料不明・交通費不明が判明しました。想定利益: ¥1,350 → ¥950"
