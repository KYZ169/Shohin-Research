"""save_profit_snapshots()の永続化テスト(実DB接続、tests/integration/conftest.py参照)。

タスク2で作成したprofit_snapshotsテーブルへ、シナリオごとに正しく行が保存されること、
excluded_cost_itemsがEnumの.name(識別子)で保存されること(app/profit/profit_engine.py
モジュールdocstring参照)を確認する。
"""

from decimal import Decimal

from app.db.models import Product, ReleaseEvent, Shop, Source
from app.db.models.profit_snapshot import ProfitSnapshot
from app.domain.enums import ProfitScenario, ShopGranularity, ShopType, SupportedEventType
from app.profit.profit_engine import calc_profit_scenarios, save_profit_snapshots


def _make_release_event(db_session) -> ReleaseEvent:
    source = Source(name="テスト情報源", base_url="https://example.com", collector_key="test_source")
    shop = Shop(name="テスト店舗", type=ShopType.STORE, granularity=ShopGranularity.NATIONAL_CHAIN)
    product = Product(name="テスト商品")
    db_session.add_all([source, shop, product])
    db_session.flush()

    event = ReleaseEvent(
        product_id=product.id,
        shop_id=shop.id,
        source_id=source.id,
        event_type=SupportedEventType.LOTTERY,
        product_url="https://example.com/products/test",
    )
    db_session.add(event)
    db_session.flush()
    return event


def test_save_profit_snapshots_persists_one_row_per_scenario(db_session):
    event = _make_release_event(db_session)

    scenario_results = calc_profit_scenarios(
        base_sale_price=Decimal("5000"),
        fee_rate=Decimal("0.10"),
        fee_fixed=Decimal("0"),
        purchase_price=Decimal("2800"),
        shipping_cost=Decimal("700"),
        travel_cost=Decimal("0"),
        packing_cost=Decimal("100"),
        other_cost=Decimal("0"),
    )

    save_profit_snapshots(
        db_session,
        event_id=event.id,
        scenario_results=scenario_results,
        purchase_price=Decimal("2800"),
        channel_name="suruga_ya",
    )
    db_session.flush()

    rows = db_session.query(ProfitSnapshot).filter_by(event_id=event.id).all()
    assert len(rows) == 3
    assert {row.scenario for row in rows} == {
        ProfitScenario.PESSIMISTIC,
        ProfitScenario.STANDARD,
        ProfitScenario.OPTIMISTIC,
    }

    standard_row = next(row for row in rows if row.scenario == ProfitScenario.STANDARD)
    assert standard_row.displayed_profit == Decimal("900")
    assert standard_row.excluded_cost_items == []
    assert standard_row.has_unconfirmed_cost is False
    assert standard_row.roi == Decimal("0.3214")
    assert standard_row.channel_name == "suruga_ya"


def test_save_profit_snapshots_stores_excluded_cost_item_identifiers_not_japanese_labels(db_session):
    """実装仕様書1.4のjsonb例(["SHIPPING_UNKNOWN",...])どおり、識別子で保存されること。"""
    event = _make_release_event(db_session)

    scenario_results = calc_profit_scenarios(
        base_sale_price=Decimal("5000"),
        fee_rate=Decimal("0.10"),
        fee_fixed=Decimal("0"),
        purchase_price=Decimal("2800"),
        shipping_cost=None,
        travel_cost=Decimal("0"),
        packing_cost=Decimal("100"),
        other_cost=Decimal("0"),
    )

    save_profit_snapshots(
        db_session, event_id=event.id, scenario_results=scenario_results, purchase_price=Decimal("2800")
    )
    db_session.flush()

    rows = db_session.query(ProfitSnapshot).filter_by(event_id=event.id).all()
    for row in rows:
        assert row.excluded_cost_items == ["SHIPPING_UNKNOWN"]
        assert row.has_unconfirmed_cost is True
