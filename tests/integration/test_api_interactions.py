"""実装仕様書14.2のボタン(応募済みにする/ウォッチリスト登録/非表示)が呼び出す
FastAPIエンドポイントのテスト(タスク14)。

実DB接続(app.main.appの標準get_db、settings.database_url)を使い、TestClient経由で
実際にリクエストを送る。他のintegrationテストのようなトランザクションロールバックの
フィクスチャは使わず(エンドポイント内でsession.commit()するため)、作成した行を
テスト末尾で明示的に削除して副作用を残さない。
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.models import LotteryEntry, Opportunity, Product, ReleaseEvent, Shop, Source, User, Watchlist
from app.db.session import SessionLocal
from app.domain.enums import MatchStatus, ShopGranularity, ShopType, SupportedEventType
from app.main import app

client = TestClient(app)


@pytest.fixture()
def db_session():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def release_event(db_session):
    unique = uuid.uuid4().hex[:8]
    source = Source(
        name=f"テスト情報源-{unique}",
        base_url="https://example.com",
        collector_key=f"test_source_api_{unique}",
    )
    shop = Shop(name=f"テストAPI店舗-{unique}", type=ShopType.STORE, granularity=ShopGranularity.NATIONAL_CHAIN)
    product = Product(name=f"テストAPI商品-{unique}")
    db_session.add_all([source, shop, product])
    db_session.flush()

    event = ReleaseEvent(
        product_id=product.id,
        shop_id=shop.id,
        source_id=source.id,
        event_type=SupportedEventType.LOTTERY,
        product_url="https://example.com/products/api-test",
    )
    db_session.add(event)
    db_session.commit()

    yield event, product

    # relationship()を定義していないため、SQLAlchemyはFK依存順を自動推論できない。
    # 段階的にcommitして明示的な削除順(子→親)を保証する。
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
def discord_id():
    return f"discord-user-{uuid.uuid4()}"


def _cleanup_user(db_session, discord_id: str) -> None:
    user = db_session.query(User).filter_by(discord_id=discord_id).one_or_none()
    if user is not None:
        db_session.query(LotteryEntry).filter_by(user_id=user.id).delete()
        db_session.query(Watchlist).filter_by(user_id=user.id).delete()
        db_session.delete(user)
        db_session.commit()


# --- POST /watchlists ---


def test_create_watchlist_for_event(db_session, release_event, discord_id):
    event, _product = release_event
    try:
        response = client.post("/watchlists", json={"discord_id": discord_id, "event_id": str(event.id)})

        assert response.status_code == 201
        body = response.json()
        assert body["event_id"] == str(event.id)
        assert body["product_id"] is None
    finally:
        _cleanup_user(db_session, discord_id)


def test_create_watchlist_is_idempotent_on_double_click(db_session, release_event, discord_id):
    event, _product = release_event
    try:
        first = client.post("/watchlists", json={"discord_id": discord_id, "event_id": str(event.id)})
        second = client.post("/watchlists", json={"discord_id": discord_id, "event_id": str(event.id)})

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

        rows = db_session.query(Watchlist).filter_by(event_id=event.id).all()
        assert len(rows) == 1
    finally:
        _cleanup_user(db_session, discord_id)


def test_create_watchlist_rejects_both_product_and_event(release_event, discord_id):
    event, product = release_event

    response = client.post(
        "/watchlists",
        json={"discord_id": discord_id, "event_id": str(event.id), "product_id": str(product.id)},
    )

    assert response.status_code == 422


def test_create_watchlist_rejects_neither_product_nor_event(discord_id):
    response = client.post("/watchlists", json={"discord_id": discord_id})

    assert response.status_code == 422


def test_delete_watchlist_is_idempotent_even_when_not_found():
    """技術分析15章: DELETE /watchlists/{id}は冪等(存在しなくても200)。"""
    response = client.delete(f"/watchlists/{uuid.uuid4()}")

    assert response.status_code == 200


def test_delete_watchlist_removes_the_row(db_session, release_event, discord_id):
    event, _product = release_event
    try:
        created = client.post("/watchlists", json={"discord_id": discord_id, "event_id": str(event.id)}).json()

        response = client.delete(f"/watchlists/{created['id']}")

        assert response.status_code == 200
        assert db_session.get(Watchlist, uuid.UUID(created["id"])) is None
    finally:
        _cleanup_user(db_session, discord_id)


# --- POST/PATCH /lottery-entries ---


def test_create_lottery_entry_marks_applied(db_session, release_event, discord_id):
    event, _product = release_event
    try:
        response = client.post("/lottery-entries", json={"discord_id": discord_id, "event_id": str(event.id)})

        assert response.status_code == 201
        body = response.json()
        assert body["event_id"] == str(event.id)
        assert body["result"] == "pending"
    finally:
        _cleanup_user(db_session, discord_id)


def test_create_lottery_entry_is_idempotent_on_double_click(db_session, release_event, discord_id):
    event, _product = release_event
    try:
        first = client.post("/lottery-entries", json={"discord_id": discord_id, "event_id": str(event.id)})
        second = client.post("/lottery-entries", json={"discord_id": discord_id, "event_id": str(event.id)})

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

        rows = db_session.query(LotteryEntry).filter_by(event_id=event.id).all()
        assert len(rows) == 1
    finally:
        _cleanup_user(db_session, discord_id)


def test_update_lottery_entry_result(db_session, release_event, discord_id):
    event, _product = release_event
    try:
        created = client.post(
            "/lottery-entries", json={"discord_id": discord_id, "event_id": str(event.id)}
        ).json()

        response = client.patch(f"/lottery-entries/{created['id']}", json={"result": "won"})

        assert response.status_code == 200
        assert response.json()["result"] == "won"
    finally:
        _cleanup_user(db_session, discord_id)


def test_update_lottery_entry_result_404_for_unknown_id():
    response = client.patch(f"/lottery-entries/{uuid.uuid4()}", json={"result": "won"})

    assert response.status_code == 404


# --- PATCH /opportunities/{id} (非表示) ---


def test_hide_opportunity_sets_status(db_session, release_event):
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

    try:
        response = client.patch(f"/opportunities/{opportunity.id}", json={"status": "hidden"})

        assert response.status_code == 200
        assert response.json()["status"] == "hidden"

        db_session.refresh(opportunity)
        assert opportunity.status == "hidden"
    finally:
        db_session.delete(opportunity)
        db_session.commit()


def test_hide_opportunity_404_for_unknown_id():
    response = client.patch(f"/opportunities/{uuid.uuid4()}", json={"status": "hidden"})

    assert response.status_code == 404
