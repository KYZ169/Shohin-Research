"""GET /manual-review-tasks・POST /manual-review-tasks/{id}/resolve のテスト。

tests/integration/test_api_interactions.pyと同じ方針(実DB接続・TestClient・
明示的クリーンアップ)に合わせる。マージ処理自体の詳細な検証は
tests/integration/test_manual_review.py側で行っているため、ここではAPI層の
配線(リクエスト→resolve_manual_review_task呼び出し→レスポンス)に絞って確認する。
"""

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.time import JST
from app.db.models import ManualReviewTask, Product
from app.db.session import SessionLocal
from app.domain.enums import ManualReviewTaskStatus
from app.main import app

client = TestClient(app)


@pytest.fixture()
def db_session():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def review_task(db_session):
    unique = uuid.uuid4().hex[:8]
    matched = Product(name=f"API統合先-{unique}")
    candidate = Product(name=f"API統合元-{unique}")
    db_session.add_all([matched, candidate])
    db_session.flush()

    task = ManualReviewTask(
        candidate_product_id=candidate.id,
        matched_product_id=matched.id,
        score=55,
        status=ManualReviewTaskStatus.PENDING,
        created_at=datetime.now(tz=JST),
    )
    db_session.add(task)
    db_session.commit()

    yield task, candidate, matched

    db_session.query(ManualReviewTask).filter_by(id=task.id).delete()
    db_session.commit()
    for product_id in (candidate.id, matched.id):
        db_session.query(Product).filter_by(id=product_id).delete()
    db_session.commit()


def test_list_manual_review_tasks_returns_pending_by_default(review_task):
    task, candidate, matched = review_task

    response = client.get("/manual-review-tasks")

    assert response.status_code == 200
    body = response.json()
    ids = {item["id"] for item in body}
    assert str(task.id) in ids
    matching = next(item for item in body if item["id"] == str(task.id))
    assert matching["candidate_product_id"] == str(candidate.id)
    assert matching["matched_product_id"] == str(matched.id)
    assert matching["status"] == "pending"


def test_resolve_confirm_merges_and_updates_task(review_task, db_session):
    task, candidate, matched = review_task

    response = client.post(
        f"/manual-review-tasks/{task.id}/resolve",
        json={"resolution": "confirm", "resolved_by": "api-test"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["resolved_by"] == "api-test"

    db_session.expire_all()
    refreshed_candidate = db_session.get(Product, candidate.id)
    assert refreshed_candidate.deleted_at is not None


def test_resolve_reject_leaves_products_untouched(review_task, db_session):
    task, candidate, matched = review_task

    response = client.post(
        f"/manual-review-tasks/{task.id}/resolve",
        json={"resolution": "reject", "resolved_by": "api-test"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    db_session.expire_all()
    refreshed_candidate = db_session.get(Product, candidate.id)
    assert refreshed_candidate.deleted_at is None


def test_resolve_twice_returns_409(review_task):
    task, _candidate, _matched = review_task

    first = client.post(
        f"/manual-review-tasks/{task.id}/resolve",
        json={"resolution": "reject", "resolved_by": "api-test"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/manual-review-tasks/{task.id}/resolve",
        json={"resolution": "confirm", "resolved_by": "api-test"},
    )
    assert second.status_code == 409


def test_resolve_unknown_task_returns_404():
    response = client.post(
        f"/manual-review-tasks/{uuid.uuid4()}/resolve",
        json={"resolution": "confirm", "resolved_by": "api-test"},
    )
    assert response.status_code == 404
