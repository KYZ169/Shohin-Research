"""ReleaseEvent(仕入れ)×MarketObservation(相場)からProfit Engine/Opportunity Scorerを
経由してOpportunityを生成するパイプライン(CLAUDE.mdタスク13)。

【Phase0スコープの簡略化(要検証・要TODO)】
- fee_rate/fee_fixed: fee_rules/shipping_rulesテーブルが未実装のため、駿河屋買取
  (買取価格提示制で出品手数料の概念が無い)を前提にfee_rate=0, fee_fixed=0を
  暫定採用する。フリマ等の手数料が発生するチャネルを組み込む際は要件を再検討すること。
- shipping_cost/travel_cost/packing_cost/other_cost: 実コスト情報を持つ手段が
  無いため、Phase0では常にNone(未確定)として扱う。これはProfit Engine(タスク9)の
  「未確定コストは正直にNoneのまま扱う」設計と整合する挙動であり、コストが0円だと
  偽装しているわけではない。
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.collectors.markets.base import MarketObservation
from app.db.models import Opportunity, Product, ReleaseEvent
from app.domain.enums import MatchStatus
from app.opportunity.opportunity_scorer import calc_score
from app.profit.profit_engine import (
    ExcludedCostItem,
    ScenarioProfitResult,
    calc_profit_scenarios,
    calc_roi,
    save_profit_snapshots,
)
from app.domain.enums import ProfitScenario

__all__ = ["OpportunityBuildResult", "build_and_score_opportunity"]

# Phase0の暫定値(モジュールdocstring参照)。fee_rules/shipping_rules実装時に置き換える。
DEFAULT_FEE_RATE = Decimal("0")
DEFAULT_FEE_FIXED = Decimal("0")


@dataclass
class OpportunityBuildResult:
    opportunity: Opportunity
    standard_displayed_profit: Decimal
    standard_excluded_cost_items: list[ExcludedCostItem]


def build_and_score_opportunity(
    session: Session,
    release_event: ReleaseEvent,
    product: Product,
    observation: MarketObservation,
    match_status: MatchStatus,
    channel_name: str,
) -> OpportunityBuildResult | None:
    """release_events.price(仕入価格)とMarketObservation.amount(想定売却価格)から
    Profit Engineでシナリオ計算し、Opportunity Scorerでスコアリングし、
    profit_snapshots/opportunitiesへ保存する。

    release_event.priceまたはobservation.amountが未確定(None)の場合、利益額そのものが
    計算不能なためOpportunityを作らずNoneを返す(実装仕様書の「未確定コストは金額計算に
    含めない」は費用項目の話であり、仕入価格・売却価格そのものが無ければ計算の起点が無い)。
    """
    if release_event.price is None or observation.amount is None:
        return None

    scenario_results: list[ScenarioProfitResult] = calc_profit_scenarios(
        base_sale_price=observation.amount,
        fee_rate=DEFAULT_FEE_RATE,
        fee_fixed=DEFAULT_FEE_FIXED,
        purchase_price=release_event.price,
        shipping_cost=None,
        travel_cost=None,
        packing_cost=None,
        other_cost=None,
    )

    save_profit_snapshots(
        session,
        event_id=release_event.id,
        scenario_results=scenario_results,
        purchase_price=release_event.price,
        channel_name=channel_name,
    )

    standard_result = next(r for r in scenario_results if r.scenario == ProfitScenario.STANDARD)
    roi = calc_roi(standard_result.displayed_profit, release_event.price)

    score = calc_score(
        displayed_profit_standard=standard_result.displayed_profit,
        roi=roi,
        confidence=observation.confidence,
        deadline_at=release_event.deadline_at,
        match_status=match_status,
    )

    opportunity = (
        session.query(Opportunity)
        .filter_by(event_id=release_event.id, best_channel_name=channel_name)
        .one_or_none()
    )
    if opportunity is None:
        opportunity = Opportunity(
            product_id=product.id,
            event_id=release_event.id,
            best_channel_name=channel_name,
        )
        session.add(opportunity)

    opportunity.product_id = product.id
    opportunity.confidence = observation.confidence
    # scoreはfloat(opportunity_scorer.calc_score参照)。浮動小数点誤差を含んだまま
    # Decimal化しないよう、一度丸めてから文字列経由でDecimal化する。
    opportunity.score = Decimal(str(round(score, 4)))
    opportunity.match_status = match_status
    session.flush()

    return OpportunityBuildResult(
        opportunity=opportunity,
        standard_displayed_profit=standard_result.displayed_profit,
        standard_excluded_cost_items=standard_result.excluded_cost_items,
    )
