"""Profit Engine(実装仕様書2章・Prompt 5、技術分析レポート12章)。

CLAUDE.md最重要方針1: 未確定コスト(送料・交通費等)は金額計算に一切含めない。
確定コストのみでdisplayed_profitを計算し、未確定項目はexcluded_cost_itemsとして
明示する(減点・按分などの補正は行わない)。

calc_displayed_profit()/calc_profit_scenarios()はregion_resolver.py/product_matcher.pyと
同様にDBセッションに依存しない純粋関数として実装する。profit_snapshotsテーブルへの
save_profit_snapshots()のみSQLAlchemy Sessionを受け取る(Prompt 5で明示的に
「保存関数も実装してください」と指示されているため)。

【excluded_cost_itemsのシリアライズ方針】
実装仕様書1.4の対応表はenum識別子(例: SHIPPING_UNKNOWN)と日本語表示ラベル
(例: 送料不明)を別カラムとして示しており、profit_snapshots.excluded_cost_itemsの
JSONB例も `["SHIPPING_UNKNOWN","TRAVEL_UNKNOWN"]` という識別子の配列になっている。
一方、実装仕様書4.1のEmbed組み立てコードは `item.value` (日本語ラベル)をそのまま
表示に使っている。この2つを両立させるため、ExcludedCostItemは
`メンバー名(.name)=識別子`・`値(.value)=日本語表示ラベル`という対応にし、
DB保存時は`.name`を、UI表示時は`.value`を使う設計とする。
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from sqlalchemy.orm import Session

from app.db.models.profit_snapshot import ProfitSnapshot
from app.domain.enums import ProfitScenario

__all__ = [
    "ExcludedCostItem",
    "calc_displayed_profit",
    "calc_roi",
    "ScenarioProfitResult",
    "calc_profit_scenarios",
    "save_profit_snapshots",
]

JST = timezone(timedelta(hours=9))


class ExcludedCostItem(str, Enum):
    """実装仕様書1.4の対応表。値(.value)は表示用の日本語ラベル、
    メンバー名(.name)はDB保存用の識別子として使う。"""

    SHIPPING_UNKNOWN = "送料不明"
    SHIPPING_VARIES_BY_STORE = "送料は店舗により異なる"
    TRAVEL_UNKNOWN = "交通費不明(店舗受取のため)"
    TRAVEL_NOT_APPLICABLE_NATIONAL_CHAIN = "交通費不明(全国チェーンのため店舗特定不可)"
    PACKING_COST_UNKNOWN = "梱包費未算出"
    OTHER_COST_UNKNOWN = "その他費用未算出"


def calc_displayed_profit(
    sale_price: Decimal,
    fee_rate: Decimal,
    fee_fixed: Decimal,
    purchase_price: Decimal,
    shipping_cost: Decimal | None,  # None = 未確定
    travel_cost: Decimal | None,  # None = 未確定
    packing_cost: Decimal | None,
    other_cost: Decimal | None,
) -> tuple[Decimal, list[ExcludedCostItem]]:
    """確定している項目のみを引き、未確定項目はexcluded_cost_itemsとして返す。
    金額換算(減点等)は一切行わない(実装仕様書2章)。
    """
    fee = (sale_price * fee_rate + fee_fixed).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    confirmed_deductions = fee + purchase_price
    excluded: list[ExcludedCostItem] = []

    if shipping_cost is not None:
        confirmed_deductions += shipping_cost
    else:
        excluded.append(ExcludedCostItem.SHIPPING_UNKNOWN)

    if travel_cost is not None:
        confirmed_deductions += travel_cost
    else:
        excluded.append(ExcludedCostItem.TRAVEL_UNKNOWN)

    if packing_cost is not None:
        confirmed_deductions += packing_cost
    else:
        excluded.append(ExcludedCostItem.PACKING_COST_UNKNOWN)

    if other_cost is not None:
        confirmed_deductions += other_cost
    else:
        excluded.append(ExcludedCostItem.OTHER_COST_UNKNOWN)

    displayed_profit = (sale_price - confirmed_deductions).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return displayed_profit, excluded


def calc_roi(displayed_profit: Decimal, purchase_price: Decimal) -> Decimal | None:
    """技術分析レポート12.3のROI(displayed_profit / purchase_price)。

    purchase_price が0円の場合は定義できないためNoneを返す(0除算を避ける)。
    calc_displayed_profitの必須引数ではなくprofit_snapshots保存時の付随指標として
    独立して提供する。
    """
    if purchase_price == 0:
        return None
    return (displayed_profit / purchase_price).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


# 技術分析レポート12.4
SCENARIO_ADJUSTMENT: dict[ProfitScenario, Decimal] = {
    ProfitScenario.PESSIMISTIC: Decimal("0.85"),
    ProfitScenario.STANDARD: Decimal("1.00"),
    ProfitScenario.OPTIMISTIC: Decimal("1.10"),
}


@dataclass
class ScenarioProfitResult:
    scenario: ProfitScenario
    displayed_profit: Decimal
    excluded_cost_items: list[ExcludedCostItem]


def calc_profit_scenarios(
    base_sale_price: Decimal,
    fee_rate: Decimal,
    fee_fixed: Decimal,
    purchase_price: Decimal,
    shipping_cost: Decimal | None,
    travel_cost: Decimal | None,
    packing_cost: Decimal | None,
    other_cost: Decimal | None,
) -> list[ScenarioProfitResult]:
    """悲観(85%)/標準(100%)/楽観(110%)の3シナリオでcalc_displayed_profitを計算する
    (実装仕様書 Prompt 5、技術分析レポート12.4の調整係数)。

    未確定コストの扱いはシナリオに関わらず共通(sale_priceのみ変動させる)ため、
    excluded_cost_itemsは3シナリオとも同じ内容になる。
    """
    results: list[ScenarioProfitResult] = []
    for scenario, factor in SCENARIO_ADJUSTMENT.items():
        adjusted_price = (base_sale_price * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        displayed_profit, excluded = calc_displayed_profit(
            sale_price=adjusted_price,
            fee_rate=fee_rate,
            fee_fixed=fee_fixed,
            purchase_price=purchase_price,
            shipping_cost=shipping_cost,
            travel_cost=travel_cost,
            packing_cost=packing_cost,
            other_cost=other_cost,
        )
        results.append(
            ScenarioProfitResult(scenario=scenario, displayed_profit=displayed_profit, excluded_cost_items=excluded)
        )
    return results


def save_profit_snapshots(
    session: Session,
    event_id: uuid.UUID,
    scenario_results: list[ScenarioProfitResult],
    purchase_price: Decimal,
    channel_name: str | None = None,
) -> list[ProfitSnapshot]:
    """profit_snapshotsテーブルへシナリオごとに1レコードずつ保存する。

    実装仕様書1.4: 「シナリオ単位で保持(1イベントにつき最大3レコード)」。
    再計算のたびに新規追加する設計のため、既存レコードのUPSERT/更新は行わない
    (technical report 8.1: 物理削除は運用側のクリーンアップジョブで別途対応)。
    """
    calculated_at = datetime.now(tz=JST)
    rows: list[ProfitSnapshot] = []

    for result in scenario_results:
        row = ProfitSnapshot(
            event_id=event_id,
            channel_name=channel_name,
            scenario=result.scenario,
            displayed_profit=result.displayed_profit,
            roi=calc_roi(result.displayed_profit, purchase_price),
            excluded_cost_items=[item.name for item in result.excluded_cost_items],
            has_unconfirmed_cost=bool(result.excluded_cost_items),
            calculated_at=calculated_at,
        )
        session.add(row)
        rows.append(row)

    return rows
