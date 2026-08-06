"""Celery Beat結線(app/scheduler/)のテスト。

タスク関数はここではCeleryのbroker経由ではなく直接呼び出す(taskオブジェクトは
通常の関数として呼び出すとbrokerを介さずその場で実行される)。

実際にCeleryワーカー/ブローカー経由でのディスパッチそのものは、ローカルRedisを
brokerとして使い手動で検証済み(実装報告に記載)。

【2026-08-06(タスク20)追記】従来は「このサンドボックスは1kuji.com/suruga-ya.jpへの
アクセスがネットワークポリシーでブロックされている」という環境依存の前提に頼って
FetchErrorの発生を検証していたが、実際にはホスト環境からの外部アクセスが到達可能な
場合があることが判明した(タスク20実装時、単体テストのつもりで実行したところ実際に
1kuji.com/on-line.1kuji.comへ到達し、本番DBへ実データが投入される事態が発生)。
タスク20でrun_ichiban_kuji_collector/run_suruga_ya_price_rising_scanの両方が
ingest_normalized_item()等でDBへ書き込むようになったため、ネットワーク到達性という
不確実な前提に依存したテストは「たまたま到達できてしまうと本番DBを汚染する」という
実害を伴うようになった。そのため、fetch()/search()自体をmonkeypatchして常に
FetchErrorを発生させる決定的なテストに書き換えた(ネットワーク・DBのいずれにも
依存しない、真の意味での単体テストに戻す)。
"""

from unittest.mock import patch

import pytest

from app.collectors.base import FetchError
from app.collectors.markets.suruga_ya import SurugaYaCollector
from app.collectors.sources.ichiban_kuji import IchibanKujiCollector
from app.scheduler.celery_app import celery_app
from app.scheduler.tasks import SURUGA_YA_SCAN_KEYWORDS, run_ichiban_kuji_collector, run_suruga_ya_price_rising_scan


async def _raise_fetch_error(self, target_url: str):
    raise FetchError(f"{target_url}: blocked (test double, network access intentionally not exercised)")


async def _raise_search_error(self, query: str, identifiers: dict, restrict: list[str] | None = None):
    raise FetchError(f"{query}: blocked (test double, network access intentionally not exercised)")


@pytest.fixture(autouse=True)
def _block_real_network_access(monkeypatch):
    """このファイルの全テストで、実際の1kuji.com/on-line.1kuji.com/suruga-ya.jpへの
    アクセスを確実に発生させない(モジュールdocstring参照)。DB書き込みを伴う
    成功パスのテストはtests/integration側の責務とする。"""
    monkeypatch.setattr(IchibanKujiCollector, "fetch", _raise_fetch_error)
    monkeypatch.setattr(SurugaYaCollector, "search", _raise_search_error)


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
    """fetch()が両方のtarget_urlsでFetchErrorを送出するケース。タスクが例外を漏らさず
    health=failingの結果を返せることを確認する。

    2026-08-06(タスク20): 取得できたアイテムが1件も無い場合はDBセッションを開かない
    実装(app/scheduler/tasks.py参照)のため、record_collector_run()以外はDB接続を
    一切必要としない(mockしなくても実DBへ触れない)。
    """
    with patch("app.scheduler.tasks.record_collector_run"):
        result = run_ichiban_kuji_collector()

    assert result["source_name"] == "ichiban_kuji"
    assert result["health"] == "failing"
    assert result["success_count"] == 0
    assert result["error_count"] == 2  # target_urls 2件とも失敗
    assert result["ingested_count"] == 0


def test_suruga_ya_task_handles_network_failure_gracefully():
    with patch("app.scheduler.tasks.record_collector_run"):
        result = run_suruga_ya_price_rising_scan()

    assert result["keyword_results"] == {}
    assert result["error_count"] == len(SURUGA_YA_SCAN_KEYWORDS)
    assert result["pending_notifications"] == []


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
