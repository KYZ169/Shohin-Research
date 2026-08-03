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

from app.collectors.base import ParseError, RawFetchResult
from app.collectors.sources.pokemon_center_online import JST, PokemonCenterOnlineCollector
from app.domain.enums import FulfillmentType, RegionSource, SupportedEventType

PRODUCT_URL = "https://www.pokemoncenter-online.com/9900000006082.html"
RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_pokemon_center.html"

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
    assert normalized.identifiers == {}  # Fixture注記4: 商品コードがJANかは未確認のため設定しない


def test_target_urls_is_empty_because_listing_page_is_unconfirmed():
    """CLAUDE.md 1.4/3節: 商品一覧ページが未確認のため自動巡回対象を持たない。"""
    collector = PokemonCenterOnlineCollector()

    assert collector.target_urls == []


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
