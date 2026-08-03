"""Opportunityの通知要否判定とEmbed組み立て(CLAUDE.mdタスク13)。

タスク7(region_resolver)・タスク11(dedupe/embed_builder)の各関数をこの順序で
つなぐ、初めての統合レイヤー:
  resolve_region() → should_include_for_region_filter() → build_dedupe_key()
  → check_and_update_dedupe_state() → (NEW/UPDATEDなら)build_embed()

実際にDiscordへ送信する部分(app/notification/discord_bot.py)は、タスク11で
明記した通りこのサンドボックスでは検証不可能なため、本モジュールは「送信すべきか、
何を送信すべきか」を決定するところまでを担当する。
"""

from dataclasses import dataclass

from app.collectors.markets.base import MarketObservation
from app.db.models import Product, ReleaseEvent, Shop, Source
from app.domain.enums import MatchStatus, ShopGranularity
from app.matcher.region_resolver import resolve_region, should_include_for_region_filter
from app.notification.dedupe import (
    DedupeCheckResult,
    DedupeDecision,
    build_dedupe_key,
    build_diff_notification_text,
    check_and_update_dedupe_state,
)
from app.notification.embed_builder import OpportunityView, build_embed
from app.profit.profit_engine import ExcludedCostItem

__all__ = ["NotificationDecision", "evaluate_notification"]


@dataclass
class NotificationDecision:
    should_send: bool
    dedupe_result: DedupeCheckResult | None
    embed: dict | None
    diff_notification_text: str | None
    excluded_reason: str | None = None  # should_send=Falseの理由(地域フィルタ等)


def evaluate_notification(
    redis_client,
    release_event: ReleaseEvent,
    shop: Shop,
    product: Product,
    source: Source,
    observation: MarketObservation,
    standard_displayed_profit,
    standard_excluded_cost_items: list[ExcludedCostItem],
    match_status: MatchStatus,
    target_prefectures: set[str],
) -> NotificationDecision:
    """実装仕様書4.3の地域フィルタ→4.2のdedupe判定→4.1のEmbed組み立てを一連実行する。

    match_statusはmarket_matching.match_observation_to_product()等が返した
    Product Matcherの照合結果。build_embed()がHIGH_PROBABILITY_MATCH/NEEDS_REVIEWの
    場合に「商品照合」フィールドを追加する(技術分析11.3原則3、地域の要確認とは別表示)。
    """
    resolved_region = resolve_region(release_event, shop)

    if not should_include_for_region_filter(resolved_region, release_event.fulfillment_type, target_prefectures):
        return NotificationDecision(
            should_send=False,
            dedupe_result=None,
            embed=None,
            diff_notification_text=None,
            excluded_reason="region_filter",
        )

    excluded_names = [item.name for item in standard_excluded_cost_items]
    dedupe_key = build_dedupe_key(
        event_id=str(release_event.id),
        shop_id=str(shop.id) if shop.granularity != ShopGranularity.NATIONAL_CHAIN else None,
        price=standard_displayed_profit,
        deadline_at=release_event.deadline_at,
        excluded_cost_items=excluded_names,
    )
    dedupe_result = check_and_update_dedupe_state(
        redis_client,
        str(release_event.id),
        dedupe_key,
        excluded_cost_items=excluded_names,
        displayed_profit=standard_displayed_profit,
    )

    if dedupe_result.decision == DedupeDecision.UNCHANGED:
        return NotificationDecision(
            should_send=False,
            dedupe_result=dedupe_result,
            embed=None,
            diff_notification_text=None,
            excluded_reason="unchanged",
        )

    excluded_cost_item_labels = {item.name: item.value for item in ExcludedCostItem}
    diff_text = build_diff_notification_text(
        previous_displayed_profit=dedupe_result.previous_displayed_profit or standard_displayed_profit,
        current_displayed_profit=standard_displayed_profit,
        newly_resolved_cost_items=dedupe_result.newly_resolved_cost_items,
        excluded_cost_item_labels=excluded_cost_item_labels,
    )

    opp_view = OpportunityView(
        product_name=product.name,
        apply_url=release_event.apply_url,
        image_url=None,
        confidence=observation.confidence,
        source_name=source.name,
        purchase_price=int(release_event.price),
        best_channel_name=source.name,
        fulfillment_type=release_event.fulfillment_type,
        region_source=resolved_region.region_source,
        region_display_text=resolved_region.display_text,
        displayed_profit=int(standard_displayed_profit),
        excluded_cost_items=standard_excluded_cost_items,
        match_status=match_status,
        deadline_at=release_event.deadline_at,
        source_url=release_event.product_url,
        recent_sold_count=None,
        listing_count=None,
        fetched_at=observation.observed_at,
    )
    embed = build_embed(opp_view)

    return NotificationDecision(
        should_send=True,
        dedupe_result=dedupe_result,
        embed=embed,
        diff_notification_text=diff_text,
    )
