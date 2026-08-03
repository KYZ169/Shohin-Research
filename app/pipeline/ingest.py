"""Collector出力(NormalizedItem/MarketObservation)をDBへ反映するパイプライン
(CLAUDE.mdタスク13)。

タスク7(app/matcher/region_resolver.py)・タスク8(app/matcher/product_matcher.py)の
純粋関数は「呼び出し側が候補となる既存商品レコードを渡す形」を前提に、DBセッションに
依存しない設計にしてきた。本モジュールがその「呼び出し側」にあたる、初めてのDB統合
レイヤーとなる。

【Phase0スコープの簡略化(要検証・要TODO)】
- Shop解決: IchibanKujiCollectorのParsedItem.shop_nameは常にNone(店舗別の粒度を
  抽出していない、タスク4参照)ため、source単位で1つの代表Shop(national_chain)を
  割り当てる簡略実装とする。店舗別粒度の抽出はCollector側の将来拡張。
- Product Matcherの候補探索: pg_trgm(タスク2で有効化済み)によるDB側の絞り込みは
  行わず、既存Productを全件取得してcalc_match_score()を総当たりする
  (Phase0のデータ量では問題にならないが、スケールしない。TODO)。
- NEEDS_REVIEW/DIFFERENT_PRODUCTの扱い: manual_review_tasksテーブルが未実装のため、
  要確認相当でも新規Productとして登録する(誤って統合するより安全側、技術分析11.3
  原則1「誤って別商品を統合する方が、統合できず個別表示されるより害が大きい」と整合)。
  将来的には要確認キューへ回す運用に置き換える必要がある(TODO)。
"""

from sqlalchemy.orm import Session

from app.collectors.base import NormalizedItem
from app.db.models import Product, ReleaseEvent, Shop, Source
from app.domain.enums import MatchStatus, ShopGranularity, ShopType
from app.matcher.product_matcher import MatchCandidate, MatchResult, calc_match_score, extract_product_attributes

__all__ = [
    "get_or_create_default_shop",
    "product_to_candidate",
    "find_best_match",
    "match_or_create_product",
    "ingest_normalized_item",
]


def get_or_create_default_shop(session: Session, source: Source) -> Shop:
    """Collectorが店舗別粒度を持たない場合の代表Shop(source名+全国チェーン扱い)。"""
    shop_name = f"{source.name}(全国)"
    shop = session.query(Shop).filter_by(name=shop_name).one_or_none()
    if shop is not None:
        return shop

    shop = Shop(name=shop_name, type=ShopType.ONLINE, granularity=ShopGranularity.NATIONAL_CHAIN)
    session.add(shop)
    session.flush()
    return shop


def product_to_candidate(product: Product) -> MatchCandidate:
    return MatchCandidate(
        name=product.name,
        release_date=product.release_date,
        attributes=extract_product_attributes(product.name),
    )


def find_best_match(
    session: Session, candidate: MatchCandidate
) -> tuple[Product | None, MatchResult | None]:
    """既存Product全件に対しcalc_match_score()を総当たりし、最良マッチを返す
    (探索方法のスケーラビリティに関するTODOはモジュールdocstring参照)。"""
    best_product: Product | None = None
    best_result: MatchResult | None = None

    for product in session.query(Product).filter(Product.deleted_at.is_(None)).all():
        result = calc_match_score(candidate, product_to_candidate(product))
        if best_result is None or result.score > best_result.score:
            best_result = result
            best_product = product

    return best_product, best_result


def match_or_create_product(
    session: Session, product_name: str, identifiers: dict[str, str] | None = None
) -> tuple[Product, MatchStatus]:
    """既存Productと照合し、AUTO_MATCH/HIGH_PROBABILITY_MATCHなら既存に紐付け、
    それ以外(NEEDS_REVIEW/DIFFERENT_PRODUCT/候補無し)は新規作成する
    (方針の理由はモジュールdocstring参照)。

    完全一致する商品名が既にある場合は、fuzzy matching(calc_match_score)を経由せず
    即座にその既存Productへ紐付ける。これはCelery Beat(タスク12)による定期再収集で
    同一ページを繰り返し取り込んだ際、JAN/型番等の識別子を持たない商品(現状の
    Collector実装では大半がこれに該当)だと商品名の類似度だけではNEEDS_REVIEW
    (40点)止まりでAUTO_MATCH/HIGH_PROBABILITY_MATCHの閾値に届かず、再収集のたびに
    重複Productが作られてしまう不具合が判明したため追加した(E2Eテストで発見)。
    文字列が完全一致するケースはfuzzy matchingの出る幕もなく安全に断定できるため、
    技術分析11.3の「誤って別商品を統合する方が害が大きい」という原則には抵触しない。
    """
    exact_match = (
        session.query(Product).filter(Product.deleted_at.is_(None), Product.name == product_name).one_or_none()
    )
    if exact_match is not None:
        return exact_match, MatchStatus.AUTO_MATCH

    candidate = MatchCandidate(
        name=product_name,
        identifiers=identifiers or {},
        attributes=extract_product_attributes(product_name),
    )

    best_product, best_result = find_best_match(session, candidate)

    if best_result is not None and best_result.status in (
        MatchStatus.AUTO_MATCH,
        MatchStatus.HIGH_PROBABILITY_MATCH,
    ):
        return best_product, best_result.status

    new_product = Product(name=product_name)
    session.add(new_product)
    session.flush()
    status = best_result.status if best_result is not None else MatchStatus.DIFFERENT_PRODUCT
    return new_product, status


def ingest_normalized_item(
    session: Session, source: Source, item: NormalizedItem
) -> tuple[ReleaseEvent, MatchStatus]:
    """NormalizedItemをProduct/Shop/ReleaseEventとしてDBへ反映する。

    同一(product_id, shop_id, event_type, start_at)の既存レコードが見つかった場合は
    価格/締切/fulfillment_type等を更新する(release_eventsのUNIQUE制約に基づく
    idempotencyの確保。定期実行(タスク12)で同じイベントを繰り返し収集しても
    重複INSERTでIntegrityErrorにならないようにするため)。
    """
    product, match_status = match_or_create_product(session, item.product_name, item.identifiers)
    shop = get_or_create_default_shop(session, source)

    existing_event = (
        session.query(ReleaseEvent)
        .filter_by(
            product_id=product.id,
            shop_id=shop.id,
            event_type=item.parsed.event_type,
            start_at=item.parsed.start_at,
        )
        .one_or_none()
    )

    if existing_event is not None:
        existing_event.price = item.parsed.price
        existing_event.deadline_at = item.parsed.deadline_at
        existing_event.announce_at = item.parsed.announce_at
        existing_event.purchase_limit_at = item.parsed.purchase_limit_at
        existing_event.fulfillment_type = item.fulfillment_type
        existing_event.region_override = item.region_override
        existing_event.region_display_text = item.region_display_text
        existing_event.region_source = item.region_source
        existing_event.store_release_at = item.parsed.extra.get("store_release_at")
        existing_event.online_release_at = item.parsed.extra.get("online_release_at")
        existing_event.product_url = item.parsed.product_url
        existing_event.apply_url = item.parsed.apply_url
        session.flush()
        return existing_event, match_status

    event = ReleaseEvent(
        product_id=product.id,
        shop_id=shop.id,
        source_id=source.id,
        event_type=item.parsed.event_type,
        start_at=item.parsed.start_at,
        announce_at=item.parsed.announce_at,
        purchase_limit_at=item.parsed.purchase_limit_at,
        price=item.parsed.price,
        deadline_at=item.parsed.deadline_at,
        fulfillment_type=item.fulfillment_type,
        region_override=item.region_override,
        region_display_text=item.region_display_text,
        region_source=item.region_source,
        store_release_at=item.parsed.extra.get("store_release_at"),
        online_release_at=item.parsed.extra.get("online_release_at"),
        product_url=item.parsed.product_url,
        apply_url=item.parsed.apply_url,
    )
    session.add(event)
    session.flush()
    return event, match_status
