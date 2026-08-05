#!/usr/bin/env python3
"""ManualReviewTask(要確認キュー)の一覧・解決用CLI(2026-08-05追加)。

NEEDS_REVIEW(要確認)相当の商品照合を人間が確定(統合)/棄却(分離維持)するための
最小限の入口。app/pipeline/manual_review.py:resolve_manual_review_task()を
そのまま呼び出す(将来のDiscordボタン/Web APIも同じ関数を呼ぶ想定、
CLAUDE.md 5節参照)。

使い方:
    venv/bin/python3 scripts/manual_review_cli.py list
    venv/bin/python3 scripts/manual_review_cli.py resolve <task_id> confirm --by "自分の名前"
    venv/bin/python3 scripts/manual_review_cli.py resolve <task_id> reject --by "自分の名前"
"""

from __future__ import annotations

import argparse
import sys
import uuid

sys.path.insert(0, ".")

from app.db.models import ManualReviewTask, Product  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.domain.enums import ManualReviewTaskStatus  # noqa: E402
from app.pipeline.manual_review import ManualReviewTaskNotPendingError, resolve_manual_review_task  # noqa: E402


def _cmd_list(args: argparse.Namespace) -> int:
    session = SessionLocal()
    try:
        tasks = (
            session.query(ManualReviewTask)
            .filter(ManualReviewTask.status == ManualReviewTaskStatus.PENDING)
            .order_by(ManualReviewTask.created_at)
            .all()
        )
        if not tasks:
            print("未解決のmanual_review_taskはありません。")
            return 0

        for task in tasks:
            candidate = session.get(Product, task.candidate_product_id)
            matched = session.get(Product, task.matched_product_id)
            print("=" * 70)
            print(f"task_id: {task.id}")
            print(f"  score: {task.score}")
            print(f"  candidate(統合元・要確認対象): {candidate.name if candidate else '(削除済み?)'}")
            print(f"  matched  (統合先候補)        : {matched.name if matched else '(削除済み?)'}")
            print(f"  created_at: {task.created_at.isoformat()}")
        print("=" * 70)
        print(f"合計 {len(tasks)} 件")
        return 0
    finally:
        session.close()


def _cmd_resolve(args: argparse.Namespace) -> int:
    session = SessionLocal()
    try:
        task = session.get(ManualReviewTask, args.task_id)
        if task is None:
            print(f"task_id={args.task_id} が見つかりません。", file=sys.stderr)
            return 1

        try:
            resolve_manual_review_task(session, task, resolution=args.resolution, resolved_by=args.by)
        except ManualReviewTaskNotPendingError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 1

        session.commit()
        print(f"task_id={task.id} を {args.resolution} で解決しました(resolved_by={args.by})。")
        if args.resolution == "confirm":
            print("candidate_product側のrelease_events/opportunities/product_identifiers/")
            print("watchlistsをmatched_productへ統合し、candidate_productはsoft-deleteしました。")
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="未解決のmanual_review_taskを一覧表示する")

    resolve_parser = subparsers.add_parser("resolve", help="1件のmanual_review_taskを解決する")
    resolve_parser.add_argument("task_id", type=uuid.UUID)
    resolve_parser.add_argument("resolution", choices=["confirm", "reject"])
    resolve_parser.add_argument("--by", required=True, help="解決した人の識別子(名前等)")

    args = parser.parse_args()

    if args.command == "list":
        return _cmd_list(args)
    return _cmd_resolve(args)


if __name__ == "__main__":
    raise SystemExit(main())
