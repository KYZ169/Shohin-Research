"""JAN/型番によるProduct照合が実際に機能することの検証(実DB接続、conftest.py参照)。

app/matcher/product_matcher.py:calc_match_score()はJAN一致(+100)/型番一致(+80)の
スコアリングを技術分析レポート11.2の当初から実装していたが、呼び出し側
(app/pipeline/ingest.py・app/pipeline/market_matching.py)がproduct_identifiersへの
永続化・読み込みを一度も配線していなかったため、実質的に一度も機能していなかった
(2026-08-05発覚、詳細はapp/pipeline/ingest.pyモジュールdocstring参照)。

このファイルは「識別子一致による自動一致が実際に発生するか」を、完全一致しない
商品名同士(=識別子一致以外では閾値に届かない組み合わせ)を使って直接検証する。
"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.collectors.base import RawFetchResult
from app.collectors.markets.base import MarketDataType, MarketObservation
from app.collectors.markets.suruga_ya import CATEGORY_GUNDAM, JST, SurugaYaCollector
from app.db.models import Product, ProductIdentifier, Source
from app.domain.enums import MatchStatus, ProductIdentifierType
from app.pipeline.ingest import match_or_create_product
from app.pipeline.market_matching import match_observation_to_product

GUNDAM_RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_gundam.html"


def test_jan_match_alone_reaches_auto_match_threshold(db_session):
    """JAN一致(+100)は単独でAUTO_MATCH_THRESHOLD(90)を超えるため、商品名が
    全く似ていなくても同一Productとして自動一致することを確認する
    (app/matcher/product_matcher.pyの閾値・スコアリング自体は変更していない)。
    """
    jan = "4573102720306"  # raw_suruga_ya_gundam.htmlで実際に確認された実在のJAN

    first_product, first_status = match_or_create_product(
        db_session, "1/144 HG アリュゼウス", identifiers={"jan": jan}
    )
    assert first_status == MatchStatus.DIFFERENT_PRODUCT  # 初回は候補が無いので新規作成

    # 商品名は意図的に全く別物にする(名前の類似度だけではNEEDS_REVIEW未満になる組み合わせ)。
    second_product, second_status = match_or_create_product(
        db_session, "ぜんぜん違う商品名テスト", identifiers={"jan": jan}
    )

    assert second_status == MatchStatus.AUTO_MATCH
    assert second_product.id == first_product.id  # 新規Productが別途作られていないこと

    identifiers = db_session.query(ProductIdentifier).filter_by(product_id=first_product.id).all()
    assert any(i.type == ProductIdentifierType.JAN and i.value == jan for i in identifiers)


def test_model_number_match_alone_reaches_high_probability_not_auto_match(db_session):
    """型番一致(+80)は単独ではAUTO_MATCH_THRESHOLD(90)未満・
    HIGH_PROBABILITY_MATCH_THRESHOLD(70)以上のため、HIGH_PROBABILITY_MATCHに
    なる(AUTO_MATCHにはならない)ことを確認する。既存のmatch_status閾値を
    変更していないことの回帰確認を兼ねる。
    """
    model = "5072030"  # raw_suruga_ya_gundam.htmlで実際に確認された実在の型番

    first_product, _ = match_or_create_product(
        db_session, "1/144 HG アリュゼウス", identifiers={"model": model}
    )
    second_product, second_status = match_or_create_product(
        db_session, "ぜんぜん違う商品名テスト2", identifiers={"model": model}
    )

    assert second_status == MatchStatus.HIGH_PROBABILITY_MATCH
    assert second_product.id == first_product.id

    identifiers = db_session.query(ProductIdentifier).filter_by(product_id=first_product.id).all()
    assert any(i.type == ProductIdentifierType.MODEL and i.value == model for i in identifiers)


def test_needs_review_match_does_not_persist_identifiers(db_session):
    """AUTO_MATCH/HIGH_PROBABILITY_MATCHに届かないNEEDS_REVIEW相当のマッチでは、
    誤って別商品にJAN/型番を紐付けて以降の照合を汚染しないよう、
    product_identifiersへは書き込まないことを確認する(ユーザー確認済みの方針)。
    """
    existing_product = Product(name="似ているようで別の商品A")
    db_session.add(existing_product)
    db_session.flush()

    _, status = match_or_create_product(
        db_session, "似ているようで別の商品B", identifiers={"jan": "4570000000009"}
    )

    # 商品名類似度0.92(+40)のみでNEEDS_REVIEW(40〜69点)になる組み合わせであることを
    # 直接確認する(calc_match_scoreで事前検証済みの数値)。
    assert status == MatchStatus.NEEDS_REVIEW

    count = (
        db_session.query(ProductIdentifier)
        .filter_by(product_id=existing_product.id, type=ProductIdentifierType.JAN, value="4570000000009")
        .count()
    )
    assert count == 0


def test_market_observation_jan_match_persists_identifier_only_above_threshold(db_session):
    """market_matching.match_observation_to_product()側でも同じ方針
    (AUTO_MATCH/HIGH_PROBABILITY_MATCHのときのみ永続化)が働くことを確認する。"""
    jan = "4573102725370"  # raw_suruga_ya_gundam.htmlで実際に確認された実在のJAN

    source_product, _ = match_or_create_product(
        db_session, "1/144 HG メッサーM01型", identifiers={"jan": jan}
    )

    observation = MarketObservation(
        product_ref="test",
        data_type=MarketDataType.BUYBACK_PRICE,
        amount=Decimal("1700"),
        count=None,
        observed_at=datetime.now(tz=JST),
        source_url="https://www.suruga-ya.jp/kaitori/kaitori_detail/999999999",
        confidence="B",
        extra={"title": "全く異なるタイトルの相場データ", "jan": jan},
    )

    match = match_observation_to_product(db_session, observation)
    assert match is not None
    matched_product, match_status = match
    assert matched_product.id == source_product.id
    assert match_status == MatchStatus.AUTO_MATCH

    identifiers = db_session.query(ProductIdentifier).filter_by(product_id=source_product.id).all()
    assert any(i.type == ProductIdentifierType.JAN and i.value == jan for i in identifiers)


def test_real_gundam_fixture_jan_and_model_drive_auto_match_end_to_end(db_session):
    """実Fixture(raw_suruga_ya_gundam.html)からSurugaYaCollectorで実際に抽出した
    JAN/型番の値を使い、一度目のingest(商品名Aで新規Product作成→識別子永続化)→
    二度目の相場観測(商品名が異なるB、同じJAN/型番)が自動的に同一Productへ
    マッチすることをend-to-endで確認する。ソース側Collector(一番くじ/ポケセン)は
    現状JAN/型番を一切抽出しない(normalize()がidentifiers={}固定)ため、
    「実データで実際に発生するケースがあるか」を検証するには、実Fixtureから
    取れた本物の識別子の値を使いつつ、2件目の商品名だけ意図的に変える構成が
    現実的な検証方法になる(CLAUDE.md 3節・4節参照)。
    """
    html = GUNDAM_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")
    raw = RawFetchResult(
        url="https://www.suruga-ya.jp/kaitori/search_buy?category=5010401",
        status_code=200,
        html=html,
        fetched_at=datetime.now(tz=JST),
    )

    collector = SurugaYaCollector(category=CATEGORY_GUNDAM)
    observations = collector.parse_observations(raw)

    # 型番・JANの両方が取れている実在の1件を使う(アリュゼウス、CLAUDE.md 1.3参照)。
    real = next(o for o in observations if o.extra["management_number"] == "603227273")
    assert real.extra["jan"] == "4573102720306"
    assert real.extra["model_number"] == "5072030"

    source = Source(name="テスト用駿河屋", base_url="https://www.suruga-ya.jp", collector_key="test_suruga_ya")
    db_session.add(source)
    db_session.flush()

    # 1件目: 実Fixtureの本物の商品名で新規Product作成(識別子も永続化される)。
    first_product, first_status = match_or_create_product(
        db_session,
        real.extra["title"],
        identifiers={"jan": real.extra["jan"], "model": real.extra["model_number"]},
    )
    assert first_status == MatchStatus.DIFFERENT_PRODUCT

    # 2件目: 商品名だけ差し替えた相場観測値(同じ実在のJAN/型番)。
    second_observation_extra = dict(real.extra)
    second_observation_extra["title"] = "別ルートで取得したので表記が異なる同一商品"
    second_observation = MarketObservation(
        product_ref=real.product_ref,
        data_type=real.data_type,
        amount=real.amount,
        count=real.count,
        observed_at=real.observed_at,
        source_url=real.source_url,
        confidence=real.confidence,
        extra=second_observation_extra,
    )

    match = match_observation_to_product(db_session, second_observation)
    assert match is not None
    matched_product, match_status = match
    assert matched_product.id == first_product.id
    assert match_status == MatchStatus.AUTO_MATCH  # JAN一致(+100)だけでAUTO_MATCH閾値を超える
