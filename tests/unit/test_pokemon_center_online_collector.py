"""PokemonCenterOnlineCollectorのparse()テスト。

以下のテストはtests/fixtures/html/pokemon_center_online_product_20260803.md
(Markdown変換済みテキスト)で観察されたテキストパターン(h1タイトル、ステータス
ラベル、「各種期間」内のラベル+日時の並び)を再現した最小限のHTMLに対して
parse()をテストするものである。本番ConoHa VPSで実際に取得した生HTML
(tests/fixtures/raw_html/raw_pokemon_center.html)による検証の結果、「各種期間」の
ラベル+値の並びはそのまま機能したが、価格(金額と「円」「税込」が別要素に分かれている)
の抽出だけは失敗することが判明し、app/collectors/sources/pokemon_center_online.pyを
修正した(モジュールdocstring参照)。raw_pokemon_center.htmlそのものを使った検証は
test_parse_against_raw_html_fixtureで行う。
"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.collectors.base import FetchError, ParseError, RawFetchResult
from app.collectors.sources.pokemon_center_online import (
    JST,
    NEW_PRODUCT_LIST_URL,
    PokemonCenterOnlineCollector,
    _classify_product_code,
)
from app.domain.enums import FulfillmentType, RegionSource, SupportedEventType

PRODUCT_URL = "https://www.pokemoncenter-online.com/9900000006082.html"
RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_pokemon_center.html"
NORMAL_SALE_RAW_HTML_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_pokemon_center_normal_sale.html"
)
NORMAL_SALE_PRODUCT_URL = "https://www.pokemoncenter-online.com/4521329432069.html"
NEW_PRODUCT_LIST_RAW_HTML_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_pokemon_center_new_product_list.html"
)
NEW_PRODUCT_LIST_SAMPLE_DETAIL_FIXTURE_PATH = (
    Path(__file__).parent.parent
    / "fixtures"
    / "raw_html"
    / "raw_pokemon_center_new_product_list_sample_detail.html"
)

# Fixture(pokemon_center_online_product_20260803.md)の該当箇所を、
# ラベル行+値行の並びを保ったまま最小限のHTMLとして再現したもの。
LOTTERY_PRODUCT_HTML = """
<html><body>
<h1>【抽選販売】ポケモンカードゲーム スカーレット＆バイオレット 拡張パック ブラックボルト BOX拡張パック</h1>
<p>品切れ</p>
<p>5,800円 税込</p>
<p>お一人様 1点限り</p>
<p>返品不可</p>
<p>予約</p>
<p>販売期間 2025年08月22日(金)15時00分</p>
<p>～2025年08月26日(火)16時59分</p>
<p>お届け予定日</p>
<p>【9月下旬発送予定】</p>
<p>商品コード</p>
<p>9900000006082</p>
<p>・抽選応募受け付け期間</p>
<p>2025年8月8日(金)16時00分～2025年8月19日(火)16時59分</p>
<p>・抽選結果発表日</p>
<p>2025年8月22日(金)15時00分以降</p>
<p>・購入および、支払い期間</p>
<p>2025年8月22日(金)15時00分～2025年8月26日(火)16時59分</p>
<p>・商品のお届け時期</p>
<p>9月下旬発送予定</p>
</body></html>
"""

# Fixture注記2: 通常販売ページでは「各種期間」セクション自体が存在しない可能性が高い、
# という推測に基づく合成テストケース(実データ未確認、要検証)。
NORMAL_SALE_PRODUCT_HTML = """
<html><body>
<h1>ポケモンカードゲーム スカーレット＆バイオレット 拡張パック 通常版</h1>
<p>販売中</p>
<p>1,800円 税込</p>
<p>お一人様 3点限り</p>
</body></html>
"""


def _raw(url: str, html: str) -> RawFetchResult:
    return RawFetchResult(url=url, status_code=200, html=html, fetched_at=datetime.now(tz=JST))


def test_parse_extracts_all_four_period_fields_from_lottery_product():
    collector = PokemonCenterOnlineCollector()

    item = collector.parse(_raw(PRODUCT_URL, LOTTERY_PRODUCT_HTML))[0]

    # 抽選応募受け付け期間: 開始はextra、終了(締切)はdeadline_atに相当
    assert item.start_at == datetime(2025, 8, 8, 16, 0, tzinfo=JST)
    assert item.deadline_at == datetime(2025, 8, 19, 16, 59, tzinfo=JST)
    # 抽選結果発表日(「以降」表記=起点)
    assert item.announce_at == datetime(2025, 8, 22, 15, 0, tzinfo=JST)
    # 購入および支払い期間の終了(購入期限)
    assert item.purchase_limit_at == datetime(2025, 8, 26, 16, 59, tzinfo=JST)
    assert item.extra["purchase_start_at"] == datetime(2025, 8, 22, 15, 0, tzinfo=JST)
    # 商品のお届け時期(自由記述)
    assert item.extra["delivery_timing_text"] == "9月下旬発送予定"


def test_parse_extracts_title_price_and_purchase_limit():
    collector = PokemonCenterOnlineCollector()

    item = collector.parse(_raw(PRODUCT_URL, LOTTERY_PRODUCT_HTML))[0]

    assert item.raw_title == "【抽選販売】ポケモンカードゲーム スカーレット＆バイオレット 拡張パック ブラックボルト BOX拡張パック"
    assert item.price == Decimal("5800")
    assert item.extra["purchase_limit_count"] == 1


def test_parse_extracts_inventory_status_from_line_directly_under_title():
    """Fixture注記3: 商品名直下の単独ラベルを在庫状況として扱う。
    「各種期間」より前(販売期間欄)にも別の"予約"という語が出るが、それとは混同しない。"""
    collector = PokemonCenterOnlineCollector()

    item = collector.parse(_raw(PRODUCT_URL, LOTTERY_PRODUCT_HTML))[0]

    assert item.extra["inventory_status"] == "品切れ"


def test_parse_extracts_product_code_from_url():
    collector = PokemonCenterOnlineCollector()

    item = collector.parse(_raw(PRODUCT_URL, LOTTERY_PRODUCT_HTML))[0]

    assert item.extra["product_code"] == "9900000006082"
    assert item.product_url == PRODUCT_URL
    assert item.apply_url == PRODUCT_URL


def test_parse_classifies_lottery_event_type_when_apply_period_section_exists():
    collector = PokemonCenterOnlineCollector()

    item = collector.parse(_raw(PRODUCT_URL, LOTTERY_PRODUCT_HTML))[0]

    assert item.event_type == SupportedEventType.LOTTERY


def test_parse_falls_back_to_normal_sale_when_period_section_is_absent():
    """Fixture注記2に基づく暫定判定(要検証): 「各種期間」セクションが無ければnormal_saleとみなす。"""
    collector = PokemonCenterOnlineCollector()

    item = collector.parse(_raw("https://www.pokemoncenter-online.com/9900000006083.html", NORMAL_SALE_PRODUCT_HTML))[0]

    assert item.event_type == SupportedEventType.NORMAL_SALE
    assert item.deadline_at is None
    assert item.announce_at is None
    assert item.purchase_limit_at is None
    assert item.price == Decimal("1800")
    assert item.extra["purchase_limit_count"] == 3


def test_parse_raises_parse_error_when_title_is_missing():
    collector = PokemonCenterOnlineCollector()

    with pytest.raises(ParseError):
        collector.parse(_raw(PRODUCT_URL, "<html><body><p>タイトルが無いページ</p></body></html>"))


def test_normalize_sets_high_confidence_for_lottery_and_lower_for_normal_sale():
    collector = PokemonCenterOnlineCollector()

    lottery_item = collector.parse(_raw(PRODUCT_URL, LOTTERY_PRODUCT_HTML))[0]
    normal_item = collector.parse(
        _raw("https://www.pokemoncenter-online.com/9900000006083.html", NORMAL_SALE_PRODUCT_HTML)
    )[0]

    lottery_normalized = collector.normalize([lottery_item])[0]
    normal_normalized = collector.normalize([normal_item])[0]

    assert lottery_normalized.confidence_hint == "A"
    assert normal_normalized.confidence_hint == "B"


def test_normalize_uses_online_shipping_and_unknown_region():
    collector = PokemonCenterOnlineCollector()
    item = collector.parse(_raw(PRODUCT_URL, LOTTERY_PRODUCT_HTML))[0]

    normalized = collector.normalize([item])[0]

    assert normalized.fulfillment_type == FulfillmentType.ONLINE_SHIPPING
    assert normalized.region_source == RegionSource.UNKNOWN
    # PRODUCT_URLの商品コード"9900000006082"は"99"始まり(インストア専用疑似コード範囲)
    # のため、identifiersには供給されない(下記test_normalize_*_internal_code_*参照)。
    assert normalized.identifiers == {}


def test_target_urls_contains_only_the_new_product_list_url():
    """2026-08-06(段階1): 新商品一覧ページが取得可能と判明し、ホワイトリスト方式で
    target_urlsに追加した(CLAUDE.md 3節/モジュールdocstring参照)。"""
    collector = PokemonCenterOnlineCollector()

    assert collector.target_urls == [NEW_PRODUCT_LIST_URL]


async def test_fetch_rejects_search_urls_other_than_the_whitelisted_listing_url():
    """/search/配下はNEW_PRODUCT_LIST_URLの完全一致のみ許可する(on-line.1kuji.comと
    同じホワイトリスト方式)。ガードはhttpxでの実通信より前に発火するため、モックなしで
    FetchErrorのみを確認できる。"""
    collector = PokemonCenterOnlineCollector()

    with pytest.raises(FetchError):
        await collector.fetch(
            "https://www.pokemoncenter-online.com/search/?prefn1=refCd&prefv1=P_PIKACHU"
        )
    with pytest.raises(FetchError):
        await collector.fetch("https://www.pokemoncenter-online.com/search/?srule=popular")


def test_parse_returns_empty_list_for_the_listing_url():
    """一覧ページには「各種期間」が無く単層のrun()経由では不完全なParsedItemしか
    作れないため、parse()はNEW_PRODUCT_LIST_URLに対して意図的に空を返す
    (クラスdocstring参照。discover_new_products()を使うこと)。"""
    collector = PokemonCenterOnlineCollector()
    html = NEW_PRODUCT_LIST_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    assert collector.parse(_raw(NEW_PRODUCT_LIST_URL, html)) == []


def test_extract_new_product_codes_against_raw_html_fixture():
    """本番で実際に取得した新商品一覧ページの生HTML(sz=500、モジュールdocstring
    「新商品一覧ページの発見・段階1対応」参照)から、13桁の商品コードを重複無く
    抽出できることを確認する(2026-08-06時点の実件数は275件)。"""
    collector = PokemonCenterOnlineCollector()
    html = NEW_PRODUCT_LIST_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    codes = collector.extract_new_product_codes(_raw(NEW_PRODUCT_LIST_URL, html))

    assert len(codes) == 275
    assert len(set(codes)) == 275  # 重複が無いこと
    assert all(len(code) == 13 and code.isdigit() for code in codes)


def test_extract_new_product_codes_raises_parse_error_when_no_links_found():
    collector = PokemonCenterOnlineCollector()

    with pytest.raises(ParseError):
        collector.extract_new_product_codes(_raw(NEW_PRODUCT_LIST_URL, "<html><body>商品なし</body></html>"))


async def test_discover_new_products_fetches_listing_then_each_individual_product(monkeypatch):
    """段階1の本体: 一覧ページから商品コードを抽出し、各コードをfetch_product()
    (既存のfetch→parse→normalize)へ渡すところまでを、実データ由来のFixture2種
    (一覧275件+個別ページ1件)を使い、モックしたfetch()で(実通信無しに)検証する。
    Beat Schedule結線・DB反映はまだ行わない(段階2、モジュールdocstring参照)ため、
    このテストもDB/Celeryには一切触れない。"""
    collector = PokemonCenterOnlineCollector()
    monkeypatch.setattr(PokemonCenterOnlineCollector, "rate_limit", lambda self: 0)

    listing_html = NEW_PRODUCT_LIST_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")
    detail_html = NEW_PRODUCT_LIST_SAMPLE_DETAIL_FIXTURE_PATH.read_text(encoding="utf-8")
    requested_urls: list[str] = []

    async def fake_fetch(target_url: str) -> RawFetchResult:
        requested_urls.append(target_url)
        if target_url == NEW_PRODUCT_LIST_URL:
            return _raw(target_url, listing_html)
        assert target_url.startswith("https://www.pokemoncenter-online.com/")
        assert target_url.endswith(".html")
        return _raw(target_url, detail_html)

    monkeypatch.setattr(collector, "fetch", fake_fetch)

    items = await collector.discover_new_products()

    assert len(items) == 275
    assert requested_urls[0] == NEW_PRODUCT_LIST_URL
    assert len(requested_urls) == 1 + 275  # 一覧1回 + 個別275回
    # discover_new_products()自身はDB反映やBeat Schedule結線を一切行わない
    # (戻り値はNormalizedItemのリストのみ)。
    assert all(item.product_name for item in items)


async def test_fetch_product_fetches_parses_and_normalizes_by_product_code(monkeypatch):
    collector = PokemonCenterOnlineCollector()

    async def fake_fetch(target_url: str) -> RawFetchResult:
        assert target_url == PRODUCT_URL
        return _raw(target_url, LOTTERY_PRODUCT_HTML)

    monkeypatch.setattr(collector, "fetch", fake_fetch)

    normalized = await collector.fetch_product("9900000006082")

    assert normalized.product_name.startswith("【抽選販売】")
    assert normalized.parsed.deadline_at == datetime(2025, 8, 19, 16, 59, tzinfo=JST)


# --- 本番VPSでの実データ検証(raw_pokemon_center.html)により判明した価格抽出バグへの対応 ---

# 実データ確認済み: 金額の数字("5,800")と単位("円")・税込表記が別要素(入れ子の<small>)に
# 分かれており、フラット化すると別行になる。修正前はPRICE_PATTERNを1行ずつ検索していたため
# 一致せずprice=Noneになっていた。
NESTED_PRICE_ELEMENT_HTML = """
<html><body>
<h1>【抽選販売】ネスト価格要素のテスト商品</h1>
<p class="stock"><span>品切れ</span></p>
<p class="price default">
    <span class="txt">5,800<small>円</small></span>
    <small class="sml">税込</small>
</p>
<p>お一人様 1点限り</p>
<p>・抽選応募受け付け期間</p>
<p>2025年8月8日(金)16時00分～2025年8月19日(火)16時59分</p>
</body></html>
"""


def test_parse_extracts_price_when_amount_and_yen_are_in_separate_nested_elements():
    """実データ確認済み(raw_pokemon_center.html): 金額と「円」「税込」が別要素に
    分かれていても価格を正しく抽出できること(修正前は price=None になっていたバグ)。"""
    collector = PokemonCenterOnlineCollector()

    item = collector.parse(_raw(PRODUCT_URL, NESTED_PRICE_ELEMENT_HTML))[0]

    assert item.price == Decimal("5800")
    assert item.extra["inventory_status"] == "品切れ"


def test_parse_against_raw_html_fixture():
    """本番ConoHa VPSで実際に取得した生HTML(raw_pokemon_center.html)そのものに
    対する検証。「各種期間」の4フィールド抽出と、修正後の価格抽出の両方が
    実データで正しく機能することを確認する。"""
    collector = PokemonCenterOnlineCollector()
    html = RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    item = collector.parse(_raw(PRODUCT_URL, html))[0]

    assert item.raw_title == "【抽選販売】ポケモンカードゲーム スカーレット＆バイオレット 拡張パック ブラックボルト BOX拡張パック"
    assert item.price == Decimal("5800")
    assert item.event_type == SupportedEventType.LOTTERY
    assert item.start_at == datetime(2025, 8, 8, 16, 0, tzinfo=JST)
    assert item.deadline_at == datetime(2025, 8, 19, 16, 59, tzinfo=JST)
    assert item.announce_at == datetime(2025, 8, 22, 15, 0, tzinfo=JST)
    assert item.purchase_limit_at == datetime(2025, 8, 26, 16, 59, tzinfo=JST)
    assert item.extra["delivery_timing_text"] == "9月下旬発送予定"
    assert item.extra["inventory_status"] == "品切れ"
    assert item.extra["purchase_limit_count"] == 1
    assert item.extra["product_code"] == "9900000006082"
    # CLAUDE.md 3節「商品コードがJANコードと一致するかの確認」: 抽選販売商品の
    # 商品コードは"99"始まり(GS1のインストア専用疑似コード範囲)であり、JANとしては
    # 扱わない(internal_code判定)。
    assert item.extra["product_code_type"] == "internal_code"


def test_parse_against_normal_sale_raw_html_fixture():
    """CLAUDE.md 3節「ポケモンセンターオンラインの通常販売ページの実データ確認」に対応する
    検証。抽選なしの通常販売商品(デッキシールド)の実ページには「各種期間」セクションが
    実際に存在せず、event_type=NORMAL_SALEと判定されることを実データで初めて確認する。
    あわせて、抽選ページとはHTML構造が異なる(各種期間ブロックが無い分レイアウトが
    変わっている)通常販売ページでも、価格・商品名・在庫状況・購入上限の抽出が
    従来の実装のまま引き続き機能することを確認する(追加の実装変更は不要だった)。"""
    collector = PokemonCenterOnlineCollector()
    html = NORMAL_SALE_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    item = collector.parse(_raw(NORMAL_SALE_PRODUCT_URL, html))[0]

    assert item.event_type == SupportedEventType.NORMAL_SALE
    assert "デッキシールド" in item.raw_title
    assert item.price == Decimal("990")
    assert item.extra["inventory_status"] == "品切れ"
    assert item.extra["purchase_limit_count"] == 5
    assert item.extra["product_code"] == "4521329432069"
    # 各種期間セクションが無いページなので、期間系フィールドは全てNoneのまま
    assert item.start_at is None
    assert item.deadline_at is None
    assert item.announce_at is None
    assert item.purchase_limit_at is None
    assert item.extra["delivery_timing_text"] is None
    # CLAUDE.md 3節「商品コードがJANコードと一致するかの確認」: 通常販売商品の
    # 商品コードは"45"始まり(GS1 Japan管理の正規事業者コード範囲)であり、
    # JANコードとして扱う(jan判定)。
    assert item.extra["product_code_type"] == "jan"

    normalized = collector.normalize([item])[0]
    # 通常販売(各種期間が確認できない)は信頼度Bになる(normalize()のconfidence_hintロジック)
    assert normalized.confidence_hint == "B"
    # "45"始まりの商品コードはJANとしてidentifiersに供給される。
    assert normalized.identifiers == {"jan": "4521329432069"}


# --- 商品コード先頭2桁によるJAN判定(サンプル5件による確認、2026-08-05) ---
#
# 通常販売商品3件("4521329432069"/"4521329413051"/"4521329338453")は全て"45"始まり、
# 抽選販売商品2件("9900000006082"/"9900000006808")は全て"99"始まりだった。
# JAN-13の国コード部のうち"45"/"49"はGS1 Japan管理の正規事業者コード範囲、
# "99"はGS1がインストアマーキング用に予約している疑似コード範囲にあたる
# (モジュールdocstring参照。サンプル5件からの経験則であり、GS1公式文書での
# 裏取りではないため高確率だが100%の保証ではない)。


@pytest.mark.parametrize(
    "product_code",
    ["4521329432069", "4521329413051", "4521329338453"],
)
def test_classify_product_code_returns_jan_for_45_prefix(product_code):
    assert _classify_product_code(product_code) == "jan"


@pytest.mark.parametrize("product_code", ["4900000000000", "4912345678901"])
def test_classify_product_code_returns_jan_for_49_prefix(product_code):
    assert _classify_product_code(product_code) == "jan"


@pytest.mark.parametrize(
    "product_code",
    ["9900000006082", "9900000006808"],
)
def test_classify_product_code_returns_internal_code_for_99_prefix(product_code):
    assert _classify_product_code(product_code) == "internal_code"


def test_classify_product_code_returns_none_when_code_is_none():
    assert _classify_product_code(None) is None


def test_normalize_omits_jan_for_internal_code_product():
    """"99"始まり(internal_code判定)の商品は、値自体はextra["product_code"]に
    残るが、identifiersには供給されない(=JAN一致スコアリングの対象外)ことを確認する。"""
    collector = PokemonCenterOnlineCollector()
    item = collector.parse(_raw(PRODUCT_URL, LOTTERY_PRODUCT_HTML))[0]
    assert item.extra["product_code"] == "9900000006082"  # 値自体は保持されている

    normalized = collector.normalize([item])[0]

    assert normalized.identifiers == {}


def test_normalize_supplies_jan_for_45_prefixed_product():
    """"45"始まり(jan判定)の商品は、identifiers["jan"]として商品コードが供給される
    ことを確認する(product_matcher.calc_match_score()のJAN一致+100の対象になる)。
    商品コードはHTML本文ではなくURL(PRODUCT_URL_PATTERN)から抽出される
    (_parse_product_detail()参照)ため、URLの商品コードのみ差し替える。"""
    collector = PokemonCenterOnlineCollector()
    item = collector.parse(
        _raw("https://www.pokemoncenter-online.com/4521329413051.html", NORMAL_SALE_PRODUCT_HTML)
    )[0]
    assert item.extra["product_code"] == "4521329413051"

    normalized = collector.normalize([item])[0]

    assert normalized.identifiers == {"jan": "4521329413051"}
