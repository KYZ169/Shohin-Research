"""地域解決ロジック(app/matcher/region_resolver.py)のテスト。

実装仕様書6章 Region Resolution Test / Fulfillment Filter Testの内容に加え、
ユーザー指摘の2点を明示的なテストケースとして含める:
- national_chainかつregion_overrideありの例外ケース(実装仕様書0章 差分3番)
- fulfillment_type=bothのときは地域フィルタを適用しない(実装仕様書4.3)
"""

import uuid

import pytest

from app.db.models.region import Region
from app.db.models.release_event import ReleaseEvent
from app.db.models.shop import Shop
from app.domain.enums import FulfillmentType, RegionSource, ShopGranularity, ShopType, SupportedEventType
from app.matcher.region_resolver import (
    ResolvedRegion,
    is_region_filter_applicable,
    matches_target_regions,
    resolve_region,
    should_include_for_region_filter,
)


def _event(**overrides) -> ReleaseEvent:
    defaults = dict(
        product_id=uuid.uuid4(),
        shop_id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        event_type=SupportedEventType.LOTTERY,
        region_override=None,
        region_display_text=None,
        region_source=RegionSource.UNKNOWN,
    )
    defaults.update(overrides)
    return ReleaseEvent(**defaults)


def _shop(**overrides) -> Shop:
    defaults = dict(
        name="テスト店舗",
        type=ShopType.STORE,
        granularity=ShopGranularity.SINGLE_STORE,
        region_id=None,
    )
    defaults.update(overrides)
    return Shop(**defaults)


# --- resolve_region: 優先順位 ---


def test_resolve_region_prefers_region_override_over_shop_region_id():
    """優先順位1位: region_overrideが設定されていればshop.region_idより優先される。"""
    region = Region(prefecture="大阪府", area_group="近畿")
    shop = _shop(granularity=ShopGranularity.REGIONAL_CHAIN, region_id=uuid.uuid4())
    event = _event(
        region_override="東京都限定",
        region_source=RegionSource.PREFECTURE,
        region_display_text="東京都の一部店舗限定",
    )

    resolved = resolve_region(event, shop, region=region)

    assert resolved.region_source == RegionSource.PREFECTURE
    assert resolved.display_text == "東京都の一部店舗限定"


def test_resolve_region_national_chain_with_region_override_is_overridden():
    """実装仕様書0章 差分3番: national_chainでもregion_overrideがあれば個別イベント単位で
    地域限定に上書きされ、「全国」にはならない。"""
    shop = _shop(granularity=ShopGranularity.NATIONAL_CHAIN, region_id=None)
    event = _event(
        region_override="愛知県限定",
        region_source=RegionSource.PREFECTURE,
        region_display_text="愛知県の一部店舗限定",
    )

    resolved = resolve_region(event, shop)

    assert resolved.region_source == RegionSource.PREFECTURE
    assert resolved.display_text == "愛知県の一部店舗限定"
    assert resolved.display_text != "全国"


def test_resolve_region_uses_shop_region_id_when_no_override():
    """優先順位2位: region_overrideが無ければshop.region_idを使用する。"""
    region = Region(prefecture="愛知県", area_group="東海")
    shop = _shop(granularity=ShopGranularity.REGIONAL_CHAIN, region_id=uuid.uuid4())
    event = _event()

    resolved = resolve_region(event, shop, region=region)

    assert resolved.region_source == RegionSource.PREFECTURE
    assert resolved.prefecture == "愛知県"


def test_resolve_region_national_chain_without_override_or_region_id_is_nationwide():
    """優先順位3位: region_override無し・region_id無しでnational_chainなら「全国」。"""
    shop = _shop(granularity=ShopGranularity.NATIONAL_CHAIN, region_id=None)
    event = _event()

    resolved = resolve_region(event, shop)

    assert resolved.region_source == RegionSource.UNKNOWN
    assert resolved.display_text == "全国"


def test_resolve_region_falls_back_to_unknown_when_nothing_resolvable():
    """優先順位4位: 何も手がかりが無ければunknown(全国扱い)。"""
    shop = _shop(granularity=ShopGranularity.SINGLE_STORE, region_id=None)
    event = _event()

    resolved = resolve_region(event, shop)

    assert resolved.region_source == RegionSource.UNKNOWN
    assert resolved.display_text is None


# --- fulfillment_type によるフィルタ適用可否 ---


@pytest.mark.parametrize(
    "fulfillment_type,expected",
    [
        (FulfillmentType.STORE_PICKUP, True),
        (FulfillmentType.ONLINE_SHIPPING, False),
        (FulfillmentType.BOTH, False),
    ],
)
def test_is_region_filter_applicable_only_for_store_pickup(fulfillment_type, expected):
    """実装仕様書4.3: store_pickupのみ地域フィルタが効き、online_shipping/bothでは効かない。"""
    assert is_region_filter_applicable(fulfillment_type) is expected


# --- should_include_for_region_filter: 実装仕様書4.3の統合テスト ---


def test_should_include_ignores_region_mismatch_when_fulfillment_type_is_both():
    """ユーザー指摘: fulfillment_type=bothのときは地域が一致しなくても除外してはいけない
    (郵送ルートがある以上、非表示にすると機会損失になる)。"""
    resolved = ResolvedRegion(region_source=RegionSource.PREFECTURE, display_text="愛知県", prefecture="愛知県")

    included = should_include_for_region_filter(
        resolved, FulfillmentType.BOTH, target_prefectures={"東京都"}
    )

    assert included is True


def test_should_include_ignores_region_mismatch_when_fulfillment_type_is_online_shipping():
    resolved = ResolvedRegion(region_source=RegionSource.PREFECTURE, display_text="愛知県", prefecture="愛知県")

    included = should_include_for_region_filter(
        resolved, FulfillmentType.ONLINE_SHIPPING, target_prefectures={"東京都"}
    )

    assert included is True


def test_should_include_applies_region_filter_for_store_pickup():
    resolved = ResolvedRegion(region_source=RegionSource.PREFECTURE, display_text="愛知県", prefecture="愛知県")

    assert should_include_for_region_filter(resolved, FulfillmentType.STORE_PICKUP, {"東京都"}) is False
    assert should_include_for_region_filter(resolved, FulfillmentType.STORE_PICKUP, {"愛知県"}) is True


# --- matches_target_regions: 除外しないケース ---


def test_matches_target_regions_area_group_is_never_excluded():
    """実装仕様書4.3: region_source=area_groupは不一致でも除外しない(要確認扱い)。"""
    resolved = ResolvedRegion(region_source=RegionSource.AREA_GROUP, display_text="東海地方", prefecture=None)

    assert matches_target_regions(resolved, target_prefectures={"東京都"}) is True


def test_matches_target_regions_unknown_is_always_included():
    resolved = ResolvedRegion(region_source=RegionSource.UNKNOWN, display_text="全国", prefecture=None)

    assert matches_target_regions(resolved, target_prefectures={"東京都"}) is True


def test_matches_target_regions_defaults_to_included_when_prefecture_unavailable():
    """CLAUDE.md最重要方針2: 判定材料が無い場合は機会損失より目視確認を優先し除外しない。"""
    resolved = ResolvedRegion(region_source=RegionSource.PREFECTURE, display_text=None, prefecture=None)

    assert matches_target_regions(resolved, target_prefectures={"東京都"}) is True


def test_matches_target_regions_excludes_when_prefecture_not_in_target_set():
    resolved = ResolvedRegion(region_source=RegionSource.PREFECTURE, display_text="愛知県", prefecture="愛知県")

    assert matches_target_regions(resolved, target_prefectures={"東京都", "大阪府"}) is False


def test_matches_target_regions_includes_when_prefecture_in_target_set():
    resolved = ResolvedRegion(region_source=RegionSource.PREFECTURE, display_text="愛知県", prefecture="愛知県")

    assert matches_target_regions(resolved, target_prefectures={"愛知県", "大阪府"}) is True
