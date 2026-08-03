"""End-to-Endパイプラインテスト(技術分析レポート18章 Issue#10「収集→通知までの
一気通貫」相当、CLAUDE.mdタスク13)。

収集(Collector.parse/normalize)→DB反映(ingest)→相場照合(market_matching)→
Profit Engine/Opportunity Scorer(opportunity_pipeline)→通知判定(notify)までを
実データではなくFixtureパターンに基づくHTMLで一気通貫させる。

【シナリオの構成について】
1kuji.com/bandaispirits.co.jpのFixture(tests/fixtures/html/)とsuruga_yaのFixture
(tests/fixtures/html/suruga_ya_kaitori_search_20260803.md)は対象商品が重ならない
(前者は一番くじ、後者はトレーディングカード)ため、E2Eの「同一商品が仕入れ側・
相場側の両方に登場する」シナリオを成立させるには、いずれか一方のFixtureパターンを
流用した構成が必要になる。ここでは:

- 仕入れ側: tests/unit/test_ichiban_kuji_collector.pyのBANDAISPIRITS_NORMAL_PRICE_ITEM_HTML
  (bandaispirits_detail_20260803.md 注記2の価格フォーマットと、実際のFixtureの
  「新着商品」欄に実在する商品名「一番くじ しぐれうい 第2弾（仮）」を組み合わせた
  合成テストHTML。タスク4で作成・レビュー済み)をそのまま再利用する。
- 相場側: suruga_ya_kaitori_search_20260803.md注記6で確認済みの検索結果テーブル
  構造に、同じ商品名で買取価格を付けた行を1件追加した合成HTMLを用いる。

いずれも実サイトの生HTMLではなくFixtureパターンに基づく構成であり、実データでの
最終検証はConoHa VPS環境で別途必要(タスク4・6のモジュールdocstring参照)。
"""

from datetime import datetime
from decimal import Decimal

import pytest
import redis as redis_module

from app.collectors.markets.suruga_ya import SurugaYaCollector
from app.collectors.sources.ichiban_kuji import IchibanKujiCollector
from app.config import settings
from app.core.time import JST
from app.db.models import Opportunity, Product, ProfitSnapshot, ReleaseEvent, Shop, Source
from app.domain.enums import FulfillmentType, MatchStatus
from app.notification.dedupe import DedupeDecision
from app.pipeline.ingest import ingest_normalized_item
from app.pipeline.market_matching import match_observation_to_product
from app.pipeline.notify import evaluate_notification
from app.pipeline.opportunity_pipeline import build_and_score_opportunity

# tests/unit/test_ichiban_kuji_collector.py と同一のFixtureパターン(モジュールdocstring参照)
BANDAISPIRITS_DETAIL_HTML = """
<html><body>
<h1>PRODUCTS INFORMATION商品情報</h1>
<h2>一番くじ しぐれうい 第2弾（仮）</h2>
<h3>商品詳細</h3>
<p>価格</p>
<p>1回790円(税10％込)</p>
<p>発売日</p>
<p>2026年09月05日(土)</p>
</body></html>
"""

# suruga_ya_kaitori_search_20260803.md注記6の検索結果テーブル構造を踏襲した合成行
SURUGA_YA_SEARCH_RESULTS_HTML = """
<html><body><table>
<tr><th>種類/タイトル</th><th>買取価格</th><th>詳細</th></tr>
<tr>
<td>一番くじ/BANDAI SPIRITS <br>
<a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU999001">一番くじ しぐれうい 第2弾（仮）</a></td>
<td>1,200円</td>
<td><a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU999001">詳細</a></td>
</tr>
</table></body></html>
"""


@pytest.fixture()
def redis_client():
    client = redis_module.Redis.from_url(settings.redis_url, decode_responses=True)
    yield client
    client.close()


def test_collect_to_notification_pipeline_end_to_end(db_session, redis_client):
    # --- 1. 収集(仕入れ側): IchibanKujiCollectorの実parse()ロジックでbandaispirits詳細ページを解析 ---
    ichiban_collector = IchibanKujiCollector()
    parsed_items = [
        ichiban_collector._parse_bandaispirits_detail(
            _raw_html(
                "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=shigureui2",
                BANDAISPIRITS_DETAIL_HTML,
            )
        )
    ]
    normalized_items = ichiban_collector.normalize(parsed_items)
    assert len(normalized_items) == 1
    normalized_item = normalized_items[0]
    assert normalized_item.product_name == "一番くじ しぐれうい 第2弾（仮）"
    assert normalized_item.parsed.price == Decimal("790")

    # --- 2. 収集(相場側): SurugaYaCollectorの実parse_observations()ロジックで検索結果を解析 ---
    suruga_collector = SurugaYaCollector()
    observations = suruga_collector.parse_observations(
        _raw_html("https://www.suruga-ya.jp/kaitori/search_buy?category=501&search_word=一番くじ", SURUGA_YA_SEARCH_RESULTS_HTML)
    )
    assert len(observations) == 1
    observation = observations[0]
    assert observation.extra["title"] == "一番くじ しぐれうい 第2弾（仮）"
    assert observation.amount == Decimal("1200")
    assert observation.confidence == "B"

    # --- 3. DB反映(仕入れ側): Product/Shop/ReleaseEventの作成 ---
    source = Source(
        name="一番くじ公式(bandaispirits.co.jp)",
        base_url="https://www.bandaispirits.co.jp",
        collector_key="ichiban_kuji",
    )
    db_session.add(source)
    db_session.flush()

    release_event, ingest_match_status = ingest_normalized_item(db_session, source, normalized_item)
    db_session.flush()

    assert release_event.price == Decimal("790")
    assert release_event.fulfillment_type == FulfillmentType.ONLINE_SHIPPING  # デフォルトフォールバック
    assert ingest_match_status == MatchStatus.DIFFERENT_PRODUCT  # 初回登録なので新規Product

    # このコードベースではORMのrelationship()を定義していない(FKカラムのみ)ため、
    # product_id/shop_idで明示的に取得する。
    product = db_session.get(Product, release_event.product_id)
    shop = db_session.get(Shop, release_event.shop_id)

    # --- 4. 相場照合: MarketObservationのタイトルを仕入れ側のProductへ照合 ---
    match = match_observation_to_product(db_session, observation)
    assert match is not None
    matched_product, market_match_status = match
    assert matched_product.id == product.id
    # 商品名は完全一致だがJAN/型番一致・発売日一致等の加点要素が無いため
    # NEEDS_REVIEW相当になる(app/pipeline/market_matching.pyモジュールdocstring参照)。
    assert market_match_status == MatchStatus.NEEDS_REVIEW

    # --- 5. Profit Engine + Opportunity Scorer ---
    build_result = build_and_score_opportunity(
        db_session,
        release_event=release_event,
        product=product,
        observation=observation,
        match_status=market_match_status,
        channel_name="suruga_ya",
    )
    db_session.flush()

    assert build_result is not None
    # 手数料0円(買取価格提示制)、送料/交通費/梱包費/その他はすべて未確定:
    # displayed_profit = 1200(標準シナリオの売却価格) - 790(仕入価格) = 410円
    assert build_result.standard_displayed_profit == Decimal("410")
    assert {item.name for item in build_result.standard_excluded_cost_items} == {
        "SHIPPING_UNKNOWN",
        "TRAVEL_UNKNOWN",
        "PACKING_COST_UNKNOWN",
        "OTHER_COST_UNKNOWN",
    }

    opportunity = db_session.query(Opportunity).filter_by(event_id=release_event.id).one()
    assert opportunity.match_status == MatchStatus.NEEDS_REVIEW
    assert opportunity.confidence == "B"
    assert opportunity.score > 0  # NEEDS_REVIEWペナルティ(-0.05)込みでも正のスコア

    profit_snapshots = db_session.query(ProfitSnapshot).filter_by(event_id=release_event.id).all()
    assert len(profit_snapshots) == 3  # 悲観/標準/楽観の3シナリオ

    # --- 6. 通知判定: 地域フィルタ→dedupe→Embed組み立て ---
    event_id = str(release_event.id)
    try:
        decision = evaluate_notification(
            redis_client,
            release_event=release_event,
            shop=shop,
            product=product,
            source=source,
            observation=observation,
            standard_displayed_profit=build_result.standard_displayed_profit,
            standard_excluded_cost_items=build_result.standard_excluded_cost_items,
            match_status=market_match_status,
            target_prefectures=set(),
        )

        assert decision.should_send is True
        assert decision.dedupe_result.decision == DedupeDecision.NEW
        assert decision.embed["title"] == "一番くじ しぐれうい 第2弾（仮）"
        assert "想定利益: ¥410" in _field(decision.embed, "想定利益")["value"]
        assert "送料不明" in _field(decision.embed, "想定利益")["value"]
        assert _field(decision.embed, "締切日時")["value"].startswith("締切は公式サイトでご確認ください")
        # market_match_status=NEEDS_REVIEWのため「商品照合」フィールドが出て、
        # 地域側の「対象地域」フィールドとは別に要確認情報が表示されること
        assert _field(decision.embed, "商品照合")["value"] == "要確認・別商品の可能性があります"

        # 同一内容で再評価すると、dedupe判定によりUNCHANGEDとなり送信しない
        second_decision = evaluate_notification(
            redis_client,
            release_event=release_event,
            shop=shop,
            product=product,
            source=source,
            observation=observation,
            standard_displayed_profit=build_result.standard_displayed_profit,
            standard_excluded_cost_items=build_result.standard_excluded_cost_items,
            match_status=market_match_status,
            target_prefectures=set(),
        )
        assert second_decision.should_send is False
        assert second_decision.dedupe_result.decision == DedupeDecision.UNCHANGED
    finally:
        redis_client.delete(f"notification:{event_id}")


def _field(embed: dict, name: str) -> dict:
    return next(f for f in embed["fields"] if f["name"] == name)


def test_ingest_normalized_item_is_idempotent_on_repeated_collection(db_session):
    """CLAUDE.mdタスク12: Celery Beatによる定期実行で同じイベントを繰り返し収集しても、
    release_eventsのUNIQUE制約(product_id, shop_id, event_type, start_at)に
    ひっかかってIntegrityErrorにならず、既存行が更新されることを確認する。
    """
    ichiban_collector = IchibanKujiCollector()
    parsed_item = ichiban_collector._parse_bandaispirits_detail(
        _raw_html(
            "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=shigureui2",
            BANDAISPIRITS_DETAIL_HTML,
        )
    )
    normalized_item = ichiban_collector.normalize([parsed_item])[0]

    source = Source(
        name="一番くじ公式(bandaispirits.co.jp)",
        base_url="https://www.bandaispirits.co.jp",
        collector_key="ichiban_kuji",
    )
    db_session.add(source)
    db_session.flush()

    first_event, _ = ingest_normalized_item(db_session, source, normalized_item)
    db_session.flush()
    first_event_id = first_event.id

    # 同じ内容を再度収集(定期実行の2回目相当)。価格が変わったと仮定して更新を検証する。
    normalized_item.parsed.price = Decimal("690")
    second_event, _ = ingest_normalized_item(db_session, source, normalized_item)
    db_session.flush()

    assert second_event.id == first_event_id  # 新規行ではなく既存行が更新される
    assert second_event.price == Decimal("690")

    all_events = db_session.query(ReleaseEvent).filter_by(product_id=first_event.product_id).all()
    assert len(all_events) == 1


def _raw_html(url: str, html: str):
    from app.collectors.base import RawFetchResult

    return RawFetchResult(url=url, status_code=200, html=html, fetched_at=datetime.now(tz=JST))
