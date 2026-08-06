"""ポケセン新商品一覧(段階2、タスク22)で本番DBに書き込まれた実データ
(Source「ポケモンセンターオンライン」・Product 275件・ReleaseEvent 275件)を使い、
一番くじで確認した「重複防止が事実上exact_match(文字列完全一致)のみに依存する」
限界(tests/integration/test_incident_recollection_dedup.py、CLAUDE.md 13.1節)と
比べて、ポケセンはJAN識別子を持つ分(実データで270/275件)重複防止が強く効くかを
実データで確認する(ユーザー依頼)。

商品コードの先頭2桁による分類(app/collectors/sources/pokemon_center_online.py参照)
により、270件はJAN識別子ありでcalc_match_score()のJAN一致(+100、単独でAUTO_MATCH
閾値90を超える)の恩恵を受けられるが、残り5件("99"始まり等のinternal_code判定、
実データではNintendo Switch本体同梱版等)は一番くじと同じく識別子を持たず、
fuzzy matchingの限界(名前類似度は上限+40点)をそのまま引き継ぐ。「ポケセンは常に
一番くじより重複防止が強い」という単純化した理解を避けるため、この非対称性も
あわせて記録する。
"""

from app.collectors.base import NormalizedItem, ParsedItem
from app.db.models import Product, ProductIdentifier, ReleaseEvent, Source
from app.domain.enums import MatchStatus
from app.pipeline.ingest import ingest_normalized_item

POKEMON_CENTER_COLLECTOR_KEY = "pokemon_center_online"  # app/scheduler/tasks.py:POKEMON_CENTER_SOURCE_COLLECTOR_KEYと同じ値


def _load_source_and_events(db_session) -> tuple[Source, list[ReleaseEvent]]:
    source = db_session.query(Source).filter_by(collector_key=POKEMON_CENTER_COLLECTOR_KEY).one_or_none()
    assert source is not None, (
        "本番Source「ポケモンセンターオンライン」(collector_key=pokemon_center_online)が"
        "見つからない。タスク22(段階2)のrun_pokemon_center_collector()を先に本番実行しておく前提。"
    )
    events = (
        db_session.query(ReleaseEvent)
        .join(Product, Product.id == ReleaseEvent.product_id)
        .filter(ReleaseEvent.source_id == source.id, Product.deleted_at.is_(None))
        .all()
    )
    assert len(events) > 0, "対象のReleaseEventが0件(想定と異なる本番データ状態)"
    return source, events


def _identifiers_for(db_session, product_id) -> dict[str, str]:
    return {
        pi.type.value: pi.value
        for pi in db_session.query(ProductIdentifier).filter_by(product_id=product_id).all()
    }


def _normalized_item_for_recollection(
    product: Product, event: ReleaseEvent, identifiers: dict[str, str], product_name: str | None = None
) -> NormalizedItem:
    name = product_name if product_name is not None else product.name
    parsed = ParsedItem(
        raw_title=name,
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
        product_name=name,
        identifiers=identifiers,
        category_hint="ポケモンカードゲーム",
        brand_hint="ポケモン",
        parsed=parsed,
        confidence_hint="B",
        fulfillment_type=event.fulfillment_type,
        region_override=event.region_override,
        region_display_text=event.region_display_text,
        region_source=event.region_source,
    )


def test_recollecting_all_pokemon_center_products_does_not_duplicate(db_session):
    """275件全件について、同一内容の再収集(定期実行の巡回2回目相当)が
    exact_match/JAN一致いずれかの経路でAUTO_MATCH以上となり、Product/ReleaseEventの
    いずれも増えないことを確認する。"""
    source, events = _load_source_and_events(db_session)

    before_product_count = db_session.query(Product).filter(Product.deleted_at.is_(None)).count()
    before_event_count = db_session.query(ReleaseEvent).filter(ReleaseEvent.source_id == source.id).count()

    for event in events:
        product = db_session.query(Product).filter_by(id=event.product_id).one()
        identifiers = _identifiers_for(db_session, product.id)
        item = _normalized_item_for_recollection(product, event, identifiers)

        resulting_event, match_status = ingest_normalized_item(db_session, source, item)

        assert match_status in (MatchStatus.AUTO_MATCH, MatchStatus.HIGH_PROBABILITY_MATCH), (
            f"'{product.name}' の再収集がAUTO_MATCH/HIGH_PROBABILITY_MATCHにならず "
            f"match_status={match_status}になった(重複Product作成のリスク)"
        )
        assert resulting_event.product_id == product.id
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


def test_title_variation_still_auto_matches_when_jan_identifier_present(db_session):
    """一番くじの限界(test_incident_recollection_dedup.py::
    test_recollecting_with_whitespace_variation_relies_entirely_on_exact_match_path)
    との対比: JAN識別子を持つポケセン商品(実データで270/275件)は、商品名が全く
    別物に変わってもJAN一致(+100、calc_match_score()、単独でAUTO_MATCH閾値90を
    超える)により重複を防げることを実データで確認する。"""
    source, events = _load_source_and_events(db_session)

    jan_event = None
    identifiers: dict[str, str] = {}
    for event in events:
        candidate = _identifiers_for(db_session, event.product_id)
        if "jan" in candidate:
            jan_event = event
            identifiers = candidate
            break
    assert jan_event is not None, "JAN識別子を持つReleaseEventが見つからない(想定と異なる本番データ状態)"

    product = db_session.query(Product).filter_by(id=jan_event.product_id).one()
    item = _normalized_item_for_recollection(
        product, jan_event, identifiers, product_name="全く別の表記になった商品名テスト"
    )

    _, match_status = ingest_normalized_item(db_session, source, item)

    assert match_status == MatchStatus.AUTO_MATCH, (
        f"JAN一致があるにもかかわらずmatch_status={match_status}になった(想定外)"
    )


def test_title_variation_without_identifier_falls_back_to_needs_review_like_ichiban_kuji(db_session):
    """一方、識別子を持たない実データ5件(商品コード先頭2桁が45/49以外、
    internal_code判定)は一番くじと同じ限界を引き継ぐ(全角スペース1文字のような
    軽微な表記ゆれでもNEEDS_REVIEWへ落ちる、CLAUDE.md 13.1節参照)。
    「ポケセンは常に一番くじより重複防止が強い」という誤解を避けるため、
    この非対称性も記録する。"""
    source, events = _load_source_and_events(db_session)

    no_id_event = None
    for event in events:
        candidate = _identifiers_for(db_session, event.product_id)
        if not candidate:
            no_id_event = event
            break
    assert no_id_event is not None, "識別子を持たないReleaseEventが見つからない(想定と異なる本番データ状態)"

    product = db_session.query(Product).filter_by(id=no_id_event.product_id).one()
    item = _normalized_item_for_recollection(product, no_id_event, {}, product_name=product.name + "　")

    _, match_status = ingest_normalized_item(db_session, source, item)

    assert match_status == MatchStatus.NEEDS_REVIEW, (
        f"識別子無し商品の表記ゆれでmatch_status={match_status}になった(想定と異なる)"
    )
