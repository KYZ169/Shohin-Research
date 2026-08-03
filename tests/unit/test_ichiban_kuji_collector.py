"""IchibanKujiCollectorのparse()テスト。

tests/fixtures/html/1kuji_com_top_20260803.md および
tests/fixtures/html/bandaispirits_detail_20260803.md はMarkdown変換済みテキストであり
CSSセレクタの検証には使えないため、それぞれのFixtureで観察されたテキストパターン
(リンクテキストの連結形式、価格/発売日ラベルの並び)を再現した最小限のHTMLに対して
parse()をテストする。実サイトの生HTML構造そのものに対する検証ではない点に注意
(app/collectors/sources/ichiban_kuji.py のモジュールdocstring参照)。
"""

from datetime import datetime
from decimal import Decimal

import pytest

from app.collectors.base import FetchError, ParseError, RawFetchResult
from app.collectors.sources.ichiban_kuji import JST, IchibanKujiCollector
from app.domain.enums import FulfillmentType, RegionSource

# tests/fixtures/html/1kuji_com_top_20260803.md の該当行をそのまま再現
TOP_PAGE_HTML = """
<html><body><div id="pickup">
<a href="https://1kuji.com/products/cutiestreet">店頭販売2026年08月04日(火)より順次発売予定オンライン販売2026年08月04日(火)17:00より販売開始予定一番くじ CUTIE STREET くじを手にする戦いなのです！</a>
<a href="https://1kuji.com/products/kingdom6">店頭販売2026年07月31日(金)より順次発売予定オンライン販売2026年08月03日(月)15:00より販売開始予定一番くじ 春秋戦国大戦キングダム The Animation 知と武の両輪</a>
<a href="https://1kuji.com/products/petitcure">オンライン販売2026年06月24日(水)17:00より販売開始予定一番くじ ぷちきゅあ</a>
</div>
<a href="https://1kuji.com/products">商品一覧</a>
</body></html>
"""

# tests/fixtures/html/bandaispirits_detail_20260803.md 注記1: 無料配布(通常の価格フォーマットでない)
BANDAISPIRITS_FREE_ITEM_HTML = """
<html><body>
<h1>PRODUCTS INFORMATION商品情報</h1>
<img src="https://www.bandaispirits.co.jp/images/uploads/product/image/10208/739e6ea0.jpg" alt="一番くじの一番くじ">
<h2>一番くじの一番くじ</h2>
<h3>商品詳細</h3>
<p>価格</p>
<p>イベント会場でアンケートにご回答いただいた方に無料配布</p>
<p>発売日</p>
<p>2024年02月23日(金・祝)～02月24日(土)</p>
<p>- 発売日（予定）は地域・店舗などによって異なる場合がございますのでご了承ください。</p>
</body></html>
"""

# 注記2の価格フォーマット(1回◯◯円(税◯％込))を、商品詳細ページの
# 価格/発売日ラベル構造に組み合わせた合成テストケース(Fixtureに実例が無いため)
BANDAISPIRITS_NORMAL_PRICE_ITEM_HTML = """
<html><body>
<h1>PRODUCTS INFORMATION商品情報</h1>
<h2>一番くじ しぐれうい 第2弾（仮）</h2>
<h3>商品詳細</h3>
<p>価格</p>
<p>1回790円(税10％込)</p>
<p>発売日</p>
<p>2026年09月05日(土)</p>
</body></html>
"""


def _raw(url: str, html: str) -> RawFetchResult:
    return RawFetchResult(url=url, status_code=200, html=html, fetched_at=datetime.now(tz=JST))


def test_parse_top_page_extracts_store_and_online_release_dates():
    collector = IchibanKujiCollector()

    items = collector.parse(_raw(collector.target_urls[0], TOP_PAGE_HTML))

    cutie = next(i for i in items if i.product_url.endswith("cutiestreet"))
    assert cutie.raw_title == "一番くじ CUTIE STREET くじを手にする戦いなのです！"
    assert cutie.extra["store_release_at"] == datetime(2026, 8, 4, tzinfo=JST)
    assert cutie.extra["online_release_at"] == datetime(2026, 8, 4, 17, 0, tzinfo=JST)
    assert cutie.extra["fulfillment_type"] == FulfillmentType.BOTH


def test_parse_top_page_handles_online_only_item():
    """Fixture注記2: 店頭販売の記載が無く「オンライン販売」のみのケース。"""
    collector = IchibanKujiCollector()

    items = collector.parse(_raw(collector.target_urls[0], TOP_PAGE_HTML))

    petitcure = next(i for i in items if i.product_url.endswith("petitcure"))
    assert petitcure.raw_title == "一番くじ ぷちきゅあ"
    assert petitcure.extra["store_release_at"] is None
    assert petitcure.extra["online_release_at"] == datetime(2026, 6, 24, 17, 0, tzinfo=JST)
    assert petitcure.extra["fulfillment_type"] == FulfillmentType.ONLINE_SHIPPING


def test_parse_top_page_ignores_non_pickup_links():
    collector = IchibanKujiCollector()

    items = collector.parse(_raw(collector.target_urls[0], TOP_PAGE_HTML))

    # "商品一覧"リンクや、日時パターンにマッチしないアンカーは除外される
    assert all(item.product_url != "https://1kuji.com/products" for item in items)
    assert len(items) == 3


def test_parse_top_page_raises_parse_error_when_no_items_found():
    collector = IchibanKujiCollector()

    with pytest.raises(ParseError):
        collector.parse(_raw(collector.target_urls[0], "<html><body>no pickup items here</body></html>"))


def test_parse_bandaispirits_detail_handles_non_standard_free_distribution_price():
    """Fixture注記1: 無料配布ケースでは価格を無理にnullにせず生テキストをextraに残す。"""
    collector = IchibanKujiCollector()

    item = collector.parse(
        _raw("https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=ichibankuji", BANDAISPIRITS_FREE_ITEM_HTML)
    )[0]

    assert item.raw_title == "一番くじの一番くじ"
    assert item.price is None
    assert item.extra["price_raw_text"] == "イベント会場でアンケートにご回答いただいた方に無料配布"


def test_parse_bandaispirits_detail_handles_date_range_by_taking_start_date_only():
    """Fixture注記4: 期間表記(〜MM月DD日)は開始日のみ採用しraw textをextraに残す(要確認事項)。"""
    collector = IchibanKujiCollector()

    item = collector.parse(
        _raw("https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=ichibankuji", BANDAISPIRITS_FREE_ITEM_HTML)
    )[0]

    assert item.start_at == datetime(2024, 2, 23, tzinfo=JST)
    assert item.extra["release_date_raw_text"] == "2024年02月23日(金・祝)～02月24日(土)"


def test_parse_bandaispirits_detail_extracts_normal_price_format():
    """Fixture注記2の価格正規表現(1回◯◯円(税◯％込))が正しく数値化されること。"""
    collector = IchibanKujiCollector()

    item = collector.parse(
        _raw(
            "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=shigureui2",
            BANDAISPIRITS_NORMAL_PRICE_ITEM_HTML,
        )
    )[0]

    assert item.raw_title == "一番くじ しぐれうい 第2弾（仮）"
    assert item.price == Decimal("790")
    assert "price_raw_text" not in item.extra
    assert item.start_at == datetime(2026, 9, 5, tzinfo=JST)


def test_normalize_uses_extracted_fulfillment_type_and_unknown_region():
    collector = IchibanKujiCollector()
    parsed_items = collector.parse(_raw(collector.target_urls[0], TOP_PAGE_HTML))

    normalized = collector.normalize(parsed_items)

    cutie = next(n for n in normalized if n.parsed.product_url.endswith("cutiestreet"))
    assert cutie.fulfillment_type == FulfillmentType.BOTH
    assert cutie.region_source == RegionSource.UNKNOWN
    assert cutie.category_hint == "一番くじ"


def test_validate_does_not_require_price_because_top_page_never_has_it():
    """1kuji.comのPICK UP ITEMには価格情報が無いため、基底クラスの価格必須チェックを緩和している。"""
    collector = IchibanKujiCollector()
    parsed_items = collector.parse(_raw(collector.target_urls[0], TOP_PAGE_HTML))
    normalized = collector.normalize(parsed_items)

    for item in normalized:
        assert item.parsed.price is None
        assert collector.validate(item) == []


async def test_run_end_to_end_against_top_page_fixture_pattern(monkeypatch):
    collector = IchibanKujiCollector()

    async def fake_fetch(target_url: str) -> RawFetchResult:
        return _raw(target_url, TOP_PAGE_HTML)

    monkeypatch.setattr(collector, "fetch", fake_fetch)

    result = await collector.run()

    assert result.success_count == 3
    assert result.error_count == 0


def test_target_urls_never_reference_on_line_1kuji_com():
    """CLAUDE.mdタスク4着手前チェックリスト: on-line.1kuji.comへの自動リクエスト禁止。"""
    collector = IchibanKujiCollector()

    assert all("on-line.1kuji.com" not in url for url in collector.target_urls)


async def test_fetch_raises_fetch_error_for_on_line_1kuji_com():
    collector = IchibanKujiCollector()

    with pytest.raises(FetchError):
        await collector.fetch("https://on-line.1kuji.com/some/apply/path")
