"""build_embed()(app/notification/embed_builder.py)のテスト。

実装仕様書4.1: excluded_cost_itemsの有無での表示分岐、region_source=area_groupの
要確認注記、deadline_at=Noneのフォールバック表示を検証する(Prompt 7)。
"""

from datetime import datetime

import pytest

from app.core.time import JST
from app.domain.enums import FulfillmentType, MatchStatus, RegionSource
from app.notification.embed_builder import (
    OpportunityView,
    build_embed,
    color_by_confidence,
    fulfillment_label,
)
from app.profit.profit_engine import ExcludedCostItem

FETCHED_AT = datetime(2026, 8, 3, 9, 0, tzinfo=JST)


def _base_opp(**overrides) -> OpportunityView:
    defaults = dict(
        product_name="一番くじ CUTIE STREET",
        apply_url="https://1kuji.com/products/cutiestreet",
        image_url="https://example.com/image.jpg",
        confidence="B",
        source_name="一番くじ公式",
        purchase_price=2800,
        best_channel_name="駿河屋買取",
        fulfillment_type=FulfillmentType.BOTH,
        region_source=RegionSource.UNKNOWN,
        region_display_text=None,
        displayed_profit=900,
        excluded_cost_items=[],
        match_status=MatchStatus.AUTO_MATCH,
        deadline_at=None,
        source_url="https://1kuji.com/products/cutiestreet",
        recent_sold_count=None,
        listing_count=None,
        fetched_at=FETCHED_AT,
    )
    defaults.update(overrides)
    return OpportunityView(**defaults)


def _field(embed: dict, name: str) -> dict:
    return next(f for f in embed["fields"] if f["name"] == name)


def test_build_embed_profit_note_without_excluded_items():
    embed = build_embed(_base_opp(excluded_cost_items=[]))

    assert _field(embed, "想定利益")["value"] == "想定利益: ¥900"


def test_build_embed_profit_note_lists_excluded_items():
    opp = _base_opp(
        displayed_profit=1600,
        excluded_cost_items=[ExcludedCostItem.SHIPPING_UNKNOWN, ExcludedCostItem.TRAVEL_UNKNOWN],
    )

    value = _field(build_embed(opp), "想定利益")["value"]

    assert "想定利益: ¥1,600" in value
    assert "- 送料不明" in value
    assert "- 交通費不明(店舗受取のため)" in value
    assert "目視確認" in value


def test_build_embed_region_unknown_shows_nationwide_label():
    opp = _base_opp(region_source=RegionSource.UNKNOWN, region_display_text=None)

    assert _field(build_embed(opp), "対象地域")["value"] == "地域情報不明(全国対象として表示)"


def test_build_embed_match_status_high_probability_match_shows_softer_warning():
    """技術分析11.3原則3: HIGH_PROBABILITY_MATCHはNEEDS_REVIEWより弱い注意喚起にする。"""
    opp = _base_opp(match_status=MatchStatus.HIGH_PROBABILITY_MATCH)

    field = _field(build_embed(opp), "商品照合")

    assert field["value"] == "高確率一致・念のためご確認ください"


def test_build_embed_match_status_needs_review_shows_stronger_warning():
    opp = _base_opp(match_status=MatchStatus.NEEDS_REVIEW)

    field = _field(build_embed(opp), "商品照合")

    assert field["value"] == "要確認・別商品の可能性があります"


def test_build_embed_match_status_auto_match_shows_no_field():
    """AUTO_MATCHは確認不要のため「商品照合」フィールド自体を出さない。"""
    opp = _base_opp(match_status=MatchStatus.AUTO_MATCH)

    embed = build_embed(opp)

    assert all(f["name"] != "商品照合" for f in embed["fields"])


def test_build_embed_match_status_field_is_distinct_from_region_review_field():
    """ユーザー指摘: 地域の「要確認」と商品照合の「要確認」を同じfield/文言で
    混同しないこと。両方が同時に発生しても別々のfieldとして出ることを確認する。"""
    opp = _base_opp(
        region_source=RegionSource.AREA_GROUP,
        region_display_text="東海地方",
        match_status=MatchStatus.NEEDS_REVIEW,
    )

    embed = build_embed(opp)
    region_field = _field(embed, "対象地域")
    match_field = _field(embed, "商品照合")

    assert region_field["name"] != match_field["name"]
    assert "大まかな範囲のみ判明・要確認" in region_field["value"]
    assert match_field["value"] == "要確認・別商品の可能性があります"
    # 商品照合フィールドの文言に地域側の要確認文言が混入していないこと
    assert "大まかな範囲" not in match_field["value"]


def test_build_embed_region_area_group_appends_review_suffix():
    opp = _base_opp(region_source=RegionSource.AREA_GROUP, region_display_text="東海地方")

    value = _field(build_embed(opp), "対象地域")["value"]

    assert value.startswith("東海地方")
    assert "大まかな範囲のみ判明・要確認" in value


def test_build_embed_region_prefecture_uses_display_text_without_suffix():
    opp = _base_opp(region_source=RegionSource.PREFECTURE, region_display_text="愛知県")

    assert _field(build_embed(opp), "対象地域")["value"] == "愛知県"


def test_build_embed_deadline_none_shows_official_site_fallback():
    opp = _base_opp(deadline_at=None, source_url="https://1kuji.com/products/cutiestreet")

    value = _field(build_embed(opp), "締切日時")["value"]

    assert "締切は公式サイトでご確認ください" in value
    assert "https://1kuji.com/products/cutiestreet" in value


def test_build_embed_deadline_present_is_formatted_in_jst():
    opp = _base_opp(deadline_at=datetime(2026, 8, 10, 23, 59, tzinfo=JST))

    value = _field(build_embed(opp), "締切日時")["value"]

    assert value == "2026年08月10日 23:59"


@pytest.mark.parametrize(
    "count_field,value,expected",
    [
        ("recent_sold_count", None, "取得不可"),
        ("recent_sold_count", 5, "5"),
        ("listing_count", None, "取得不可"),
        ("listing_count", 12, "12"),
    ],
)
def test_build_embed_unavailable_counts_show_fallback_text(count_field, value, expected):
    field_name = "直近成約件数" if count_field == "recent_sold_count" else "現在出品数"
    opp = _base_opp(**{count_field: value})

    assert _field(build_embed(opp), field_name)["value"] == expected


def test_build_embed_omits_thumbnail_when_image_url_missing():
    opp = _base_opp(image_url=None)

    assert build_embed(opp)["thumbnail"] is None


@pytest.mark.parametrize(
    "confidence,expected_color",
    [("A", 0x2ECC71), ("B", 0x3498DB), ("C", 0xF1C40F), ("D", 0x95A5A6)],
)
def test_color_by_confidence(confidence, expected_color):
    assert color_by_confidence(confidence) == expected_color


@pytest.mark.parametrize(
    "fulfillment_type,expected_label",
    [
        (FulfillmentType.STORE_PICKUP, "店舗受取"),
        (FulfillmentType.ONLINE_SHIPPING, "郵送"),
        (FulfillmentType.BOTH, "店舗受取/郵送"),
    ],
)
def test_fulfillment_label(fulfillment_type, expected_label):
    assert fulfillment_label(fulfillment_type) == expected_label
