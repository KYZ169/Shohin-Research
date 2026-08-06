"""ManualReviewTask解決処理(app/pipeline/manual_review.py)のテスト(実DB接続)。

NEEDS_REVIEW時にmatch_or_create_product()がmanual_review_taskを作成すること、
confirm解決が4テーブル(release_events/opportunities/product_identifiers/
watchlists)を正しくマージすること、reject解決が何もマージしないこと、
既に解決済みのタスクを再解決しようとするとエラーになることを確認する。
"""

from datetime import datetime

import pytest

from app.core.time import JST
from app.db.models import (
    ManualReviewTask,
    Opportunity,
    Product,
    ProductIdentifier,
    ReleaseEvent,
    Shop,
    Source,
    User,
    Watchlist,
)
from app.domain.enums import (
    ManualReviewTaskStatus,
    MatchStatus,
    ProductIdentifierType,
    ShopGranularity,
    ShopType,
    SupportedEventType,
)
from app.pipeline.ingest import match_or_create_product
from app.pipeline.manual_review import ManualReviewTaskNotPendingError, resolve_manual_review_task


def test_needs_review_creates_manual_review_task(db_session):
    """NEEDS_REVIEW相当の照合が実際にmanual_review_tasksへ1行記録されることを確認する。"""
    first_product, _ = match_or_create_product(db_session, "似ているようで別の商品A")

    candidate, status = match_or_create_product(
        db_session, "似ているようで別の商品B", identifiers={"jan": "4570000000001"}
    )
    assert status == MatchStatus.NEEDS_REVIEW

    task = db_session.query(ManualReviewTask).filter_by(candidate_product_id=candidate.id).one()
    assert task.matched_product_id == first_product.id
    assert task.status == ManualReviewTaskStatus.PENDING
    assert 40 <= task.score < 70


def test_confirm_merges_release_events_and_soft_deletes_candidate(db_session):
    matched = Product(name="統合先商品")
    candidate = Product(name="統合元商品")
    db_session.add_all([matched, candidate])
    db_session.flush()

    source = Source(name="テスト情報源", base_url="https://example.com", collector_key="test_manual_review")
    shop_a = Shop(name="店舗A", type=ShopType.ONLINE, granularity=ShopGranularity.NATIONAL_CHAIN)
    shop_b = Shop(name="店舗B", type=ShopType.ONLINE, granularity=ShopGranularity.NATIONAL_CHAIN)
    db_session.add_all([source, shop_a, shop_b])
    db_session.flush()

    # candidateに複数(異なる店舗)のrelease_eventsを紐づける(ユーザー確認事項1)。
    event1 = ReleaseEvent(
        product_id=candidate.id, shop_id=shop_a.id, source_id=source.id,
        event_type=SupportedEventType.LOTTERY, product_url="https://example.com/a",
    )
    event2 = ReleaseEvent(
        product_id=candidate.id, shop_id=shop_b.id, source_id=source.id,
        event_type=SupportedEventType.LOTTERY, product_url="https://example.com/b",
    )
    db_session.add_all([event1, event2])
    db_session.flush()

    task = ManualReviewTask(
        candidate_product_id=candidate.id, matched_product_id=matched.id,
        score=55, status=ManualReviewTaskStatus.PENDING, created_at=datetime.now(tz=JST),
    )
    db_session.add(task)
    db_session.flush()

    # candidateに紐づくOpportunityも1件作り、confirm後にMANUALLY_CONFIRMEDへ
    # 更新されること(2026-08-05、Discordボタン結線時に追加)を確認する。
    opportunity = Opportunity(
        product_id=candidate.id, event_id=event1.id, best_channel_name="suruga_ya",
        confidence="B", score="0.5", match_status=MatchStatus.NEEDS_REVIEW,
    )
    db_session.add(opportunity)
    db_session.flush()

    resolve_manual_review_task(db_session, task, resolution="confirm", resolved_by="tester")

    db_session.refresh(event1)
    db_session.refresh(event2)
    assert event1.product_id == matched.id
    assert event2.product_id == matched.id

    db_session.refresh(candidate)
    assert candidate.deleted_at is not None

    db_session.refresh(task)
    assert task.status == ManualReviewTaskStatus.CONFIRMED
    assert task.resolved_by == "tester"
    assert task.resolved_at is not None

    db_session.refresh(opportunity)
    assert opportunity.match_status == MatchStatus.MANUALLY_CONFIRMED
    assert opportunity.product_id == matched.id  # Opportunity自体もReassignTablesで付け替え済み

    # 4テーブルいずれにもcandidateへの参照が残っていないこと。
    for model in (ReleaseEvent, Opportunity, ProductIdentifier, Watchlist):
        assert db_session.query(model).filter(model.product_id == candidate.id).count() == 0


def test_confirm_deduplicates_product_identifiers_on_conflict(db_session):
    """matched側に既に同じ(type, value)の識別子がある場合、candidate側は
    付け替えずに削除され、IntegrityErrorにならないことを確認する。"""
    matched = Product(name="統合先商品2")
    candidate = Product(name="統合元商品2")
    db_session.add_all([matched, candidate])
    db_session.flush()

    shared_jan = "4570000000099"
    db_session.add(ProductIdentifier(product_id=matched.id, type=ProductIdentifierType.JAN, value=shared_jan))
    db_session.add(ProductIdentifier(product_id=candidate.id, type=ProductIdentifierType.JAN, value=shared_jan))
    # candidate側だけが持つ識別子(型番)は普通に付け替えられることも合わせて確認する。
    db_session.add(ProductIdentifier(product_id=candidate.id, type=ProductIdentifierType.MODEL, value="9999999"))
    db_session.flush()

    task = ManualReviewTask(
        candidate_product_id=candidate.id, matched_product_id=matched.id,
        score=75, status=ManualReviewTaskStatus.PENDING, created_at=datetime.now(tz=JST),
    )
    db_session.add(task)
    db_session.flush()

    resolve_manual_review_task(db_session, task, resolution="confirm", resolved_by="tester")

    matched_identifiers = db_session.query(ProductIdentifier).filter_by(product_id=matched.id).all()
    values = {(i.type, i.value) for i in matched_identifiers}
    assert (ProductIdentifierType.JAN, shared_jan) in values  # 重複せず1件のまま
    assert (ProductIdentifierType.MODEL, "9999999") in values  # 付け替えられている
    jan_count = sum(1 for i in matched_identifiers if i.type == ProductIdentifierType.JAN and i.value == shared_jan)
    assert jan_count == 1

    assert db_session.query(ProductIdentifier).filter_by(product_id=candidate.id).count() == 0


def test_confirm_deduplicates_watchlists_on_conflict(db_session):
    """同じユーザーがcandidate/matched両方をウォッチしていた場合、
    (user_id, product_id)のUNIQUE制約に違反せず重複が解消されることを確認する。"""
    matched = Product(name="統合先商品3")
    candidate = Product(name="統合元商品3")
    db_session.add_all([matched, candidate])
    db_session.flush()

    user = User(discord_id="123456789012345678", created_at=datetime.now(tz=JST))
    db_session.add(user)
    db_session.flush()

    db_session.add(Watchlist(user_id=user.id, product_id=matched.id, created_at=datetime.now(tz=JST)))
    db_session.add(Watchlist(user_id=user.id, product_id=candidate.id, created_at=datetime.now(tz=JST)))
    db_session.flush()

    task = ManualReviewTask(
        candidate_product_id=candidate.id, matched_product_id=matched.id,
        score=80, status=ManualReviewTaskStatus.PENDING, created_at=datetime.now(tz=JST),
    )
    db_session.add(task)
    db_session.flush()

    resolve_manual_review_task(db_session, task, resolution="confirm", resolved_by="tester")

    matched_watchlists = db_session.query(Watchlist).filter_by(product_id=matched.id, user_id=user.id).all()
    assert len(matched_watchlists) == 1
    assert db_session.query(Watchlist).filter_by(product_id=candidate.id).count() == 0


def test_reject_does_not_touch_products(db_session):
    matched = Product(name="統合先商品4")
    candidate = Product(name="統合元商品4")
    db_session.add_all([matched, candidate])
    db_session.flush()

    source = Source(name="テスト情報源4", base_url="https://example.com", collector_key="test_manual_review4")
    shop = Shop(name="店舗4", type=ShopType.ONLINE, granularity=ShopGranularity.NATIONAL_CHAIN)
    db_session.add_all([source, shop])
    db_session.flush()
    event = ReleaseEvent(
        product_id=candidate.id, shop_id=shop.id, source_id=source.id,
        event_type=SupportedEventType.LOTTERY, product_url="https://example.com/reject",
    )
    db_session.add(event)
    db_session.flush()
    # candidateに紐づくOpportunityがreject後にDIFFERENT_PRODUCTへ更新されること
    # (2026-08-05、Discordボタン結線時に追加)を確認する。
    opportunity = Opportunity(
        product_id=candidate.id, event_id=event.id, best_channel_name="suruga_ya",
        confidence="B", score="0.5", match_status=MatchStatus.NEEDS_REVIEW,
    )
    db_session.add(opportunity)
    db_session.flush()

    task = ManualReviewTask(
        candidate_product_id=candidate.id, matched_product_id=matched.id,
        score=45, status=ManualReviewTaskStatus.PENDING, created_at=datetime.now(tz=JST),
    )
    db_session.add(task)
    db_session.flush()

    resolve_manual_review_task(db_session, task, resolution="reject", resolved_by="tester")

    db_session.refresh(candidate)
    assert candidate.deleted_at is None  # 分離されたまま維持される

    db_session.refresh(task)
    assert task.status == ManualReviewTaskStatus.REJECTED
    assert task.resolved_by == "tester"

    db_session.refresh(opportunity)
    assert opportunity.match_status == MatchStatus.DIFFERENT_PRODUCT
    assert opportunity.product_id == candidate.id  # rejectはマージしないのでproduct_idは不変


def test_resolving_already_resolved_task_raises(db_session):
    matched = Product(name="統合先商品5")
    candidate = Product(name="統合元商品5")
    db_session.add_all([matched, candidate])
    db_session.flush()

    task = ManualReviewTask(
        candidate_product_id=candidate.id, matched_product_id=matched.id,
        score=50, status=ManualReviewTaskStatus.PENDING, created_at=datetime.now(tz=JST),
    )
    db_session.add(task)
    db_session.flush()

    resolve_manual_review_task(db_session, task, resolution="reject", resolved_by="tester")

    with pytest.raises(ManualReviewTaskNotPendingError):
        resolve_manual_review_task(db_session, task, resolution="confirm", resolved_by="tester2")


def test_merge_failure_rolls_back_partial_reassignment(db_session, monkeypatch):
    """マージ処理の途中で例外が起きた場合、begin_nested()により
    それまでの部分的な付け替えがロールバックされることを確認する
    (ユーザー確認事項3: 部分的にしか終わらない状態を防ぐ)。
    """
    matched = Product(name="統合先商品6")
    candidate = Product(name="統合元商品6")
    db_session.add_all([matched, candidate])
    db_session.flush()

    source = Source(name="テスト情報源6", base_url="https://example.com", collector_key="test_manual_review6")
    shop = Shop(name="店舗6", type=ShopType.ONLINE, granularity=ShopGranularity.NATIONAL_CHAIN)
    db_session.add_all([source, shop])
    db_session.flush()

    event = ReleaseEvent(
        product_id=candidate.id, shop_id=shop.id, source_id=source.id,
        event_type=SupportedEventType.LOTTERY, product_url="https://example.com/x",
    )
    db_session.add(event)
    db_session.flush()
    opportunity = Opportunity(
        product_id=candidate.id, event_id=event.id, best_channel_name="suruga_ya",
        confidence="B", score="0.5", match_status=MatchStatus.NEEDS_REVIEW,
    )
    db_session.add(opportunity)
    db_session.flush()

    task = ManualReviewTask(
        candidate_product_id=candidate.id, matched_product_id=matched.id,
        score=60, status=ManualReviewTaskStatus.PENDING, created_at=datetime.now(tz=JST),
    )
    db_session.add(task)
    db_session.flush()

    import app.pipeline.manual_review as manual_review_module

    def _boom(*args, **kwargs):
        raise RuntimeError("わざと失敗させる")

    monkeypatch.setattr(manual_review_module, "_assert_no_remaining_references", _boom)

    with pytest.raises(RuntimeError):
        resolve_manual_review_task(db_session, task, resolution="confirm", resolved_by="tester")

    # release_eventの付け替え(_merge_candidate_into_matched内で検証より前に実行済み)も
    # 含め、SAVEPOINTごとロールバックされていること。
    db_session.refresh(event)
    assert event.product_id == candidate.id  # 付け替えられていない(ロールバック済み)

    db_session.refresh(candidate)
    assert candidate.deleted_at is None  # soft-deleteもロールバック済み

    db_session.refresh(task)
    assert task.status == ManualReviewTaskStatus.PENDING  # タスク自体も未解決のまま

    # _update_opportunity_match_status()による更新も同じSAVEPOINT内のため、
    # 例外発生時はこちらもロールバックされ、NEEDS_REVIEWのまま残ること。
    db_session.refresh(opportunity)
    assert opportunity.match_status == MatchStatus.NEEDS_REVIEW
