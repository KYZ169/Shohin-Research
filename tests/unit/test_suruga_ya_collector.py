"""SurugaYaCollectorのparse_observations()テスト。

以下のテストはtests/fixtures/html/suruga_ya_kaitori_search_20260803.md
(Markdown変換済みのパイプテーブル)をもとに、実サイトも<table>構造である可能性が
高いと推測して最小限のHTMLを再構成したものである。本番ConoHa VPSで実際に取得した
生HTML(tests/fixtures/raw_html/raw_suruga_ya_search.html)による検証の結果、
<table><tr>構造という推測自体は正しかったが、詳細ページへのリンク(href)が絶対URL
ではなく相対パスだったこと、一部のアンカーがhref=""をNoneとして返すことによる
TypeErrorが実際に発生することを確認し、app/collectors/markets/suruga_ya.pyを
修正した(モジュールdocstring参照)。raw_suruga_ya_search.htmlそのものを使った検証は
test_parse_observations_against_raw_html_fixtureで行う。
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.collectors.base import ParseError, RawFetchResult
from app.collectors.markets.suruga_ya import JST, SurugaYaCollector

RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_search.html"
GRADED_RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_graded.html"

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


# tests/fixtures/html/suruga_ya_jan_confirmation_20260803.md を<table>に再構成したもの。
# 雑貨・小物カテゴリではJANコード(13桁)が一覧ページ時点で取得でき、管理番号も
# "GU"+数字ではなく数字のみの形式になる(注記1・3)。全角スペース(U+3000)が
# JANコードと管理番号の区切りに使われている点もFixtureのまま再現している。
JAN_CONFIRMATION_HTML = f"""
<html><body><table>
{HEADER_ROW}
<tr>
<td></td><td><img src="https://example.com/img.jpg?shinaban=608000898001"></td>
<td>小物(キャラクター) <br>
<a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/608000898">一番くじ 西尾維新アニメプロジェクト</a></td>
<td>4983164650440　608000898</td>
<td>メールにてお見積</td>
<td><a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/608000898">詳細</a></td>
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


def test_parse_observations_jan_is_none_when_not_present_in_trading_card_rows():
    """suruga_ya_jan_confirmation_20260803.md注記2: トレカ系カテゴリではJAN記載が無い。"""
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


# --- suruga_ya_jan_confirmation_20260803.md: JANコード抽出(タスク15) ---


def test_parse_observations_extracts_jan_code_when_present():
    """注記1: 「発売日/型番/JANコード/管理番号」列の13桁数値をJANコードとして抽出する。"""
    collector = SurugaYaCollector()

    observation = collector.parse_observations(_raw(_search_url(""), JAN_CONFIRMATION_HTML))[0]

    assert observation.extra["jan"] == "4983164650440"


def test_parse_observations_extracts_numeric_only_management_number():
    """注記3: 管理番号は"GU"+数字だけでなく数字のみの形式もある(雑貨・小物カテゴリ)。"""
    collector = SurugaYaCollector()

    observation = collector.parse_observations(_raw(_search_url(""), JAN_CONFIRMATION_HTML))[0]

    assert observation.extra["management_number"] == "608000898"
    assert isinstance(observation.extra["management_number"], str)


def test_parse_observations_supports_both_management_number_formats_in_one_call():
    """トレカ系("GU"+数字)と雑貨・小物系(数字のみ)の両方が同じ検索結果に混在しても
    正しく管理番号を抽出できることを確認する。"""
    collector = SurugaYaCollector()
    mixed_html = f"""
    <html><body><table>
    {HEADER_ROW}
    <tr>
    <td></td><td><img src="a.jpg"></td>
    <td>遊戯王/UR/魔法 <br>
    <a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU630031">LOCH-JP003[UR]：黒魔導のカーテン</a></td>
    <td>2026/02/28 GU630031</td>
    <td>400円</td>
    <td><a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/GU630031">詳細</a></td>
    </tr>
    <tr>
    <td></td><td><img src="https://example.com/img.jpg?shinaban=608000898001"></td>
    <td>小物(キャラクター) <br>
    <a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/608000898">一番くじ 西尾維新アニメプロジェクト</a></td>
    <td>4983164650440　608000898</td>
    <td>メールにてお見積</td>
    <td><a href="https://www.suruga-ya.jp/kaitori/kaitori_detail/608000898">詳細</a></td>
    </tr>
    </table></body></html>
    """

    observations = collector.parse_observations(_raw(_search_url(""), mixed_html))
    management_numbers = {o.extra["management_number"] for o in observations}

    assert management_numbers == {"GU630031", "608000898"}
    jan_by_management_number = {o.extra["management_number"]: o.extra["jan"] for o in observations}
    assert jan_by_management_number["GU630031"] is None
    assert jan_by_management_number["608000898"] == "4983164650440"


# 注記4(画像URLのshinabanパラメータへのフォールバック)は実装していない。
# _find_detail_anchor()と_build_observation()のcode_match抽出が同じDETAIL_URL_PATTERNに
# 依存しているため、アンカーが見つかった時点でcode_matchも必ず成功し、
# 「URL由来の抽出が失敗した場合」という前提そのものが到達不能(=テスト不可能な死んだコード)
# になるため、app/collectors/markets/suruga_ya.py側で意図的に実装を見送っている。


# --- 本番VPSでの実データ検証(raw_suruga_ya_search.html)により判明した問題への対応 ---

# 実データ確認済み: 詳細ページへのリンクは絶対URLではなく相対パス
# "/kaitori/kaitori_detail/{管理番号}"。
RELATIVE_HREF_HTML = f"""
<html><body><table>
{HEADER_ROW}
<tr>
<td></td><td><img src="a.jpg"></td>
<td>フィギュア <br>
<a href="/kaitori/kaitori_detail/602038446">一番くじ ワンピースメモリーズ C賞 エースフィギュア</a></td>
<td>2012/11/10 4983164683417 602038446</td>
<td>32,000円</td>
<td><a href="/kaitori/kaitori_detail/602038446"><img src="/ansinSearch/syousai.gif"></a></td>
</tr>
</table></body></html>
"""

# 実データ確認済み: 全選択チェックボックス用のリンク等、href=""(selectolaxはNoneとして
# 返すことがある)の<a>が同じ行に混在するケース。DETAIL_URL_PATTERN.match(None)で
# TypeErrorになっていたバグの再現。
HREF_NONE_HTML = f"""
<html><body><table>
{HEADER_ROW}
<tr>
<td><a href="" onclick="chBxOn(); return false">全てをチェックする</a></td>
<td><img src="a.jpg"></td>
<td>フィギュア <br>
<a href="/kaitori/kaitori_detail/602038446">一番くじ ワンピースメモリーズ C賞 エースフィギュア</a></td>
<td>2012/11/10 602038446</td>
<td>32,000円</td>
<td><a href="/kaitori/kaitori_detail/602038446">詳細</a></td>
</tr>
</table></body></html>
"""


def test_parse_observations_supports_relative_detail_url():
    """実データ確認済み(raw_suruga_ya_search.html): hrefが相対パスでも抽出でき、
    source_urlは絶対URLに変換されること。"""
    collector = SurugaYaCollector()

    observation = collector.parse_observations(_raw(_search_url(""), RELATIVE_HREF_HTML))[0]

    assert observation.extra["management_number"] == "602038446"
    assert observation.extra["jan"] == "4983164683417"
    assert observation.source_url == "https://www.suruga-ya.jp/kaitori/kaitori_detail/602038446"


def test_parse_observations_does_not_crash_on_anchor_with_none_href():
    """実データ確認済み(raw_suruga_ya_search.html): 全選択チェックボックス用のリンク等、
    hrefがNoneとして返るアンカーが同じ行に存在してもTypeErrorにならないこと
    (前回報告済みのバグ)。"""
    collector = SurugaYaCollector()

    observations = collector.parse_observations(_raw(_search_url(""), HREF_NONE_HTML))

    assert len(observations) == 1
    assert observations[0].extra["management_number"] == "602038446"


def test_parse_observations_against_raw_html_fixture():
    """本番ConoHa VPSで実際に取得した生HTML(raw_suruga_ya_search.html)そのものに
    対する検証。相対hrefとNone href混在により修正前は例外が発生していたが、
    修正後は実際の検索結果20件が正しく抽出できることを確認する。"""
    collector = SurugaYaCollector()
    html = RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url(""), html))

    assert len(observations) == 20
    assert all(o.source_url.startswith("https://www.suruga-ya.jp/kaitori/kaitori_detail/") for o in observations)

    # JANコードと管理番号の両方が取れる実データの1件
    jan_item = next(o for o in observations if o.extra["management_number"] == "604075105")
    assert jan_item.extra["jan"] == "4573102677556"
    assert jan_item.amount == Decimal("800")

    # [価格上昇中]タグが実際に付いている1件
    rising_item = next(o for o in observations if o.extra["management_number"] == "602318430")
    assert rising_item.extra["trend"] == "price_rising"

    # メールにてお見積(amount=None)の1件
    quote_item = next(o for o in observations if o.extra["management_number"] == "620009877")
    assert quote_item.amount is None
    assert quote_item.confidence == "D"
    assert quote_item.extra["quote_required"] is True

    # 鑑定品価格(GRADED_PRICE_PATTERN)についてはraw_suruga_ya_search.htmlには該当商品が
    # 含まれていなかったが、raw_suruga_ya_graded.htmlによる検証を
    # test_parse_observations_graded_price_against_raw_html_fixtureで別途行っている。


def test_parse_observations_graded_price_against_raw_html_fixture():
    """CLAUDE.md 3節「駿河屋の鑑定品価格の実データでの動作確認」に対応する検証。
    本番ConoHa VPSで実際に取得した「鑑定品高価買取中」フィルタ結果(raw_suruga_ya_graded.html)
    には、鑑定品価格(【PSA/GEM MT 10】：)がラベルと金額が別要素(<label>直下のテキストと
    その中の<font>)に分かれた実DOM構造で20件中19件に含まれる。GRADED_PRICE_PATTERNの
    \\s*は行送り(改行)込みの空白にもマッチするため、テキストフラット化後も追加の実装変更
    無しで正しくgrade/amountの両方が分離抽出できることを確認する(実データでの初検証)。"""
    collector = SurugaYaCollector()
    html = GRADED_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url(""), html))

    assert len(observations) == 20

    graded_items = [o for o in observations if "graded_price" in o.extra]
    assert len(graded_items) == 19
    assert all(o.extra["graded_price"]["grade"] == "PSA/GEM MT 10" for o in graded_items)
    assert all(isinstance(o.extra["graded_price"]["amount"], Decimal) for o in graded_items)

    # 無鑑定品価格と鑑定品価格が正しく分離されている代表例
    # (【PSA/GEM MT 10】：9,000円 の直前に無鑑定価格500円が別途記載されている行)
    sample = next(o for o in graded_items if o.extra["graded_price"]["amount"] == Decimal("9000"))
    assert sample.amount == Decimal("500")

    # メールにてお見積(quote_required)の1件は、鑑定品価格が併記されていない
    # (=graded_priceが無いのは実データの仕様であり、抽出漏れではない)ことを確認する
    quote_required_items = [o for o in observations if o.extra.get("quote_required")]
    assert len(quote_required_items) == 1
    assert "graded_price" not in quote_required_items[0].extra
    assert quote_required_items[0].amount is None
