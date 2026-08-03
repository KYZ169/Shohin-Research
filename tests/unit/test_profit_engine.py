"""Profit Engine(app/profit/profit_engine.py)の純粋関数テスト。

技術分析レポート12.5の具体例をそのままテストケースとして実装する(実装仕様書 Prompt 5)。
"""

from decimal import Decimal

from app.domain.enums import ProfitScenario
from app.profit.profit_engine import ExcludedCostItem, calc_displayed_profit, calc_profit_scenarios, calc_roi

# 技術分析レポート12.5の前提: 想定販売価格5,000円、手数料率10%(固定費0円)、
# 送料700円、仕入価格2,800円、梱包費100円、交通費0円、その他費用0円
BASE_KWARGS = dict(
    fee_rate=Decimal("0.10"),
    fee_fixed=Decimal("0"),
    purchase_price=Decimal("2800"),
    shipping_cost=Decimal("700"),
    travel_cost=Decimal("0"),
    packing_cost=Decimal("100"),
    other_cost=Decimal("0"),
)


def test_calc_displayed_profit_matches_12_5_standard_scenario():
    """手数料500円、net_profit = 5,000-500-700-2,800-0-100-0 = 900円。"""
    displayed_profit, excluded = calc_displayed_profit(sale_price=Decimal("5000"), **BASE_KWARGS)

    assert displayed_profit == Decimal("900")
    assert excluded == []


def test_calc_profit_scenarios_matches_12_5_pessimistic_and_optimistic():
    """悲観(85%): 4,250円→225円。楽観(110%): 5,500円→1,350円。"""
    results = calc_profit_scenarios(base_sale_price=Decimal("5000"), **BASE_KWARGS)
    by_scenario = {r.scenario: r for r in results}

    assert by_scenario[ProfitScenario.PESSIMISTIC].displayed_profit == Decimal("225")
    assert by_scenario[ProfitScenario.STANDARD].displayed_profit == Decimal("900")
    assert by_scenario[ProfitScenario.OPTIMISTIC].displayed_profit == Decimal("1350")
    assert all(r.excluded_cost_items == [] for r in results)


def test_calc_displayed_profit_excludes_unconfirmed_shipping_cost_without_deducting_it():
    kwargs = dict(BASE_KWARGS)
    kwargs["shipping_cost"] = None

    displayed_profit, excluded = calc_displayed_profit(sale_price=Decimal("5000"), **kwargs)

    # 送料700円を引かない: 5,000-500-2,800-100 = 1,600円
    assert displayed_profit == Decimal("1600")
    assert excluded == [ExcludedCostItem.SHIPPING_UNKNOWN]


def test_calc_displayed_profit_excludes_all_unconfirmed_costs_simultaneously():
    displayed_profit, excluded = calc_displayed_profit(
        sale_price=Decimal("5000"),
        fee_rate=Decimal("0.10"),
        fee_fixed=Decimal("0"),
        purchase_price=Decimal("2800"),
        shipping_cost=None,
        travel_cost=None,
        packing_cost=None,
        other_cost=None,
    )

    # 手数料500円と仕入価格2,800円のみ確定: 5,000-500-2,800 = 1,700円
    assert displayed_profit == Decimal("1700")
    assert excluded == [
        ExcludedCostItem.SHIPPING_UNKNOWN,
        ExcludedCostItem.TRAVEL_UNKNOWN,
        ExcludedCostItem.PACKING_COST_UNKNOWN,
        ExcludedCostItem.OTHER_COST_UNKNOWN,
    ]


def test_calc_displayed_profit_can_be_negative_when_costs_exceed_sale_price():
    """CLAUDE.md最重要方針2: 損失も隠さずそのまま表示する(補正しない)。"""
    displayed_profit, excluded = calc_displayed_profit(
        sale_price=Decimal("1000"),
        fee_rate=Decimal("0.10"),
        fee_fixed=Decimal("0"),
        purchase_price=Decimal("2800"),
        shipping_cost=Decimal("700"),
        travel_cost=Decimal("0"),
        packing_cost=Decimal("100"),
        other_cost=Decimal("0"),
    )

    assert displayed_profit < 0
    assert excluded == []


def test_excluded_cost_item_value_is_japanese_label_and_name_is_identifier():
    """実装仕様書1.4: .valueは表示ラベル(Discord Embed用)、.nameはDB保存用の識別子。"""
    assert ExcludedCostItem.SHIPPING_UNKNOWN.value == "送料不明"
    assert ExcludedCostItem.SHIPPING_UNKNOWN.name == "SHIPPING_UNKNOWN"


def test_calc_roi_matches_12_5_example():
    """ROI = 900 / 2,800 ≈ 32.1%。"""
    roi = calc_roi(displayed_profit=Decimal("900"), purchase_price=Decimal("2800"))

    assert roi == Decimal("0.3214")


def test_calc_roi_returns_none_when_purchase_price_is_zero():
    assert calc_roi(displayed_profit=Decimal("900"), purchase_price=Decimal("0")) is None
