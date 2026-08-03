"""地域解決ロジック(実装仕様書1.3・4.3)。

実装仕様書 Prompt 4:
「地域解決ロジックを実装してください。
優先順位: release_events.region_override > shops.region_id >
shops.granularity(national_chainなら全国) > unknown(全国扱い)」

【スコープに関する注記】
CLAUDE.mdタスク7は「Product Matcher実装」と題されているが、実際にPrompt 4が指す内容は
本モジュールの地域解決ロジックのみ。技術分析レポート11章の商品照合スコアリング
(JAN一致・商品名類似度等による同一商品判定)はユーザー確認の上で別タスクとして扱い、
本モジュールには含めない。
"""

from dataclasses import dataclass

from app.db.models.release_event import ReleaseEvent
from app.db.models.region import Region
from app.db.models.shop import Shop
from app.domain.enums import FulfillmentType, RegionSource, ShopGranularity

__all__ = [
    "ResolvedRegion",
    "resolve_region",
    "is_region_filter_applicable",
    "matches_target_regions",
    "should_include_for_region_filter",
]


@dataclass
class ResolvedRegion:
    region_source: RegionSource
    display_text: str | None
    prefecture: str | None  # 都道府県単位まで特定できた場合のみ設定


def resolve_region(event: ReleaseEvent, shop: Shop, region: Region | None = None) -> ResolvedRegion:
    """実装仕様書1.3の優先順位で地域を解決する。

    優先順位:
      1. event.region_override が設定されていれば最優先
         (national_chainのshopであっても、個別イベント単位の地域限定がここで上書きされる。
          実装仕様書0章 差分3番「national_chainの例外地域限定」に対応)
      2. shop.region_id があれば、その参照先Regionのprefectureを使用
      3. shop.granularity == national_chain なら「全国」
      4. 上記いずれも無ければ region_source = unknown として全国扱い

    region: shop.region_idが指すRegionレコード。呼び出し側でJOIN/取得して渡す
      (この関数自体はDBセッションに依存しない純粋な関数として保つため)。
    """
    if event.region_override:
        return ResolvedRegion(
            region_source=event.region_source,
            display_text=event.region_display_text or event.region_override,
            prefecture=None,
        )

    if shop.region_id is not None:
        prefecture = region.prefecture if region is not None else None
        return ResolvedRegion(
            region_source=RegionSource.PREFECTURE,
            display_text=prefecture,
            prefecture=prefecture,
        )

    if shop.granularity == ShopGranularity.NATIONAL_CHAIN:
        return ResolvedRegion(region_source=RegionSource.UNKNOWN, display_text="全国", prefecture=None)

    return ResolvedRegion(region_source=RegionSource.UNKNOWN, display_text=None, prefecture=None)


def is_region_filter_applicable(fulfillment_type: FulfillmentType) -> bool:
    """実装仕様書4.3: 地域フィルタはstore_pickupのみに適用する。

    online_shipping/bothは郵送ルートが存在するため、地域が一致しなくても
    非表示にしてはいけない(郵送で購入できる機会を隠すと機会損失になるため)。
    """
    return fulfillment_type == FulfillmentType.STORE_PICKUP


def matches_target_regions(resolved: ResolvedRegion, target_prefectures: set[str]) -> bool:
    """resolved regionがtarget_prefecturesに一致するか判定する。

    実装仕様書4.3:
    - region_source == area_group の場合は不一致でも除外しない
      (大まかな範囲のみ判明・要確認ラベルの付与は通知レイヤーの責務)。
    - region_source == unknown は全国扱いのため常に一致とみなす。
    - prefectureまで特定できていない場合(判定材料が無い場合)も除外しない
      (CLAUDE.md最重要方針2: 機会損失より目視確認を優先する)。
    """
    if resolved.region_source in (RegionSource.UNKNOWN, RegionSource.AREA_GROUP):
        return True
    if resolved.prefecture is None:
        return True
    return resolved.prefecture in target_prefectures


def should_include_for_region_filter(
    resolved: ResolvedRegion, fulfillment_type: FulfillmentType, target_prefectures: set[str]
) -> bool:
    """実装仕様書4.3の通知対象絞り込みロジック全体のエントリポイント。

    fulfillment_type in (online_shipping, both) では常にTrue
    (=地域フィルタを適用せず対象に含める)。
    """
    if not is_region_filter_applicable(fulfillment_type):
        return True
    return matches_target_regions(resolved, target_prefectures)
