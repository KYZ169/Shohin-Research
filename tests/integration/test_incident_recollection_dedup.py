"""タスク20の事故(CLAUDE.md 12節)で本番DBに書き込まれた一番くじの実データ
(Source「一番くじ公式」・Product 32件・ReleaseEvent 36件)が、今後の定期実行
(6時間毎のrun_ichiban_kuji_collector)による再収集で重複登録されないことの検証
(実DB接続、conftest.py参照)。

app/pipeline/ingest.py:match_or_create_product()には、商品名が完全一致する場合に
calc_match_score()によるfuzzy matchingを経由せず即座に既存Productへ紐付ける
exact_match経路が実装済み(同モジュールdocstring参照。識別子を持たない商品を
定期再収集した際、商品名の類似度だけではNEEDS_REVIEW止まりで重複Productが
作られる不具合をE2Eテストで発見し追加された経路)。一番くじCollectorは常に
identifiers={}(CLAUDE.md 1.1)であるため、この事故データの再収集がAUTO_MATCHで
正しく既存Productへ吸収されるかどうかは、このexact_match経路が実際に機能するか
そのものの検証になる。

机上のコードレビューではなく、事故によって実際にDBへ書き込まれた本物のProduct名・
ReleaseEventの内容を使い、「もう一度同じページを収集したら」を再現して検証する
(合成データでは「たまたま実データの傾向と違う」を見逃すリスクがあるため)。
"""

from app.collectors.base import NormalizedItem, ParsedItem
from app.db.models import Product, ReleaseEvent, Source
from app.domain.enums import MatchStatus
from app.pipeline.ingest import ingest_normalized_item

ICHIBAN_KUJI_COLLECTOR_KEY = "ichiban_kuji"  # app/scheduler/tasks.py:ICHIBAN_KUJI_SOURCE_COLLECTOR_KEYと同じ値


def _load_incident_source_and_events(db_session) -> tuple[Source, list[ReleaseEvent]]:
    source = db_session.query(Source).filter_by(collector_key=ICHIBAN_KUJI_COLLECTOR_KEY).one_or_none()
    assert source is not None, (
        "本番Source「一番くじ公式」(collector_key=ichiban_kuji)が見つからない。"
        "タスク20の事故で書き込まれたはずのデータが前提と異なる状態でこのテストは検証にならない。"
    )
    events = (
        db_session.query(ReleaseEvent)
        .join(Product, Product.id == ReleaseEvent.product_id)
        .filter(ReleaseEvent.source_id == source.id, Product.deleted_at.is_(None))
        .all()
    )
    assert len(events) > 0, "対象のReleaseEventが0件(事故当時のデータが想定と異なる)"
    return source, events


def _normalized_item_for_recollection(product: Product, event: ReleaseEvent) -> NormalizedItem:
    """既存のProduct/ReleaseEventの内容から、「もう一度同じページをfetch→parse→
    normalizeした場合に得られるはずのNormalizedItem」を再構築する。"""
    parsed = ParsedItem(
        raw_title=product.name,
        price=event.price,
        image_urls=[],
        event_type=event.event_type,
        start_at=event.start_at,
        deadline_at=event.deadline_at,
        announce_at=event.announce_at,
        purchase_limit_at=event.purchase_limit_at,
        apply_url=event.apply_url,
        product_url=event.product_url,
        shop_name=None,
        extra={
            "store_release_at": event.store_release_at,
            "online_release_at": event.online_release_at,
        },
    )
    return NormalizedItem(
        product_name=product.name,
        identifiers={},  # CLAUDE.md 1.1: 一番くじはJAN/型番を一切抽出しない
        category_hint=None,
        brand_hint=None,
        parsed=parsed,
        confidence_hint="B",
        fulfillment_type=event.fulfillment_type,
        region_override=event.region_override,
        region_display_text=event.region_display_text,
        region_source=event.region_source,
    )


def test_recollecting_incident_ichiban_kuji_products_does_not_duplicate(db_session):
    """事故で書き込まれた36件のReleaseEvent全件について、同一内容の再収集
    (ingest_normalized_item()の再実行)がexact_match経路でAUTO_MATCHとなり、
    Product/ReleaseEventのいずれも新規重複を作らないことを確認する。"""
    source, events = _load_incident_source_and_events(db_session)

    before_product_count = db_session.query(Product).filter(Product.deleted_at.is_(None)).count()
    before_event_count = db_session.query(ReleaseEvent).filter(ReleaseEvent.source_id == source.id).count()

    for event in events:
        product = db_session.query(Product).filter_by(id=event.product_id).one()
        item = _normalized_item_for_recollection(product, event)

        resulting_event, match_status = ingest_normalized_item(db_session, source, item)

        assert match_status in (MatchStatus.AUTO_MATCH, MatchStatus.HIGH_PROBABILITY_MATCH), (
            f"'{product.name}' の再収集がAUTO_MATCH/HIGH_PROBABILITY_MATCHにならず "
            f"match_status={match_status}になった(重複Product作成のリスク)"
        )
        assert resulting_event.product_id == product.id, (
            f"'{product.name}' の再収集が別Productに紐づいた(product_id不一致)"
        )
        assert resulting_event.id == event.id, (
            f"'{product.name}' の再収集が既存ReleaseEventを更新せず、"
            f"別のReleaseEventとして扱われた(id不一致、重複作成の疑い)"
        )

    after_product_count = db_session.query(Product).filter(Product.deleted_at.is_(None)).count()
    after_event_count = db_session.query(ReleaseEvent).filter(ReleaseEvent.source_id == source.id).count()

    assert after_product_count == before_product_count, (
        f"再収集によりProductが増えた({before_product_count} -> {after_product_count})"
    )
    assert after_event_count == before_event_count, (
        f"再収集によりReleaseEventが増えた({before_event_count} -> {after_event_count})"
    )


def test_recollecting_with_whitespace_variation_relies_entirely_on_exact_match_path(db_session):
    """match_or_create_product()のexact_match経路(商品名の完全一致)が効かなくなった
    瞬間、一番くじ商品の再収集がAUTO_MATCHにもHIGH_PROBABILITY_MATCHにも届かず
    NEEDS_REVIEWへ落ちることを、事故データのうち1件を使って確認する(現状の実際の
    挙動を記録する回帰テストであり、「直すべきバグ」としては扱わない。閾値・スコア式は
    変更しない。CLAUDE.md 0.5により商品照合の確定ロジック変更は要協議のため)。

    【この結果が意味すること】
    _title_similarity()は比較前に空白を除去するため、全角スペース1文字混入それ自体は
    類似度をほぼ1.0に保つ。しかし識別子(JAN/型番)を持たず(CLAUDE.md 1.1)、
    Productにrelease_dateも保存されない(match_or_create_product()の新規作成経路が
    release_dateを設定しないため)一番くじ商品では、名前類似度による加点が上限+40点
    (calc_match_score()の_name_similarity_score())にしかならず、これは
    NEEDS_REVIEW_THRESHOLD(40)と同値でAUTO_MATCH(90)/HIGH_PROBABILITY_MATCH(70)には
    届かない。つまり一番くじ商品の重複防止は、事実上match_or_create_product()の
    「商品名が文字列として完全一致する場合のみ即座に紐付ける」exact_match経路のみに
    依存しており、fuzzy matching(calc_match_score())側は空白1文字の差ですら
    AUTO_MATCH/HIGH_PROBABILITY_MATCHへ橋渡しできない。サイト側のtitle表記が
    (空白除去では吸収できない形で)将来変わった場合、重複Productが作られる
    リスクがある。
    """
    source, events = _load_incident_source_and_events(db_session)
    event = events[0]
    product = db_session.query(Product).filter_by(id=event.product_id).one()

    item = _normalized_item_for_recollection(product, event)
    item.product_name = product.name + "　"  # 全角スペース1文字の混入(exact_match経路を意図的に外す)

    _, match_status = ingest_normalized_item(db_session, source, item)

    assert match_status == MatchStatus.NEEDS_REVIEW, (
        f"現状の実装の想定と異なりmatch_status={match_status}になった。"
        "このテストはexact_match経路への依存度を可視化する回帰テストのため、"
        "挙動が変わった場合はこのテスト自体とCLAUDE.md 3節の記録を更新すること。"
    )
