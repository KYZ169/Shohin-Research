"""Collector稼働状況確認用CLI(運用整備タスク)のための実行履歴記録。

app/scheduler/tasks.pyの各Collectorタスクが、実行を終えるたびに(成功/失敗問わず)
collector_runsへ1行書き込む。CLI側(scripts/collector_status.py)はここに
書かれた最新行をタスク名ごとに読むだけで、Celery自体の内部状態には触れない。

run_ichiban_kuji_collector/run_suruga_ya_price_rising_scanはいずれも
FetchError/ParseErrorを内部でcatchして常に正常returnする設計(タスク自体を
失敗させてCeleryのリトライを誘発させたくないため)のため、例外の有無ではなく
各タスクが自分の結果(health/error_count等)から判断したstatusを明示的に渡す
方式にしている(try/exceptで自動判定する方式は、この「catchして正常return」設計と
噛み合わないため採用しなかった)。
"""

from datetime import datetime

from app.core.time import JST
from app.db.models import CollectorRun
from app.db.session import SessionLocal


def record_collector_run(
    task_name: str,
    started_at: datetime,
    status: str,
    summary: str | None = None,
    error_message: str | None = None,
) -> None:
    """statusは"success" | "failure"。呼び出し側のタスク結果に基づいて渡すこと。"""
    session = SessionLocal()
    try:
        session.add(
            CollectorRun(
                task_name=task_name,
                started_at=started_at,
                finished_at=datetime.now(tz=JST),
                status=status,
                summary=summary,
                error_message=error_message,
            )
        )
        session.commit()
    finally:
        session.close()
