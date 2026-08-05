"""ManualReviewTask(要確認キュー)の解決処理(2026-08-05追加)。

app/pipeline/ingest.py:match_or_create_product()がNEEDS_REVIEW時に作成する
candidate_product(独立した新規Product)を、人間が「確定(統合)」または「棄却(分離維持)」
するための処理。MatchStatus.MANUALLY_CONFIRMEDの実運用先。

【マージ(確定)処理の設計方針】
NEEDS_REVIEW時点で既にcandidate_productは独立したProduct行として存在し、
release_events/opportunities/product_identifiers/watchlistsの4テーブルから
外部キー参照されうる(app/pipeline/ingest.pyモジュールdocstring「Phase0スコープの
簡略化」参照)。したがって「確定」は単なるstatus更新ではなく、この4テーブル分の
参照をcandidate_product→matched_productへ付け替える実質的なマージ処理になる。

- 複数release_events(異なる店舗・異なる日程)が候補にあっても、技術分析レポート9章の
  イベント/商品分離の設計上、複数あること自体は問題無いため基本的に全件付け替える。
  ただしrelease_eventsには(product_id, shop_id, event_type, start_at)のUNIQUE制約が
  あるため、matched_product側に全く同じ(shop_id, event_type, start_at)の行が
  既にある場合(同一イベントの重複観測)だけは、product_identifiers/watchlistsと同様に
  重複を避けてcandidate側の行を削除する扱いにする(単純な全件付け替えだと
  IntegrityErrorになるため)。
- product_identifiers/watchlistsも(product_id, type/value)や(user_id, product_id)に
  UNIQUE制約があるため、付け替え先(matched_product)に既に同じ行がある場合は
  重複を避けてcandidate側の行を削除する(付け替えるとIntegrityErrorになるため)。
- 付け替え漏れが「削除済みのはずのProductが静かに参照され続ける」バグに直結するため、
  マージの最後に4テーブル全てでcandidate_product_idへの参照が0件であることを
  アサーションで検証する。
- 全体をsession.begin_nested()(SAVEPOINT)で包み、途中で例外(アサーション失敗含む)が
  発生した場合はそのブロックだけを確実にロールバックする。外側のセッション/
  トランザクションの状態に依存しない、自己完結した保証にするため。
"""

from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.core.time import JST
from app.db.models import ManualReviewTask, Opportunity, Product, ProductIdentifier, ReleaseEvent, Watchlist
from app.domain.enums import ManualReviewTaskStatus

__all__ = ["resolve_manual_review_task", "ManualReviewTaskNotPendingError"]

# 付け替え時にUNIQUE制約の衝突を避けるため、重複していれば削除(付け替えない)で
# 済ませるテーブル。(model, 制約に使うカラム名のタプル)
_DEDUPE_ON_CONFLICT_TABLES: tuple[tuple[type, tuple[str, ...]], ...] = (
    # uq_release_events_product_shop_type_start(product_id, shop_id, event_type, start_at)対策。
    # matched_product側に同一イベントが既にあれば、candidate側は重複として削除する。
    (ReleaseEvent, ("shop_id", "event_type", "start_at")),
    (ProductIdentifier, ("type", "value")),
    # uq_watchlists_user_product(user_id, product_id)対策。ここで扱う行はCHECK制約上
    # event_id IS NULL(product_id指定)のものだけなので、判定はuser_idのみで良い
    # (付け替え後のproduct_idはmatched_id側で揃うため、残る変数はuser_idだけ)。
    (Watchlist, ("user_id",)),
)
# 単純に全件付け替えれば良いテーブル(UNIQUE制約がproduct_id自体を含まない)。
_REASSIGN_TABLES: tuple[type, ...] = (Opportunity,)
# 全4テーブル(検証用)。
_ALL_PRODUCT_ID_TABLES: tuple[type, ...] = (ReleaseEvent, Opportunity, ProductIdentifier, Watchlist)


class ManualReviewTaskNotPendingError(Exception):
    """既に解決済み(confirmed/rejected)のタスクを再度解決しようとした場合。"""


def resolve_manual_review_task(
    session: Session,
    task: ManualReviewTask,
    resolution: Literal["confirm", "reject"],
    resolved_by: str,
) -> ManualReviewTask:
    """taskをconfirm(統合)またはreject(分離維持)で解決する。

    confirmの場合、candidate_product配下の4テーブル分のレコードをmatched_product側へ
    付け替え、candidate_productをsoft-delete(deleted_at設定)する。rejectの場合は
    taskのstatusを更新するのみで、Product側には一切手を入れない
    (candidate_productは元々独立したProductとして正しく分離済みのため)。

    処理全体をSAVEPOINT(session.begin_nested())で包み、途中で例外が発生した場合は
    このブロックの変更のみを確実にロールバックする(モジュールdocstring参照)。
    """
    if task.status != ManualReviewTaskStatus.PENDING:
        raise ManualReviewTaskNotPendingError(
            f"manual_review_task {task.id} は既に{task.status.value}で解決済みです"
        )

    with session.begin_nested():
        if resolution == "confirm":
            _merge_candidate_into_matched(session, task)
        elif resolution != "reject":
            raise ValueError(f"resolutionは'confirm'または'reject'である必要があります: {resolution!r}")

        task.status = (
            ManualReviewTaskStatus.CONFIRMED if resolution == "confirm" else ManualReviewTaskStatus.REJECTED
        )
        task.resolved_at = datetime.now(tz=JST)
        task.resolved_by = resolved_by
        session.flush()

    return task


def _merge_candidate_into_matched(session: Session, task: ManualReviewTask) -> None:
    candidate_id = task.candidate_product_id
    matched_id = task.matched_product_id

    for model, conflict_cols in _DEDUPE_ON_CONFLICT_TABLES:
        _reassign_with_dedupe(session, model, candidate_id, matched_id, conflict_cols)

    for model in _REASSIGN_TABLES:
        session.query(model).filter(model.product_id == candidate_id).update(
            {"product_id": matched_id}, synchronize_session=False
        )

    session.flush()

    candidate_product = session.get(Product, candidate_id)
    candidate_product.deleted_at = datetime.now(tz=JST)
    session.flush()

    _assert_no_remaining_references(session, candidate_id)


def _reassign_with_dedupe(
    session: Session, model: type, candidate_id, matched_id, conflict_cols: tuple[str, ...]
) -> None:
    """model.product_id == candidate_id の行をmatched_idへ付け替える。
    ただしmatched_id側に(conflict_cols全て一致する)行が既にあれば、UNIQUE制約
    違反を避けるためcandidate側の行を付け替えずに削除する。
    """
    matched_rows = session.query(model).filter(model.product_id == matched_id).all()
    matched_signatures = {tuple(getattr(row, col) for col in conflict_cols) for row in matched_rows}

    candidate_rows = session.query(model).filter(model.product_id == candidate_id).all()
    for row in candidate_rows:
        signature = tuple(getattr(row, col) for col in conflict_cols)
        if signature in matched_signatures:
            session.delete(row)
        else:
            row.product_id = matched_id
            matched_signatures.add(signature)
    session.flush()


def _assert_no_remaining_references(session: Session, candidate_id) -> None:
    """付け替え漏れが無いことを、4テーブル全てに対して直接検証する。"""
    for model in _ALL_PRODUCT_ID_TABLES:
        remaining = session.query(model).filter(model.product_id == candidate_id).count()
        assert remaining == 0, (
            f"{model.__tablename__}にcandidate_product({candidate_id})への参照が"
            f"{remaining}件残っています(マージの付け替え漏れ)"
        )
