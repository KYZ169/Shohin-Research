"""OpportunityActionView(app/notification/interaction_view.py)のボタンコールバック
テスト(タスク14)。

discord.Interactionはモックするが、httpx.ASGITransport経由で実際のFastAPIアプリ
(app.main.app)・実DBに接続し、「ボタンを押すと正しいエンドポイントが正しい
ペイロードで呼ばれ、DBに正しく反映されるか」を検証する。discord側のボタン押下→
コールバック起動という経路そのものは、モジュールdocstringのとおり未検証。
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.db.models import LotteryEntry, Opportunity, Product, ReleaseEvent, Shop, Source, User, Watchlist
from app.db.session import SessionLocal
from app.domain.enums import MatchStatus, ShopGranularity, ShopType, SupportedEventType
from app.main import app
from app.notification.interaction_view import OpportunityActionView


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
