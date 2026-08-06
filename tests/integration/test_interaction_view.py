"""OpportunityActionView(app/notification/interaction_view.py)のボタンコールバック
テスト(タスク14)。

discord.Interactionはモックするが、httpx.ASGITransport経由で実際のFastAPIアプリ
(app.main.app)・実DBに接続し、「ボタンを押すと正しいエンドポイントが正しい
ペイロードで呼ばれ、DBに正しく反映されるか」を検証する。discord側のボタン押下→
コールバック起動という経路そのものは、モジュールdocstringのとおり未検証。
"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.time import JST
from app.db.models import LotteryEntry, ManualReviewTask, Opportunity, Product, ReleaseEvent, Shop, Source, User, Watchlist
from app.db.session import SessionLocal
from app.domain.enums import ManualReviewTaskStatus, MatchStatus, ShopGranularity, ShopType, SupportedEventType
from app.main import app
from app.notification.interaction_view import OpportunityActionView, _ManualReviewConfirmView


@pytest.fixture()
def db_session():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def release_event(db_session):
    unique = uuid.uuid4().hex[:8]
    source = Source(
        name=f"テスト情報源-view-{unique}",
        base_url="https://example.com",
        collector_key=f"test_source_view_{unique}",
    )
    shop = Shop(name=f"テストView店舗-{unique}", type=ShopType.STORE, granularity=ShopGranularity.NATIONAL_CHAIN)
    product = Product(name=f"テストView商品-{unique}")
    db_session.add_all([source, shop, product])
    db_session.flush()

    event = ReleaseEvent(
        product_id=product.id,
        shop_id=shop.id,
        source_id=source.id,
        event_type=SupportedEventType.LOTTERY,
        product_url="https://example.com/products/view-test",
    )
    db_session.add(event)
    db_session.commit()

    yield event, product

    db_session.query(LotteryEntry).filter_by(event_id=event.id).delete()
    db_session.query(Watchlist).filter_by(event_id=event.id).delete()
    db_session.query(Opportunity).filter_by(event_id=event.id).delete()
    db_session.commit()

    db_session.delete(event)
    db_session.commit()

    db_session.delete(product)
    db_session.delete(shop)
    db_session.delete(source)
    db_session.commit()


@pytest.fixture()
def manual_review_setup(db_session):
    """「照合を確定する」/「別商品として分離」ボタンのテスト用に、NEEDS_REVIEW相当の
    候補Product+matched Product+pendingなManualReviewTask+それに紐づくOpportunityを
    一式作る(app/pipeline/manual_review.py:resolve_manual_review_task()が実際に
    参照・更新する形をそのまま再現する)。"""
    unique = uuid.uuid4().hex[:8]
    matched = Product(name=f"統合先-view-{unique}")
    candidate = Product(name=f"統合元-view-{unique}")
    db_session.add_all([matched, candidate])
    db_session.flush()

    source = Source(
        name=f"テスト情報源-mrv-{unique}", base_url="https://example.com", collector_key=f"test_mrv_{unique}"
    )
    shop = Shop(name=f"テストMRV店舗-{unique}", type=ShopType.ONLINE, granularity=ShopGranularity.NATIONAL_CHAIN)
    db_session.add_all([source, shop])
    db_session.flush()

    event = ReleaseEvent(
        product_id=candidate.id,
        shop_id=shop.id,
        source_id=source.id,
        event_type=SupportedEventType.LOTTERY,
        product_url=f"https://example.com/mrv-{unique}",
    )
    db_session.add(event)
    db_session.flush()

    opportunity = Opportunity(
        product_id=candidate.id,
        event_id=event.id,
        best_channel_name="suruga_ya",
        confidence="B",
        score="0.5",
        match_status=MatchStatus.NEEDS_REVIEW,
    )
    db_session.add(opportunity)

    task = ManualReviewTask(
        candidate_product_id=candidate.id,
        matched_product_id=matched.id,
        score=55,
        status=ManualReviewTaskStatus.PENDING,
        created_at=datetime.now(tz=JST),
    )
    db_session.add(task)
    db_session.commit()

    yield task, candidate, matched, event, opportunity

    db_session.query(Opportunity).filter_by(event_id=event.id).delete()
    db_session.query(ManualReviewTask).filter_by(id=task.id).delete()
    db_session.commit()
    db_session.delete(event)
    db_session.commit()
    db_session.delete(candidate)
    db_session.delete(matched)
    db_session.delete(shop)
    db_session.delete(source)
    db_session.commit()


@pytest.fixture()
async def http_client():
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    yield client
    await client.aclose()


def _mock_interaction(discord_user_id: int) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = discord_user_id
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    return interaction


def _cleanup_user(db_session, discord_id: str) -> None:
    user = db_session.query(User).filter_by(discord_id=discord_id).one_or_none()
    if user is not None:
        db_session.query(LotteryEntry).filter_by(user_id=user.id).delete()
        db_session.query(Watchlist).filter_by(user_id=user.id).delete()
        db_session.delete(user)
        db_session.commit()


async def test_mark_applied_button_creates_lottery_entry(db_session, release_event, http_client):
    event, _product = release_event
    discord_user_id = 1000 + hash(event.id) % 100000
    view = OpportunityActionView(event_id=str(event.id), opportunity_id="unused", http_client=http_client)
    interaction = _mock_interaction(discord_user_id)

    try:
        await view.mark_applied.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        args, kwargs = interaction.response.send_message.call_args
        assert "応募済みにしました" in args[0]
        assert kwargs.get("ephemeral") is True

        entry = db_session.query(LotteryEntry).filter_by(event_id=event.id).one()
        assert entry.result.value == "pending"
    finally:
        _cleanup_user(db_session, str(discord_user_id))


async def test_add_to_watchlist_button_creates_watchlist(db_session, release_event, http_client):
    event, _product = release_event
    discord_user_id = 2000 + hash(event.id) % 100000
    view = OpportunityActionView(event_id=str(event.id), opportunity_id="unused", http_client=http_client)
    interaction = _mock_interaction(discord_user_id)

    try:
        await view.add_to_watchlist.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        args, _kwargs = interaction.response.send_message.call_args
        assert "ウォッチリストに登録しました" in args[0]

        row = db_session.query(Watchlist).filter_by(event_id=event.id).one()
        assert row.event_id == event.id
    finally:
        _cleanup_user(db_session, str(discord_user_id))


async def test_hide_button_sets_opportunity_status_and_edits_message(db_session, release_event, http_client):
    event, product = release_event
    opportunity = Opportunity(
        product_id=product.id,
        event_id=event.id,
        best_channel_name="suruga_ya",
        confidence="B",
        score="0.5",
        match_status=MatchStatus.AUTO_MATCH,
    )
    db_session.add(opportunity)
    db_session.commit()

    view = OpportunityActionView(
        event_id=str(event.id), opportunity_id=str(opportunity.id), http_client=http_client
    )
    interaction = _mock_interaction(3000)

    await view.hide_opportunity.callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    _args, kwargs = interaction.response.edit_message.call_args
    assert kwargs["content"] == "非表示にしました。"
    assert kwargs["embed"] is None
    assert kwargs["view"] is None

    db_session.refresh(opportunity)
    assert opportunity.status == "hidden"


async def test_hide_button_reports_failure_for_unknown_opportunity(release_event, http_client):
    event, _product = release_event
    view = OpportunityActionView(
        event_id=str(event.id), opportunity_id=str(uuid.uuid4()), http_client=http_client
    )
    interaction = _mock_interaction(4000)

    await view.hide_opportunity.callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "処理に失敗しました" in args[0]
    assert kwargs.get("ephemeral") is True
    interaction.response.edit_message.assert_not_awaited()


async def test_mark_applied_reports_connection_error_instead_of_silently_failing(release_event):
    """2026-08-05実機検証で発見: app/bot/main.py(常時起動Bot)からapi_base_urlの
    旧デフォルト値で到達できないAPIを叩いた際、httpx.ConnectErrorがinteraction.response
    呼び出し前に飛び、Discord側が「応答しませんでした」になっていた。到達不可能な
    api_base_urlを指定しても、必ずinteraction.response.send_messageが呼ばれる
    (Discordのinteractionを応答無しのまま失敗させない)ことを確認する。
    """
    event, _product = release_event
    # 到達不能なポート(何もlistenしていない)を指定し、ConnectErrorを確実に発生させる。
    view = OpportunityActionView(
        event_id=str(event.id), opportunity_id="unused", api_base_url="http://localhost:1"
    )
    interaction = _mock_interaction(5000)

    await view.mark_applied.callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "処理に失敗しました" in args[0]
    assert "API接続エラー" in args[0]
    assert kwargs.get("ephemeral") is True


# --- manual_review_tasks結線(「照合を確定する」/「別商品として分離」)のテスト ---


def _find_button(view, label: str):
    return next(c for c in view.children if getattr(c, "label", None) == label)


async def test_manual_review_buttons_only_added_when_task_id_provided():
    """CLAUDE.md 3節「Discordボタンでのマージ確定UI」対応。manual_review_task_idを
    渡さない限り、既存3ボタンの構成は変わらない(既存Opportunity通知への影響が無いこと)。"""
    view_without = OpportunityActionView(event_id="e", opportunity_id="o")
    labels_without = {getattr(c, "label", None) for c in view_without.children}
    assert labels_without == {"応募済みにする", "ウォッチリスト登録", "非表示"}

    view_with = OpportunityActionView(event_id="e", opportunity_id="o", manual_review_task_id=str(uuid.uuid4()))
    labels_with = {getattr(c, "label", None) for c in view_with.children}
    assert labels_with == {"応募済みにする", "ウォッチリスト登録", "非表示", "照合を確定する", "別商品として分離"}


async def test_confirm_match_button_only_prompts_and_does_not_resolve_yet(db_session, manual_review_setup, http_client):
    """誤操作防止: 1回目のクリックではAPIを叩かず、ephemeralな確認メッセージを
    送るだけであることを確認する。"""
    task, _candidate, _matched, event, opportunity = manual_review_setup
    view = OpportunityActionView(
        event_id=str(event.id),
        opportunity_id=str(opportunity.id),
        manual_review_task_id=str(task.id),
        http_client=http_client,
    )
    button = _find_button(view, "照合を確定する")
    interaction = _mock_interaction(6000)

    await button.callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "本当によろしいですか" in args[0]
    assert kwargs.get("ephemeral") is True
    assert isinstance(kwargs.get("view"), _ManualReviewConfirmView)

    db_session.refresh(task)
    assert task.status == ManualReviewTaskStatus.PENDING  # まだ何も実行されていない


async def test_separate_product_button_only_prompts_and_does_not_resolve_yet(
    db_session, manual_review_setup, http_client
):
    task, _candidate, _matched, event, opportunity = manual_review_setup
    view = OpportunityActionView(
        event_id=str(event.id),
        opportunity_id=str(opportunity.id),
        manual_review_task_id=str(task.id),
        http_client=http_client,
    )
    button = _find_button(view, "別商品として分離")
    interaction = _mock_interaction(6100)

    await button.callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    args, _kwargs = interaction.response.send_message.call_args
    assert "本当によろしいですか" in args[0]
    assert "分離" in args[0]

    db_session.refresh(task)
    assert task.status == ManualReviewTaskStatus.PENDING


async def test_confirm_execution_merges_and_sets_manually_confirmed(db_session, manual_review_setup, http_client):
    """確認ダイアログの「実行する」を押した時点で初めてマージが実行され、
    Opportunity.match_statusがMANUALLY_CONFIRMEDになり、product_identifiers等も
    matched側へ付け替わることを確認する(CLAUDE.md 3節対応)。"""
    task, candidate, matched, _event, opportunity = manual_review_setup
    confirm_view = _ManualReviewConfirmView(
        manual_review_task_id=str(task.id), resolution="confirm", api_base_url="unused", http_client=http_client
    )
    interaction = _mock_interaction(7000)

    await confirm_view.execute.callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    _args, kwargs = interaction.response.edit_message.call_args
    assert "統合" in kwargs["content"]
    assert kwargs["view"] is None

    db_session.refresh(task)
    assert task.status == ManualReviewTaskStatus.CONFIRMED
    assert task.resolved_by == f"discord:{interaction.user}"

    db_session.refresh(opportunity)
    assert opportunity.match_status == MatchStatus.MANUALLY_CONFIRMED
    assert opportunity.product_id == matched.id  # マージによりmatched側へ付け替わっている

    db_session.refresh(candidate)
    assert candidate.deleted_at is not None  # soft-delete済み


async def test_separate_execution_marks_different_product_without_merging(db_session, manual_review_setup, http_client):
    """「別商品として分離」の実行はマージを一切行わず、match_statusのみ
    DIFFERENT_PRODUCTへ更新することを確認する(CLAUDE.md 3節対応)。"""
    task, candidate, _matched, _event, opportunity = manual_review_setup
    confirm_view = _ManualReviewConfirmView(
        manual_review_task_id=str(task.id), resolution="reject", api_base_url="unused", http_client=http_client
    )
    interaction = _mock_interaction(7100)

    await confirm_view.execute.callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    _args, kwargs = interaction.response.edit_message.call_args
    assert "分離" in kwargs["content"]

    db_session.refresh(task)
    assert task.status == ManualReviewTaskStatus.REJECTED

    db_session.refresh(opportunity)
    assert opportunity.match_status == MatchStatus.DIFFERENT_PRODUCT
    assert opportunity.product_id == candidate.id  # マージしていないのでproduct_idは不変

    db_session.refresh(candidate)
    assert candidate.deleted_at is None  # 分離維持(soft-deleteされない)


async def test_manual_review_cancel_button_does_not_call_api(manual_review_setup):
    """キャンセルは一切ネットワークアクセスしない(到達不能なapi_base_urlでも
    エラーにならず、単にキャンセル表示するだけ)ことを確認する。"""
    task, _candidate, _matched, _event, _opportunity = manual_review_setup
    confirm_view = _ManualReviewConfirmView(
        manual_review_task_id=str(task.id), resolution="confirm", api_base_url="http://localhost:1", http_client=None
    )
    interaction = _mock_interaction(7200)

    await confirm_view.cancel.callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    _args, kwargs = interaction.response.edit_message.call_args
    assert "キャンセルしました" in kwargs["content"]


async def test_confirm_execution_reports_failure_when_task_already_resolved(db_session, manual_review_setup):
    """既に解決済みのタスクを再度実行しようとした場合(409)、ephemeralに失敗表示され、
    例外にはならないことを確認する。1回目・2回目でそれぞれ別のhttpx.AsyncClientを使う
    (async with文のライフサイクル上、同一クライアントをリクエストを跨いで再利用できない
    ため。本番コード自体は呼び出しごとに`_make_client()`で新規clientを作るため問題ない)。
    """
    task, _candidate, _matched, _event, _opportunity = manual_review_setup

    def _new_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")

    first_view = _ManualReviewConfirmView(
        manual_review_task_id=str(task.id), resolution="confirm", api_base_url="unused", http_client=_new_client()
    )
    await first_view.execute.callback(_mock_interaction(7300))

    second_view = _ManualReviewConfirmView(
        manual_review_task_id=str(task.id), resolution="confirm", api_base_url="unused", http_client=_new_client()
    )
    interaction = _mock_interaction(7301)

    await second_view.execute.callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    _args, kwargs = interaction.response.edit_message.call_args
    assert "失敗" in kwargs["content"]
