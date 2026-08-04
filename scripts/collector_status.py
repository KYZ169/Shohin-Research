#!/usr/bin/env python3
"""Collector稼働状況確認用CLI(運用整備タスク)。

app/scheduler/tasks.pyの各Collectorタスクがcollector_runsテーブル(DB)に
書き込む実行履歴を読み、タスクごとの最終実行結果と、想定実行間隔
(app.scheduler.celery_app.beat_schedule)に対して最終実行からどれだけ
経過しているかを表示する。beat_scheduleに登録されているのに一度も
実行履歴が無いタスクは「未実行」として警告する。

使い方:
    venv/bin/python3 scripts/collector_status.py
    venv/bin/python3 scripts/collector_status.py --history 5   # 直近5件の履歴も表示
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from app.core.time import JST  # noqa: E402
from app.db.models import CollectorRun  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.scheduler.celery_app import celery_app  # noqa: E402

# 想定実行間隔に対して何倍を超えたら「遅延」とみなすか(実行タイミングのブレを許容するための余裕)。
OVERDUE_MULTIPLIER = 1.5


def _expected_intervals() -> dict[str, timedelta]:
    intervals: dict[str, timedelta] = {}
    for entry in celery_app.conf.beat_schedule.values():
        schedule = entry["schedule"]
        seconds = float(schedule) if not hasattr(schedule, "total_seconds") else schedule.total_seconds()
        intervals[entry["task"]] = timedelta(seconds=seconds)
    return intervals


def _format_timedelta(td: timedelta) -> str:
    total_minutes = int(td.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}時間{minutes}分"
    return f"{minutes}分"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=int, default=0, help="タスクごとに直近N件の履歴も表示する(既定: 0=表示しない)")
    args = parser.parse_args()

    expected_intervals = _expected_intervals()
    now = datetime.now(tz=JST)

    session = SessionLocal()
    try:
        known_task_names = sorted(
            set(expected_intervals) | {row[0] for row in session.query(CollectorRun.task_name).distinct()}
        )

        if not known_task_names:
            print("collector_runsに記録が無く、beat_scheduleにも登録タスクがありません。")
            return 0

        overall_ok = True
        for task_name in known_task_names:
            print("=" * 70)
            print(task_name)

            latest = (
                session.query(CollectorRun)
                .filter(CollectorRun.task_name == task_name)
                .order_by(CollectorRun.finished_at.desc())
                .first()
            )

            if latest is None:
                overall_ok = False
                print("  [ 未実行 ] 実行履歴がありません(beat起動直後、またはタスク自体が失敗している可能性)")
                continue

            elapsed = now - latest.finished_at
            interval = expected_intervals.get(task_name)
            overdue = interval is not None and elapsed > interval * OVERDUE_MULTIPLIER

            mark = "OK" if latest.status == "success" and not overdue else "NG"
            if mark == "NG":
                overall_ok = False

            print(f"  [ {mark} ] 最終実行: {latest.finished_at.isoformat()} ({_format_timedelta(elapsed)}前) status={latest.status}")
            if interval is not None:
                print(f"        想定間隔: {_format_timedelta(interval)}毎" + ("  ⚠ 想定間隔を大幅に超過しています" if overdue else ""))
            if latest.summary:
                print(f"        summary: {latest.summary}")
            if latest.error_message:
                print(f"        error: {latest.error_message}")

            if args.history > 0:
                history = (
                    session.query(CollectorRun)
                    .filter(CollectorRun.task_name == task_name)
                    .order_by(CollectorRun.finished_at.desc())
                    .limit(args.history)
                    .all()
                )
                print(f"        --- 直近{len(history)}件 ---")
                for run in history:
                    print(f"        {run.finished_at.isoformat()}  {run.status:8s}  {run.summary or run.error_message or ''}")

        print("=" * 70)
        print("総合判定: OK" if overall_ok else "総合判定: NG(要確認)")
        return 0 if overall_ok else 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
