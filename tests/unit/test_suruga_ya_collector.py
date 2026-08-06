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
from app.collectors.markets.suruga_ya import (
    CATEGORY_DUEL_MASTERS,
    CATEGORY_GUNDAM,
    CATEGORY_NINTENDO_SWITCH,
    CATEGORY_ONE_PIECE_CARD,
    CATEGORY_YUGIOH,
    JST,
    SurugaYaCollector,
)

RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_search.html"
GRADED_RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_graded.html"
ONE_PIECE_RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_onepiece.html"
GUNDAM_RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_gundam.html"
SWITCH_RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_switch.html"
SWITCH_AMIIBO_RAW_HTML_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_switch_amiibo.html"
)
SWITCH_HARDWARE_RAW_HTML_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_switch_hardware.html"
)
YUGIOH_RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_yugioh.html"
DUEL_MASTERS_RAW_HTML_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_suruga_ya_duelmasters.html"
)

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


def test_parse_observations_supports_other_genre_category_without_code_change():
    """CLAUDE.md 3節「駿河屋Collectorの他ジャンル対応確認」に対応する検証。
    category=5010800115(ワンピースカードゲーム)で本番ConoHa VPSから実際に取得した
    生HTML(raw_suruga_ya_onepiece.html)を、コード変更無しでそのままparse_observations()に
    通しても20件全件が正しく抽出できることを確認する。この実データには管理番号が
    "GU"始まりと"GN"始まりの両方が混在しているが、主経路のDETAIL_URL_PATTERNが
    接頭辞非依存([A-Za-z0-9]+)でURLから直接抽出しているため、"GU"限定の
    MANAGEMENT_NUMBER_PATTERN(フォールバック)を変更する必要が無いことも合わせて確認する。
    """
    collector = SurugaYaCollector(category=CATEGORY_ONE_PIECE_CARD)
    html = ONE_PIECE_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url("トレカ"), html))

    assert len(observations) == 20
    assert all(o.extra["management_number"] is not None for o in observations)

    gu_items = [o for o in observations if o.extra["management_number"].startswith("GU")]
    gn_items = [o for o in observations if o.extra["management_number"].startswith("GN")]
    assert gu_items and gn_items  # 接頭辞が混在した実データであることの前提確認

    # GN始まりの管理番号(GU以外の接頭辞)でも価格が正しく取れている代表例
    sample = next(o for o in observations if o.extra["management_number"] == "GN627559")
    assert sample.amount == Decimal("45000")
    assert sample.confidence == "B"

    rising_items = [o for o in observations if o.extra.get("trend") == "price_rising"]
    assert len(rising_items) == 1
    assert rising_items[0].extra["management_number"] == "GU745830"

    quote_required_items = [o for o in observations if o.extra.get("quote_required")]
    assert len(quote_required_items) == 10
    assert all(o.amount is None and o.confidence == "D" for o in quote_required_items)


def test_parse_observations_extracts_model_number_for_gundam_category():
    """CLAUDE.md 3節「駿河屋Collectorのガンダムカテゴリ対応確認」に対応する検証。
    category=5010401(ガンダムプラモデル)で実際に取得した生HTML
    (raw_suruga_ya_gundam.html)には、トレカ系(50108配下)には無かった「型番」が
    「発売日/型番/JANコード/管理番号」の4要素として独立して含まれる
    (例: "2026/04/25<br>5072030<br>4573102720306<br>603227273")。
    MODEL_NUMBER_PATTERNがこの型番を正しく抽出し、JAN/管理番号と混同しないことを
    実データで確認する。20件中19件は型番ありだが、1件は型番欄自体が空
    (実データの仕様であり、抽出漏れではない)ため、その1件のみmodel_number=Noneに
    なることも合わせて確認する。
    """
    collector = SurugaYaCollector(category=CATEGORY_GUNDAM)
    html = GUNDAM_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url(""), html))

    assert len(observations) == 20

    # 型番・JAN・管理番号がいずれも正しく分離抽出されている代表例
    # (2026/04/25<br>5072030<br>4573102720306<br>603227273)
    sample = next(o for o in observations if o.extra["management_number"] == "603227273")
    assert sample.extra["model_number"] == "5072030"
    assert sample.extra["jan"] == "4573102720306"
    assert sample.amount == Decimal("6600")

    with_model = [o for o in observations if o.extra["model_number"] is not None]
    without_model = [o for o in observations if o.extra["model_number"] is None]
    assert len(with_model) == 19
    assert len(without_model) == 1
    # 型番欄が空の1件でも、JAN・管理番号自体は正しく取れていることを確認する
    # (型番の有無が他の識別子の抽出に影響しないことの確認)。
    assert without_model[0].extra["management_number"] == "603230907"
    assert without_model[0].extra["jan"] == "4580886841455"

    # 型番がJANコード(13桁)や管理番号として誤認されていないことの確認
    # (MODEL_NUMBER_PATTERNのmodelグループは常にJAN/管理番号と別の値であるべき)。
    for o in with_model:
        assert o.extra["model_number"] != o.extra["jan"]
        assert o.extra["model_number"] != o.extra["management_number"]


def test_parse_observations_extracts_alphanumeric_model_number_for_switch_category():
    """CLAUDE.md 3節「駿河屋Collectorのニンテンドースイッチカテゴリ対応確認」に対応する検証。
    category=20038(ニンテンドースイッチ)で実際に取得した生HTML(raw_suruga_ya_switch.html)には、
    ガンダムカテゴリ(型番が数字のみ、例:"5072030")とは異なり、"HAC-P-BQPYA"のような
    英数字+ハイフン混在の型番が使われている。MODEL_NUMBER_PATTERNの`\\S+`は元々
    数字限定の実装ではなかったため、コード変更無しでこの形式でも正しく抽出できることを
    実データ20件全件で確認する。
    """
    collector = SurugaYaCollector(category=CATEGORY_NINTENDO_SWITCH)
    html = SWITCH_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url(""), html))

    assert len(observations) == 20
    assert all(o.extra["model_number"] is not None for o in observations)

    sample = next(o for o in observations if o.extra["management_number"] == "109004901")
    assert sample.extra["model_number"] == "HAC-P-BQPYA"
    assert sample.extra["jan"] == "4988602180015"
    assert sample.amount == Decimal("4000")

    # 型番がJAN/管理番号と誤認されていないことの確認(英数字混在でも同様に成立すること)。
    for o in observations:
        assert o.extra["model_number"] != o.extra["jan"]
        assert o.extra["model_number"] != o.extra["management_number"]


def test_parse_observations_preserves_category_text_for_switch_category():
    """CLAUDE.md 3節「駿河屋Collectorのニンテンドースイッチカテゴリ対応確認」に対応する検証。
    技術分析レポート8章で懸念されていた「同じキーワードでも別商品単位になりうる」問題への
    対応として、行の商品種別ラベル(class="category"のdiv)をextra["category_text"]として
    保持するようにした。ニンテンドースイッチカテゴリの実データ20件全てで
    "ニンテンドースイッチソフト"が正しく抽出できることを確認する
    (実データが全件ソフトのみのため、周辺機器/amiibo/本体での分岐確認はCLAUDE.md 3節に
    別途未確認事項として記録している)。
    """
    collector = SurugaYaCollector(category=CATEGORY_NINTENDO_SWITCH)
    html = SWITCH_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url(""), html))

    assert len(observations) == 20
    assert all(o.extra["category_text"] == "ニンテンドースイッチソフト" for o in observations)


def test_parse_observations_category_text_strips_badge_markup():
    """[価格上昇中]/[新規追加]等のバッジがcategory divに併記される行でも、
    category_textにバッジ文言が混入せず商品種別ラベルのみが取れることを確認する。"""
    collector = SurugaYaCollector(category=CATEGORY_NINTENDO_SWITCH)
    html = SWITCH_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url(""), html))

    rising_item = next(o for o in observations if o.extra.get("trend") == "price_rising")
    assert rising_item.extra["category_text"] == "ニンテンドースイッチソフト"


def test_parse_observations_category_text_differs_by_genre():
    """category_textはニンテンドースイッチ専用の特殊対応ではなく、既存カテゴリ
    (ガンダムプラモデル)でも一貫して機能する全カテゴリ共通のフィールドであることを確認する。"""
    collector = SurugaYaCollector(category=CATEGORY_GUNDAM)
    html = GUNDAM_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url(""), html))

    assert all(o.extra["category_text"] == "プラモデル" for o in observations)


def test_parse_observations_category_text_is_none_when_absent():
    """class="category"要素を持たない(=テストの再構成Fixtureのような)HTMLでは、
    category_textがNoneのまま安全に扱われ、例外も発生しないことを確認する。"""
    collector = SurugaYaCollector()

    observations = collector.parse_observations(_raw(_search_url(), SEARCH_RESULTS_HTML))

    assert all(o.extra["category_text"] is None for o in observations)


# --- 遊戯王OCG・デュエル・マスターズカテゴリでの他ジャンル対応確認(2026-08-06) ---


def test_parse_observations_supports_yugioh_category_without_code_change():
    """CLAUDE.md 3節「駿河屋Collectorの遊戯王OCGカテゴリ対応確認」に対応する検証。
    category=501080040(遊戯王OCG、50108配下)で実際に取得した生HTML
    (raw_suruga_ya_yugioh.html)を、コード変更無しでそのままparse_observations()に
    通しても20件全件が正しく抽出できることを確認する。
    """
    collector = SurugaYaCollector(category=CATEGORY_YUGIOH)
    html = YUGIOH_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url(""), html))

    assert len(observations) == 20
    assert all(o.extra["management_number"] is not None for o in observations)
    assert all(o.extra["management_number"].startswith("GU") for o in observations)
    # トレカ系カテゴリ全般の既知の傾向(CLAUDE.md 1.3)通り、JAN/型番は付与されない。
    assert all(o.extra["jan"] is None for o in observations)
    assert all(o.extra["model_number"] is None for o in observations)
    assert all(o.extra["category_text"].startswith("遊戯王") for o in observations)

    sample = next(o for o in observations if o.extra["management_number"] == "GU807865")
    assert sample.extra["title"] == "BETB-JP036[UR]：真紅眼の超越黒竜"
    assert sample.extra["release_date"] == date(2026, 7, 18)
    assert sample.amount is None
    assert sample.confidence == "D"
    assert sample.extra["quote_required"] is True

    rising_items = [o for o in observations if o.extra.get("trend") == "price_rising"]
    assert len(rising_items) == 6

    quote_required_items = [o for o in observations if o.extra.get("quote_required")]
    assert len(quote_required_items) == 5
    assert all(o.amount is None and o.confidence == "D" for o in quote_required_items)


def test_parse_observations_supports_duel_masters_category_without_code_change():
    """CLAUDE.md 3節「駿河屋Collectorのデュエル・マスターズカテゴリ対応確認」に対応する検証。
    category=501080020(デュエル・マスターズ、50108配下)で実際に取得した生HTML
    (raw_suruga_ya_duelmasters.html)を、コード変更無しでそのままparse_observations()に
    通しても20件全件が正しく抽出できることを確認する。
    """
    collector = SurugaYaCollector(category=CATEGORY_DUEL_MASTERS)
    html = DUEL_MASTERS_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url(""), html))

    assert len(observations) == 20
    assert all(o.extra["management_number"] is not None for o in observations)
    assert all(o.extra["management_number"].startswith("GU") for o in observations)
    assert all(o.extra["jan"] is None for o in observations)
    assert all(o.extra["model_number"] is None for o in observations)
    assert all(o.extra["category_text"].startswith("デュエルマスターズ") for o in observations)

    sample = next(o for o in observations if o.extra["management_number"] == "GU760491")
    assert sample.extra["title"] == "DM1/DM1[DMR]：烈しき切札 ドギラゴン逆"
    assert sample.extra["release_date"] == date(2026, 6, 13)
    assert sample.amount == Decimal("4000")
    assert sample.confidence == "B"

    rising_items = [o for o in observations if o.extra.get("trend") == "price_rising"]
    assert len(rising_items) == 2

    quote_required_items = [o for o in observations if o.extra.get("quote_required")]
    assert len(quote_required_items) == 7
    assert all(o.amount is None and o.confidence == "D" for o in quote_required_items)


# --- ニンテンドースイッチhardsoft軸(amiibo/本体)でのcategory_text確認(2026-08-06) ---


def test_parse_observations_category_text_identifies_amiibo():
    """CLAUDE.md 3節「category_textがamiibo/本体でも区別できるか未確認」に対応する検証。
    category=20038で`restrict[]=hardsoft=amiibo`相当の実HTML
    (raw_suruga_ya_switch_amiibo.html)を取得したところ、category_textが
    "ソフト"ではなく"amiibo"という別の値を正しく返すことを実データで確認する。
    """
    collector = SurugaYaCollector(category=CATEGORY_NINTENDO_SWITCH)
    html = SWITCH_AMIIBO_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url(""), html))

    assert len(observations) == 20
    assert all(o.extra["category_text"] == "amiibo" for o in observations)
    # amiiboもJAN/型番は取得できる(ソフト同様、既存実装のまま機能することの確認)。
    assert all(o.extra["jan"] is not None for o in observations)
    assert all(o.extra["model_number"] is not None for o in observations)

    sample = next(o for o in observations if o.extra["management_number"] == "106011655")
    assert sample.extra["title"] == "amiibo ダブルセット[ノア/ミオ] (ゼノブレイドシリーズ)"
    assert sample.extra["model_number"] == "NVL-E-AZ2A"
    assert sample.amount == Decimal("1600")


def test_parse_observations_category_text_identifies_hardware():
    """CLAUDE.md 3節「category_textがamiibo/本体でも区別できるか未確認」に対応する検証。
    category=20038で`restrict[]=hardsoft=本体`相当の実HTML
    (raw_suruga_ya_switch_hardware.html)を取得したところ、category_textが
    "ソフト"ではなく"ニンテンドースイッチハード"という、ゲーム機本体という
    これまでと異なる商品単位を示す値を正しく返すことを実データで確認する。
    """
    collector = SurugaYaCollector(category=CATEGORY_NINTENDO_SWITCH)
    html = SWITCH_HARDWARE_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    observations = collector.parse_observations(_raw(_search_url(""), html))

    assert len(observations) == 20
    assert all(o.extra["category_text"] == "ニンテンドースイッチハード" for o in observations)
    assert all(o.extra["jan"] is not None for o in observations)

    sample = next(o for o in observations if o.extra["management_number"] == "109105123")
    assert sample.extra["title"] == "Nintendo Switch Lite本体 ハイラルエディション"
    assert sample.extra["model_number"] == "HDH-S-DAZAA"
    assert sample.amount == Decimal("19000")

    # 型番欄が空の1件を除き、型番も既存実装のまま抽出できていることの確認
    # (ガンダムカテゴリと同様、型番欄自体が空の実データが1件だけ含まれていた)。
    with_model = [o for o in observations if o.extra["model_number"] is not None]
    assert len(with_model) == 19


def test_parse_observations_category_text_distinguishes_switch_product_units():
    """技術分析レポート8章で懸念されていた「商品単位の混同」対策の前提確認:
    同じcategory=20038(ニンテンドースイッチ)でも、hardsoftが異なれば
    category_textが明確に異なる値を返し、ソフト/amiibo/本体を区別できることを
    3種類のFixtureを横断して確認する(Product Matcher側でこの値を実際に
    照合スコアリングへ組み込むかどうかはCLAUDE.md 3節に別課題として記録、未着手)。
    """
    collector = SurugaYaCollector(category=CATEGORY_NINTENDO_SWITCH)

    software = collector.parse_observations(
        _raw(_search_url(""), SWITCH_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8"))
    )
    amiibo = collector.parse_observations(
        _raw(_search_url(""), SWITCH_AMIIBO_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8"))
    )
    hardware = collector.parse_observations(
        _raw(_search_url(""), SWITCH_HARDWARE_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8"))
    )

    software_texts = {o.extra["category_text"] for o in software}
    amiibo_texts = {o.extra["category_text"] for o in amiibo}
    hardware_texts = {o.extra["category_text"] for o in hardware}

    assert software_texts == {"ニンテンドースイッチソフト"}
    assert amiibo_texts == {"amiibo"}
    assert hardware_texts == {"ニンテンドースイッチハード"}
    # 3種類とも互いに異なる値であること(混同していないこと)の直接確認。
    assert software_texts.isdisjoint(amiibo_texts)
    assert software_texts.isdisjoint(hardware_texts)
    assert amiibo_texts.isdisjoint(hardware_texts)
