"""SurugaYaCollectorのparse_observations()テスト。

tests/fixtures/html/suruga_ya_kaitori_search_20260803.md はMarkdown変換済みの
パイプテーブルであり、実際のHTML(<table><tr><td>)そのものではない。Fixtureが
表形式で出力されていることから実サイトも<table>構造である可能性が高いと推測して
最小限のHTMLを再構成しテストしている。実サイトのDOM構造そのものへの検証ではない点に
注意(app/collectors/markets/suruga_ya.py のモジュールdocstring参照)。
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.collectors.base import ParseError, RawFetchResult
from app.collectors.markets.suruga_ya import JST, SurugaYaCollector

HEADER_ROW = """
<tr>
<th><input type="checkbox"></th><th>商品画像</th><th>種類/タイトル</th>
<th>発売日/型番/JANコード/管理番号</th><th>買取価格</th><th>詳細/売却カートへ</th>
</tr>
"""

# tests/fixtures/html/suruga_ya_kaitori_search_20260803.md の各行を<table>に再構成したもの
SEARCH_RESULTS_HTML = f"""
<html><body><table>
{HEADER_ROW}
<tr>
<td></td><td><img src="a.jpg"></td>
<td>遊戯王/UR/魔法/LIMIT OVER COLLECTION -THE HEROES- <br>
<a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU630031">LOCH-JP003[UR]：黒魔導のカーテン</a></td>
<td>2026/02/28 GU630031</td>
<td>400円</td>
<td><a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU630031">詳細</a></td>
</tr>
<tr>
<td></td><td><img src="b.jpg"></td>
<td>ポケモンカードゲーム/SAR/水/MEGA 拡張パック ニンジャスピナー <br>
<a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU634065">114/083[SAR]：(キラ)メガゲッコウガex</a></td>
<td>2026/03/13 GU634065</td>
<td>30,000円 【PSA/GEM MT 10】： 60,000円</td>
<td><a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU634065">詳細</a></td>
</tr>
<tr>
<td></td><td><img src="c.jpg"></td>
<td>デュエルマスターズ/SR/光/[DM26-RP1]逆札篇 第1弾 「逆転神VS切札竜」 [価格上昇中] <br>
<a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU683685">S2/S11[SR]：世界のY チャクラ・デル・フィン</a></td>
<td>2026/04/11 GU683685</td>
<td>1,800円</td>
<td><a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU683685">詳細</a></td>
</tr>
<tr>
<td></td><td><img src="d.jpg"></td>
<td>遊戯王/SR/効果モンスター/カオス・オリジンズ <br>
<a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU699773">CORI-JP001[SR]：超魔剣士ブラック・カオス</a></td>
<td>2026/04/25 GU699773</td>
<td>メールにてお見積 （<a href="https://www.suruga-ya.jp/man/kaitori/ansin.html">詳しくはこちら</a>）</td>
<td><a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU699773">詳細</a></td>
</tr>
</table></body></html>
"""


def _raw(url: str, html: str) -> RawFetchResult:
    return RawFetchResult(url=url, status_code=200, html=html, fetched_at=datetime.now(tz=JST))


def _search_url(search_word: str = "一番くじ") -> str:
    return f"https://www.suruga-ya.jp/kaitori/search_buy?category=501&search_word={search_word}"


def test_parse_observations_extracts_plain_price():
    collector = SurugaYaCollector()

    observations = collector.parse_observations(_raw(_search_url(), SEARCH_RESULTS_HTML))

    loch = next(o for o in observations if o.extra["management_number"] == "GU630031")
    assert loch.amount == Decimal("400")
    assert loch.confidence == "B"
    assert loch.extra["release_date"] == date(2026, 2, 28)
    assert loch.source_url == "https://www.suruga-ya.jp/kaitori/kaitori_detail/GU630031"
    assert loch.extra["title"] == "LOCH-JP003[UR]：黒魔導のカーテン"


def test_parse_observations_header_row_is_skipped():
    collector = SurugaYaCollector()

    observations = collector.parse_observations(_raw(_search_url(), SEARCH_RESULTS_HTML))

    assert len(observations) == 4


def test_parse_observations_separates_graded_price_from_ungraded_amount():
    """Fixture注記3: amountには無鑑定品の価格のみを採用し、鑑定品価格はextraに分離する。"""
    collector = SurugaYaCollector()

    observations = collector.parse_observations(_raw(_search_url(), SEARCH_RESULTS_HTML))

    graded_item = next(o for o in observations if o.extra["management_number"] == "GU634065")
    assert graded_item.amount == Decimal("30000")
    assert graded_item.extra["graded_price"] == {"grade": "PSA/GEM MT 10", "amount": Decimal("60000")}


def test_parse_observations_flags_price_rising_trend():
    """Fixture注記4: [価格上昇中]タグの有無をextra["trend"]として保持する。"""
    collector = SurugaYaCollector()

    observations = collector.parse_observations(_raw(_search_url(), SEARCH_RESULTS_HTML))

    rising_item = next(o for o in observations if o.extra["management_number"] == "GU683685")
    assert rising_item.extra["trend"] == "price_rising"

    non_rising_item = next(o for o in observations if o.extra["management_number"] == "GU630031")
    assert "trend" not in non_rising_item.extra


def test_parse_observations_email_quote_case_never_uses_dummy_amount():
    """Fixture注記2: メールにてお見積の場合、amount=Noneとし0円等のダミー値を入れない。"""
    collector = SurugaYaCollector()

    observations = collector.parse_observations(_raw(_search_url(), SEARCH_RESULTS_HTML))

    quote_item = next(o for o in observations if o.extra["management_number"] == "GU699773")
    assert quote_item.amount is None
    assert quote_item.confidence == "D"
    assert quote_item.extra["quote_required"] is True


def test_parse_observations_jan_is_none_and_marked_as_todo():
    """Fixture未検証項目: 一覧ページにJAN実値が無いためNoneのままにする。"""
    collector = SurugaYaCollector()

    observations = collector.parse_observations(_raw(_search_url(), SEARCH_RESULTS_HTML))

    assert all(o.extra["jan"] is None for o in observations)


def test_parse_observations_uses_search_word_from_url_as_product_ref():
    collector = SurugaYaCollector()

    observations = collector.parse_observations(_raw(_search_url("一番くじ"), SEARCH_RESULTS_HTML))

    assert all(o.product_ref == "一番くじ" for o in observations)


def test_parse_observations_raises_parse_error_when_no_rows_found():
    collector = SurugaYaCollector()

    with pytest.raises(ParseError):
        collector.parse_observations(_raw(_search_url(), "<html><body><table></table></body></html>"))


def test_filter_irrelevant_default_is_pass_through():
    collector = SurugaYaCollector()
    observations = collector.parse_observations(_raw(_search_url(), SEARCH_RESULTS_HTML))

    assert collector.filter_irrelevant(observations, product_hint=None) == observations
