"""Celery Beat結線(app/scheduler/)のテスト。

タスク関数はここではCeleryのbroker経由ではなく直接呼び出す(taskオブジェクトは
通常の関数として呼び出すとbrokerを介さずその場で実行される)。ネットワーク到達性が
無いサンドボックス環境でも、Collector.run()/search()が例外を外へ漏らさず
FetchErrorとして正しく捕捉されることを確認できる(タスク3で実装したrun()の
エラーハンドリングと同じ理由)。

実際にCeleryワーカー/ブローカー経由でのディスパッチそのものは、ローカルRedisを
brokerとして使い手動で検証済み(実装報告に記載)。
"""

from unittest.mock import patch

from app.scheduler.celery_app import celery_app
from app.scheduler.tasks import run_ichiban_kuji_collector, run_suruga_ya_price_rising_scan


def test_beat_schedule_includes_ichiban_kuji_every_6_hours():
    """CLAUDE.md 8.5.4: 一番くじは6時間に1回。"""
    entry = celery_app.conf.beat_schedule["ichiban-kuji-every-6-hours"]

    assert entry["task"] == "app.scheduler.tasks.run_ichiban_kuji_collector"
    assert entry["schedule"] == 6 * 60 * 60


def test_beat_schedule_includes_suruga_ya_price_rising_scan():
    """CLAUDE.md 1.3: 駿河屋は価格上昇中フィルタで1日1〜2回。"""
    entry = celery_app.conf.beat_schedule["suruga-ya-price-rising-scan-twice-daily"]

    assert entry["task"] == "app.scheduler.tasks.run_suruga_ya_price_rising_scan"
    assert 1 * 24 * 60 * 60 >= entry["schedule"] >= 1 * 60 * 60


def test_tasks_are_registered_under_expected_names():
    assert "app.scheduler.tasks.run_ichiban_kuji_collector" in celery_app.tasks
    assert "app.scheduler.tasks.run_suruga_ya_price_rising_scan" in celery_app.tasks


def test_ichiban_kuji_task_handles_network_failure_gracefully():
    """このサンドボックスは1kuji.comへのアクセスがネットワークポリシーでブロックされて
    いるため、FetchErrorが発生する。タスクが例外を漏らさずhealth=failingの結果を
    返せることを確認する(タスク3のrun()実装のエラーハンドリングが正しく機能する)。

    record_collector_run()はDB接続を必要とするため(運用整備タスク、Collector稼働
    状況CLI用にcollector_runsへ記録する処理)、この純粋な単体テストではpatchして
    DB接続無しで完結させる。実際にDBへ書き込まれることの確認は
    tests/integration側の責務とする。
    """
    with patch("app.scheduler.tasks.record_collector_run"):
        result = run_ichiban_kuji_collector()

    assert result["source_name"] == "ichiban_kuji"
    assert result["health"] in {"failing", "ok"}  # 万一将来ネットワーク到達可能でも壊れない
    assert result["success_count"] >= 0


def test_suruga_ya_task_handles_network_failure_gracefully():
    from app.scheduler.tasks import SURUGA_YA_SCAN_KEYWORDS

    with patch("app.scheduler.tasks.record_collector_run"):
        result = run_suruga_ya_price_rising_scan()

    assert isinstance(result["keyword_results"], dict)
    # ネットワーク到達不可のため全キーワードが失敗し、成功件数0でerror_countが
    # 巡回キーワード数と一致するはず(将来到達可能になれば自然にkeyword_resultsが埋まる)。
    assert result["error_count"] + len(result["keyword_results"]) == len(SURUGA_YA_SCAN_KEYWORDS)


def test_ichiban_kuji_task_records_collector_run_with_correct_task_name():
    """運用整備タスク(Collector稼働状況CLI)向けの記録が、正しいtask_name・statusで
    呼ばれることを検証する。DB書き込みそのものはmockし、呼び出し引数のみ確認する。"""
    with patch("app.scheduler.tasks.record_collector_run") as mock_record:
        result = run_ichiban_kuji_collector()

    mock_record.assert_called_once()
    args, kwargs = mock_record.call_args
    assert args[0] == "app.scheduler.tasks.run_ichiban_kuji_collector"
    expected_status = "success" if result["health"] in {"ok", "degraded"} else "failure"
    assert kwargs["status"] == expected_status


def test_suruga_ya_task_records_collector_run_with_correct_task_name():
    with patch("app.scheduler.tasks.record_collector_run") as mock_record:
        run_suruga_ya_price_rising_scan()

    mock_record.assert_called_once()
    args, kwargs = mock_record.call_args
    assert args[0] == "app.scheduler.tasks.run_suruga_ya_price_rising_scan"
    assert kwargs["status"] in {"success", "failure"}
