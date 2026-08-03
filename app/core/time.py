"""共有タイムゾーン定数。

プロジェクト全体の前提(技術分析レポート冒頭): 内部UTC保存/表示Asia/Tokyo。
複数モジュール(collectors/profit/opportunity)で個別にJSTを定義していたのを
ここに一元化する。
"""

from datetime import timedelta, timezone

JST = timezone(timedelta(hours=9))
